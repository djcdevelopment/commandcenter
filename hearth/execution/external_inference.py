"""Idempotent import of direct-owned, externally executed physical attempts.

This is an accounting entry point, never a scheduler or an inference provider.
The import lock spans a lifecycle; durable canonical facts remain the authority
after interruption. No raw NDJSON append or projection-row mutation is used.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
from pathlib import Path

from .ledger import ExecutionLedger
from .model import new_execution_event

SOURCE = {"transport": "external", "adapter": "deepagents-direct", "execution_mode": "external",
          "accounting_owner": "direct"}
ADAPTERS = {"deepagents.physical-attempt.v1": "deepagents-direct",
            "hermes.physical-attempt.v1": "hermes-direct"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def identity(prefix, key):
    return prefix + "_" + hashlib.sha256(key.encode()).hexdigest()[:32]


def validate_receipt(receipt):
    if receipt.get("schema") not in ADAPTERS:
        raise ValueError("unrecognized receipt schema")
    if receipt.get("accounting_owner") != "direct" or receipt.get("execution_mode") != "external":
        raise ValueError("only explicitly direct-owned external attempts may be imported")
    for key in ("run_id", "attempt_id", "logical_call_id", "framework_run_id", "supervisor_id",
                "model_alias", "started_at", "finished_at", "phase", "outcome"):
        if not isinstance(receipt.get(key), str) or not receipt[key]:
            raise ValueError("missing receipt identity: " + key)
    if receipt["phase"] not in {"setup", "task", "restoration"}:
        raise ValueError("invalid phase")
    if receipt["outcome"] not in {"succeeded", "failed", "unknown"}:
        raise ValueError("invalid terminal outcome")
    provider = receipt.get("provider") or {}
    if provider.get("execution_class") != "local" or not provider.get("identity_sha256"):
        raise ValueError("verified local provider identity required")
    usage = receipt.get("usage")
    if usage is not None:
        if set(usage) != {"tokens_in", "tokens_out"}:
            raise ValueError("usage requires exactly tokens_in and tokens_out")
        for value in usage.values():
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("usage must be nonnegative integer or explicitly unknown")
    canonical(receipt)


def import_receipt(ledger: ExecutionLedger, receipt: dict):
    validate_receipt(receipt)
    adapter = ADAPTERS[receipt["schema"]]
    key = adapter.removesuffix("-direct") + "/direct/" + receipt["run_id"] + "/" + receipt["attempt_id"]
    source = {**SOURCE, "adapter": adapter}
    content_hash = hashlib.sha256(canonical(receipt).encode()).hexdigest()
    job_id, request_id, invocation_id = (identity(prefix, key) for prefix in ("job", "req", "inv"))
    # Use the ledger's existing cross-process append lock, including projection
    # refresh before lookup. append() is reentrant under the same lock.
    with ledger._lock, ledger._interprocess_append_lock():
        if ledger._projection_is_stale():
            ledger.rebuild()
        state = ledger.get_job(job_id)
        if state and state["desired"].get("receipt_sha256") != content_hash:
            raise ValueError("conflicting external receipt replay: " + receipt["attempt_id"])
        if state and state["status"] in {"succeeded", "failed"}:
            return {"job_id": job_id, "invocation_id": invocation_id, "duplicate": True}

        def append(kind, observed=None):
            event = new_execution_event(kind, request_id=request_id, job_id=job_id,
                invocation_id=invocation_id if kind.startswith("invocation.") else None,
                principal={"type": "external_runner", "id": adapter, "authenticated": False},
                source=source, operation="inference.external",
                desired={"idempotency_key": key, "receipt_sha256": content_hash,
                         "receipt": receipt} if kind == "request.accepted" else None,
                observed=observed)
            event["event_id"] = identity("evt", key + "/" + kind)
            event["timestamp"] = receipt["finished_at"] if kind.endswith(("succeeded", "failed")) else receipt["started_at"]
            ledger.append(event)

        if state is None:
            append("request.accepted")
        state = ledger.get_job(job_id)
        if not state["invocations"]:
            append("invocation.started", {"execution_mode": "external", "accounting_owner": "direct"})
        state = ledger.get_job(job_id)
        outcome = "succeeded" if receipt["outcome"] == "succeeded" else "failed"
        if state["invocations"][0]["status"] == "running":
            append("invocation." + outcome, {"external_receipt": receipt, "receipt_sha256": content_hash})
        append("job." + outcome)
    return {"job_id": job_id, "invocation_id": invocation_id, "duplicate": False}


def import_outbox(outbox: Path, ledger: ExecutionLedger):
    import sqlite3
    with closing(sqlite3.connect(outbox)) as db:
        rows = db.execute("SELECT attempt_id, terminal FROM attempts WHERE terminal IS NOT NULL ORDER BY rowid").fetchall()
    results = []
    for attempt_id, terminal in rows:
        result = import_receipt(ledger, json.loads(terminal))
        with closing(sqlite3.connect(outbox)) as db:
            with db:
                db.execute("UPDATE attempts SET imported_job_id=? WHERE attempt_id=?", (result["job_id"], attempt_id))
        results.append(result)
    return results
