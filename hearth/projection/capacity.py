"""Capacity projection over the HEARTH ledger (Job Shop Scheduler slice JS2).

Pure read, mcp-free. Buckets ledger events by (task_class, node, model, tool) and
computes duration/token distributions the CP-SAT job-shop scheduler consumes as its
processing-time estimates. Contract: hearth/contracts/capacity.v1.schema.json.

`ok` on a ledger event is not a uniform signal: for an inference call it means
"succeeded", but a timer-driven tool may report it as "took action". This
projection therefore reads ok ONLY where the event names its failure, and counts
the incoherent ok:false/error:null shape as indeterminate instead (see
_is_indeterminate). Emitters split the two meanings via the event's `outcome`
field; ok answers "did it do its job", outcome answers "which branch".

A parallel effort is adding optional `task_class` and `model` fields to ledger events;
most historical events predate that and will not carry them. Missing values fall back
to `None` in the bucket key rather than being dropped — every event is still counted
somewhere. Malformed lines are skipped and counted, never raised.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_LEDGER = Path("hearth/var/ledger/events.ndjson")

BucketKey = tuple[Any, Any, Any, Any, str]


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile via stdlib sorted-index (no numpy dependency)."""
    if not sorted_values:
        raise ValueError("cannot take a percentile of an empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = int(round(fraction * (len(sorted_values) - 1)))
    index = max(0, min(index, len(sorted_values) - 1))
    return sorted_values[index]


def _empty_accumulator() -> dict:
    return {
        "calls": 0,
        "ok": 0,
        "determinate": 0,  # calls whose ok flag is trustworthy (see _is_indeterminate)
        "indeterminate": 0,
        "durations_ms": [],  # only from ok:true events
        "tokens_out_per_s": [],
        "last_seen": None,
    }


def _is_indeterminate(event: dict) -> bool:
    """True when an event reports failure but names no failure.

    `ok:false` with `error:null` is structurally incoherent: the emitter claimed
    something went wrong while declining to say what. Historically this shape had
    exactly one source -- bankedfire_drain.tick, which keyed `ok` on "did this
    timer dispatch work" rather than "did this timer do its job", so 592 healthy
    idle no-ops projected as failures and dragged the bucket to ok_rate 0.0084.
    Across the other ~7,900 ledger events every ok:false carried a real error
    (SSH TimeoutExpired, ImportError, ...), so this predicate isolates that one
    defect without suppressing a single genuine fault.

    Ledger events are immutable, so the 592 bad records cannot be repaired in
    place; reinterpreting them at projection time is the CQRS/ES-correct repair
    (the read model is derived, the event log is not rewritten). Emission is
    fixed going forward -- ledger.new_event now refuses to persist this shape --
    so this predicate is a historical-corpus rule, not a licence for new
    emitters to keep omitting errors.
    """
    return not event.get("ok") and not event.get("error")


def _bucket_key(event: dict) -> BucketKey:
    caller = event.get("caller") or {}
    task_class = event.get("task_class")
    node = caller.get("node")
    model = event.get("model")
    runner_class = caller.get("runner_class")
    tool = event.get("tool") or "unknown"
    return (task_class, node, model, runner_class, tool)


def build_capacity_document(ledger_path: Path = DEFAULT_LEDGER) -> dict:
    """Read the ledger at ledger_path and return a capacity.v1 document (dict).

    Rules:
    - ok:false events count toward calls/ok_rate but are excluded from duration
      percentiles and token-rate samples.
    - EXCEPT ok:false events that name no error (see _is_indeterminate): those
      report a failure they cannot describe, so they count toward `calls` and
      `indeterminate` but are excluded from the ok_rate denominator entirely.
      A bucket with no determinate calls gets ok_rate null, never 0.0.
    - Missing task_class/model on an event yields a null in that bucket's key
      rather than dropping the event.
    - Malformed (non-JSON) lines are skipped; they do not raise and are not
      counted into any bucket.
    - tokens_out_per_s is only sampled when both tokens_out and duration_ms are
      present and duration_ms > 0.
    """
    accumulators: dict[BucketKey, dict] = {}
    newest_ts: str | None = None
    line_count = 0

    if ledger_path.exists():
        for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            line_count += 1
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            key = _bucket_key(event)
            acc = accumulators.setdefault(key, _empty_accumulator())
            acc["calls"] += 1

            ts = event.get("ts")
            if isinstance(ts, str):
                if newest_ts is None or ts > newest_ts:
                    newest_ts = ts
                if acc["last_seen"] is None or ts > acc["last_seen"]:
                    acc["last_seen"] = ts

            if _is_indeterminate(event):
                acc["indeterminate"] += 1
                continue

            acc["determinate"] += 1
            ok = bool(event.get("ok"))
            if ok:
                acc["ok"] += 1
                duration_ms = event.get("duration_ms")
                if isinstance(duration_ms, (int, float)):
                    acc["durations_ms"].append(float(duration_ms))
                    cost = event.get("cost") or {}
                    tokens_out = cost.get("tokens_out")
                    if isinstance(tokens_out, (int, float)) and duration_ms > 0:
                        seconds = duration_ms / 1000.0
                        acc["tokens_out_per_s"].append(tokens_out / seconds)

    buckets = []
    for (task_class, node, model, runner_class, tool), acc in accumulators.items():
        durations = sorted(acc["durations_ms"])
        if durations:
            duration_summary = {
                "p50": _percentile(durations, 0.50),
                "p90": _percentile(durations, 0.90),
                "mean": round(sum(durations) / len(durations), 4),
                "max": max(durations),
            }
        else:
            duration_summary = {"p50": None, "p90": None, "mean": None, "max": None}

        token_rates = sorted(acc["tokens_out_per_s"])
        tokens_out_per_s_p50 = _percentile(token_rates, 0.50) if token_rates else None

        buckets.append({
            "task_class": task_class,
            "node": node,
            "runner_class": runner_class,
            "model": model,
            "tool": tool,
            "calls": acc["calls"],
            # Rate over DETERMINATE calls only. A bucket with no determinate
            # evidence gets null rather than 0.0 -- "we never learned" must not
            # render as "it always failed" (same null-means-no-samples
            # convention duration_ms/tokens_out_per_s_p50 already use).
            "ok_rate": (round(acc["ok"] / acc["determinate"], 4)
                        if acc["determinate"] else None),
            "indeterminate": acc["indeterminate"],
            "duration_ms": duration_summary,
            "tokens_out_per_s_p50": tokens_out_per_s_p50,
            "last_seen": acc["last_seen"],
        })

    buckets.sort(key=lambda b: (
        b["task_class"] or "", b["node"] or "", b["model"] or "",
        b["runner_class"] or "", b["tool"],
    ))

    # Corpus provenance (CQRS-ES-STANDARDIZATION.md step 4): this ledger is a single
    # file, not a tree, so there is no per-file (relpath, line_count) set to hash the
    # way tools/workflow/corpus.py's Corpus.enumerate does for event-file trees. Kept
    # in the same spirit (content-shaped, not mtime/size-based) but simplified to the
    # single quantity that matters for one file: its non-blank line count. Scheme:
    # sha256("events.ndjson:<line_count>") — deterministic, changes iff the ledger's
    # event count changes, stable across checkouts/clones of identical content.
    corpus_digest = f"sha256:{hashlib.sha256(f'events.ndjson:{line_count}'.encode('utf-8')).hexdigest()}"

    return {
        "contract_version": "capacity.v1",
        "evidence_watermark": newest_ts,
        # Monotonic-ish primary count for the corpus regression guard (CQRS/ES plan step 2):
        # a normal re-projection over more ledger evidence should never yield fewer buckets,
        # so bucket_count doubles as guard_write's count field alongside evidence_watermark.
        "bucket_count": len(buckets),
        "buckets": buckets,
        "corpus_digest": corpus_digest,
        "corpus_event_count": line_count,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.projection.capacity",
        description="Project HEARTH ledger capacity buckets (pure read).",
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args(argv[1:])

    print(json.dumps(build_capacity_document(args.ledger), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
