"""Media-engine occupancy and the shared-memory spill guard.

TWO DIMENSIONS, TWO SOURCES -- ON PURPOSE
-----------------------------------------
A B70 can be busy in two unrelated ways, and collapsing them would make the
whole lane design pointless:

* **model / compute occupancy** -- llama-server holding the card for Vulkan
  inference. Probed via its own ``/slots`` endpoint
  (``hearth.toolsurface.occupancy.probe_omen_arc_slots``), NOT via performance
  counters: llama-server runs under an S4U scheduled task and its per-process
  counters read 0 even while it is working.
* **media-engine occupancy** -- QSV decode/encode work, which is what a render
  lane actually contends for. That is this module.

NEVER NAME AN ENGINE HERE
-------------------------
The engine that carries QSV work is discovered by calibration and stored per
lane in ``lanes.json`` (see hearth.media.lanes). On this machine it is
``videodecode`` -- there is no ``VideoEncode`` node at all -- but that is an
accident of Intel's current taxonomy, not a fact to hardcode. Everything below
reads ``lane.media_engines``. If a driver update renames or moves the node,
recalibration fixes the whole stack; no code here changes.

FAIL CLOSED
-----------
An unreadable counter is not an idle GPU. Every failure path reports
``busy=True, known=False`` so a lane whose state cannot be established is
withheld rather than scheduled onto.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

# A lane is considered busy with media work above this total utilisation.
# Deliberately low: a lane at 20% of its codec engine is already sharing, and
# the cost of being wrong is a stuttering render, not a lost one.
MEDIA_BUSY_PCT = 20.0

# Spill guard. `GPU Adapter Memory\Shared Usage` rising well above idle means
# the card has started spilling into system memory over PCIe -- the failure mode
# that previously cost 22% throughput on this box. Per-LANE idle baselines are
# measured at calibration time; this is the allowed rise above that.
SPILL_RISE_LIMIT_GB = 0.5

_ENGINE_RE = re.compile(
    r"luid_(?P<high>0x[0-9a-fA-F]+)_(?P<low>0x[0-9a-fA-F]+)"
    r"_phys_(?P<phys>\d+)_eng_(?P<eng>\d+)_engtype_(?P<engtype>\w+)",
    re.IGNORECASE,
)
_ADAPTER_RE = re.compile(
    r"luid_(?P<high>0x[0-9a-fA-F]+)_(?P<low>0x[0-9a-fA-F]+)_phys_(?P<phys>\d+)",
    re.IGNORECASE,
)

# Summed across every process, unlike calibration which filters to one PID:
# here we care that the ENGINE is busy, not who is using it. OBS recording and a
# stray ffmpeg contend just as much as our own render does.
_ENGINE_UTILISATION_PS = r"""
$paths = (Get-Counter -ListSet 'GPU Engine').PathsWithInstances |
         Where-Object { $_ -match 'Utilization' }
if ($paths) {
  $samples = (Get-Counter -Counter $paths -ErrorAction SilentlyContinue).CounterSamples
  foreach ($s in $samples) { "$($s.Path)|$($s.CookedValue)" }
}
"""

_SHARED_MEMORY_PS = r"""
$samples = (Get-Counter -Counter '\GPU Adapter Memory(*)\Shared Usage' -ErrorAction SilentlyContinue).CounterSamples
foreach ($s in $samples) { "$($s.Path)|$($s.CookedValue)" }
"""


@dataclass(frozen=True)
class MediaOccupancy:
    """What we know about one lane's media engines right now."""

    lane_id: str
    busy: bool
    known: bool
    utilisation_pct: float
    engines: tuple
    detail: str

    def to_dict(self) -> dict:
        return {
            "lane_id": self.lane_id,
            "busy": self.busy,
            "known": self.known,
            "utilisation_pct": round(self.utilisation_pct, 2),
            "engines": list(self.engines),
            "detail": self.detail,
        }


def _run_powershell(script: str, powershell: str = "powershell", timeout: int = 60) -> str:
    proc = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout, errors="replace",
    )
    return proc.stdout or ""


def parse_engine_samples(output: str) -> list:
    """Parse ``path|value`` lines into ``{luid, engtype, value}`` records."""
    samples = []
    for line in output.splitlines():
        path, _, raw = line.strip().rpartition("|")
        if not path:
            continue
        match = _ENGINE_RE.search(path)
        if not match:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        samples.append({
            "luid": "luid_%s_%s" % (
                match.group("high").lower(), match.group("low").lower()
            ),
            "engtype": match.group("engtype"),
            "value": value,
        })
    return samples


