"""The operator history: append-only, monotonic, verified on read, digests only.

`runs/operator/_system/history.ndjson` is a separate domain log (ADR-0010: two
ledgers, two bounded contexts, joined by identifiers and never merged), and each
run keeps its own `runs/operator/<run_id>/history.ndjson` (D-104).

Nothing is ever rewritten. Sequence assignment is serialized across PROCESSES
with the same OS file-lock pattern the execution ledger uses
(`hearth/execution/ledger.py`), because two processes each reading "the last
sequence" and each appending N+1 is not theoretical here — it happened on
2026-09-04 and left a stream that would not rebuild.

**Read is a verification, not a parse (WI-G2a).** The WI-G2 candidate's reader
detected no corruption at all: bad JSON mid-file, an out-of-order sequence, a
duplicate sequence, a wrong `event_id` and a payload tampered with under an
unchanged `event_id` all read back as "9 events, no error", and a truncated
final line was silently dropped — after which `append` derived the next sequence
from `max(existing)` and silently REUSED the dropped number. Every one of those
is now a refusal that names the line, the sequence and both identities.

One condition is tolerated and reported rather than refused: an interrupted
final write (a partial last line with no terminating newline). History replays
through the last complete event, `read_with_status` reports `truncated_tail`,
and `append` REFUSES until `reconcile_tail` has moved the damaged bytes aside —
recovery never rewrites accepted history.

Payloads carry identities, counts and field NAMES. Never prompt text, never file
contents, never a caller key (D-104, D-115).
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from hearth.operator import paths
from hearth.operator.canonical import (CanonicalError, canonical_json, rfc3339, sha256_hex,
                                       utc_now, validate_contract)

try:                                     # pragma: no cover - platform split
    import msvcrt

    def _lock_file(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(handle) -> None:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
except ImportError:                      # pragma: no cover - platform split
    import fcntl

    def _lock_file(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(handle) -> None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


CONTRACT_VERSION = "operator-history-event.v1"
SYSTEM_RUN_ID = "_system"

EVENT_TYPES = (
    "task.received",
    "capacity.observed",
    "snapshot.invalidated",
    "catalog.compiled",
    "route.proposed",
    "route.validated",
    "route.rejected",
    "authority.evaluated",
    "approval.requested",
    "human.decided",
    "approval.revoked",
    "resources.leased",
    "step.dispatched",
    "attempt.recorded",
    "tool.called",
    "artifact.produced",
    "verification.recorded",
    "resources.released",
    "outcome.final",
    "decision.invalidated",
    "decision.superseded",
    "correction.recorded",
)

# The events that carry `test_mode` when the run is a test-mode run. D-113 names
# the validated, invalidated, dispatched, verification and outcome events as the
# MINIMUM; `route.proposed`, `route.rejected`, `approval.requested` and
# `human.decided` are here too, because the proposal that opened a test-mode run
# and the approval that gated it are part of the same fact.
#
# Enforced by `append` since WI-G2b (condition 3): before that this constant was
# declared and consulted nowhere, so the flag could have been stamped on any
# event and nothing would have said otherwise.
TEST_MODE_EVENT_TYPES = frozenset({
    "route.proposed", "route.validated", "route.rejected", "approval.requested",
    "human.decided", "decision.invalidated", "decision.superseded", "step.dispatched",
    "verification.recorded", "outcome.final",
})


class HistoryError(RuntimeError):
    """Raised when history cannot be appended or cannot be trusted on read.
    Never swallowed: an unrecorded observation is indistinguishable from one
    that never happened, and an unverified one is worse — it looks recorded."""


@contextmanager
def _append_lock(timeout_s: float = 30.0) -> Iterator[None]:
    lock_path = paths.history_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    handle = open(lock_path, "a+b")
    try:
        while True:
            try:
                _lock_file(handle)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise HistoryError(
                        f"could not take the operator history append lock within "
                        f"{timeout_s:.0f}s ({lock_path}): {exc}") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock_file(handle)
    finally:
        handle.close()


def event_identity(event: dict) -> str:
    """The identity an event must carry in `event_id`: SHA-256 over the
    canonical JSON of the event minus that field."""
    return sha256_hex(canonical_json(
        {key: value for key, value in event.items() if key != "event_id"}))


def read_with_status(path: Optional[Path] = None,
                     expected_run_id: Optional[str] = None) -> tuple[list[dict], dict]:
    """Every event in a history file, verified, plus what the file's tail is.

    Returns ``(events, status)`` where status is
    ``{path, count, truncated_tail, quarantine_pending}``. Raises HistoryError on
    any corruption that is NOT an interrupted final write, naming the line.
    """
    target = Path(path) if path else paths.history_path()
    status = {"path": str(target), "count": 0, "truncated_tail": False}
    if not target.is_file():
        return [], status

    raw = target.read_text(encoding="utf-8")
    lines = raw.splitlines()
    ends_with_newline = raw.endswith("\n") or raw == ""
    events: list[dict] = []
    run_id = expected_run_id

    for index, text in enumerate(lines):
        number = index + 1
        stripped = text.strip()
        if not stripped:
            if number != len(lines):
                raise HistoryError(
                    f"{target}: line {number} is blank inside the stream; an "
                    "append-only history has no holes. Reconcile the file "
                    "(`operator history <run_id> --reconcile-tail`) before appending.")
            continue
        try:
            row = json.loads(stripped)
        except ValueError as exc:
            if number == len(lines) and not ends_with_newline:
                # An interrupted final write. Reported, never guessed at.
                status["truncated_tail"] = True
                break
            raise HistoryError(
                f"{target}: line {number} is not valid JSON ({exc}). A damaged row "
                "inside accepted history is not a missing row — copy the file aside "
                "and reconcile it by hand; this reader will not guess.") from exc

        try:
            validate_contract(row, CONTRACT_VERSION, label=f"{target} line {number}")
        except CanonicalError as exc:
            raise HistoryError(str(exc)) from exc

        declared = row.get("event_id")
        computed = event_identity(row)
        if declared != computed:
            raise HistoryError(
                f"{target}: line {number} (sequence {row.get('sequence')}) carries "
                f"event_id {declared}, but its content hashes to {computed} — the "
                "bytes changed after the identity was assigned. A history row is "
                "not authoritative because it exists (D-104).")

        sequence = int(row["sequence"])
        if sequence != len(events) + 1:
            raise HistoryError(
                f"{target}: line {number} declares sequence {sequence} where "
                f"{len(events) + 1} was due. Sequences are 1..N, strictly "
                "increasing, with no duplicates and no gaps.")

        if run_id is None:
            run_id = str(row["run_id"])
        elif str(row["run_id"]) != run_id:
            raise HistoryError(
                f"{target}: line {number} belongs to run {row['run_id']!r}, but this "
                f"stream is run {run_id!r}. Two runs never share a history file.")

        events.append(row)

    status["count"] = len(events)
    status["run_id"] = run_id
    return events, status


def read_all(path: Optional[Path] = None) -> list[dict]:
    """Every complete, verified event in file order.

    Raises HistoryError on corruption; an interrupted final write is tolerated
    (use `read_with_status` when the caller must know that it happened).
    """
    events, _ = read_with_status(path)
    return events


def verify(path: Optional[Path] = None,
           expected_run_id: Optional[str] = None) -> dict:
    """Verify a history file end to end and return the report.

    The report is ``{path, count, truncated_tail, run_id, first_sequence,
    last_sequence, event_types}``. Raises HistoryError with the specific defect.
    """
    events, status = read_with_status(path, expected_run_id)
    return {
        **status,
        "first_sequence": events[0]["sequence"] if events else 0,
        "last_sequence": events[-1]["sequence"] if events else 0,
        "event_types": sorted({str(row["event_type"]) for row in events}),
    }


def reconcile_tail(path: Optional[Path] = None) -> dict:
    """Move an interrupted final write aside so appending can resume.

    Accepted history is never rewritten: the complete events keep their exact
    bytes, and the partial tail is copied to a `.damaged-<timestamp>` sidecar
    beside the file before it is dropped. Refuses when the tail is not a partial
    write, because that damage is not recoverable by truncation.
    """
    target = Path(path) if path else paths.history_path()
    if not target.is_file():
        raise HistoryError(f"no history file at {target}")
    events, status = read_with_status(target)
    if not status["truncated_tail"]:
        raise HistoryError(
            f"{target}: nothing to reconcile — the file has no interrupted final "
            "write. Corruption inside accepted history is reconciled by hand.")

    raw = target.read_bytes()
    text = target.read_text(encoding="utf-8")
    keep_text = "".join(line + "\n" for line in text.splitlines()[:len(events)])
    damaged = text[len(keep_text):]
    stamp = rfc3339(utc_now()).replace(":", "").replace("-", "")
    quarantine = target.with_name(f"{target.name}.damaged-{stamp}")
    with _append_lock():
        quarantine.write_text(damaged, encoding="utf-8", newline="")
        target.write_text(keep_text, encoding="utf-8", newline="")
    return {"path": str(target), "quarantine_path": str(quarantine),
            "events_kept": len(events), "damaged_bytes": len(raw) - len(keep_text.encode("utf-8"))}


def append(event_type: str, payload: dict, refs: Optional[dict] = None,
           path: Optional[Path] = None, run_id: Optional[str] = None,
           envelope_id: Optional[str] = None, test_mode: bool = False) -> dict:
    """Append one event and return it, identity included.

    The file is verified under the lock before the sequence is assigned, so a
    damaged or interrupted stream refuses the write instead of continuing on top
    of it. The row is flushed and fsynced inside the lock.
    """
    if event_type not in EVENT_TYPES:
        raise HistoryError(
            f"{event_type!r} is not one of the event types this writer owns "
            f"({', '.join(EVENT_TYPES)})")
    if test_mode and event_type not in TEST_MODE_EVENT_TYPES:
        # TEST_MODE_EVENT_TYPES was a constant nothing consulted (WI-G2a
        # verification, condition 3): D-113 names the events that carry the flag,
        # and a flag on any other event would make "this run was a test" a claim
        # the taxonomy never agreed to.
        raise HistoryError(
            f"{event_type!r} does not carry test_mode: D-113 flags "
            f"{', '.join(sorted(TEST_MODE_EVENT_TYPES))}. Append it without the flag, or "
            "add the event type to history.TEST_MODE_EVENT_TYPES deliberately.")
    if path:
        target = Path(path)
    elif run_id and run_id != SYSTEM_RUN_ID:
        target = paths.run_history_path(run_id)
    else:
        target = paths.history_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _append_lock():
        existing, status = read_with_status(target, run_id or SYSTEM_RUN_ID)
        if status["truncated_tail"]:
            raise HistoryError(
                f"{target} ends in an interrupted write: history replays through "
                f"sequence {len(existing)}, but the tail is damaged. Reconcile it "
                "first (`operator history <run_id> --reconcile-tail`, which copies "
                "the partial bytes aside and rewrites nothing that was accepted). "
                "Appending now would reuse a sequence number.")
        sequence = len(existing) + 1
        event = {
            "contract_version": CONTRACT_VERSION,
            "sequence": sequence,
            "timestamp": rfc3339(utc_now()),
            "run_id": run_id or SYSTEM_RUN_ID,
            "event_type": event_type,
            "refs": dict(refs or {}),
            "payload": dict(payload),
        }
        if envelope_id:
            event["envelope_id"] = envelope_id
        if test_mode:
            event["test_mode"] = True
        event["event_id"] = event_identity(event)
        try:
            validate_contract(event, CONTRACT_VERSION, label=f"{event_type} event")
        except CanonicalError as exc:
            raise HistoryError(f"refusing to append a {event_type} event: {exc}") from exc
        with open(target, "a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return event


def read_run_history(run_id: str) -> list[dict]:
    """Every verified event for one run."""
    events, _ = read_with_status(paths.run_history_path(run_id), run_id)
    return events


def last_of(event_type: str, path: Optional[Path] = None) -> Optional[dict]:
    for event in reversed(read_all(path)):
        if event.get("event_type") == event_type:
            return event
    return None
