#!/usr/bin/env python3
"""FF-CENSUS -- a pre-run ping across every known GPU and GPU consumer.

Standing control for the Factory Frontier campaign. Run this BEFORE every cell in
a series, and again after if the cell moved weights around.

Why this exists: on 2026-08-29 an entire campaign lap was measured against a box
that was silently running on ONE B70 instead of two, because placement was
assumed rather than observed. Vulkan enumeration order on this machine is
NONDETERMINISTIC -- it reshuffles between runs, so an index-based device filter
that is correct in one process is wrong in the next, with no error and no
warning.

So the load-bearing field here is `vulkan_enumeration`: the device order THIS
run actually saw. Everything else is context for it.

Read-only. Emits one JSON row (probe="FF-CENSUS") to the FF receipts ledger and
prints a human summary. Never kills or modifies anything.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

LEDGER = r"E:\work\battlemage\ff-probes\ff-receipts.jsonl"
B70TOOLS = r"E:\work\b70tools\build\b70tools.exe"
LLAMA_BENCH = r"E:\work\llamacpp-knee\build\bin\llama-bench.exe"
SCRATCH = r"E:\work\battlemage\ff-probes\_census"

PS_PROCS = (
    "Get-Process | Where-Object { $_.ProcessName -match "
    "'llama-server|llama-bench|ollama|ovms|ffmpeg|comfy|blender|Bf6' } | "
    "Select-Object Id,ProcessName,@{n='ws_gb';e={[math]::Round($_.WorkingSet64/1GB,2)}} | "
    "ConvertTo-Json -Compress -AsArray"
)
PS_PORTS = (
    "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
    "Where-Object { $_.LocalPort -in 8082,8083,8084,8090,8710,18184,18186,18200 } | "
    "Select-Object LocalPort,OwningProcess | ConvertTo-Json -Compress -AsArray"
)
PS_HOST = (
    "$os=Get-CimInstance Win32_OperatingSystem; [pscustomobject]@{ "
    "commit_used_gb=[math]::Round(($os.TotalVirtualMemorySize-$os.FreeVirtualMemory)/1MB,1); "
    "commit_limit_gb=[math]::Round($os.TotalVirtualMemorySize/1MB,1); "
    "ram_free_gb=[math]::Round($os.FreePhysicalMemory/1MB,1) } | ConvertTo-Json -Compress"
)

IGCL_CAVEAT = (
    "IGCL on the top slot misreports voltage/frequency and its activity counters run "
    "45-94x wall clock; temperature is credible. IGCL also goes silent under concurrent "
    "Vulkan init -- exactly when a saturation metric would want to sample."
)

# MEASURED 2026-08-29: gpu.adapter.vram.local.bytes_committed is an ACTIVITY-WINDOW
# signal, not a steady-state residency measure. Sampled shortly after a server start it
# reported 29.57 GB (pre-fix, one card) and 14.86/15.78 GB (post-fix, both cards) --
# both corroborated by llama-server's own load report. Sampled again once the process
# settled it read ~0.00 GB on both cards while the model was demonstrably resident and
# serving at 86.5 tok/s decode. Reproduced with and without a prior Vulkan instance, so
# it is not perturbation from this script. b70tools' README already names this:
# "Workload VRAM residency invisible -- model weights are invisible to v1".
# => NEVER gate placement on this field. Authorities, in order:
#      1. llama-server's own load report (one-shot, at model load, needs -lv 5)
#      2. per-card TEMPERATURE delta (credible per b70tools; both cards warm = both working)
#      3. this counter, and only inside the post-start activity window
RESIDENCY_CAVEAT = (
    "local_committed is an activity-window signal, not steady-state residency; it reads "
    "~0 on a healthy resident model once the server settles. Placement authority is the "
    "server's own load report, with per-card temperature as corroboration."
)


def _run(cmd, timeout=180, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=env, errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001 -- a census never raises, it reports
        return -1, "census: command failed: %s" % (exc,)


def _powershell(script, timeout=90):
    rc, out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   timeout=timeout)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def vulkan_enumeration():
    """The device order THIS process sees -- the whole point of the census.

    Deliberately runs with NO GGML_VK_VISIBLE_DEVICES so we observe the true,
    unfiltered order. That order is the thing which reshuffles.
    """
    env = dict(os.environ)
    env.pop("GGML_VK_VISIBLE_DEVICES", None)
    rc, out = _run([LLAMA_BENCH, "--list-devices"], env=env)
    devices, available = [], []
    for line in out.splitlines():
        m = re.match(r"ggml_vulkan:\s+(\d+)\s+=\s+(.+?)\s+\|", line)
        if m:
            devices.append({"index": int(m.group(1)), "name": m.group(2).strip()})
        m2 = re.match(r"\s+(Vulkan\d+):\s+(.+?)\s+\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)", line)
        if m2:
            available.append({"handle": m2.group(1), "name": m2.group(2).strip(),
                              "total_mib": int(m2.group(3)), "free_mib": int(m2.group(4))})
    arc_indices = [d["index"] for d in devices if "Arc" in d["name"]]
    return {
        "ok": rc == 0 and bool(devices),
        "devices": devices,
        "available": available,
        "arc_indices_this_run": arc_indices,
        "igpu_at_index_0": bool(devices) and "Arc" not in devices[0]["name"],
        "note": ("index-based selection is UNSAFE on this box; this order is not stable "
                 "between runs. Prefer no filter (ggml-vulkan selects dedicated GPUs by "
                 "device TYPE) over GGML_VK_VISIBLE_DEVICES or -dev/--device, both positional."),
    }


def gb(v):
    """Bytes -> GB, preserving null. `null != 0` is a campaign invariant."""
    return round(v / 1024 ** 3, 3) if isinstance(v, (int, float)) else None


def parse_events(path):
    """Parse one b70tools-jsonl-compact-v1 capture -> (ident, last, disagreements).

    Extracted so `ff_provenance.py` can replay the STORED adapter-probe captures
    through the exact parser a live census uses. An archived probe and a live
    sample must be read identically or the anchor table is not comparable to the
    census rows it will be cited against.

    `last` keeps the final value seen per (adapter, metric) -- these captures are
    multi-tick and we want the settled reading, not the first.
    """
    ident, last, disagreements = {}, {}, []
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = e.get("k")
        if kind == "ai":
            ident[e.get("a")] = e
        elif kind == "ms":
            last[(e.get("a"), e.get("n"))] = e.get("v")
        elif kind == "dr":
            disagreements.append({"rule": e.get("rule"), "adapter": e.get("a"),
                                  "confidence": e.get("cf")})
    return ident, last, disagreements


def adapters_from_events(ident, last, disagreements):
    """Adapter rows + the two placement corroborations, from parsed events."""
    adapters = []
    for a, i in sorted(ident.items(), key=lambda kv: kv[1].get("bdf") or ""):
        adapters.append({
            "bdf": i.get("bdf"),
            "desc": i.get("desc"),
            "local_committed_gb": gb(last.get((a, "gpu.adapter.vram.local.bytes_committed"))),
            "non_local_committed_gb": gb(last.get((a, "gpu.adapter.vram.non_local.bytes_committed"))),
            "vram_temp_c": last.get((a, "vram.temperature_c")),
        })
    arc = [a for a in adapters if "Arc" in (a.get("desc") or "")]
    temps = [a["vram_temp_c"] for a in arc if isinstance(a.get("vram_temp_c"), (int, float))]
    both_warm = len(temps) == 2 and abs(temps[0] - temps[1]) <= 8
    idle_counter = all((a.get("local_committed_gb") or 0) < 0.5 for a in arc) and bool(arc)
    return {"adapters": adapters, "disagreements": disagreements,
            "igcl_caveat": IGCL_CAVEAT, "residency_caveat": RESIDENCY_CAVEAT,
            "local_committed_reads_idle": idle_counter,
            "both_arc_cards_warm": both_warm,
            "temp_spread_c": (round(abs(temps[0] - temps[1]), 1) if len(temps) == 2 else None)}


def adapters_via_b70tools():
    """Per-PCI-BDF residency and temperature. BDF is the stable identity."""
    os.makedirs(SCRATCH, exist_ok=True)
    rc, _out = _run([B70TOOLS, "--run", "--ticks", "4", "--cadence-ms", "500",
                     "--flush-every-tick", "--out", SCRATCH])
    path = os.path.join(SCRATCH, "events.jsonl")
    if not os.path.exists(path):
        return {"ok": False, "reason": "b70tools produced no events.jsonl", "adapters": []}
    out = adapters_from_events(*parse_events(path))
    out["ok"] = rc == 0
    return out


def consumers():
    """Who is holding the GPUs, and where the host wall sits."""
    return {
        "processes": _powershell(PS_PROCS) or [],
        "listening_ports": _powershell(PS_PORTS) or [],
        "host": _powershell(PS_HOST),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="FF pre-run device/consumer census")
    ap.add_argument("--label", default="", help="run label this census belongs to")
    ap.add_argument("--phase", default="pre", choices=["pre", "post"])
    ap.add_argument("--no-ledger", action="store_true", help="print only, do not append")
    args = ap.parse_args()

    row = {
        "ts": datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat(),
        "probe": "FF-CENSUS",
        "phase": args.phase,
        "run_label": args.label or None,
        "vulkan_enumeration": vulkan_enumeration(),
        "adapters": adapters_via_b70tools(),
        "consumers": consumers(),
    }

    enum = row["vulkan_enumeration"]
    label = (" / " + args.label) if args.label else ""
    print("=== FF-CENSUS (%s%s) ===" % (args.phase, label))
    print("  vulkan enumeration THIS run:")
    for d in enum.get("devices", []):
        print("     %d = %s" % (d["index"], d["name"]))
    if enum.get("devices"):
        print("     arc_indices=%s  igpu_at_index_0=%s"
              % (enum["arc_indices_this_run"], enum["igpu_at_index_0"]))
        print("     ^ an index filter is only ever valid for THIS order")
    print("  adapters by PCI BDF (stable identity):")
    for a in row["adapters"].get("adapters") or []:
        print("     %-14s %-34s local=%-8s non_local=%-8s temp=%s"
              % (a["bdf"], (a["desc"] or "")[:32], a["local_committed_gb"],
                 a["non_local_committed_gb"], a["vram_temp_c"]))
    ad = row["adapters"]
    if ad.get("local_committed_reads_idle"):
        print("     NOTE: local_committed reads ~0 on both Arc cards. That is EXPECTED for a")
        print("           settled resident model -- it is an activity-window counter, not")
        print("           steady-state residency. Do NOT read it as 'weights unloaded'.")
    if ad.get("temp_spread_c") is not None:
        print("     arc temp spread = %s C  (both_warm=%s -- corroborates both cards working)"
              % (ad["temp_spread_c"], ad["both_arc_cards_warm"]))
    procs = row["consumers"].get("processes") or []
    if isinstance(procs, dict):
        procs = [procs]
    if procs:
        print("  consumers: " + ", ".join(
            "%s(%s)" % (p.get("ProcessName"), p.get("Id")) for p in procs))
    h = row["consumers"].get("host")
    if h:
        print("  host: commit %s / %s GB, ram_free %s GB"
              % (h.get("commit_used_gb"), h.get("commit_limit_gb"), h.get("ram_free_gb")))

    if not args.no_ledger:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("  -> appended to %s" % LEDGER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
