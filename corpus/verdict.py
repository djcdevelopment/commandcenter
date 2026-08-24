#!/usr/bin/env python3
"""Host-aware safety verdict over a b70tools telemetry stream.

WHY THIS EXISTS
---------------
`b70tools verdict` refuses to proceed when host RAM exceeds a compiled-in 26.00 GB.
That constant was calibrated for the AM4 box's 32 GB DDR4 -- 26/32 is 81% of RAM,
and the thing it was really detecting was GPU work spilling into host memory.

On a 128 GB host the same constant fires at 20% utilisation and reports
`status: broken` on a completely healthy idle machine. The policy is stale, not
the instrument: b70tools' event stream is fine, it is the threshold sitting on top
that assumed a machine that no longer exists.

So this re-derives the judgement from the same events.jsonl, with thresholds
computed from the host actually present. It does not replace b70tools -- it
replaces one constant, and adds the check that caught a real problem on the Z890
board: per-adapter telemetry symmetry.

Exit codes match b70tools' convention: 0 healthy, 2 refuse to proceed.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path

CONTRACT_VERSION = "corpus-verdict.v1"

# Fraction of installed host RAM above which we refuse. 26/32 GB on the original
# rig == 0.8125; keeping the same policy, expressed as a ratio instead of a
# constant, is what makes it portable to a different machine.
HOST_RAM_REFUSE_FRACTION = 0.8125
HOST_RAM_WARN_FRACTION = 0.70

# Committed non-local (host-resident) VRAM above this is a genuine spill, which is
# what the original host-RAM proxy was really trying to catch. Measured directly.
NON_LOCAL_SPILL_WARN_BYTES = 2 * 1024 ** 3
NON_LOCAL_SPILL_REFUSE_BYTES = 8 * 1024 ** 3

# Two identical cards should be observed at comparable rates. Anything below this
# ratio means one card is materially less observable than its twin.
ADAPTER_SAMPLE_SYMMETRY_WARN = 0.5

KEY_METRICS = (
    "gpu.energy_j_counter",
    "gpu.engine.utilization_pct",
    "gpu.adapter.vram.local.bytes_committed",
)


def load_events(path: Path) -> list[dict]:
    events = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _samples(events: list[dict], name: str, adapter: str | None = None) -> list[float]:
    out = []
    for event in events:
        if event.get("k") != "ms" or event.get("n") != name:
            continue
        if adapter is not None and event.get("a") != adapter:
            continue
        value = event.get("v")
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def _adapter_names(events: list[dict]) -> dict[str, str]:
    names = {}
    for event in events:
        if event.get("k") == "ai":
            adapter = event.get("a")
            desc = event.get("desc") or event.get("d") or event.get("description")
            if adapter:
                names[adapter] = str(desc) if desc else adapter
    return names


def evaluate(events_path: Path, host_total_bytes: int | None = None) -> dict:
    events = load_events(events_path)
    if not events:
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "unknown",
            "ok": False,
            "exit_code": 2,
            "events_path": str(events_path),
            "issues": ["no parseable events"],
            "checks": {},
        }

    issues: list[str] = []
    warnings: list[str] = []

    # ---- host memory -----------------------------------------------------
    total_samples = _samples(events, "host.memory.total_bytes")
    used_samples = _samples(events, "host.memory.used_bytes")
    total = host_total_bytes or (int(total_samples[0]) if total_samples else 0)
    peak_used = max(used_samples) if used_samples else 0

    host_check = {
        "total_bytes": total,
        "peak_used_bytes": peak_used,
        "peak_used_fraction": (peak_used / total) if total else None,
        "refuse_above_bytes": int(total * HOST_RAM_REFUSE_FRACTION) if total else None,
        "samples": len(used_samples),
    }
    if not total:
        warnings.append("host.memory.total_bytes absent; cannot judge host pressure")
    elif peak_used > total * HOST_RAM_REFUSE_FRACTION:
        issues.append(
            f"[host-ram] peak {peak_used / 1024**3:.2f} GB exceeds "
            f"{HOST_RAM_REFUSE_FRACTION:.0%} of {total / 1024**3:.1f} GB installed"
        )
    elif peak_used > total * HOST_RAM_WARN_FRACTION:
        warnings.append(
            f"[host-ram] peak {peak_used / 1024**3:.2f} GB is above "
            f"{HOST_RAM_WARN_FRACTION:.0%} of installed"
        )

    # ---- per-adapter observability --------------------------------------
    names = _adapter_names(events)
    sample_counts: collections.Counter = collections.Counter()
    metric_coverage: dict[str, set] = collections.defaultdict(set)
    for event in events:
        if event.get("k") != "ms":
            continue
        adapter = event.get("a")
        if not adapter:
            continue
        sample_counts[adapter] += 1
        metric_coverage[adapter].add(event.get("n"))

    # Cards that carry local VRAM telemetry are the discrete compute adapters.
    compute_adapters = [
        adapter for adapter in sample_counts
        if "gpu.adapter.vram.local.bytes_committed" in metric_coverage[adapter]
        or "vram.local.budget_bytes" in metric_coverage[adapter]
    ]

    adapters_check = []
    for adapter in sorted(compute_adapters):
        local = _samples(events, "gpu.adapter.vram.local.bytes_committed", adapter)
        non_local = _samples(events, "gpu.adapter.vram.non_local.bytes_committed", adapter)
        peak_non_local = max(non_local) if non_local else 0
        missing = [m for m in KEY_METRICS if m not in metric_coverage[adapter]]
        adapters_check.append({
            "adapter": adapter,
            "description": names.get(adapter),
            "samples": sample_counts[adapter],
            "distinct_metrics": len(metric_coverage[adapter]),
            "missing_key_metrics": missing,
            "peak_local_vram_bytes": max(local) if local else 0,
            "peak_non_local_vram_bytes": peak_non_local,
        })
        if missing:
            warnings.append(
                f"[telemetry] {adapter} missing {', '.join(missing)}"
            )
        if peak_non_local > NON_LOCAL_SPILL_REFUSE_BYTES:
            issues.append(
                f"[vram-spill] {adapter} committed "
                f"{peak_non_local / 1024**3:.2f} GB of host-resident VRAM"
            )
        elif peak_non_local > NON_LOCAL_SPILL_WARN_BYTES:
            warnings.append(
                f"[vram-spill] {adapter} committed "
                f"{peak_non_local / 1024**3:.2f} GB non-local"
            )

    # ---- symmetry between identical cards --------------------------------
    # Two cards of the same model should be observed at comparable rates. On the
    # Z890 board the bus-4 B70 reported 206 samples against the bus-9 card's 824,
    # because IGCL power telemetry kept dropping out on one slot. Nothing in
    # b70tools' own verdict notices that, and it silently degrades every power and
    # thermal number attributed to the quieter card.
    symmetry_check = None
    by_description: dict[str, list[dict]] = collections.defaultdict(list)
    for entry in adapters_check:
        by_description[entry["description"] or "unknown"].append(entry)
    for description, group in by_description.items():
        if len(group) < 2:
            continue
        counts = [entry["samples"] for entry in group]
        ratio = min(counts) / max(counts) if max(counts) else 0.0
        symmetry_check = {
            "description": description,
            "sample_counts": {e["adapter"]: e["samples"] for e in group},
            "ratio": round(ratio, 3),
        }
        if ratio < ADAPTER_SAMPLE_SYMMETRY_WARN:
            warnings.append(
                f"[telemetry-symmetry] {len(group)} x '{description}' observed at very "
                f"different rates ({', '.join(str(c) for c in sorted(counts))} samples, "
                f"ratio {ratio:.2f}). Power and thermal figures for the quieter card are "
                f"weaker evidence than the busier one -- do not average them together."
            )

    # ---- disagreements ---------------------------------------------------
    disagreements = collections.Counter()
    for event in events:
        if event.get("k") == "dr":
            disagreements[event.get("rule") or event.get("n") or "unknown"] += 1

    status = "healthy" if not issues else "broken"
    if status == "healthy" and warnings:
        status = "degraded"

    return {
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "ok": not issues,
        "exit_code": 0 if not issues else 2,
        "events_path": str(events_path),
        "event_count": len(events),
        "checks": {
            "host_memory": host_check,
            "adapters": adapters_check,
            "symmetry": symmetry_check,
            "disagreements": dict(disagreements),
        },
        "issues": issues,
        "warnings": warnings,
        "policy": {
            "host_ram_refuse_fraction": HOST_RAM_REFUSE_FRACTION,
            "note": "thresholds derived from installed RAM, not a constant calibrated "
                    "for a 32 GB host",
        },
    }


def _human(result: dict) -> str:
    host = result["checks"].get("host_memory", {})
    lines = [
        f"status     {result['status']}  (ok={result['ok']}, exit={result['exit_code']})",
        f"events     {result.get('event_count', 0)}",
    ]
    if host.get("total_bytes"):
        lines.append(
            f"host ram   peak {host['peak_used_bytes'] / 1024**3:.2f} GB of "
            f"{host['total_bytes'] / 1024**3:.1f} GB "
            f"({host['peak_used_fraction']:.1%}); refuse above "
            f"{host['refuse_above_bytes'] / 1024**3:.1f} GB"
        )
    lines.append("adapters")
    for entry in result["checks"].get("adapters", []):
        lines.append(
            f"  {entry['adapter']}  {entry['samples']:>5} samples"
            f"  {entry['distinct_metrics']:>3} metrics"
            f"  local {entry['peak_local_vram_bytes'] / 1024**3:.2f} GB"
            f"  non-local {entry['peak_non_local_vram_bytes'] / 1024**3:.2f} GB"
            f"   {entry['description'] or ''}"
        )
    dis = result["checks"].get("disagreements") or {}
    if dis:
        lines.append("disagreements")
        for rule, count in sorted(dis.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {count:>4}  {rule}")
    for issue in result["issues"]:
        lines.append(f"ISSUE      {issue}")
    for warning in result["warnings"]:
        lines.append(f"warn       {warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Host-aware safety verdict over b70tools telemetry."
    )
    parser.add_argument("events", type=Path,
                        help="path to events.jsonl (or a run dir containing one)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--host-total-bytes", type=int, default=None,
                        help="override installed RAM (default: read from the stream)")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures")
    args = parser.parse_args(argv)

    path = args.events
    if path.is_dir():
        candidate = path / "events.jsonl"
        path = candidate if candidate.exists() else path

    result = evaluate(path, args.host_total_bytes)
    print(json.dumps(result, indent=2) if args.json else _human(result))

    if args.strict and result["warnings"]:
        return 2
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
