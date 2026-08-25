"""The gaming/OBS gate: which lane to withhold, and when.

NOT A GLOBAL PAUSE
------------------
Rendering does not stop because you started playing. OBS records with
``obs_qsv11_hevc`` on ONE Arc B70, so exactly one card's media engine is
genuinely contended; the other is free. The gate therefore withholds a LANE,
and the scheduler keeps the queue moving on whatever remains -- which is why
`select_lane_candidates` takes a per-lane gate rather than a global flag.

WHICH LANE
----------
Resolved by measurement, not assumption. OBS's own log says which adapter it
loaded ("Loading up D3D11 on adapter ... (0)"), but that index is OBS's
enumeration and disagrees with ffmpeg's on the same boot. So the lane is
identified by the LUID its process is actually touching, via the same
performance counters the lane map is calibrated against.

FAIL SAFE
---------
If a game or a recording is running and the contended lane CANNOT be identified,
both lanes are withheld. Guessing wrong here means stuttering the thing the
machine exists to do.

THE bf6.exe TRAP
----------------
There are two. The real game lives under Steam; a 57 KB stub lives at
``E:\\omen\\bf6-highlights\\runtime\\bf6.exe`` and exists to test capture
auto-start. Name-only detection matches both, so detection keys on the full
image path and the stub counts only in explicit test mode.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

# The real game. Matched on path, never on name alone.
GAME_IMAGE = "bf6.exe"
GAME_PATH_MARKER = os.path.join("steamapps", "common", "Battlefield 6")

# The capture-test stub. Counts as "gaming" only when explicitly enabled.
TEST_STUB_MARKER = os.path.join("bf6-highlights", "runtime")
TEST_MODE_ENV = "HEARTH_RENDER_TEST_MODE"

OBS_IMAGE = "obs64.exe"
OBS_LOG_DIR = r"%APPDATA%\obs-studio\logs"
RECORDING_START = "==== Recording Start"
RECORDING_STOP = "==== Recording Stop"

_PROCESS_PS = r"""
Get-CimInstance Win32_Process -Filter "Name='%s'" |
  ForEach-Object { "$($_.ProcessId)|$($_.ExecutablePath)" }
"""

_PID_ENGINE_PS = r"""
$paths = (Get-Counter -ListSet 'GPU Engine').PathsWithInstances |
         Where-Object { $_ -match ('pid_' + $env:HEARTH_GATE_PID + '_') -and $_ -match 'Utilization' }
