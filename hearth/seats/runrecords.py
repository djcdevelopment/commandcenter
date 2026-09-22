"""Import dated run records as ``run.record-attempt.v1`` receipts (ADR-0047, amended).

``python -m hearth.seats.runrecords [--ledger FILE] [--dry-run] [--json]``

The seat harvest reads what a server wrote about itself. Before the seats kept
logs, research on this machine was written down by its drivers: a benchmark
lab's JSON, a planning matrix's scored cells, a load test's request rows, a
bench table's rows. Those are self-reports, but they are dated, per-request
and retained, and Derek admitted them on 2026-09-19 so the July work could be
seen beside the August seats. The rule is the smallest one that keeps the
count honest:

- one attempt per recorded cell, request or bench row; re-scoring passes over
  the same outputs are not counted;
- tokens only when the record itself carries them, never derived from a target
  or a chunk count;
- a record that summarizes work another receipt already covers is excluded
  (the Qwen3.8 summary rows in the corpus restate the seat logs);
- the timestamp is the record's own when it has one, else the run directory
  stamp, and the receipt says which and how far it may be off.

Every importer is a pure function of its files; idempotency is the attempt
identity, derived from the adapter, the file and the row.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from hearth.seats.receipts import (
    RUN_SCHEMA, RUN_SOURCE_TRANSPORT, SeatReceiptsLedger, canonical, identity,
    provider_identity, validate_run_record,
)

DEFAULT_LEDGER = "hearth/var/seats/run-records.ndjson"
REPORT_SCHEMA = "run.records-report.v1"
UNRECORDED = "unrecorded"

#: (adapter, root). Read-only inputs on this workstation; nothing is written there.
DEFAULT_SOURCES = (
    ("ollama-backend-lab", r"C:\work\tuning\ollama-backend-lab\results"),
    ("planning-matrix", r"C:\work\commandcenter\hearth\var\experiments"),
    ("am4-load-test", r"C:\work\commandcenter\am4-fleet-node\results\raw-2026-07-18-gambit"),
    ("bench-rows", r"C:\work\commandcenter\corpus\backfills"),
)
#: Bench-row adapters that restate work another receipt already covers.
BENCH_ROW_ADAPTERS_EXCLUDED = {"qwen38-summary.json.v1"}
#: Bench-row adapters whose n_prompt / n_gen are per-run token counts.
BENCH_ROW_ADAPTERS_WITH_TOKENS = {"llama-bench.json.v1"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _source(adapter: str) -> dict[str, str]:
    return {"transport": RUN_SOURCE_TRANSPORT, "adapter": adapter,
            "execution_mode": "external", "accounting_owner": "direct"}


def _receipt(adapter: str, run_id: str, row_key: str, row: dict[str, Any], *, model: str,
             started: datetime, finished: datetime, method: str, error_bound_s: int,
             usage: Optional[dict[str, Optional[int]]], reason: Optional[str], outcome: str,
             harvested_at: str) -> dict[str, Any]:
    run = identity("run", adapter + "/" + run_id)
    return {
        "schema": RUN_SCHEMA,
        "run_id": run,
        "attempt_id": identity("att", run + "/" + row_key),
        "record_sha256": _sha(canonical(row)),
        "provider": {"execution_class": "local", "identity_sha256": provider_identity(model, UNRECORDED, 0),
                     "model_name": model},
        "started_at": _rfc3339(started),
        "finished_at": _rfc3339(finished),
        "timestamp_derivation": {"method": method, "error_bound_s": error_bound_s},
        "usage": usage,
        "usage_unknown_reason": reason,
        "outcome": outcome,
        "source": _source(adapter),
        "harvested_at": harvested_at,
    }


def _load_json_lenient(path: Path) -> Any:
    raw = path.read_bytes()
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return json.loads(raw.decode("cp1252"))


# --- importers ---------------------------------------------------------------

def import_ollama_backend_lab(root: Path, harvested_at: str) -> Iterable[dict[str, Any]]:
    """Each JSON record is one measured call with PromptTokens / OutputTokens.

    The CSV siblings duplicate the JSON and are not read. Timestamps are naive
    local time (the lab ran in Pacific time), so the derivation says so with an
    error bound of a day.
    """
    for path in sorted(root.glob("*.json")):
        data = _load_json_lenient(path)
        rows = data if isinstance(data, list) else [data]
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or "PromptTokens" not in row:
                continue
            started = datetime.fromisoformat(str(row["Timestamp"])).replace(tzinfo=timezone.utc)
            wall = float(row.get("WallSeconds") or 0)
            usage = {"tokens_in": int(row["PromptTokens"]), "tokens_out": int(row["OutputTokens"])}
            model = str(row.get("Model") or UNRECORDED)
            yield _receipt("ollama-backend-lab", path.stem, str(index), row, model=model,
                           started=started, finished=started + timedelta(seconds=wall),
                           method="record_timestamp_naive_local", error_bound_s=86400,
                           usage=usage, reason=None, outcome="succeeded", harvested_at=harvested_at)


def _stamp_from_dirname(name: str) -> Optional[datetime]:
    for part in name.split("-"):
        if len(part) == 16 and part.endswith("Z") and part[8] == "T":
            return datetime.strptime(part, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return None


def import_planning_matrix(root: Path, harvested_at: str) -> Iterable[dict[str, Any]]:
    """One attempt per scored cell of the planning matrix, study and doc-adr runs.

    A cell is a planning task the planner and critic worked through and a judge
    scored. ``cost.tokens_out`` is kept when present; prompt tokens were never
    recorded, so ``tokens_in`` is null. Doc-adr rows record the tokens of a
    cloud generation call, which are not local work and are not kept. Judge
    re-scoring files are not counted. Cells share the run directory stamp.
    """
    for run_dir in sorted(root.iterdir()):
        name = run_dir.name
        if not run_dir.is_dir() or not name.startswith(("matrix-", "study-", "doc-adr-bench-")):
            continue
        stamp = _stamp_from_dirname(name)
        if stamp is None:
            continue
        rows: list[dict[str, Any]] = []
        dataset = run_dir / "dataset.json"
        if dataset.exists():
            data = json.loads(dataset.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("rows") or data.get("cells") or []
            rows = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        else:
            rows_file = run_dir / "rows.jsonl"
            if rows_file.exists():
                rows = [json.loads(line) for line in rows_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        for index, row in enumerate(rows):
            # Cell ids repeat across repetitions inside one run, so the row
            # position is part of the identity.
            key = f"{row.get('cell_id') or row.get('task_id') or 'row'}#{index}"
            if name.startswith("doc-adr-bench-"):
                model = "qwen3-coder:30b (judge)"
                usage, reason = None, "record carries the tokens of a cloud generation call, not local work"
            else:
                planner = (row.get("planner") or {}).get("model") or UNRECORDED
                critic = (row.get("critic") or {}).get("model") or UNRECORDED
                model = f"{planner} -> {critic}"
                tokens_out = (row.get("cost") or {}).get("tokens_out")
                if type(tokens_out) is int and tokens_out >= 0:
                    usage, reason = {"tokens_in": None, "tokens_out": tokens_out}, None
                else:
                    usage, reason = None, "cell record carries no token count"
            duration_ms = float((row.get("cost") or {}).get("duration_ms") or row.get("duration_ms") or 0)
            ok = row.get("ok")
            outcome = "succeeded" if ok is True else ("failed" if ok is False else "unknown")
            yield _receipt("planning-matrix", name, key, row, model=model, started=stamp,
                           finished=stamp + timedelta(milliseconds=duration_ms),
                           method="run_directory_stamp", error_bound_s=86400,
                           usage=usage, reason=reason, outcome=outcome, harvested_at=harvested_at)


def import_am4_load_test(root: Path, harvested_at: str) -> Iterable[dict[str, Any]]:
    """One attempt per request row of the July load test on the resident MoE.

    The rows carry latencies and a prompt target, not token counts, so usage is
    unknown; ``t_start`` is an epoch second, so the timestamp is the record's own.
    """
    path = root / "requests.jsonl"
    if not path.exists():
        return
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        started = datetime.fromtimestamp(float(row["t_start"]), tz=timezone.utc)
        total = float(row.get("total_s") or 0)
        outcome = "succeeded" if row.get("ok") is True else "failed"
        # Tags repeat across waves, so the row position is part of the identity.
        yield _receipt("am4-load-test", root.name, f"{row.get('tag') or 'row'}#{index}", row, model=UNRECORDED,
                       started=started, finished=started + timedelta(seconds=total),
                       method="record_timestamp", error_bound_s=0, usage=None,
                       reason="request record carries a prompt target and a chunk count, not token counts",
                       outcome=outcome, harvested_at=harvested_at)


def import_bench_rows(root: Path, harvested_at: str) -> Iterable[dict[str, Any]]:
    """One attempt per bench row, excluding rows that restate other receipts.

    llama-bench rows carry ``n_prompt`` / ``n_gen`` per run and ``n_runs``, so
    their tokens are counted; batched-bench rows would be counted as attempts
    with unknown usage, but the backfilled ones carry no timestamp and a
    receipt without a date is not admitted; summary rows of the Qwen3.8
    campaign are excluded because the seat logs already receipt that work.
    """
    for path in sorted(root.glob("*.jsonl")):
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            row = json.loads(line)
            adapter = str((row.get("source") or {}).get("adapter") or "")
            if adapter in BENCH_ROW_ADAPTERS_EXCLUDED:
                continue
            stamp = row.get("timestamp")
            if not isinstance(stamp, str):
                continue
            started = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            n_runs = int(row.get("n_runs") or 1)
            if adapter in BENCH_ROW_ADAPTERS_WITH_TOKENS:
                usage = {"tokens_in": int(row.get("n_prompt") or 0) * n_runs,
                         "tokens_out": int(row.get("n_gen") or 0) * n_runs}
                reason = None
            else:
                usage, reason = None, "batched-bench row records throughput, not per-sequence token counts"
            yield _receipt("bench-rows", path.stem, str(row.get("row_id") or index), row,
                           model=str(row.get("model") or UNRECORDED), started=started, finished=started,
                           method="record_timestamp", error_bound_s=0, usage=usage, reason=reason,
                           outcome="succeeded", harvested_at=harvested_at)


IMPORTERS = {
    "ollama-backend-lab": import_ollama_backend_lab,
    "planning-matrix": import_planning_matrix,
    "am4-load-test": import_am4_load_test,
    "bench-rows": import_bench_rows,
}


def harvest(ledger_path: Path, sources: Iterable[tuple[str, str]] = DEFAULT_SOURCES, *,
            dry_run: bool = False, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    harvested_at = _rfc3339(now)
    ledger = SeatReceiptsLedger(Path(ledger_path))
    known = ledger.attempt_ids()
    report_rows = []
    totals = {"attempts": 0, "measured": 0, "unknown": 0, "failed": 0, "tokens_in": 0, "tokens_out": 0,
              "new_receipts": 0, "sources": 0}
    for adapter, root in sources:
        root = Path(root)
        row = {"adapter": adapter, "present": root.exists(), "attempts": 0, "measured": 0, "unknown": 0,
               "failed": 0, "tokens_in": 0, "tokens_out": 0, "new_receipts": 0}
        if root.exists():
            receipts = list(IMPORTERS[adapter](root, harvested_at))
            for receipt in receipts:
                validate_run_record(receipt)
                row["attempts"] += 1
                usage = receipt["usage"]
                if usage is None:
                    row["unknown"] += 1
                else:
                    row["measured"] += 1
                    row["tokens_in"] += usage["tokens_in"] or 0
                    row["tokens_out"] += usage["tokens_out"] or 0
                row["failed"] += int(receipt["outcome"] == "failed")
            if len({r["attempt_id"] for r in receipts}) != len(receipts):
                raise ValueError(f"{adapter}: attempt identities are not unique within the import")
            fresh = []
            for receipt in receipts:
                if receipt["attempt_id"] not in known:
                    known.add(receipt["attempt_id"])
                    fresh.append(receipt)
            row["new_receipts"] = len(fresh)
            if fresh and not dry_run:
                written = ledger.append(fresh, validator=validate_run_record)
                if written != len(fresh):
                    raise ValueError(f"{adapter}: ledger wrote {written} of {len(fresh)} receipts")
            totals["sources"] += 1
        for key in ("attempts", "measured", "unknown", "failed", "tokens_in", "tokens_out", "new_receipts"):
            totals[key] += row[key]
        report_rows.append(row)
    return {"schema": REPORT_SCHEMA, "dry_run": dry_run, "at": harvested_at, "sources": report_rows, "totals": totals}


def render_table(report: dict[str, Any]) -> str:
    lines = [f"{'adapter':22} {'present':>7} {'attempts':>8} {'measured':>8} {'unknown':>7} {'failed':>6} {'tok_in':>10} {'tok_out':>8} {'new':>6}"]
    for row in report["sources"]:
        lines.append(f"{row['adapter']:22} {str(row['present']):>7} {row['attempts']:8} {row['measured']:8} {row['unknown']:7} "
                     f"{row['failed']:6} {row['tokens_in']:10} {row['tokens_out']:8} {row['new_receipts']:6}")
    t = report["totals"]
    lines.append(f"total: attempts {t['attempts']}, measured {t['measured']}, unknown {t['unknown']}, failed {t['failed']}, "
                 f"tokens in {t['tokens_in']:,}, out {t['tokens_out']:,}, new receipts {t['new_receipts']}"
                 + ("  [dry run: nothing written]" if report["dry_run"] else ""))
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    from hearth.toolsurface._scope import resolve_in_scope

    parser = argparse.ArgumentParser(prog="python -m hearth.seats.runrecords",
                                     description="Import dated run records as research-run receipts (ADR-0047).")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = harvest(resolve_in_scope(args.ledger), dry_run=args.dry_run)
    except Exception as exc:
        print(f"run records: FAILED {exc}")
        return 1
    print(json.dumps(report, sort_keys=True) if args.json else render_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
