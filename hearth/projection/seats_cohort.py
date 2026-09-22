"""The research-run cohort of the public snapshot (ADR-0047, Phase B).

Two private ledgers feed it: the seat receipts harvested from servers' own
timing logs (``hearth/var/seats/receipts.ndjson``) and the run records
imported from dated driver records (``hearth/var/seats/run-records.ndjson``).
Each is validated with the validator that wrote it and reduced to what the
public page may carry: six non-negative integers plus a count of sources, a
weekly attempt count per ISO week, the first and last day, and the prefix
digest of the file. Seat basenames, ports, model names, run identities and
exact timestamps never leave this module.

The cohort is a sibling of the gateway and execution scans, never a merge into
them: a research run is not a gateway observation and not an execution job, so
it joins no family, no lane and no existing counter.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from hearth.seats.receipts import SeatReceiptError, validate_receipt, validate_run_record

COHORT_KEYS = ("attempts", "measured_attempts", "unknown_usage_attempts", "failed_attempts",
               "tokens_in", "tokens_out")


class SeatCohortError(RuntimeError):
    """A cohort ledger cannot be projected: fail closed, like the execution replay."""


def _week_start(day: str) -> str:
    value = date.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).isoformat()


def _scan(path: Path, validator: Callable[[Any], None], source_key: str, label: str) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    counters = {key: 0 for key in COHORT_KEYS}
    sources: set[str] = set()
    attempt_ids: set[str] = set()
    weekly: Counter[str] = Counter()
    days: list[str] = []
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for position, encoded in enumerate(stream, start=1):
            digest.update(encoded)
            if not encoded.strip():
                continue
            try:
                receipt = json.loads(encoded.decode("utf-8"))
                validator(receipt)
            except (UnicodeDecodeError, json.JSONDecodeError, SeatReceiptError) as exc:
                raise SeatCohortError(f"{label} row {position} is not a valid receipt") from exc
            if receipt["attempt_id"] in attempt_ids:
                raise SeatCohortError(f"{label} row {position} repeats an attempt")
            attempt_ids.add(receipt["attempt_id"])
            sources.add(receipt[source_key])
            usage = receipt["usage"]
            measured = usage is not None
            counters["attempts"] += 1
            counters["measured_attempts"] += int(measured)
            counters["unknown_usage_attempts"] += int(not measured)
            counters["failed_attempts"] += int(receipt["outcome"] == "failed")
            if measured:
                counters["tokens_in"] += usage.get("tokens_in") or 0
                counters["tokens_out"] += usage.get("tokens_out") or 0
            day = str(receipt["finished_at"])[:10]
            days.append(day)
            weekly[_week_start(day)] += 1
    if not days:
        return None
    return {
        "public": {**counters, "sources": len(sources)},
        "weekly": dict(weekly),
        "first_day": min(days),
        "last_day": max(days),
        "prefix_sha256": digest.hexdigest(),
    }


def scan_seats(path: Path) -> dict[str, Any] | None:
    """Aggregate the seat ledger, or None when there is no ledger at all."""
    return _scan(path, validate_receipt, "seat_id", "seat ledger")


def scan_records(path: Path) -> dict[str, Any] | None:
    """Aggregate the run-record ledger, or None when there is no ledger at all."""
    return _scan(path, validate_run_record, "run_id", "run-record ledger")


def combine(*parts: dict[str, Any] | None) -> dict[str, Any] | None:
    """One cohort from the present ledgers; None when none is present."""
    present = [part for part in parts if part is not None]
    if not present:
        return None
    public = {key: sum(part["public"][key] for part in present) for key in COHORT_KEYS}
    public["sources"] = sum(part["public"]["sources"] for part in present)
    weekly: Counter[str] = Counter()
    for part in present:
        weekly.update(part["weekly"])
    return {
        "public": public,
        "weekly": dict(weekly),
        "first_day": min(part["first_day"] for part in present),
        "last_day": max(part["last_day"] for part in present),
    }