if ($paths) {
  $s = (Get-Counter -Counter $paths -ErrorAction SilentlyContinue).CounterSamples
  foreach ($x in $s) { "$($x.Path)|$($x.CookedValue)" }
}
"""

_ENGINE_RE = re.compile(
    r"luid_(?P<high>0x[0-9a-fA-F]+)_(?P<low>0x[0-9a-fA-F]+)"
    r"_phys_\d+_eng_\d+_engtype_(?P<engtype>\w+)", re.IGNORECASE,
)


@dataclass(frozen=True)
class GateState:
    """What is running, and which lane that costs us."""

    gaming: bool
    recording: bool
    contended_luid: Optional[str]
    detail: str

    @property
    def active(self) -> bool:
        return self.gaming or self.recording

    def to_dict(self) -> dict:
        return {
            "gaming": self.gaming,
            "recording": self.recording,
            "contended_luid": self.contended_luid,
            "detail": self.detail,
            "active": self.active,
        }


def _powershell(script: str, env: Optional[dict] = None, timeout: int = 30) -> str:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout, errors="replace",
        env=env or os.environ.copy(),
    )
    return proc.stdout or ""


def parse_processes(output: str) -> list:
    """Parse ``pid|path`` lines into ``[(pid, path)]``."""
    found = []
    for line in output.splitlines():
        pid, _, path = line.strip().partition("|")
        if not pid.strip().isdigit():
            continue
        found.append((int(pid.strip()), path.strip()))
    return found


def classify_game(processes: Sequence, test_mode: bool = False) -> tuple:
    """``(is_gaming, detail)`` from candidate bf6.exe processes.

    The stub under ``bf6-highlights/runtime`` is ignored unless test mode is on.
    A name-only match would treat the capture test-harness as a live game and
    withhold a lane for nothing.
    """
    for pid, path in processes:
        lowered = (path or "").lower()
        if GAME_PATH_MARKER.lower() in lowered:
            return True, "bf6.exe running from the Steam install (pid %d)" % pid
        if TEST_STUB_MARKER.lower() in lowered:
            if test_mode:
                return True, "bf6.exe TEST STUB accepted (%s=1, pid %d)" % (
                    TEST_MODE_ENV, pid)
            continue
        if path:
            continue
        # An unreadable path is not evidence of the stub; treat as the game.
        return True, "bf6.exe running, path unreadable (pid %d)" % pid
    return False, "no Battlefield 6 process"


def newest_obs_log(log_dir: Optional[Path] = None) -> Optional[Path]:
    directory = Path(log_dir) if log_dir else Path(os.path.expandvars(OBS_LOG_DIR))
    try:
        logs = sorted(directory.glob("*.txt"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return logs[-1] if logs else None


def recording_from_log(text: str) -> Optional[bool]:
    """Whether OBS is recording, from its own log markers.

    Returns None when the log says nothing either way -- which is different from
    "not recording" and must not be read as such.
    """
    last = None
    for line in text.splitlines():
        if RECORDING_START in line:
            last = True
        elif RECORDING_STOP in line:
            last = False
    return last


def parse_pid_engines(output: str) -> dict:
    """Total utilisation per ``(luid, engtype)`` for one process."""
    totals: dict = {}
    for line in output.splitlines():
        path, _, raw = line.strip().rpartition("|")
        match = _ENGINE_RE.search(path)
        if not match:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        key = ("luid_%s_%s" % (match.group("high").lower(),
                               match.group("low").lower()),
               match.group("engtype"))
        totals[key] = totals.get(key, 0.0) + value
    return totals


def busiest_luid(totals: dict, engines: Sequence) -> Optional[str]:
    """Which adapter a process is doing MEDIA work on, if any.

    Restricted to the calibrated media engines so a process merely holding a
    Vulkan device does not look like a recorder.
    """
    wanted = {str(name).lower() for name in engines}
    per_luid: dict = {}
    for (luid, engtype), value in totals.items():
        if engtype.lower() in wanted:
            per_luid[luid] = per_luid.get(luid, 0.0) + value
    if not per_luid:
        return None
    best = max(per_luid, key=lambda key: per_luid[key])
    return best if per_luid[best] > 0.0 else None


def probe(
    lanes: Sequence,
    *,
    process_probe: Optional[Callable] = None,
    engine_probe: Optional[Callable] = None,
    log_reader: Optional[Callable] = None,
    test_mode: Optional[bool] = None,
) -> GateState:
    """Establish the current gate state. Every probe is injectable for tests."""
    if test_mode is None:
        test_mode = os.environ.get(TEST_MODE_ENV) == "1"

    def _processes(image):
        if process_probe is not None:
            return process_probe(image)
        return parse_processes(_powershell(_PROCESS_PS % image))

    gaming, game_detail = classify_game(_processes(GAME_IMAGE), test_mode=test_mode)

    obs = _processes(OBS_IMAGE)
    recording, obs_detail, contended = False, "OBS not running", None
    if obs:
        text = None
        if log_reader is not None:
            text = log_reader()
        else:
            log = newest_obs_log()
            if log is not None:
                try:
                    text = log.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = None
        state = recording_from_log(text) if text is not None else None
        if state is None:
            # OBS is up but its log is unreadable or silent. Unknown, not idle.
            recording, obs_detail = True, "OBS running, recording state unknown"
        else:
            recording = state
            obs_detail = "OBS recording" if state else "OBS running, not recording"

        if recording:
            engines = set()
            for lane in lanes:
                engines.update(getattr(lane, "media_engines", ()) or ())
            pid = obs[0][0]
            if engine_probe is not None:
                totals = engine_probe(pid)
            else:
                env = dict(os.environ, HEARTH_GATE_PID=str(pid))
                totals = parse_pid_engines(_powershell(_PID_ENGINE_PS, env=env))
            contended = busiest_luid(totals, engines)

    detail = "; ".join([game_detail, obs_detail])
    return GateState(gaming=gaming, recording=recording,
                     contended_luid=contended, detail=detail)


def make_gate(lanes_provider: Callable, **probe_kwargs) -> Callable:
    """Build the per-lane gate the render scheduler consumes.

    Returns ``gate(lane) -> (withheld, reason)``.
    """

    def gate(lane) -> tuple:
        lanes = list(lanes_provider() or ())
        state = probe(lanes, **probe_kwargs)
        if not state.active:
            return False, ""
        if state.contended_luid is None:
            # Something is using a card and we cannot tell which. Withholding
            # both is the safe direction: guessing wrong stutters the game or
            # the recording, which is the thing the machine exists to do.
            return True, "%s; contended lane unidentified, withholding all" % state.detail
        if lane.luid.lower() == state.contended_luid.lower():
            return True, "%s on %s" % (state.detail, lane.lane_id)
        return False, ""

    return gate