def parse_shared_memory(output: str) -> dict:
    """Parse shared-usage samples into ``{luid_token: bytes}``."""
    totals: dict = {}
    for line in output.splitlines():
        path, _, raw = line.strip().rpartition("|")
        if not path:
            continue
        match = _ADAPTER_RE.search(path)
        if not match:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        token = "luid_%s_%s" % (match.group("high").lower(), match.group("low").lower())
        totals[token] = totals.get(token, 0.0) + value
    return totals


def utilisation_for(samples: Sequence, luid: str, engines: Sequence) -> float:
    """Total utilisation on one adapter, restricted to the CALIBRATED engines.

    Engine names are compared case-insensitively because Windows spells them
    inconsistently between the instance path and the counter set.
    """
    wanted = {str(name).lower() for name in engines}
    total = 0.0
    for sample in samples:
        if sample["luid"].lower() != luid.lower():
            continue
        if sample["engtype"].lower() in wanted:
            total += float(sample["value"])
    return total


def media_occupancy(
    lane,
    sampler: Optional[Callable] = None,
    busy_pct: float = MEDIA_BUSY_PCT,
    powershell: str = "powershell",
) -> MediaOccupancy:
    """Whether a lane's media engines are currently carrying work.

    ``sampler`` is injectable so tests can drive this without a GPU -- and so
    the test suite can prove the engine names come from the LANE rather than
    from a constant in this file.
    """
    engines = tuple(getattr(lane, "media_engines", ()) or ())
    if not engines:
        # An uncalibrated lane is not an idle lane. Refuse to guess.
        return MediaOccupancy(
            lane_id=lane.lane_id, busy=True, known=False, utilisation_pct=0.0,
            engines=(),
            detail="lane has no calibrated media_engines; recalibrate before use",
        )
    try:
        raw = sampler() if sampler is not None else _run_powershell(
            _ENGINE_UTILISATION_PS, powershell=powershell
        )
        samples = parse_engine_samples(raw) if isinstance(raw, str) else list(raw)
    except Exception as exc:  # counters are best-effort infrastructure
        return MediaOccupancy(
            lane_id=lane.lane_id, busy=True, known=False, utilisation_pct=0.0,
            engines=engines,
            detail="could not sample GPU Engine counters: %s" % (exc,),
        )

    total = utilisation_for(samples, lane.luid, engines)
    busy = total >= busy_pct
    return MediaOccupancy(
        lane_id=lane.lane_id, busy=busy, known=True, utilisation_pct=total,
        engines=engines,
        detail="%s at %.2f%% (threshold %.1f%%)"
               % ("+".join(engines), total, busy_pct),
    )


def shared_memory_gb(
    luid: str,
    sampler: Optional[Callable] = None,
    powershell: str = "powershell",
) -> Optional[float]:
    """Shared (system-memory) usage attributed to one adapter, in GB.

    ``None`` when it cannot be read -- callers treat that as "unknown", which
    the spill guard resolves the safe way.

    Adapter-level, deliberately: per-process GPU memory counters read 0 for the
    S4U-launched llama-server, so a per-process reading would miss exactly the
    tenant whose spill matters most.
    """
    try:
        raw = sampler() if sampler is not None else _run_powershell(
            _SHARED_MEMORY_PS, powershell=powershell
        )
        totals = parse_shared_memory(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return None
    value = totals.get(luid.lower())
    if value is None:
        return None
    return value / (1024.0 ** 3)


def spilling(
    lane,
    idle_baseline_gb: Optional[float] = None,
    limit_gb: float = SPILL_RISE_LIMIT_GB,
    sampler: Optional[Callable] = None,
    powershell: str = "powershell",
) -> tuple:
    """``(is_spilling, detail)`` for one lane's shared-memory usage.

    Unknown readings are NOT treated as spilling: the spill guard drops a lane
    from candidacy, and a counter that cannot be read should not silently
    remove all render capacity. Media occupancy already fails closed, so an
    unreadable-counter machine still refuses to schedule.
    """
    current = shared_memory_gb(lane.luid, sampler=sampler, powershell=powershell)
    if current is None:
        return False, "shared memory unreadable for %s" % (lane.lane_id,)
    baseline = idle_baseline_gb
    if baseline is None:
        baseline = float(getattr(lane, "idle_shared_gb", 0.0) or 0.0)
    rise = current - baseline
    if rise > limit_gb:
        return True, (
            "%s shared usage %.3f GB is %.3f GB above the %.3f GB baseline "
            "(limit %.3f GB)" % (lane.lane_id, current, rise, baseline, limit_gb)
        )
    return False, "%s shared usage %.3f GB (baseline %.3f GB)" % (
        lane.lane_id, current, baseline
    )
