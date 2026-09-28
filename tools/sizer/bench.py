"""Latency and cost of one MiniLM pass per device (ADR-0050, lap N1: does the NPU beat the CPU?).

    ~/.venvs/npu/bin/python tools/sizer/bench.py [--ir ~/models/minilm-ov/fp16] [--device NPU --device CPU]
        [--seq 256] [--n 50] [--cache ~/.cache/ov_npu] [--json out.json]

Per device: compile time cold and cached (CACHE_DIR), per-call latency p50/p90 over n calls on a real
instruction, the NPU's sysfs busy-time and memory deltas (proof it ran, and the DDR it costs), and the
process RSS delta. Read-only on the system; writes only the compile cache and the optional JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import statistics
import time
from pathlib import Path

SYSFS = Path("/sys/bus/pci/devices/0000:00:0b.0")
TEXT = ("In at most 250 words: how does this module decide what to dispatch in one tick? Cover the per-lane "
        "slot caps, why an experiment ends the tick, how a proofing brief becomes a whole_file proposal, and "
        "how failed or stale candidates are excluded. Cite every claim as /input/bankedfire_linux.py:N.")


def sysfs(name: str):
    try:
        return int((SYSFS / name).read_text().strip())
    except (OSError, ValueError):
        return None


def rss_kb() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ir", type=Path, default=Path.home() / "models" / "minilm-ov" / "fp16")
    ap.add_argument("--device", action="append", default=None)
    ap.add_argument("--seq", type=int, default=256)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "ov_npu")
    ap.add_argument("--no-cache-cold", action="store_true", help="skip the cold compile (do not wipe the cache dir)")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    devices = args.device or ["NPU", "CPU"]

    import numpy as np
    import openvino as ov
    from transformers import AutoTokenizer

    core = ov.Core()
    available = core.available_devices
    tok = AutoTokenizer.from_pretrained(args.ir, local_files_only=True)
    enc = tok([TEXT], padding="max_length", max_length=args.seq, truncation=True, return_tensors="np")
    report = {"ir": str(args.ir), "seq": args.seq, "n": args.n, "available_devices": available, "openvino": ov.__version__,
              "npu_idle": {"busy_us": sysfs("npu_busy_time_us"), "mem_bytes": sysfs("npu_memory_utilization"),
                           "mhz": sysfs("npu_current_frequency_mhz")}, "devices": {}}
    for dev in devices:
        entry: dict = {}
        if dev not in available:
            entry["skipped"] = f"{dev} not in available_devices"
            report["devices"][dev] = entry
            continue
        model = core.read_model(str(args.ir / "openvino_model.xml"))
        names = {i.get_any_name() for i in model.inputs}
        feed = {k: v for k, v in enc.items() if k in names}
        cfg = {"PERFORMANCE_HINT": "LATENCY"}
        cache_dir = args.cache / dev.lower()
        if not args.no_cache_cold and cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cfg["CACHE_DIR"] = str(cache_dir)
        busy0, mem0, rss0 = sysfs("npu_busy_time_us"), sysfs("npu_memory_utilization"), rss_kb()
        t0 = time.perf_counter(); compiled = core.compile_model(model, dev, cfg); entry["compile_cold_s"] = round(time.perf_counter() - t0, 3)
        t0 = time.perf_counter(); compiled2 = core.compile_model(core.read_model(str(args.ir / "openvino_model.xml")), dev, cfg); entry["compile_cached_s"] = round(time.perf_counter() - t0, 3)
        del compiled2
        out = compiled.output(0)
        for _ in range(5):
            compiled(feed)[out]
        lat = []
        for _ in range(args.n):
            t = time.perf_counter(); compiled(feed)[out]; lat.append((time.perf_counter() - t) * 1000)
        lat.sort()
        entry["latency_ms"] = {"p50": round(statistics.median(lat), 2), "p90": round(lat[int(0.9 * (len(lat) - 1))], 2),
                               "min": round(lat[0], 2), "max": round(lat[-1], 2)}
        entry["throughput_per_s"] = round(1000 / statistics.median(lat), 1)
        time.sleep(1.0)
        busy1, mem1 = sysfs("npu_busy_time_us"), sysfs("npu_memory_utilization")
        entry["npu_busy_us_delta"] = (busy1 - busy0) if busy0 is not None and busy1 is not None else None
        entry["npu_mem_bytes_delta"] = (mem1 - mem0) if mem0 is not None and mem1 is not None else None
        entry["npu_mhz_after"] = sysfs("npu_current_frequency_mhz")
        entry["rss_kb_delta"] = rss_kb() - rss0
        try:
            entry["full_device_name"] = core.get_property(dev, "FULL_DEVICE_NAME")
        except Exception:  # noqa: BLE001
            pass
        emb = compiled(feed)[out]
        entry["output_shape"] = list(emb.shape)
        report["devices"][dev] = entry
        del compiled
    print(json.dumps(report, indent=2))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
