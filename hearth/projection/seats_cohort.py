"""The research-seat cohort of the public snapshot (ADR-0047, Phase B).

Reads the third ledger, ``hearth/var/seats/receipts.ndjson``, validates every
receipt with the same validator that wrote it, and returns only what the public
page may carry: seven non-negative integers, a weekly attempt count per ISO
week, the first and last day, and the prefix digest of the file. The seat
basename, port, model name and exact timestamps never leave this function.

It is a sibling of the gateway and execution scans, never a merge into them:
a seat attempt is not a gateway observation and not an execution job, so it
joins no family, no lane and no existing counter.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from hearth.seats.receipts import SeatReceiptError, validate_receipt

COHORT_KEYS = ("attempts", "measured_attempts", "unknown_usage_attempts", "failed_attempts",
               "tokens_in", "tokens_out")


class SeatCohortError(RuntimeError):
    """The seat ledger cannot be projected: fail closed, like the execution replay."""


def _week_start(day: str) -> str:
    value = date.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).isoformat()


def scan_seats(path: Path) -> dict[str, Any] | None:
    """Aggregate the seat ledger, or return None when there is no ledger at all."""
    path = Path(path)
    if not path.exists():
        return None
    counters = {key: 0 for key in COHORT_KEYS}
    seats: set[str] = set()
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
                validate_receipt(receipt)
            except (UnicodeDecodeError, json.JSONDecodeError, SeatReceiptError) as exc:
                raise SeatCohortError(f"seat ledger row {position} is not a valid receipt") from exc
            if receipt["attempt_id"] in attempt_ids:
                raise SeatCohortError(f"seat ledger row {position} repeats an attempt")
            attempt_ids.add(receipt["attempt_id"])
            seats.add(receipt["seat_id"])
            usage = receipt["usage"]
            measured = usage is not None
            counters["attempts"] += 1
            counters["measured_attempts"] += int(measured)
            counters["unknown_usage_attempts"] += int(not measured)
            counters["failed_attempts"] += int(receipt["outcome"] not in {"succeeded", "unknown"})
            if measured:
                counters["tokens_in"] += usage["tokens_in"]
                counters["tokens_out"] += usage["tokens_out"]
            day = str(receipt["finished_at"])[:10]
            days.append(day)
            weekly[_week_start(day)] += 1
    if not days:
        return None
    return {
        "public": {**counters, "seats": len(seats)},
        "weekly": dict(weekly),
        "first_day": min(days),
        "last_day": max(days),
        "prefix_sha256": digest.hexdigest(),
    }
