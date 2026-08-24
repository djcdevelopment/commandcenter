#!/usr/bin/env python3
"""Resolve Vulkan device indices by hardware identity, never by bare number.

WHY THIS EXISTS
---------------
On the AM4 box, llama.cpp's Vulkan backend enumerated the two Arc Pro B70s as
`Vulkan0` and `Vulkan1`. Every benchmark script in `E:\\work\\battlemage` therefore
hardcodes `GGML_VK_VISIBLE_DEVICES='0'` / `'1'` to mean "card 0" / "card 1".

On the Z890 board the integrated GPU enumerates FIRST:

    Vulkan0: Intel(R) Graphics                  (74286 MiB, 103494 MiB free)
    Vulkan1: Intel(R) Arc(TM) Pro B70 Graphics  (32558 MiB,  31789 MiB free)
    Vulkan2: Intel(R) Arc(TM) Pro B70 Graphics  (32558 MiB,  31789 MiB free)

Running those scripts unchanged does not fail. It benchmarks the integrated
graphics and reports the numbers under a B70 label. Worse, llama.cpp believes the
iGPU has 103 GB free -- so even a 59 GB model "fits" there, off system RAM, and a
result that never touched a discrete card lands in the corpus as a dual-B70
measurement.

A silent wrong-hardware substitution is the most expensive kind of measurement bug
because nothing about the output looks wrong. So: no bare indices anywhere in this
round. Ask for hardware, get indices, and refuse to hand back an integrated device
unless explicitly asked for one.

Usage:
    python -m corpus.vkdevices                    # show the resolved device table
    python -m corpus.vkdevices --select b70:0     # -> "1"
    python -m corpus.vkdevices --select b70:all   # -> "1,2"
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CONTRACT_VERSION = "vulkan-devices.v1"

DEFAULT_ENGINE = Path(r"E:\work\battlemage\llamacpp-win-vulkan")

# `ggml_vulkan: N = <name> (<vendor>) | uma: 1 | ...`
_GGML_LINE = re.compile(
    r"^ggml_vulkan:\s*(?P<index>\d+)\s*=\s*(?P<name>.+?)\s*\((?P<vendor>[^)]*)\)\s*\|"
    r"(?P<flags>.*)$"
)
# `  VulkanN: <name> (<total> MiB, <free> MiB free)`
_AVAIL_LINE = re.compile(
    r"^\s*Vulkan(?P<index>\d+):\s*(?P<name>.+?)\s*\((?P<total>\d+)\s*MiB,"
    r"\s*(?P<free>\d+)\s*MiB free\)"
)


class DeviceResolutionError(RuntimeError):
    """Raised when a selector cannot be satisfied by the hardware present."""


def _flag(flags: str, key: str) -> str | None:
    match = re.search(rf"\b{re.escape(key)}\s*:\s*(\S+)", flags)
    return match.group(1) if match else None


def list_devices(engine_dir: Path = DEFAULT_ENGINE, timeout: int = 120) -> list[dict]:
    """Enumerate Vulkan devices exactly as the benchmark engine sees them.

    Deliberately shells out to the same binary the benchmarks use, rather than
    querying Vulkan independently: the only enumeration order that matters is the
    one llama.cpp will actually apply.
    """
    binary = engine_dir / "llama-bench.exe"
    if not binary.exists():
        binary = engine_dir / "llama-bench"
    if not binary.exists():
        raise DeviceResolutionError(f"no llama-bench binary under {engine_dir}")

    proc = subprocess.run(
        [str(binary), "--list-devices"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=str(engine_dir),
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")

    devices: dict[int, dict] = {}
    for line in text.splitlines():
        ggml = _GGML_LINE.match(line.strip())
        if ggml:
            index = int(ggml.group("index"))
            flags = ggml.group("flags")
            uma = _flag(flags, "uma")
            entry = devices.setdefault(index, {"index": index})
            entry.update({
                "name": ggml.group("name").strip(),
                "vendor": ggml.group("vendor").strip(),
                # uma:1 means unified memory -- an integrated part sharing system
                # RAM. This is the engine's own view of integrated-ness, which is
                # the view that matters here.
                "uma": uma == "1",
                "fp16": _flag(flags, "fp16") == "1",
                "bf16": _flag(flags, "bf16") == "1",
                "matrix_cores": _flag(flags, "matrix cores")
                or _flag(flags, "cores"),
            })
            continue

        avail = _AVAIL_LINE.match(line)
        if avail:
            index = int(avail.group("index"))
            entry = devices.setdefault(index, {"index": index})
            entry.setdefault("name", avail.group("name").strip())
            entry["total_mib"] = int(avail.group("total"))
            entry["free_mib"] = int(avail.group("free"))

    if not devices:
        raise DeviceResolutionError(
            f"{binary.name} --list-devices produced no recognisable devices; "
            f"exit={proc.returncode}"
        )

    result = []
    for index in sorted(devices):
        entry = devices[index]
        name = entry.get("name", "")
        entry["is_b70"] = "B70" in name
        entry["is_discrete"] = not entry.get("uma", False)
        entry.setdefault("total_mib", None)
        entry.setdefault("free_mib", None)
        result.append(entry)
    return result


def resolve(selector: str, devices: list[dict] | None = None) -> list[int]:
    """Turn a hardware selector into Vulkan indices.

    Selectors:
        b70:all      every discrete B70, in PCI enumeration order
        b70:0        the first B70   (NOT Vulkan device 0)
        b70:1        the second B70
        discrete:all every non-UMA device
        igpu         the integrated device -- must be asked for by name
    """
    devices = devices if devices is not None else list_devices()
    selector = selector.strip().lower()

    if selector == "igpu":
        matches = [d["index"] for d in devices if not d["is_discrete"]]
        if not matches:
            raise DeviceResolutionError("no integrated device present")
        return matches[:1]

    if selector.startswith("discrete:"):
        pool = [d for d in devices if d["is_discrete"]]
        which = selector.split(":", 1)[1]
    elif selector.startswith("b70:"):
        pool = [d for d in devices if d["is_b70"] and d["is_discrete"]]
        which = selector.split(":", 1)[1]
    else:
        raise DeviceResolutionError(
            f"unrecognised selector {selector!r}; "
            "use b70:N, b70:all, discrete:all or igpu"
        )

    if not pool:
        raise DeviceResolutionError(
            f"selector {selector!r} matched no hardware. Present: "
            + ", ".join(f"Vulkan{d['index']}={d['name']}" for d in devices)
        )

    if which == "all":
        return [d["index"] for d in pool]

    try:
        ordinal = int(which)
    except ValueError as exc:
        raise DeviceResolutionError(f"bad ordinal in selector {selector!r}") from exc

    if ordinal >= len(pool):
        raise DeviceResolutionError(
            f"selector {selector!r} asks for ordinal {ordinal} but only "
            f"{len(pool)} matching device(s) present"
        )
    return [pool[ordinal]["index"]]


def visible_devices(selector: str, devices: list[dict] | None = None) -> str:
    """The value to assign to GGML_VK_VISIBLE_DEVICES for this selector."""
    return ",".join(str(i) for i in resolve(selector, devices))


def assert_no_igpu(selector: str, devices: list[dict] | None = None) -> None:
    """Fail loudly if a selector would put work on the integrated GPU."""
    devices = devices if devices is not None else list_devices()
    by_index = {d["index"]: d for d in devices}
    for index in resolve(selector, devices):
        if not by_index[index]["is_discrete"]:
            raise DeviceResolutionError(
                f"selector {selector!r} resolves to Vulkan{index} "
                f"({by_index[index]['name']}), which is integrated. "
                "Refusing: a benchmark on the iGPU labelled as a discrete card "
                "poisons the corpus."
            )


def _table(devices: list[dict]) -> str:
    lines = [
        f"{'vulkan':<8} {'kind':<12} {'total':>10} {'free':>10}  name",
        "-" * 74,
    ]
    for device in devices:
        kind = "B70" if device["is_b70"] else (
            "discrete" if device["is_discrete"] else "INTEGRATED"
        )
        total = f"{device['total_mib']} MiB" if device["total_mib"] else "?"
        free = f"{device['free_mib']} MiB" if device["free_mib"] else "?"
        lines.append(
            f"Vulkan{device['index']:<2} {kind:<12} {total:>10} {free:>10}  "
            f"{device['name']}"
        )
    lines.append("")
    for selector in ("b70:all", "b70:0", "b70:1"):
        try:
            lines.append(f"  {selector:<12} -> GGML_VK_VISIBLE_DEVICES="
                         f"{visible_devices(selector, devices)}")
        except DeviceResolutionError as exc:
            lines.append(f"  {selector:<12} -> unavailable ({exc})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve Vulkan device indices by hardware, not by number."
    )
    parser.add_argument("--engine", type=Path, default=DEFAULT_ENGINE,
                        help="directory holding llama-bench")
    parser.add_argument("--select", help="selector to resolve, e.g. b70:all")
    args = parser.parse_args(argv)

    devices = list_devices(args.engine)

    if args.select:
        try:
            assert_no_igpu(args.select, devices)
            print(visible_devices(args.select, devices))
        except DeviceResolutionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    print(_table(devices))
    return 0


if __name__ == "__main__":
    sys.exit(main())
