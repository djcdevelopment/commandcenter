"""Host + Arc telemetry for admission (ADR-0045 P4; ADR-0040 P6 'Arc telemetry is BUILD').

Two readers, both injectable:

- ``commit_free_gb()`` -- Windows commit headroom (``GlobalMemoryStatusEx().ullAvailPageFile``, the
  same quantity ``Win32_OperatingSystem.FreeVirtualMemory`` reports and the campaign's
  ``Get-Q38CommitFreeGB`` gated on). Loading a model with ``--no-mmap`` charges commit ~1:1.
- ``b70_snapshot()`` -- one short b70tools capture (``--run --ticks 1 --cadence-ms 250 --no-sleep
  --flush-every-tick --out <scratch>``, the ``campaign/qwen38/scripts/lib.ps1`` invocation) parsed
  by ``parse_b70_events`` -- a port of ``campaign/ff-probes/ff_census.py:135-185``
  (``b70tools-jsonl-compact-v1``: ``ai`` adapter-identity rows carry ``bdf``/``desc``; ``ms``
  metric rows carry ``n``/``a``/``v``). **BDF is the stable card identity** (ADR-0042); Vulkan
  indices are per-process and advisory.

RESIDENCY_CAVEAT (carried from ff_census verbatim in spirit): ``gpu.adapter.vram.local.bytes_committed``
is an activity-window signal -- it reads ~0 on a healthy, settled resident model and climbs only
while the driver is actively touching allocations. Never gate steady-state residency on it; use it
as the *delta* corroboration inside a load's post-start window, which is what
``placement.assert_placement(bdf_before, bdf_after)`` does. Missing telemetry is reported as
``None`` and never as 0 (``null != 0`` is a campaign invariant).
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

DEFAULT_B70TOOLS = r"E:\work\b70tools\build\b70tools.exe"
B70_DESC_MARKER = "Arc(TM) Pro B70"
_GB = 1024.0 ** 3

METRIC_LOCAL = "gpu.adapter.vram.local.bytes_committed"
METRIC_NON_LOCAL = "gpu.adapter.vram.non_local.bytes_committed"
METRIC_GPU_TEMP = "gpu.temperature_c"
METRIC_VRAM_TEMP = "vram.temperature_c"
METRIC_DVM = "gpu.adapter.vram.dedicated.bytes"


@dataclass(frozen=True)
class CardTelemetry:
    bdf: str
    desc: str
    adapter_id: Optional[str] = None
    dedicated_vram_gb: Optional[float] = None
    local_committed_gb: Optional[float] = None
    non_local_committed_gb: Optional[float] = None
    gpu_temp_c: Optional[float] = None
    vram_temp_c: Optional[float] = None

    @property
    def is_b70(self) -> bool:
        return B70_DESC_MARKER in (self.desc or "")


@dataclass(frozen=True)
class HostTelemetry:
    sampled_at: str
    commit_free_gb: Optional[float]
    cards: tuple = ()               # CardTelemetry (B70s only)
    source: str = "b70tools"
    note: Optional[str] = None

    def by_bdf(self) -> dict:
        return {c.bdf: c for c in self.cards}

    def local_committed_by_bdf(self) -> dict:
        return {c.bdf: c.local_committed_gb for c in self.cards}


def commit_free_gb() -> Optional[float]:
    """Windows commit headroom in GB via GlobalMemoryStatusEx; None off-Windows or on failure."""
    if os.name != "nt":
        return None

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return None
        return round(status.ullAvailPageFile / _GB, 2)
    except Exception:  # noqa: BLE001
        return None


def _to_gb(value) -> Optional[float]:
    try:
        return round(float(value) / _GB, 3)
    except (TypeError, ValueError):
        return None


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_b70_events(text: str) -> tuple:
    """Parse b70tools-jsonl-compact-v1 into per-BDF CardTelemetry (B70s only; last value wins).

    Tolerates the UTF-8 BOM b70tools writes and skips malformed lines. Adapters with no ``bdf``
    are ignored -- identity is the whole point.
    """
    identity: dict = {}      # adapter_id -> {"bdf", "desc", "dvm"}
    last: dict = {}          # (adapter_id, metric) -> value
    for line in (text or "").lstrip("\ufeff").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        kind = row.get("k")
        if kind == "ai":
            adapter = row.get("a") or row.get("id") or row.get("adapter_id")
            if adapter:
                identity[adapter] = {"bdf": row.get("bdf"), "desc": row.get("desc") or "",
                                     "dvm": row.get("dvm")}
        elif kind == "ms":
            adapter, metric = row.get("a"), row.get("n")
            if adapter and metric:
                last[(adapter, metric)] = row.get("v")
    cards = []
    for adapter, ident in identity.items():
        bdf = ident.get("bdf")
        if not bdf:
            continue
        cards.append(CardTelemetry(
            bdf=str(bdf), desc=str(ident.get("desc") or ""), adapter_id=adapter,
            dedicated_vram_gb=_to_gb(ident.get("dvm")) if ident.get("dvm") is not None else _to_gb(last.get((adapter, METRIC_DVM))),
            local_committed_gb=_to_gb(last[(adapter, METRIC_LOCAL)]) if (adapter, METRIC_LOCAL) in last else None,
            non_local_committed_gb=_to_gb(last[(adapter, METRIC_NON_LOCAL)]) if (adapter, METRIC_NON_LOCAL) in last else None,
            gpu_temp_c=_to_float(last.get((adapter, METRIC_GPU_TEMP))),
            vram_temp_c=_to_float(last.get((adapter, METRIC_VRAM_TEMP))),
        ))
    b70s = tuple(sorted((c for c in cards if c.is_b70), key=lambda c: c.bdf))
    return b70s


def b70_snapshot(runner: Callable = subprocess.run, b70tools: str = DEFAULT_B70TOOLS,
                 scratch: Optional[Path] = None, timeout_s: float = 60.0,
                 commit_reader: Callable[[], Optional[float]] = commit_free_gb) -> HostTelemetry:
    """One passive b70tools tick + commit headroom. Never raises; absent telemetry -> None fields."""
    stamp = datetime.now(timezone.utc).isoformat()
    commit = None
    try:
        commit = commit_reader()
    except Exception:  # noqa: BLE001
        commit = None
    if not Path(b70tools).is_file():
        return HostTelemetry(stamp, commit, (), "none", f"b70tools not found at {b70tools}")
    out_dir = Path(scratch) if scratch else Path(tempfile.mkdtemp(prefix="hearth-b70-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [b70tools, "--run", "--ticks", "1", "--cadence-ms", "250", "--no-sleep",
           "--flush-every-tick", "--out", str(out_dir)]
    try:
        runner(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except Exception as exc:  # noqa: BLE001
        return HostTelemetry(stamp, commit, (), "b70tools", f"b70tools run failed: {exc}")
    events = out_dir / "events.jsonl"
    if not events.is_file():
        return HostTelemetry(stamp, commit, (), "b70tools", "b70tools wrote no events.jsonl")
    try:
        cards = parse_b70_events(events.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return HostTelemetry(stamp, commit, (), "b70tools", f"parse failed: {exc}")
    note = None if len(cards) == 2 else f"expected 2 B70 adapters, saw {len(cards)}"
    return HostTelemetry(stamp, commit, cards, "b70tools", note)
