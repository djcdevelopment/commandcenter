#!/usr/bin/env python3
"""Machine fingerprint -- capture hardware identity per run, never declare it in config.

The estate's parts are fluid: cards move between boxes, boards get swapped, drives
get carried from one machine to the next. A `hardware_profile_id` typed into a
config file goes stale silently and then mislabels every observation formed after
the change -- as `omen-rtx5070-2026H2` did, on a box with no NVIDIA card in it.

So every benchmark run stamps what the machine *is*, measured at run time.

`hw_id` hashes the PHYSICAL configuration only: board, CPU, RAM class, and the set
of GPUs present. Driver version, BIOS revision and OS build are recorded but NOT
hashed -- those are exactly the things you want to vary and compare *within* one
machine identity.

Usage:
    python -m corpus.fingerprint              # human summary
    python -m corpus.fingerprint --json       # machine-readable, for run manifests
    python -m corpus.fingerprint --id         # just the hw_id
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

CONTRACT_VERSION = "machine-fingerprint.v1"

# Vendor IDs we care to name. Anything else is reported by raw id.
_VENDOR_NAMES = {
    "8086": "intel",
    "10de": "nvidia",
    "1002": "amd",
    "1a03": "aspeed",
}

# Display adapters that are management/BMC hardware, not compute. Recorded, but
# excluded from the GPU signature so that a KVM or BMC does not mint a new
# machine identity.
_NON_COMPUTE_VENDORS = {"1a03"}

_GPU_CLASS_GUID = "{4d36e968-e325-11ce-bfc1-08002be10318}"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _slug(text: str) -> str:
    text = re.sub(r"\(R\)|\(TM\)|\(C\)", "", text, flags=re.I)
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-{2,}", "-", text)


def _run(cmd: list[str], timeout: int = 60) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _powershell(script: str) -> object | None:
    """Run a PowerShell snippet that emits JSON; return the parsed object."""
    raw = _run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
    ).strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _parse_pnp(device_id: str) -> dict:
    """Pull vendor/device/subsys out of a Windows PNPDeviceID."""
    ven = re.search(r"VEN_([0-9A-Fa-f]{4})", device_id or "")
    dev = re.search(r"DEV_([0-9A-Fa-f]{4})", device_id or "")
    sub = re.search(r"SUBSYS_([0-9A-Fa-f]{8})", device_id or "")
    return {
        "vendor_id": ven.group(1).lower() if ven else None,
        "device_id": dev.group(1).lower() if dev else None,
        "subsys_id": sub.group(1).lower() if sub else None,
    }


def _parse_ps_date(value: object) -> str | None:
    """ConvertTo-Json renders DateTime as /Date(<epoch ms>)/. Return ISO yyyy-mm-dd."""
    match = re.search(r"/Date\((-?\d+)", str(value or ""))
    if not match:
        return None
    import datetime as _dt

    seconds = int(match.group(1)) / 1000.0
    try:
        stamp = _dt.datetime.fromtimestamp(seconds, _dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return stamp.date().isoformat()


def _parse_location(value: object) -> tuple[str | None, bool | None]:
    """Turn 'PCI bus 4, device 0, function 0' into ('0000:04:00.0', is_integrated).

    Normalising to Linux's BDF notation is what lets a Windows fingerprint and a
    Linux fingerprint of the SAME machine be joined -- which is the entire point
    of measuring this board under both operating systems.

    Bus 0 means the device hangs off the root complex: an integrated GPU. Discrete
    cards sit behind a PCIe root port on a non-zero bus.
    """
    match = re.search(r"PCI bus (\d+), device (\d+), function (\d+)", str(value or ""))
    if not match:
        return None, None
    bus, device, function = (int(g) for g in match.groups())
    return f"0000:{bus:02x}:{device:02x}.{function}", bus == 0


# --------------------------------------------------------------------------
# windows collection
# --------------------------------------------------------------------------

def _windows_vram_by_desc() -> dict[str, int]:
    """Real VRAM sizes, from the display class registry keys.

    Win32_VideoController.AdapterRAM is a signed 32-bit field: it reports 4 GB for
    every card larger than that, which is useless on a 32 GB B70.
    """
    script = (
        "$k = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\"
        + _GPU_CLASS_GUID
        + "'; "
        "Get-ChildItem $k -ErrorAction SilentlyContinue | ForEach-Object { "
        "  $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue; "
        "  $size = $p.'HardwareInformation.qwMemorySize'; "
        "  if ($size) { [pscustomobject]@{ Desc = $p.DriverDesc; Bytes = [uint64]$size } } "
        "} | ConvertTo-Json -Compress"
    )
    by_desc: dict[str, int] = {}
    for entry in _as_list(_powershell(script)):
        if isinstance(entry, dict) and entry.get("Desc"):
            desc = str(entry["Desc"])
            size = int(entry.get("Bytes") or 0)
            # Several adapters can share a DriverDesc; keep the largest.
            by_desc[desc] = max(by_desc.get(desc, 0), size)
    return by_desc


def _collect_windows() -> dict:
    board = _powershell(
        "Get-CimInstance Win32_BaseBoard | "
        "Select-Object Manufacturer,Product,Version | ConvertTo-Json -Compress"
    ) or {}
    bios = _powershell(
        "Get-CimInstance Win32_BIOS | Select-Object "
        "SMBIOSBIOSVersion,Manufacturer | ConvertTo-Json -Compress"
    ) or {}
    system = _powershell(
        "Get-CimInstance Win32_ComputerSystem | Select-Object "
        "Name,Manufacturer,Model,TotalPhysicalMemory,NumberOfProcessors,"
        "NumberOfLogicalProcessors | ConvertTo-Json -Compress"
    ) or {}
    cpus = _as_list(_powershell(
        "Get-CimInstance Win32_Processor | Select-Object "
        "Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed "
        "| ConvertTo-Json -Compress"
    ))
    dimm_rows = _as_list(_powershell(
        "Get-CimInstance Win32_PhysicalMemory | Select-Object "
        "Capacity,Speed,ConfiguredClockSpeed,PartNumber | ConvertTo-Json -Compress"
    ))
    video = _as_list(_powershell(
        "Get-CimInstance Win32_VideoController | ForEach-Object { "
        "  $loc = (Get-PnpDeviceProperty -InstanceId $_.PNPDeviceID "
        "    -KeyName 'DEVPKEY_Device_LocationInfo' -ErrorAction SilentlyContinue).Data; "
        "  [pscustomobject]@{ Name = $_.Name; PNPDeviceID = $_.PNPDeviceID; "
        "    DriverVersion = $_.DriverVersion; DriverDate = $_.DriverDate; Location = $loc } "
        "} | ConvertTo-Json -Compress"
    ))

    vram_by_desc = _windows_vram_by_desc()

    gpus = []
    for card in video:
        if not isinstance(card, dict):
            continue
        ids = _parse_pnp(card.get("PNPDeviceID", ""))
        name = card.get("Name") or "unknown"
        pci_address, integrated = _parse_location(card.get("Location"))
        reported_memory = vram_by_desc.get(name)

        # An integrated GPU has no dedicated memory: the registry reports its
        # shared-system ceiling, which on this box reads as 72 GB and is not VRAM
        # in any sense a benchmark cares about. Record it, but not as vram_bytes.
        gpus.append({
            "name": name,
            "vendor": _VENDOR_NAMES.get(ids["vendor_id"] or "", ids["vendor_id"]),
            **ids,
            "pci_address": pci_address,
            "pnp_device_id": card.get("PNPDeviceID"),
            "integrated": integrated,
            "driver_version": card.get("DriverVersion"),
            "driver_date": _parse_ps_date(card.get("DriverDate")),
            "vram_bytes": None if integrated else reported_memory,
            "shared_memory_bytes": reported_memory if integrated else None,
            "vram_source": None if integrated else "registry:qwMemorySize",
            "compute": ids["vendor_id"] not in _NON_COMPUTE_VENDORS,
        })

    dimms = [
        {
            "capacity_bytes": int(d.get("Capacity") or 0),
            "speed_mts": d.get("Speed"),
            "configured_mts": d.get("ConfiguredClockSpeed"),
            "part_number": (d.get("PartNumber") or "").strip() or None,
        }
        for d in dimm_rows if isinstance(d, dict)
    ]

    cpu = cpus[0] if cpus and isinstance(cpus[0], dict) else {}
    return {
        "hostname": system.get("Name") or platform.node(),
        "board": {
            "vendor": (board.get("Manufacturer") or "").strip() or None,
            "product": (board.get("Product") or "").strip() or None,
            "version": (board.get("Version") or "").strip() or None,
        },
        "bios": {
            "version": str(bios.get("SMBIOSBIOSVersion") or "").strip() or None,
            "vendor": (bios.get("Manufacturer") or "").strip() or None,
        },
        "chassis": {
            "vendor": (system.get("Manufacturer") or "").strip() or None,
            "model": (system.get("Model") or "").strip() or None,
        },
        "cpu": {
            "model": (cpu.get("Name") or "").strip() or None,
            "sockets": system.get("NumberOfProcessors"),
            "cores": cpu.get("NumberOfCores"),
            "threads": system.get("NumberOfLogicalProcessors"),
            "max_clock_mhz": cpu.get("MaxClockSpeed"),
        },
        "memory": {
            "total_bytes": int(system.get("TotalPhysicalMemory") or 0),
            "dimms": dimms,
        },
        "gpus": gpus,
    }


# --------------------------------------------------------------------------
# linux collection
# --------------------------------------------------------------------------

def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _linux_gpu_vram(slot: str) -> int | None:
    """Largest prefetchable BAR >= 256 MB, as a proxy for local device memory.

    Intel's real LMEM total is only exposed through privileged debugfs; the BAR
    size is stable and needs no privileges. Recorded as a proxy, not a claim.
    """
    resource = _read(f"/sys/bus/pci/devices/{slot}/resource")
    if not resource:
        return None
    best = 0
    for entry in resource.splitlines()[:6]:
        parts = entry.split()
        if len(parts) < 2:
            continue
        try:
            size = int(parts[1], 16) - int(parts[0], 16) + 1
        except ValueError:
            continue
        if size >= 256 * 1024 * 1024:
            best = max(best, size)
    return best or None


def _collect_linux() -> dict:
    dmi = "/sys/class/dmi/id"
    cpuinfo = _read("/proc/cpuinfo") or ""

    model = None
    for line in cpuinfo.splitlines():
        if line.lower().startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    threads = len(re.findall(r"^processor\s*:", cpuinfo, flags=re.M)) or None
    cores_match = re.search(r"cpu cores\s*:\s*(\d+)", cpuinfo)
    cores = int(cores_match.group(1)) if cores_match else None

    total_bytes = 0
    mem_match = re.search(r"MemTotal:\s+(\d+) kB", _read("/proc/meminfo") or "")
    if mem_match:
        total_bytes = int(mem_match.group(1)) * 1024

    gpus = []
    for line in _run(["lspci", "-nnD"]).splitlines():
        if not re.search(r"(VGA compatible|3D controller|Display controller)", line):
            continue
        slot = line.split()[0]
        ids = re.findall(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]", line)
        vendor_id, device_id = ids[-1] if ids else (None, None)
        name = re.sub(r"^\S+\s+[^:]+:\s*", "", line)
        name = re.sub(r"\s*\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]", "", name).strip()
        name = re.sub(r"\s*\(rev [0-9a-f]+\)\s*$", "", name).strip()

        driver = None
        driver_link = Path(f"/sys/bus/pci/devices/{slot}/driver")
        if driver_link.is_symlink():
            driver = driver_link.resolve().name

        vendor_id = (vendor_id or "").lower() or None
        # Same rule as Windows: bus 0 is the root complex, i.e. integrated.
        bus_match = re.match(r"[0-9a-f]{4}:([0-9a-f]{2}):", slot)
        integrated = int(bus_match.group(1), 16) == 0 if bus_match else None
        bar_bytes = _linux_gpu_vram(slot)

        gpus.append({
            "name": name,
            "vendor": _VENDOR_NAMES.get(vendor_id or "", vendor_id),
            "vendor_id": vendor_id,
            "device_id": (device_id or "").lower() or None,
            "subsys_id": None,
            "pci_address": slot,
            "pnp_device_id": None,
            "integrated": integrated,
            "driver_version": driver,
            "driver_date": None,
            "vram_bytes": None if integrated else bar_bytes,
            "shared_memory_bytes": None,
            "vram_source": None if integrated else "sysfs:pci-bar",
            "compute": vendor_id not in _NON_COMPUTE_VENDORS,
        })

    return {
        "hostname": platform.node(),
        "board": {
            "vendor": _read(f"{dmi}/board_vendor"),
            "product": _read(f"{dmi}/board_name"),
            "version": _read(f"{dmi}/board_version"),
        },
        "bios": {
            "version": _read(f"{dmi}/bios_version"),
            "vendor": _read(f"{dmi}/bios_vendor"),
        },
        "chassis": {
            "vendor": _read(f"{dmi}/sys_vendor"),
            "model": _read(f"{dmi}/product_name"),
        },
        "cpu": {
            "model": model,
            "sockets": None,
            "cores": cores,
            "threads": threads,
            "max_clock_mhz": None,
        },
        "memory": {"total_bytes": total_bytes, "dimms": []},
        "gpus": gpus,
    }


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def _ram_class_gb(total_bytes: int) -> int:
    """Round installed RAM to the nearest 8 GB.

    Reported totals move by a few hundred MB depending on what the firmware
    reserves, so the raw byte count is not a stable identity component.
    """
    if not total_bytes:
        return 0
    return int(round((total_bytes / 1024 ** 3) / 8.0) * 8)


def _gpu_signature(gpus: list[dict]) -> list[str]:
    """Sorted 'vendor:device:subsys xN' terms over compute GPUs only."""
    counts: dict[str, int] = {}
    for gpu in gpus:
        if not gpu.get("compute"):
            continue
        key = ":".join(
            str(gpu.get(field) or "?")
            for field in ("vendor_id", "device_id", "subsys_id")
        )
        counts[key] = counts.get(key, 0) + 1
    return [f"{key}x{counts[key]}" for key in sorted(counts)]


def compute_identity(facts: dict) -> dict:
    """Derive hw_id + hw_label from collected facts.

    Deliberately excluded from the hash: driver version, BIOS revision, OS build,
    hostname. Those vary within one machine and are precisely what a benchmark
    round exists to compare.
    """
    board = facts["board"]
    cpu = facts["cpu"]
    ram_gb = _ram_class_gb(facts["memory"]["total_bytes"])
    signature = _gpu_signature(facts["gpus"])

    canonical = "|".join([
        "board=" + (board.get("vendor") or "?") + "/" + (board.get("product") or "?"),
        "cpu=" + (cpu.get("model") or "?"),
        f"ram_gb={ram_gb}",
        "gpus=" + (",".join(signature) or "none"),
    ])
    hw_id = "hw-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    # Label the discrete cards. An iGPU sits on every consumer part and would
    # drown the label without distinguishing anything.
    discrete = [
        gpu for gpu in facts["gpus"]
        if gpu.get("compute") and gpu.get("integrated") is False
    ]
    gpu_label = "no-discrete-gpu"
    if discrete:
        by_name: dict[str, int] = {}
        for gpu in discrete:
            by_name[gpu["name"]] = by_name.get(gpu["name"], 0) + 1
        gpu_label = "+".join(
            f"{count}x{_slug(name)}" for name, count in sorted(by_name.items())
        )

    label = "+".join(part for part in [
        _slug(board.get("product") or "unknown-board"),
        _slug(cpu.get("model") or "unknown-cpu"),
        f"{ram_gb}gb",
        gpu_label,
    ] if part)

    return {
        "hw_id": hw_id,
        "hw_label": label,
        "hw_canonical": canonical,
        "ram_class_gb": ram_gb,
        "gpu_signature": signature,
    }


def fingerprint() -> dict:
    system = platform.system()
    if system == "Windows":
        facts = _collect_windows()
    elif system == "Linux":
        facts = _collect_linux()
    else:
        raise SystemExit(f"unsupported platform: {system}")

    facts["os"] = {
        "system": system,
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
    }
    facts["contract_version"] = CONTRACT_VERSION
    facts.update(compute_identity(facts))
    return facts


def _human(fp: dict) -> str:
    total_gb = fp["memory"]["total_bytes"] / 1024 ** 3
    lines = [
        f"hw_id     {fp['hw_id']}",
        f"hw_label  {fp['hw_label']}",
        f"host      {fp['hostname']}  ({fp['os']['system']} {fp['os']['release']}"
        f" build {fp['os']['version']})",
        f"board     {fp['board']['vendor']} {fp['board']['product']}"
        f"  BIOS {fp['bios']['version']}",
        f"cpu       {fp['cpu']['model']}  ({fp['cpu']['cores']}C/{fp['cpu']['threads']}T)",
        f"memory    {total_gb:.1f} GB installed  (class {fp['ram_class_gb']} GB,"
        f" {len(fp['memory']['dimms'])} dimm(s))",
        "gpus",
    ]
    for gpu in sorted(fp["gpus"], key=lambda g: g.get("pci_address") or ""):
        vram = gpu.get("vram_bytes")
        shared = gpu.get("shared_memory_bytes")
        if vram:
            memory_text = f"vram {vram / 1024 ** 3:.1f} GB"
        elif shared:
            memory_text = f"shared {shared / 1024 ** 3:.1f} GB (no dedicated)"
        else:
            memory_text = "vram unknown"
        tags = []
        if gpu.get("integrated"):
            tags.append("integrated")
        if not gpu.get("compute"):
            tags.append("non-compute")
        suffix = f"   [{', '.join(tags)}]" if tags else ""
        lines.append(f"  - {gpu.get('pci_address') or '?':<14} {gpu['name']}{suffix}")
        lines.append(
            f"      {gpu['vendor_id']}:{gpu['device_id']}  {memory_text}"
            f"  driver {gpu.get('driver_version')} ({gpu.get('driver_date') or 'n/a'})"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture this machine's hardware identity.")
    parser.add_argument("--json", action="store_true", help="emit the full record as JSON")
    parser.add_argument("--id", action="store_true", help="emit only the hw_id")
    parser.add_argument("--out", type=Path, help="also write the JSON record to this path")
    args = parser.parse_args(argv)

    fp = fingerprint()

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(fp, indent=2) + "\n", encoding="utf-8")

    if args.id:
        print(fp["hw_id"])
    elif args.json:
        print(json.dumps(fp, indent=2))
    else:
        print(_human(fp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
