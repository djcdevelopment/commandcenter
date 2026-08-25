"""Adapter identity and lane calibration for the dual Arc Pro B70 render lanes.

WHY THIS MODULE EXISTS
----------------------
ffmpeg addresses a QSV device by a *DXGI adapter index*, and that index is not a
stable name for a piece of hardware. Measured on this box, on one boot:

    ffmpeg (plain EnumAdapters)           iGPU, WARP, Arc, Arc
    OBS 31+ (EnumAdapterByGpuPreference)  Arc, Arc, iGPU

Same machine, same boot, different order. Task Manager disagrees with both.
Hardcoding ``child_device=2`` therefore encodes on whatever card answers to
index 2 today -- possibly the integrated GPU, possibly the card OBS is already
using to record.

Worse, the obvious default is a trap: ``-init_hw_device qsv:hw_any`` silently
selects DXGI adapter 0, which on this box is the Arrow Lake iGPU. A pipeline
that omits ``child_device`` does not fail; it quietly encodes 4K on the iGPU.

So we calibrate at startup and bind three identities together:

    deviceUUID   (permanent)  -- derived from PCI topology, survives reboots
    deviceLUID   (per boot)   -- the key the Windows performance counters use
    child_device (per boot)   -- the only thing ffmpeg will accept

The UUID is the anchor. Vulkan encodes the PCI bus/device in bytes 8-9 of
deviceUUID, which is why ``868023e2-0000-0000-0900-...`` and ``...-0400-...``
are the same two cards Get-PnpDevice reports at PCI bus 9 and bus 4. That gives
every lane a name (``b70@bus4``) meaning the same card next week.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

# ------------------------------------------------------------------ constants

INTEL_VENDOR_ID = 0x8086
ARC_B70_DEVICE_ID = 0xE223          # discrete Arc Pro B70 (Battlemage)
ARROWLAKE_IGPU_DEVICE_ID = 0x7D67   # the integrated GPU `hw_any` would pick
WARP_VENDOR_ID = 0x1414             # Microsoft Basic Render Driver placeholder

# DXGI enumeration is sparse and contains placeholders; probing past this is
# pointless on a two-GPU workstation but cheap enough to be generous.
MAX_DXGI_PROBE_INDEX = 6

# ---------------------------------------------------------------------------
# MEDIA-ENGINE IDENTITY IS CALIBRATED, NOT NAMED.
#
# The obvious implementation watches `engtype_VideoEncode`. On this box that
# counter DOES NOT EXIST -- not during an encode, not ever. Measured 2026-08-25,
# sampling a live 4K60 `h264_qsv` encode:
#
#     engtype_VideoDecode      74.89 %   <-- this IS the encode
#     engtype_3D               12.09 %
#
# Intel's Battlemage driver reports the whole multi-format codec engine (VDBOX)
# under the *VideoDecode* name. An occupancy model looking for "VideoEncode"
# reads 0% forever, so a saturated lane appears permanently free -- the same
# class of error as reading an open port as a ready model.
#
# The fix is NOT to hardcode "VideoDecode" instead. That would swap one
# accidental fact for another, and a future driver update that moves or renames
# the node would silently reintroduce the same blindness. So calibration RUNS a
# real encode, watches which engines light up, and persists the answer per lane
# in lanes.json. Consumers ask the lane, never the constant.
#
# Classification is by DOMINANCE rather than by name: the engines carrying at
# least MEDIA_DOMINANCE_FRACTION of the busiest engine's utilisation are the
# media engines. On the measured profile above that selects VideoDecode alone
# and correctly rejects 3D at 16% of peak -- which matters because 3D is also
# what a Vulkan inference workload uses, and conflating them would defeat the
# whole point of modelling model-occupancy and media-occupancy separately.
#
# Two calibration hazards found the hard way:
#   * the probe must SATURATE the encoder. With a trivial `color=black` source
#     the encoder idles between frames and the profile inverts completely --
#     3D 16.17%, VideoDecode 0.00%. A 4K60 testsrc is heavy enough.
#   * per-PID counters cannot be read for llama-server (it runs under an S4U
#     scheduled task), so the compute side of the model is probed via its
#     /slots endpoint instead, not via these counters.
MEDIA_DOMINANCE_FRACTION = 0.5
MEDIA_MIN_ACTIVITY_PCT = 5.0

# Retained ONLY as a last-resort hint when a lane predates calibration. Never
# consult this in the gate or the scheduler; use lane.media_engines.
FALLBACK_MEDIA_ENGINE_HINT = "video"

SCHEMA_VERSION = 2  # v2: lanes carry calibrated media_engines + engine_profile

_UUID_RE = re.compile(
    r"^([0-9a-f]{8})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{12})$"
)
_LUID_RE = re.compile(r"^([0-9a-f]{8})-([0-9a-f]{8})$")

# ffmpeg -v verbose prints (note the name itself contains parens, so the
# capture must be greedy to the LAST paren, not the first):
#   [AVHWDeviceContext @ ..] Using device 8086:e223 (Intel(R) Arc(TM) Pro B70 Graphics).
_FFMPEG_DEVICE_RE = re.compile(
    r"Using device ([0-9a-fA-F]{1,4}):([0-9a-fA-F]{1,4})\s*\((.*)\)"
)

# pid_12164_luid_0x00000000_0x0001714B_phys_0_eng_5_engtype_Compute
_COUNTER_RE = re.compile(
    r"pid_(?P<pid>\d+)_luid_(?P<high>0x[0-9a-fA-F]+)_(?P<low>0x[0-9a-fA-F]+)"
    r"_phys_(?P<phys>\d+)_eng_(?P<eng>\d+)_engtype_(?P<engtype>\w+)",
    re.IGNORECASE,
)


class CalibrationError(RuntimeError):
    """Adapter identity could not be established. Callers must fail closed."""


# -------------------------------------------------------------------- models

@dataclass(frozen=True)
class VulkanDevice:
    """One physical adapter as Vulkan reports it."""

    index: int
    name: str
    vendor_id: int
    device_id: int
    device_uuid: str
    luid_high: int
    luid_low: int
    pci_bus: int
    pci_device: int

    @property
    def is_arc_b70(self) -> bool:
        return self.vendor_id == INTEL_VENDOR_ID and self.device_id == ARC_B70_DEVICE_ID

    @property
    def counter_token(self) -> str:
        return luid_counter_token(self.luid_high, self.luid_low)

    @property
    def lane_id(self) -> str:
        return f"b70@bus{self.pci_bus}"


@dataclass
class Lane:
    """A calibrated render lane: a physical card bound to an ffmpeg index."""

    lane_id: str
    pci_bus: int
    device_uuid: str
    luid: str                      # counter token, e.g. luid_0x00000000_0x0001714b
    child_device: Optional[int]    # ffmpeg -init_hw_device index; None = unbound
    # CALIBRATED: engine types observed carrying a real QSV encode on THIS lane.
    # The gate and scheduler must read this, never a hardcoded counter name.
    media_engines: list = field(default_factory=list)
    # The full utilisation profile the classification came from, kept so a human
    # can see why an engine was or was not selected.
    engine_profile: dict = field(default_factory=dict)
    engtypes: list = field(default_factory=list)
    healthy: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Calibration:
    """The persisted lane map plus the fingerprint that validates it."""

    schema_version: int
    fingerprint: dict
    lanes: list
    calibrated_at: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "fingerprint": self.fingerprint,
            "lanes": [lane.to_dict() for lane in self.lanes],
            "calibrated_at": self.calibrated_at,
        }

    def healthy_lanes(self) -> list:
        """Healthy lanes, ordered by lane_id -- the deterministic scheduling order."""
        return sorted(
            (lane for lane in self.lanes if lane.healthy),
            key=lambda lane: lane.lane_id,
        )


# --------------------------------------------------------- pure: identity math

def pci_from_device_uuid(device_uuid: str) -> tuple:
    """Extract ``(pci_bus, pci_device)`` from a Vulkan deviceUUID.

    Intel's Windows driver encodes PCI topology in bytes 8-9 -- the fourth
    hyphen-separated group. Verified against Get-PnpDeviceProperty on this box::

        868023e2-0000-0000-0900-000000000000 -> bus 0x09 device 0x00
        868023e2-0000-0000-0400-000000000000 -> bus 0x04 device 0x00
        8680677d-0600-0000-0002-000000000000 -> bus 0x00 device 0x02 (iGPU)

    This is what makes a lane name stable across reboots and driver reinstalls.
    """
    match = _UUID_RE.match(device_uuid.strip().lower())
    if not match:
        raise CalibrationError("malformed deviceUUID: %r" % (device_uuid,))
    group = match.group(4)
    return int(group[0:2], 16), int(group[2:4], 16)


def decode_vulkan_luid(device_luid: str) -> tuple:
    """Decode Vulkan's little-endian deviceLUID into ``(HighPart, LowPart)``.

    Vulkan prints the raw 8 bytes; Windows counters print the two 32-bit halves
    as big-endian hex. ``2f690100-00000000`` is LowPart 0x0001692F, HighPart 0.
    Getting the byte order wrong yields a token matching no counter instance, so
    the lane would silently never be observed -- a failure that looks like an
    idle GPU rather than like a bug.
    """
    match = _LUID_RE.match(device_luid.strip().lower())
    if not match:
        raise CalibrationError("malformed deviceLUID: %r" % (device_luid,))

    def _little_endian(word: str) -> int:
        octets = [word[i:i + 2] for i in range(0, 8, 2)]
        return int("".join(reversed(octets)), 16)

    return _little_endian(match.group(2)), _little_endian(match.group(1))


def luid_counter_token(high: int, low: int) -> str:
    """Render the LUID the way the ``GPU Engine`` counter set names instances."""
    return "luid_0x%08x_0x%08x" % (high, low)


# ------------------------------------------------------------- pure: parsing

def parse_vulkaninfo(text: str) -> list:
    """Parse ``vulkaninfo`` output into physical adapters.

    Tolerates both the full dump and ``--summary``, but note --summary omits
    deviceLUID; devices without a LUID are skipped rather than guessed at,
    because a lane we cannot observe on the counters is a lane we cannot
    schedule safely.
    """
    devices = []
    current: dict = {}

    def _flush() -> None:
        required = {"index", "deviceUUID", "deviceLUID", "vendorID", "deviceID"}
        if not required.issubset(current):
            current.clear()
            return
        try:
            bus, dev = pci_from_device_uuid(current["deviceUUID"])
            high, low = decode_vulkan_luid(current["deviceLUID"])
        except CalibrationError:
            current.clear()
            return
        devices.append(
            VulkanDevice(
                index=current["index"],
                name=current.get("deviceName", "unknown"),
                vendor_id=int(current["vendorID"], 16),
                device_id=int(current["deviceID"], 16),
                device_uuid=current["deviceUUID"],
                luid_high=high,
                luid_low=low,
                pci_bus=bus,
                pci_device=dev,
            )
        )
        current.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        header = re.match(r"^GPU(\d+):?$", line)
        if header:
            _flush()
            current["index"] = int(header.group(1))
            continue
        if "=" not in line or "index" not in current:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in {"deviceUUID", "deviceLUID", "vendorID", "deviceID", "deviceName"}:
            # A device block repeats across vulkaninfo sections; keep the first.
            current.setdefault(key, value)
    _flush()
    return devices


def parse_ffmpeg_device_probe(output: str) -> Optional[tuple]:
    """Pull ``(vendor_id, device_id, name)`` from an ffmpeg -v verbose device init.

    Returns None when ffmpeg could not create a device. That is NOT the same as
    "no GPU at this index": DXGI exposes WARP placeholders (1414:008c) that fail
    device creation, and treating that failure as "end of adapters" would stop
    the scan before reaching a real card at a higher index.
    """
    match = _FFMPEG_DEVICE_RE.search(output)
    if not match:
        return None
    return int(match.group(1), 16), int(match.group(2), 16), match.group(3).strip()


def parse_counter_instances(instance_paths: Iterable, pid: Optional[int] = None) -> list:
    """Parse ``GPU Engine`` counter instance names into structured records.

    Instance names look like::

        \\GPU Engine(pid_12164_luid_0x00000000_0x0001714B_phys_0_eng_5_engtype_Compute)\\...

    Filtering by ``pid`` is what binds an ffmpeg index to a physical LUID: run a
    known encode, then ask which adapter that process actually touched.
    """
    records = []
    for path in instance_paths:
        match = _COUNTER_RE.search(path)
        if not match:
            continue
        if pid is not None and int(match.group("pid")) != pid:
            continue
        records.append(
            {
                "pid": int(match.group("pid")),
                "luid": luid_counter_token(
                    int(match.group("high"), 16), int(match.group("low"), 16)
                ),
                "phys": int(match.group("phys")),
                "eng": int(match.group("eng")),
                "engtype": match.group("engtype"),
            }
        )
    return records


def engine_profile_for_luid(samples: Sequence, luid: str) -> dict:
    """Total utilisation per engine type on one adapter, from live samples.

    Each sample is ``{"luid", "engtype", "value"}``. Values are summed across
    instances of the same type, because one logical engine is exposed as several
    ``eng_N`` nodes.
    """
    profile: dict = {}
    for sample in samples:
        if sample["luid"].lower() != luid.lower():
            continue
        engtype = sample["engtype"]
        profile[engtype] = profile.get(engtype, 0.0) + float(sample["value"])
    return {name: round(value, 2) for name, value in profile.items()}


def classify_media_engines(profile: dict) -> list:
    """Pick the engines carrying the media work, by dominance rather than name.

    Returns the engine types at or above ``MEDIA_DOMINANCE_FRACTION`` of the
    busiest engine, and above a noise floor. Deliberately name-blind: if a
    driver update moves QSV encode onto a differently-named node, this still
    finds it, whereas hardcoding "VideoEncode" (which does not exist here) or
    "VideoDecode" (which does today, by accident of Intel's taxonomy) would
    silently report a saturated lane as idle.

    On the measured profile -- VideoDecode 74.89, 3D 12.09 -- this selects
    VideoDecode alone and rejects 3D at 16% of peak. Rejecting 3D matters:
    Vulkan inference also uses it, and counting it would collapse the
    model-occupancy and media-occupancy dimensions into one.
    """
    if not profile:
        return []
    peak = max(profile.values())
    if peak < MEDIA_MIN_ACTIVITY_PCT:
        return []
    threshold = max(peak * MEDIA_DOMINANCE_FRACTION, MEDIA_MIN_ACTIVITY_PCT)
    return sorted(name for name, value in profile.items() if value >= threshold)


def build_fingerprint(
    devices: Sequence, driver_version: str, ffmpeg_version: str
) -> dict:
    """The identity of the whole calibration.

    A change to any of these invalidates a stored lane map and -- per the
    accepted-capacity rule -- marks a prior two-lane benchmark stale. Sorted so
    the fingerprint is order-independent, since adapter enumeration order is
    exactly the thing we do not trust.
    """
    return {
        "driver_version": driver_version,
        "adapter_uuids": sorted(device.device_uuid for device in devices),
        "ffmpeg_version": ffmpeg_version,
    }


def fingerprint_matches(stored: dict, live: dict) -> bool:
    """Whether a stored calibration still describes the machine in front of us."""
    if not stored or not live:
        return False
    keys = ("driver_version", "adapter_uuids", "ffmpeg_version")
    return all(stored.get(key) == live.get(key) for key in keys)


def select_b70_devices(devices: Sequence) -> list:
    """The Arc Pro B70s, ordered by PCI bus so lane ids are deterministic."""
    return sorted(
        (device for device in devices if device.is_arc_b70),
        key=lambda device: device.pci_bus,
    )


# ------------------------------------------------------------------ IO shell

def default_lanes_path() -> Path:
    configured = os.environ.get("HEARTH_RENDER_LANES")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "var" / "render" / "lanes.json"


def save_calibration(calibration: Calibration, path: Optional[Path] = None) -> Path:
    """Persist the lane map atomically -- a torn lanes.json is unreadable state."""
    target = Path(path) if path is not None else default_lanes_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(calibration.to_dict(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def load_calibration(path: Optional[Path] = None) -> Optional[Calibration]:
    """Read a stored lane map, or None when absent/unreadable.

    Fails soft to None rather than raising: an unreadable lane map means
    "recalibrate", and the scheduler treats zero healthy lanes as zero capacity,
    which is the safe direction.
    """
    target = Path(path) if path is not None else default_lanes_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return Calibration(
            schema_version=int(raw["schema_version"]),
            fingerprint=dict(raw["fingerprint"]),
            lanes=[Lane(**lane) for lane in raw["lanes"]],
            calibrated_at=str(raw["calibrated_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


# ------------------------------------------------------- IO shell: discovery

def _run(command: Sequence, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )


def discover_binary(name: str, env_var: str) -> str:
    """Locate a tool, preferring an explicit override.

    ffmpeg here is a winget install that is on PATH for an interactive shell but
    not necessarily for a service-launched process, so the override matters.
    """
    configured = os.environ.get(env_var)
    if configured:
        return configured
    found = shutil.which(name)
    if found:
        return found
    raise CalibrationError(
        "cannot find %s on PATH; set %s to its full path" % (name, env_var)
    )


def ffmpeg_version(ffmpeg: str) -> str:
    proc = _run([ffmpeg, "-hide_banner", "-version"], timeout=30)
    first = (proc.stdout or "").splitlines()
    if not first:
        raise CalibrationError("ffmpeg -version produced no output")
    match = re.search(r"ffmpeg version (\S+)", first[0])
    return match.group(1) if match else first[0].strip()


def read_vulkan_devices(vulkaninfo: Optional[str] = None) -> list:
    """Enumerate physical adapters via vulkaninfo (the full dump, not --summary)."""
    binary = vulkaninfo or discover_binary("vulkaninfo", "HEARTH_VULKANINFO")
    proc = _run([binary], timeout=120)
    devices = parse_vulkaninfo(proc.stdout or "")
    if not devices:
        raise CalibrationError("vulkaninfo returned no usable devices")
    return devices


def driver_version_from(devices: Sequence, raw_text: str = "") -> str:
    """Driver version, used as part of the calibration fingerprint."""
    match = re.search(r"driverInfo\s*=\s*(\S+)", raw_text)
    if match:
        return match.group(1)
    return "unknown"


def probe_d3d11_indices(ffmpeg: str, max_index: int = MAX_DXGI_PROBE_INDEX) -> dict:
    """Map each DXGI index to (vendor_id, device_id, name).

    Indices that fail device creation are simply absent from the result. They
    are NOT treated as the end of the list -- WARP placeholders (1414:008c) sit
    between real adapters on this box, so an early break would hide a card.
    """
    found = {}
    for index in range(max_index + 1):
        proc = _run(
            [
                ffmpeg, "-hide_banner", "-v", "verbose",
                "-init_hw_device", "d3d11va=d:%d" % index,
                "-f", "lavfi", "-i", "nullsrc",
                "-frames:v", "0", "-f", "null", "-",
            ],
            timeout=60,
        )
        parsed = parse_ffmpeg_device_probe((proc.stderr or "") + (proc.stdout or ""))
        if parsed is not None:
            found[index] = parsed
    return found


def candidate_arc_indices(probe: dict) -> list:
    """DXGI indices that are Arc Pro B70s, in ascending index order."""
    return sorted(
        index
        for index, (vendor, device, _name) in probe.items()
        if vendor == INTEL_VENDOR_ID and device == ARC_B70_DEVICE_ID
    )


# -------------------------------------------------- IO shell: index -> LUID

_COUNTER_PS = r"""
$paths = (Get-Counter -ListSet 'GPU Engine').PathsWithInstances |
         Where-Object { $_ -match ('pid_' + $env:HEARTH_PROBE_PID + '_') }
$paths | ForEach-Object { $_ }
"""

# Utilisation, not just instance names. Instance EXISTENCE proves nothing here:
# a process that merely opened a Vulkan device has instances on every engine of
# every adapter. Only a utilisation reading distinguishes work from presence.
_UTILISATION_PS = r"""
$paths = (Get-Counter -ListSet 'GPU Engine').PathsWithInstances |
         Where-Object { $_ -match ('pid_' + $env:HEARTH_PROBE_PID + '_') -and
                        $_ -match 'Utilization' }
if ($paths) {
  $samples = (Get-Counter -Counter $paths -ErrorAction SilentlyContinue).CounterSamples
  foreach ($s in $samples) { "$($s.Path)|$($s.CookedValue)" }
}
"""


def sample_counter_instances(pid: int, powershell: str = "powershell") -> list:
    """List `GPU Engine` counter instance paths belonging to one process.

    This is the only mechanism that distinguishes the two B70s: both report
    8086:e223 to ffmpeg, and ffmpeg's device options accept an index only
    (vendor_id= is silently ignored). So we run a known encode and ask Windows
    which physical adapter that process touched.
    """
    env = dict(os.environ, HEARTH_PROBE_PID=str(pid))
    proc = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", _COUNTER_PS],
        capture_output=True,
        text=True,
        timeout=120,
        errors="replace",
        env=env,
    )
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def sample_counter_utilisation(pid: int, powershell: str = "powershell") -> list:
    """Sample live per-engine utilisation for one process.

    Returns ``[{"luid", "engtype", "value"}, ...]``. This is what makes media
    identity calibrated rather than assumed -- we run a known encode and read
    which engines actually carry load.
    """
    env = dict(os.environ, HEARTH_PROBE_PID=str(pid))
    proc = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", _UTILISATION_PS],
        capture_output=True, text=True, timeout=180, errors="replace", env=env,
    )
    samples = []
    for line in (proc.stdout or "").splitlines():
        path, _, raw = line.strip().rpartition("|")
        if not path:
            continue
        match = _COUNTER_RE.search(path)
        if not match or int(match.group("pid")) != pid:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        samples.append({
            "luid": luid_counter_token(
                int(match.group("high"), 16), int(match.group("low"), 16)
            ),
            "engtype": match.group("engtype"),
            "value": value,
        })
    return samples


def bind_index_to_lane(
    ffmpeg: str,
    child_device: int,
    settle_seconds: float = 5.0,
    powershell: str = "powershell",
) -> tuple:
    """Run a saturating QSV encode on `child_device` and observe the adapter.

    Returns ``(luid_token, media_engines, engine_profile)``.

    Two things are established at once, and both need a REAL encode:

    * which physical card this ffmpeg index reaches -- the two B70s are
      indistinguishable to ffmpeg (both report 8086:e223, and vendor_id= is
      silently ignored), so the only way to tell them apart is to run work and
      ask Windows which adapter it landed on;
    * which engine types carry that work, so the gate never has to guess a
      counter name.

    The source is a 4K60 testsrc and the process is killed after sampling. Both
    details are load-bearing: a short clip finishes before the counters can be
    read (instances vanish with the process), and a trivial source leaves the
    encoder idling between frames, which inverts the engine profile entirely.
    """
    command = [
        ffmpeg, "-hide_banner", "-v", "error",
        "-init_hw_device",
        "qsv=hw:hw_any,child_device=%d,child_device_type=d3d11va" % child_device,
        "-f", "lavfi",
        "-i", "testsrc=size=3840x2160:rate=60:duration=300",
        "-pix_fmt", "nv12", "-c:v", "h264_qsv", "-f", "null", "-",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, errors="replace",
    )
    try:
        time.sleep(settle_seconds)
        if process.poll() is not None:
            stderr = (process.stderr.read() if process.stderr else "") or ""
            raise CalibrationError(
                "probe encode on child_device=%d exited early: %s"
                % (child_device, stderr.strip()[:200] or "no error output")
            )
        samples = sample_counter_utilisation(process.pid, powershell=powershell)
        if not samples:
            raise CalibrationError(
                "no GPU Engine utilisation observed for child_device=%d"
                % child_device
            )
        # The adapter carrying the most work is the one this index reaches.
        totals: dict = {}
        for sample in samples:
            totals[sample["luid"]] = totals.get(sample["luid"], 0.0) + sample["value"]
        busiest = max(totals, key=lambda key: totals[key])
        if totals[busiest] < MEDIA_MIN_ACTIVITY_PCT:
            raise CalibrationError(
                "probe encode on child_device=%d produced no measurable GPU load; "
                "cannot bind a lane" % child_device
            )
        profile = engine_profile_for_luid(samples, busiest)
        media = classify_media_engines(profile)
        if not media:
            raise CalibrationError(
                "probe encode on child_device=%d lit no engine above the noise "
                "floor; profile=%r" % (child_device, profile)
            )
        return busiest, media, profile
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=15)


def calibrate(
    ffmpeg: Optional[str] = None,
    vulkaninfo: Optional[str] = None,
    powershell: str = "powershell",
) -> Calibration:
    """Discover the B70 lanes and bind each to the ffmpeg index that reaches it.

    Every lane starts unhealthy and is promoted only when a real encode has been
    observed landing on its LUID. A lane we could not bind stays in the map with
    ``healthy=False`` and a reason, because "this card exists but we cannot
    address it" is information the scheduler needs, not something to hide.
    """
    ffmpeg_bin = ffmpeg or discover_binary("ffmpeg", "HEARTH_FFMPEG")
    vulkan_bin = vulkaninfo or discover_binary("vulkaninfo", "HEARTH_VULKANINFO")

    raw = _run([vulkan_bin], timeout=120).stdout or ""
    devices = parse_vulkaninfo(raw)
    if not devices:
        raise CalibrationError("vulkaninfo returned no usable devices")

    b70s = select_b70_devices(devices)
    if not b70s:
        raise CalibrationError("no Arc Pro B70 adapters found")

    probe = probe_d3d11_indices(ffmpeg_bin)
    indices = candidate_arc_indices(probe)

    # luid -> child_device, established by observation rather than by position.
    binding = {}
    failures = {}
    for index in indices:
        try:
            luid, media, profile = bind_index_to_lane(
                ffmpeg_bin, index, powershell=powershell
            )
        except CalibrationError as exc:
            failures[index] = str(exc)
            continue
        binding[luid] = (index, media, profile)

    lanes = []
    for device in b70s:
        token = device.counter_token
        if token in binding:
            index, media, profile = binding[token]
            lanes.append(
                Lane(
                    lane_id=device.lane_id,
                    pci_bus=device.pci_bus,
                    device_uuid=device.device_uuid,
                    luid=token,
                    child_device=index,
                    media_engines=media,
                    engine_profile=profile,
                    engtypes=sorted(profile),
                    healthy=True,
                    detail="bound by observed encode on child_device=%d; "
                           "media engines %s selected from %r"
                           % (index, ", ".join(media), profile),
                )
            )
        else:
            lanes.append(
                Lane(
                    lane_id=device.lane_id,
                    pci_bus=device.pci_bus,
                    device_uuid=device.device_uuid,
                    luid=token,
                    child_device=None,
                    media_engines=[],
                    engine_profile={},
                    engtypes=[],
                    healthy=False,
                    detail="no ffmpeg index bound to this adapter; "
                           + ("probe errors: %s" % failures if failures else "not observed"),
                )
            )

    calibration = Calibration(
        schema_version=SCHEMA_VERSION,
        fingerprint=build_fingerprint(
            devices, driver_version_from(devices, raw), ffmpeg_version(ffmpeg_bin)
        ),
        lanes=sorted(lanes, key=lambda lane: lane.lane_id),
        calibrated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return calibration
