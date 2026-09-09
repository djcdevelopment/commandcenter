"""Reduce the execution ledger to what the lab was actually asked to do.

Read-only, no GPU, no network. Deterministic: the output embeds no wall clock, so
an unchanged ledger reduces to identical bytes and a diff means the corpus moved.

**Read this as a capability audit, not a demand curve.** The traffic recorded here
is shaped by what the lab can currently accept, not by what anyone wants from it --
so "the workload is shallow and single-tenant" is a statement about the system's
reach, not about demand. The durable findings are the ones about the lab's own
plumbing: which routing lanes have ever fired, how dispatches are chosen, and how
concentrated the callers are. Those stay true whatever the future mix turns out to be.

Channel blind spots, stated because every observation channel here has one:

* This ledger records **door-mediated** calls. Production llama-server serves HTTP
  directly on :8082 under ArcServe -- that traffic never appears here, so this file
  cannot answer "how busy were the cards".
* The span crosses fleet changes (am4-oxen died 2026-08-20), so backend shares are
  historical, not a current configuration.
* Only ``local_generate`` carries token counts; every other tool contributes events
  but no depth.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

DEFAULT_LEDGER = Path("hearth/var/ledger/events.ndjson")
DEFAULT_OFFLOAD = Path("knowledge/offload.json")

#: ADR-0039 depth thresholds: the 27B delivers 2.63x the incumbent's jobs/hour at 8K
#: and 5.49x at 32K, having lost at the 512-token operating point.
DEPTH_THRESHOLDS = (2048, 8192, 32768)

#: Rungs whose compute is already paid for. Everything else bills per call.
SUNK_BACKENDS = frozenset({"omen-arc", "omen-arc-oss", "omen-swap", "omen-ollama",
                           "am4-moe", "am4-oxen", "fx99-ollama", "comfy-xpu"})


def read_events(path: Path) -> Iterator[dict]:
    """Yield parsed rows, counting rather than raising on damaged lines."""
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                yield {"_unparseable": True}


def _ranked(counter: Counter, limit: Optional[int] = None) -> list[dict]:
    """Deterministic ordering: count descending, then key, so reruns are byte-stable."""
    items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    if limit is not None:
        items = items[:limit]
    return [{"value": key, "count": count} for key, count in items]


def _quantile(ordered: list[int], fraction: float) -> int:
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _epoch(timestamp: str) -> Optional[float]:
    import datetime as dt

    try:
        return dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def concurrency_profile(events: list[dict]) -> dict:
    """Sweep-line over [start, start+duration] to find real overlap.

    Our benchmarks assume sixteen concurrent clients. This reports what the door
    actually saw, which is the number that says whether that assumption is a
    measurement or an aspiration.
    """
    spans = []
    for row in events:
        start = _epoch(row.get("ts") or "")
        duration = row.get("duration_ms")
        if start is None or not isinstance(duration, (int, float)):
            continue
        spans.append((start, start + max(0.0, float(duration)) / 1000.0))
    if not spans:
        return {"samples": 0}
    edges: list[tuple[float, int]] = []
    for start, end in spans:
        edges.append((start, 1))
        edges.append((end, -1))
    edges.sort(key=lambda item: (item[0], -item[1]))
    live = 0
    peak = 0
    histogram: Counter = Counter()
    previous = edges[0][0]
    for moment, delta in edges:
        if moment > previous and live > 0:
            histogram[live] += 1
        live += delta
        peak = max(peak, live)
        previous = moment
    busy_seconds = sum(end - start for start, end in spans)
    span_seconds = max(end for _, end in spans) - min(start for start, _ in spans)
    return {
        "samples": len(spans),
        "peak_in_flight": peak,
        "intervals_by_in_flight": _ranked(histogram),
        "busy_hours": round(busy_seconds / 3600.0, 3),
        "span_hours": round(span_seconds / 3600.0, 3),
        "duty_cycle": round(busy_seconds / span_seconds, 6) if span_seconds > 0 else None,
    }


def reduce_ledger(rows: Iterable[dict], offload: Optional[dict] = None) -> dict:
    tools: Counter = Counter()
    profiles: Counter = Counter()
    unparseable = 0
    total = 0
    duration_ms = 0.0
    earliest: Optional[str] = None
    latest: Optional[str] = None
    inference: list[dict] = []

    for row in rows:
        if row.get("_unparseable"):
            unparseable += 1
            continue
        total += 1
        tools[row.get("tool")] += 1
        profiles[row.get("profile")] += 1
        if isinstance(row.get("duration_ms"), (int, float)):
            duration_ms += float(row["duration_ms"])
        timestamp = row.get("ts")
        if isinstance(timestamp, str):
            earliest = timestamp if earliest is None or timestamp < earliest else earliest
            latest = timestamp if latest is None or timestamp > latest else latest
        cost = row.get("cost") or {}
        if cost.get("tokens_in") is not None:
            inference.append(row)

    depths = sorted(int(row["cost"]["tokens_in"] or 0) for row in inference)
    routed = Counter(row.get("routed_by") for row in inference)
    pinned = sum(count for value, count in routed.items()
                 if isinstance(value, str) and value.startswith("pinned:"))
    family = sum(count for value, count in routed.items()
                 if isinstance(value, str) and value.startswith("family:"))
    opportunistic = sum(count for value, count in routed.items()
                        if isinstance(value, str) and value.startswith("tag:"))
    backends = Counter(row.get("backend") for row in inference)
    sunk_calls = sum(count for value, count in backends.items() if value in SUNK_BACKENDS)

    report: dict[str, Any] = {
        "contract_version": "workload.v1",
        "reading": "capability audit, not a demand curve -- see module docstring",
        "corpus": {
            "events": total,
            "unparseable": unparseable,
            "earliest": earliest,
            "latest": latest,
            "wall_hours_recorded": round(duration_ms / 3_600_000, 3),
            "tools": _ranked(tools, 15),
            "profiles": _ranked(profiles, 10),
        },
        "inference": {
            "calls": len(inference),
            "share_of_events": round(len(inference) / total, 6) if total else None,
            "tokens_in": sum(depths),
            "tokens_out": sum(int((row.get("cost") or {}).get("tokens_out") or 0)
                              for row in inference),
            "wall_hours": round(sum(float(row.get("duration_ms") or 0)
                                    for row in inference) / 3_600_000, 3),
            "depth": {
                "min": depths[0] if depths else None,
                "median": _quantile(depths, 0.5),
                "p90": _quantile(depths, 0.90),
                "p95": _quantile(depths, 0.95),
                "max": depths[-1] if depths else None,
                "at_or_above": {
                    str(threshold): {
                        "calls": sum(1 for value in depths if value >= threshold),
                        "share": round(sum(1 for value in depths if value >= threshold) / len(depths), 6)
                        if depths else None,
                    }
                    for threshold in DEPTH_THRESHOLDS
                },
            },
            "occupancy": _ranked(Counter(row.get("occupancy") for row in inference)),
            "callers": _ranked(Counter((row.get("caller") or {}).get("id") for row in inference), 10),
            "backends": _ranked(backends, 12),
            "sunk_share": round(sunk_calls / len(inference), 6) if inference else None,
        },
        "routing": {
            "routed_by": _ranked(routed, 15),
            "pinned": pinned,
            "family": family,
            "opportunistic_tag": opportunistic,
            "pinned_share": round(pinned / len(inference), 6) if inference else None,
            "note": "a caller pin suppresses authored family evidence in _family_route, so "
                    "depth_override (ADR-0039) cannot fire on a pinned call",
        },
        "concurrency": concurrency_profile(inference),
    }

    if offload is not None:
        recorded = offload.get("totals", {})
        report["reconciliation"] = {
            "offload_projection_calls": recorded.get("calls"),
            "offload_projection_tokens_in": recorded.get("tokens_in"),
            "ledger_inference_calls": len(inference),
            "ledger_tokens_in": sum(depths),
            "tokens_in_match": recorded.get("tokens_in") == sum(depths),
            "call_gap": (recorded.get("calls") - len(inference))
            if isinstance(recorded.get("calls"), int) else None,
            "note": "tokens reconcile exactly; the projection counts more CALLS than the "
                    "ledger has token-bearing rows, so some dispatches reach the projection "
                    "without a recorded cost -- the gap is a finding, not a rounding error",
        }
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--offload", type=Path, default=DEFAULT_OFFLOAD)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    offload = None
    if args.offload and args.offload.is_file():
        offload = json.loads(args.offload.read_text(encoding="utf-8-sig"))
    report = reduce_ledger(read_events(args.ledger), offload)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")

    corpus, inf, routing = report["corpus"], report["inference"], report["routing"]
    print(f"events {corpus['events']} ({corpus['unparseable']} unparseable) "
          f"{corpus['earliest']} -> {corpus['latest']}")
    print(f"inference {inf['calls']} calls = {inf['share_of_events']:.4%} of events, "
          f"{inf['wall_hours']} h of {corpus['wall_hours_recorded']} h recorded")
    print(f"depth median {inf['depth']['median']} p95 {inf['depth']['p95']} max {inf['depth']['max']}; "
          f">=8K {inf['depth']['at_or_above']['8192']['calls']} "
          f">=32K {inf['depth']['at_or_above']['32768']['calls']}")
    print(f"routing pinned {routing['pinned']} / family {routing['family']} / "
          f"tag {routing['opportunistic_tag']}  (pinned share {routing['pinned_share']:.1%})")
    print(f"peak in-flight {report['concurrency'].get('peak_in_flight')}, "
          f"duty cycle {report['concurrency'].get('duty_cycle')}")
    if args.out:
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
