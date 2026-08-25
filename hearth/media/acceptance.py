"""Accepted render capacity -- the last benchmark that passed, not the hardware.

    Production capability = the last accepted benchmark,
    NOT what the hardware theoretically exposes.

Two calibrated B70 lanes exist. Whether BOTH may be used concurrently is a
question about measured coexistence with the resident inference tenant, and the
answer is an artifact written by the Phase 7 benchmark, not a property of the
silicon.

The scheduler reads ``accepted_lane_count`` and obeys it. It never reasons about
history, and it never infers capacity from how many lanes calibrated healthy.

STALENESS DOES NOT RAISE CAPACITY
--------------------------------
When the machine changes materially -- GPU driver version, the set of adapters,
or the ffmpeg/libvpl version -- a previous two-lane acceptance stops being
evidence about the machine in front of us. The record is marked ``stale`` and
two-lane use becomes *eligible for re-benchmark*. It does NOT become allowed.
A stale record keeps serving the count it was accepted at until someone re-runs
the coexistence calibration and accepts a new one.

Drifting upward on a driver update is exactly the failure this prevents.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

# With no accepted benchmark, one lane is the conservative floor: enough to be
# useful, not enough to contend for both cards against the inference tenant.
DEFAULT_LANE_COUNT = 1

# The fields whose change invalidates a coexistence result.
FINGERPRINT_KEYS = ("driver_version", "adapter_uuids", "ffmpeg_version")


@dataclass(frozen=True)
class Acceptance:
    accepted_lane_count: int
    calibration_fingerprint: dict
    measured_at: str
    stale: bool
    receipts: dict
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "accepted_lane_count": self.accepted_lane_count,
            "calibration_fingerprint": self.calibration_fingerprint,
            "measured_at": self.measured_at,
            "stale": self.stale,
            "receipts": self.receipts,
            "detail": self.detail,
        }


def default_acceptance_path() -> Path:
    configured = os.environ.get("HEARTH_RENDER_ACCEPTANCE")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "var" / "render" / "acceptance.json"


def conservative_default(detail: str) -> Acceptance:
    return Acceptance(
        accepted_lane_count=DEFAULT_LANE_COUNT,
        calibration_fingerprint={},
        measured_at="",
        stale=True,
        receipts={},
        detail=detail,
    )


def load_acceptance(path: Optional[Path] = None) -> Acceptance:
    """Read the accepted-capacity record.

    Any problem -- missing, unreadable, malformed, wrong schema -- yields the
    conservative one-lane default rather than an exception or an optimistic
    guess. Absent evidence is not evidence of capacity.
    """
    target = Path(path) if path is not None else default_acceptance_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return conservative_default("no usable acceptance record (%s)" % (exc,))
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return conservative_default("acceptance record has an unknown schema")
    count = raw.get("accepted_lane_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return conservative_default("acceptance record has a bad lane count")
    return Acceptance(
        accepted_lane_count=count,
        calibration_fingerprint=dict(raw.get("calibration_fingerprint") or {}),
        measured_at=str(raw.get("measured_at", "")),
        stale=bool(raw.get("stale", False)),
        receipts=dict(raw.get("receipts") or {}),
        detail=str(raw.get("detail", "")),
    )


def save_acceptance(acceptance: Acceptance, path: Optional[Path] = None) -> Path:
    target = Path(path) if path is not None else default_acceptance_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(acceptance.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def fingerprint_changed(accepted: dict, live: dict) -> bool:
    """Whether the machine has changed materially since acceptance."""
    if not accepted:
        return True
    return any(accepted.get(key) != live.get(key) for key in FINGERPRINT_KEYS)


def reconcile(acceptance: Acceptance, live_fingerprint: dict) -> Acceptance:
    """Mark an acceptance stale when the machine no longer matches it.

    Deliberately does NOT change ``accepted_lane_count``. Staleness makes a
    higher count eligible for re-benchmark; only an actual accepted benchmark
    grants it.
    """
    if not fingerprint_changed(acceptance.calibration_fingerprint, live_fingerprint):
        return acceptance
    return Acceptance(
        accepted_lane_count=acceptance.accepted_lane_count,
        calibration_fingerprint=acceptance.calibration_fingerprint,
        measured_at=acceptance.measured_at,
        stale=True,
        receipts=acceptance.receipts,
        detail="calibration fingerprint changed since acceptance; re-run the "
               "coexistence benchmark to change capacity",
    )
