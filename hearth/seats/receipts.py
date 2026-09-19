"""The ``seat.physical-attempt.v1`` receipt and its append-only ledger.

A receipt is one task the seat log says the server processed. It is built from
the server ``print_timing`` block, never from a driver self-report, and it is
immutable once written. The ledger is a third input beside the gateway and
execution ledgers and is never merged into either: importing these rows as
execution jobs would inflate door-brokered job counts and re-lane research
work into "Inference jobs" (ADR-0047, decision 3).

Privacy is enforced at the row: no drive-letter path may appear in any string
value, and the model is identified by name, build and context, never by file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from hearth.execution.ledger import _lock_file, _unlock_file


def canonical(value: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace, no NaN. Byte-identical to the
    ``canonical`` helper of the direct-inference receipt importer so the two
    receipt families hash the same way."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def identity(prefix: str, key: str) -> str:
    """``<prefix>_<first 32 hex of sha256(key)>``, the same shape the execution
    ledger uses for job, request and invocation ids."""
    return prefix + "_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]

SCHEMA = "seat.physical-attempt.v1"
SOURCE = {"transport": "server-log", "adapter": "seat-log-harvest",
          "execution_mode": "external", "accounting_owner": "direct"}
DERIVATION_METHOD = "log_mtime_minus_last_elapsed"
ERROR_BOUND_S = 2
OUTCOMES = {"succeeded", "unknown"}
RECEIPT_KEYS = {
    "schema", "seat_id", "attempt_id", "task", "slot", "log_basename", "seat_epoch_sha256",
    "provider", "started_at", "finished_at", "timestamp_derivation", "usage",
    "usage_unknown_reason", "timing", "outcome", "source", "harvested_at",
}
PROVIDER_KEYS = {"execution_class", "identity_sha256", "model_name", "build", "n_ctx_seq"}
DERIVATION_KEYS = {"method", "epoch_start", "log_mtime", "error_bound_s"}
_DRIVE_PATH = re.compile(r"[A-Za-z]:\\")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class SeatReceiptError(ValueError):
    """A receipt that must not be written or trusted."""


def provider_identity(model_name: str, build: str, n_ctx_seq: int) -> str:
    return hashlib.sha256(f"{model_name}|{build}|{n_ctx_seq}".encode("utf-8")).hexdigest()


def seat_identity(basename: str, seat_epoch_sha256: str) -> str:
    return identity("seat", basename + "/" + seat_epoch_sha256)


def attempt_identity(seat_id: str, task: int) -> str:
    return identity("att", seat_id + "/" + str(task))


def _walk_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _require_str(receipt: dict, key: str) -> None:
    if not isinstance(receipt.get(key), str) or not receipt[key]:
        raise SeatReceiptError("missing receipt identity: " + key)


def validate_receipt(receipt: Any) -> None:
    if not isinstance(receipt, dict):
        raise SeatReceiptError("receipt must be an object")
    if set(receipt) != RECEIPT_KEYS:
        raise SeatReceiptError(
            f"bad receipt keys: missing={sorted(RECEIPT_KEYS - set(receipt))} "
            f"extra={sorted(set(receipt) - RECEIPT_KEYS)}")
    if receipt["schema"] != SCHEMA:
        raise SeatReceiptError("unrecognized receipt schema")
    for key in ("seat_id", "attempt_id", "log_basename", "seat_epoch_sha256",
                "started_at", "finished_at", "outcome", "harvested_at"):
        _require_str(receipt, key)
    if not _HEX64.match(receipt["seat_epoch_sha256"]):
        raise SeatReceiptError("seat_epoch_sha256 must be a sha256 hex digest")
    for key in ("task", "slot"):
        if type(receipt.get(key)) is not int or receipt[key] < 0:
            raise SeatReceiptError(key + " must be a nonnegative integer")
    if receipt["outcome"] not in OUTCOMES:
        raise SeatReceiptError("invalid terminal outcome")
    if receipt["source"] != SOURCE:
        raise SeatReceiptError("receipt source must be the seat-log harvest")
    provider = receipt.get("provider")
    if not isinstance(provider, dict) or set(provider) != PROVIDER_KEYS:
        raise SeatReceiptError("provider requires exactly " + ", ".join(sorted(PROVIDER_KEYS)))
    if provider["execution_class"] != "local" or not _HEX64.match(str(provider["identity_sha256"])):
        raise SeatReceiptError("verified local provider identity required")
    if not isinstance(provider["model_name"], str) or not provider["model_name"]:
        raise SeatReceiptError("provider.model_name required")
    if not isinstance(provider["build"], str) or not provider["build"]:
        raise SeatReceiptError("provider.build required")
    if type(provider["n_ctx_seq"]) is not int or provider["n_ctx_seq"] <= 0:
        raise SeatReceiptError("provider.n_ctx_seq must be a positive integer")
    derivation = receipt.get("timestamp_derivation")
    if not isinstance(derivation, dict) or set(derivation) != DERIVATION_KEYS:
        raise SeatReceiptError("timestamp_derivation requires exactly " + ", ".join(sorted(DERIVATION_KEYS)))
    if derivation["method"] != DERIVATION_METHOD or derivation["error_bound_s"] != ERROR_BOUND_S:
        raise SeatReceiptError("unrecognized timestamp derivation")
    usage = receipt["usage"]
    if usage is None:
        if not isinstance(receipt["usage_unknown_reason"], str) or not receipt["usage_unknown_reason"]:
            raise SeatReceiptError("unknown usage requires a reason")
        if receipt["outcome"] != "unknown" or receipt["timing"] is not None:
            raise SeatReceiptError("a task without usage is unknown and carries no timing")
    else:
        if receipt["usage_unknown_reason"] is not None:
            raise SeatReceiptError("measured usage carries no unknown reason")
        if not isinstance(usage, dict) or set(usage) != {"tokens_in", "tokens_out"}:
            raise SeatReceiptError("usage requires exactly tokens_in and tokens_out")
        for value in usage.values():
            if type(value) is not int or value < 0:
                raise SeatReceiptError("usage must be nonnegative integers")
        timing = receipt["timing"]
        if not isinstance(timing, dict) or set(timing) != {"prompt_ms", "predicted_ms"}:
            raise SeatReceiptError("timing requires exactly prompt_ms and predicted_ms")
        for value in timing.values():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise SeatReceiptError("timing must be nonnegative numbers")
        if receipt["outcome"] != "succeeded":
            raise SeatReceiptError("a measured task is succeeded")
    for text in _walk_strings(receipt):
        if _DRIVE_PATH.search(text):
            raise SeatReceiptError("a receipt never carries a filesystem path")
    canonical(receipt)


RUN_SCHEMA = "run.record-attempt.v1"
RUN_SOURCE_TRANSPORT = "run-record"
RUN_OUTCOMES = {"succeeded", "failed", "unknown"}
RUN_RECORD_KEYS = {
    "schema", "run_id", "attempt_id", "record_sha256", "provider", "started_at", "finished_at",
    "timestamp_derivation", "usage", "usage_unknown_reason", "outcome", "source", "harvested_at",
}
RUN_DERIVATION_METHODS = {"record_timestamp", "record_timestamp_naive_local", "run_directory_stamp"}
RUN_PROVIDER_KEYS = {"execution_class", "identity_sha256", "model_name"}


def validate_run_record(receipt: Any) -> None:
    """A dated run record: one attempt a driver wrote down, admitted as a receipt.

    Looser than a seat receipt in exactly two places, and strict everywhere
    else: the timestamp may come from the record itself or from the run
    directory stamp (the derivation names which, with its error bound), and
    usage may be partial (a record that carries only tokens out keeps them;
    a field the record never had is null, never guessed).
    """
    if not isinstance(receipt, dict):
        raise SeatReceiptError("run record must be an object")
    if set(receipt) != RUN_RECORD_KEYS:
        raise SeatReceiptError(
            f"bad run record keys: missing={sorted(RUN_RECORD_KEYS - set(receipt))} "
            f"extra={sorted(set(receipt) - RUN_RECORD_KEYS)}")
    if receipt["schema"] != RUN_SCHEMA:
        raise SeatReceiptError("unrecognized run record schema")
    for key in ("run_id", "attempt_id", "record_sha256", "started_at", "finished_at", "outcome", "harvested_at"):
        _require_str(receipt, key)
    if not _HEX64.match(receipt["record_sha256"]):
        raise SeatReceiptError("record_sha256 must be a sha256 hex digest")
    if receipt["outcome"] not in RUN_OUTCOMES:
        raise SeatReceiptError("invalid run outcome")
    source = receipt.get("source")
    if (not isinstance(source, dict) or source.get("transport") != RUN_SOURCE_TRANSPORT
            or not isinstance(source.get("adapter"), str) or not source["adapter"]
            or source.get("execution_mode") != "external" or source.get("accounting_owner") != "direct"
            or set(source) != {"transport", "adapter", "execution_mode", "accounting_owner"}):
        raise SeatReceiptError("run record source must be a direct-owned run-record adapter")
    provider = receipt.get("provider")
    if not isinstance(provider, dict) or set(provider) != RUN_PROVIDER_KEYS:
        raise SeatReceiptError("run provider requires exactly " + ", ".join(sorted(RUN_PROVIDER_KEYS)))
    if provider["execution_class"] != "local" or not _HEX64.match(str(provider["identity_sha256"])):
        raise SeatReceiptError("verified local provider identity required")
    if not isinstance(provider["model_name"], str) or not provider["model_name"]:
        raise SeatReceiptError("provider.model_name required")
    derivation = receipt.get("timestamp_derivation")
    if (not isinstance(derivation, dict) or set(derivation) != {"method", "error_bound_s"}
            or derivation["method"] not in RUN_DERIVATION_METHODS
            or type(derivation["error_bound_s"]) is not int or derivation["error_bound_s"] < 0):
        raise SeatReceiptError("unrecognized run timestamp derivation")
    usage = receipt["usage"]
    if usage is None:
        if not isinstance(receipt["usage_unknown_reason"], str) or not receipt["usage_unknown_reason"]:
            raise SeatReceiptError("unknown usage requires a reason")
    else:
        if receipt["usage_unknown_reason"] is not None:
            raise SeatReceiptError("recorded usage carries no unknown reason")
        if not isinstance(usage, dict) or set(usage) != {"tokens_in", "tokens_out"}:
            raise SeatReceiptError("usage requires exactly tokens_in and tokens_out")
        if all(value is None for value in usage.values()):
            raise SeatReceiptError("usage with no fields is unknown usage")
        for value in usage.values():
            if value is not None and (type(value) is not int or value < 0):
                raise SeatReceiptError("usage fields must be nonnegative integers or null")
    for text in _walk_strings(receipt):
        if _DRIVE_PATH.search(text):
            raise SeatReceiptError("a receipt never carries a filesystem path")
    canonical(receipt)


class SeatReceiptsLedger:
    """Append-only NDJSON with a cross-process lock beside it."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def exists(self) -> bool:
        return self.path.exists()

    def rows(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)

    def attempt_ids(self) -> set[str]:
        return {str(row.get("attempt_id")) for row in self.rows()}

    @contextmanager
    def _locked(self, timeout_s: float = 30.0) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_s
        handle = open(self.lock_path, "a+b")
        try:
            while True:
                try:
                    _lock_file(handle)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise SeatReceiptError(
                            f"could not take the seat-receipts append lock within "
                            f"{timeout_s:.0f}s ({self.lock_path.name}): {exc}") from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                _unlock_file(handle)
        finally:
            handle.close()

    def append(self, receipts: list[dict[str, Any]], timeout_s: float = 30.0,
               validator=validate_receipt) -> int:
        """Validate, then append every receipt whose attempt_id is new. Returns the count."""
        for receipt in receipts:
            validator(receipt)
        if not receipts:
            return 0
        with self._locked(timeout_s):
            known = self.attempt_ids()
            fresh = []
            for receipt in receipts:
                if receipt["attempt_id"] in known:
                    continue
                known.add(receipt["attempt_id"])
                fresh.append(receipt)
            if not fresh:
                return 0
            with self.path.open("a", encoding="utf-8") as stream:
                for receipt in fresh:
                    stream.write(canonical(receipt) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return len(fresh)
