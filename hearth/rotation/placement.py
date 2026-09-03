"""Placement assertion from the llama-server load report (ADR-0042, ADR-0045 P3).

Pure. Parses the ``-lv 5`` load report as llama-swap ``GET /logs`` (or ``--log-file``)
carries it, and decides whether the weights landed where the rung intends -- never from a
Vulkan index, always from what the server itself reported:

    llama_prepare_model_devices: using device Vulkan1 (Intel(R) Arc(TM) Pro B70 Graphics) ...
    load_tensors:      Vulkan1 model buffer size =  8464.87 MiB
    llama_kv_cache:    Vulkan1 KV buffer size =   512.00 MiB
    ...
    - Vulkan0 : Intel(R) Graphics (...)                 <- the iGPU in the enumeration

Fail closed: an unparseable report is NOT ok. The optional per-BDF commit-delta corroboration
(b70tools `local_committed` rising on exactly the expected cards) is the second method
ADR-0042 names; when telemetry is absent the verdict says so rather than pretending.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

B70_MARKER = "Arc(TM) Pro B70"
IGPU_MARKER = "Intel(R) Graphics"

# Device names carry their own parentheses -- "Intel(R) Arc(TM) Pro B70 Graphics" -- so the
# capture runs non-greedily to the ")" that precedes " - <free>" or the end of the line.
_USING_RE = re.compile(r"using device (Vulkan\d+) \((.+?)\)(?=\s*-|\s*$)", re.M)
_ENUM_RE = re.compile(r"^\s*-\s*(Vulkan\d+)\s*:\s*(.+?)\s*$", re.M)
_TRAILING_VENDOR_RE = re.compile(r"\s+\([^()]*\)\s*$")   # " (Intel Corporation)" but not "(TM)"


def _enum_name(rest: str) -> str:
    """'Intel(R) Arc(TM) Pro B70 Graphics (Intel Corporation) | uma: 0 (32558 MiB)' -> the name."""
    name = rest.split(" | ", 1)[0].strip()
    return _TRAILING_VENDOR_RE.sub("", name).strip()
_BUFFER_RE = re.compile(r"(Vulkan\d+|CPU\S*)\s+model buffer size\s*=\s*([0-9.]+)\s*MiB")
_KV_RE = re.compile(r"(Vulkan\d+|CPU\S*)\s+KV buffer size\s*=\s*([0-9.]+)\s*MiB")
_COMPUTE_RE = re.compile(r"(Vulkan\d+|CPU\S*)\s+compute buffer size\s*=\s*([0-9.]+)\s*MiB")
_OFFLOAD_RE = re.compile(r"offloaded (\d+)/(\d+) layers to GPU")


@dataclass(frozen=True)
class DeviceReport:
    handle: str                    # "Vulkan1" -- positional, per process, NOT an identity
    name: str                      # what the server printed for it
    model_mib: float = 0.0
    kv_mib: float = 0.0
    compute_mib: float = 0.0

    @property
    def is_b70(self) -> bool:
        return B70_MARKER in self.name

    @property
    def is_igpu(self) -> bool:
        return IGPU_MARKER in self.name and not self.is_b70

    @property
    def total_mib(self) -> float:
        return self.model_mib + self.kv_mib + self.compute_mib


@dataclass(frozen=True)
class LoadReport:
    devices: tuple = ()            # DeviceReport, in the order the report named them
    using: tuple = ()              # handles named by "using device"
    offloaded_layers: Optional[int] = None
    total_layers: Optional[int] = None
    parsed: bool = False           # False when no placement lines were found at all

    def with_weights(self) -> list:
        return [d for d in self.devices if d.model_mib > 0]


@dataclass(frozen=True)
class PlacementVerdict:
    ok: bool
    reason: str
    expected_cards: int
    b70_with_weights: int
    igpu_with_weights: bool
    per_card_gb: dict = field(default_factory=dict)      # handle -> GB (model+kv+compute)
    bdf_delta_gb: dict = field(default_factory=dict)      # bdf -> delta (when telemetry given)
    bdf_corroborated: Optional[bool] = None               # None = no telemetry supplied


def parse_load_report(text: str) -> LoadReport:
    """Parse whatever placement lines exist in ``text`` (llama-swap /logs, --log-file, stderr)."""
    names: dict = {}
    for handle, rest in _ENUM_RE.findall(text or ""):
        names.setdefault(handle, _enum_name(rest))
    using = []
    for handle, name in _USING_RE.findall(text or ""):
        names[handle] = name.strip()
        if handle not in using:
            using.append(handle)
    model: dict = {}
    for handle, mib in _BUFFER_RE.findall(text or ""):
        model[handle] = model.get(handle, 0.0) + float(mib)
    kv: dict = {}
    for handle, mib in _KV_RE.findall(text or ""):
        kv[handle] = kv.get(handle, 0.0) + float(mib)
    compute: dict = {}
    for handle, mib in _COMPUTE_RE.findall(text or ""):
        compute[handle] = compute.get(handle, 0.0) + float(mib)
    handles = list(names)
    for handle in list(model) + list(kv) + list(compute):
        if handle not in handles:
            handles.append(handle)
    devices = tuple(DeviceReport(h, names.get(h, ""), model.get(h, 0.0), kv.get(h, 0.0),
                                 compute.get(h, 0.0)) for h in handles)
    offloaded = total = None
    match = _OFFLOAD_RE.search(text or "")
    if match:
        offloaded, total = int(match.group(1)), int(match.group(2))
    parsed = bool(model or using)
    return LoadReport(devices, tuple(using), offloaded, total, parsed)


def assert_placement(report: LoadReport, expected_cards: int,
                     bdf_before: Optional[dict] = None, bdf_after: Optional[dict] = None,
                     min_delta_gb: float = 1.0) -> PlacementVerdict:
    """Decide whether the weights landed on exactly ``expected_cards`` B70s and never on the iGPU.

    ``bdf_before``/``bdf_after`` are optional ``{bdf: local_committed_gb}`` snapshots; when
    both are given, exactly ``expected_cards`` BDFs must have risen by >= ``min_delta_gb``
    (the commit-signature corroboration) or the verdict fails.
    """
    per_card = {d.handle: round(d.total_mib / 1024.0, 3) for d in report.devices if d.total_mib > 0}
    weighted = report.with_weights()
    b70s = [d for d in weighted if d.is_b70]
    igpu = any(d.is_igpu for d in weighted)
    unknown = [d for d in weighted if not d.is_b70 and not d.is_igpu and d.handle.startswith("Vulkan")]

    if not report.parsed:
        return PlacementVerdict(False, "unparseable: no placement lines (was the server launched with -lv 5?)",
                                expected_cards, 0, False, per_card)
    if igpu:
        return PlacementVerdict(False, "iGPU holds weights (ADR-0042: index-selected the wrong device)",
                                expected_cards, len(b70s), True, per_card)
    if unknown:
        return PlacementVerdict(False, "weights on a Vulkan device whose name is not a B70: %s"
                                % ", ".join(f"{d.handle}={d.name!r}" for d in unknown),
                                expected_cards, len(b70s), False, per_card)
    if len(b70s) != expected_cards:
        return PlacementVerdict(False, "expected %d B70(s) with weights, report shows %d"
                                % (expected_cards, len(b70s)), expected_cards, len(b70s), False, per_card)
    if report.offloaded_layers is not None and report.total_layers is not None \
            and report.offloaded_layers < report.total_layers:
        return PlacementVerdict(False, "only %d/%d layers offloaded -- weights spilled to host"
                                % (report.offloaded_layers, report.total_layers),
                                expected_cards, len(b70s), False, per_card)

    deltas: dict = {}
    corroborated: Optional[bool] = None
    if bdf_before is not None and bdf_after is not None:
        for bdf in set(bdf_before) | set(bdf_after):
            before = bdf_before.get(bdf)
            after = bdf_after.get(bdf)
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                deltas[bdf] = round(after - before, 3)
        rose = [bdf for bdf, delta in deltas.items() if delta >= min_delta_gb]
        corroborated = len(rose) == expected_cards
        if not corroborated:
            return PlacementVerdict(False, "commit signature disagrees: %d BDF(s) rose >= %.1f GB, expected %d"
                                    % (len(rose), min_delta_gb, expected_cards),
                                    expected_cards, len(b70s), False, per_card, deltas, False)
    return PlacementVerdict(True, "weights on %d B70(s), iGPU clean" % len(b70s),
                            expected_cards, len(b70s), False, per_card, deltas, corroborated)
