"""Economics summary over the HEARTH ledger.

Pure read, mcp-free. The seed of the knowledge-per-local-hour metric and
the naadam frontier-vs-local scoreboard: per runner_class and per tool,
{calls, ok_rate, total_duration_ms, tokens_in, tokens_out}.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_LEDGER = Path("hearth/var/ledger/events.ndjson")

COST_CLASS_MAP = {
    "omen-ollama": "sunk",
    "am4-oxen": "sunk",
    "gcp-gemini": "trial",
    "gcp-gemini-pro": "trial",
}


def _empty_bucket() -> dict:
    return {
        "calls": 0,
        "ok": 0,
        "ok_rate": 0.0,
        "total_duration_ms": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }


def _add(bucket: dict, event: dict) -> None:
    bucket["calls"] += 1
    if event.get("ok"):
        bucket["ok"] += 1
    bucket["total_duration_ms"] += int(event.get("duration_ms") or 0)
    cost = event.get("cost") or {}
    bucket["tokens_in"] += int(cost.get("tokens_in") or 0)
    bucket["tokens_out"] += int(cost.get("tokens_out") or 0)


def _finalize(bucket: dict) -> dict:
    bucket["ok_rate"] = round(bucket["ok"] / bucket["calls"], 4) if bucket["calls"] else 0.0
    return bucket


def summarize(ledger_path: Path = DEFAULT_LEDGER) -> dict:
    """Summarize the ledger. Returns
    {per_runner_class, per_tool, frontier_vs_local, events, parse_errors}."""
    per_runner_class: dict[str, dict] = {}
    per_tool: dict[str, dict] = {}
    events = 0
    parse_errors = 0

    if ledger_path.exists():
        for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            events += 1
            runner_class = (event.get("caller") or {}).get("runner_class") or "unknown"
            tool = event.get("tool") or "unknown"
            _add(per_runner_class.setdefault(runner_class, _empty_bucket()), event)
            _add(per_tool.setdefault(tool, _empty_bucket()), event)

    for bucket in per_runner_class.values():
        _finalize(bucket)
    for bucket in per_tool.values():
        _finalize(bucket)

    frontier_vs_local = {
        "frontier": per_runner_class.get("frontier") or _empty_bucket(),
        "local": per_runner_class.get("local") or _empty_bucket(),
    }

    return {
        "per_runner_class": dict(sorted(per_runner_class.items())),
        "per_tool": dict(sorted(per_tool.items())),
        "frontier_vs_local": frontier_vs_local,
        "events": events,
        "parse_errors": parse_errors,
    }


def build_offload_document(ledger_path: Path = DEFAULT_LEDGER) -> dict:
    """Build the offload.v1 document from the ledger (S2, executor-aware).

    Scope: inference-class local_generate events only. Buckets by executor
    backend (S1 provenance field) with a model-name fallback for pre-S1 rows;
    cost classes sunk/trial/unknown make the offload ratio and the
    $-saved-vs-metered-frontier estimate computable.
    """
    totals = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
    per_class = {
        "sunk": {"calls": 0, "tokens_in": 0, "tokens_out": 0},
        "trial": {"calls": 0, "tokens_in": 0, "tokens_out": 0},
        "unknown": {"calls": 0, "tokens_in": 0, "tokens_out": 0},
    }
    buckets_data = {}
    newest_ts = None
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

            if event.get("task_class") != "inference" or event.get("tool") != "local_generate":
                continue

            backend = event.get("backend")
            model = event.get("model") or ""

            if backend:
                cost_class = COST_CLASS_MAP.get(backend, "unknown")
                bucket_key = backend
            else:
                lower_model = model.lower()
                if "gemini" in lower_model:
                    cost_class = "trial"
                elif "qwen" in lower_model or "oxen" in lower_model or "gguf" in lower_model:
                    cost_class = "sunk"
                else:
                    cost_class = "unknown"
                bucket_key = f"model:{model}"

            acc = buckets_data.setdefault(bucket_key, {
                "backend": bucket_key,
                "cost_class": cost_class,
                "models": set(),
                "calls": 0,
                "ok": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "last_seen": None,
            })

            if model:
                acc["models"].add(model)
            acc["calls"] += 1
            if event.get("ok"):
                acc["ok"] += 1

            cost = event.get("cost") or {}
            tokens_in = int(cost.get("tokens_in") or 0)
            tokens_out = int(cost.get("tokens_out") or 0)

            acc["tokens_in"] += tokens_in
            acc["tokens_out"] += tokens_out

            totals["calls"] += 1
            totals["tokens_in"] += tokens_in
            totals["tokens_out"] += tokens_out

            per_class[cost_class]["calls"] += 1
            per_class[cost_class]["tokens_in"] += tokens_in
            per_class[cost_class]["tokens_out"] += tokens_out

            ts = event.get("ts")
            if isinstance(ts, str):
                if newest_ts is None or ts > newest_ts:
                    newest_ts = ts
                if acc["last_seen"] is None or ts > acc["last_seen"]:
                    acc["last_seen"] = ts

    buckets = []
    for bk, acc in buckets_data.items():
        buckets.append({
            "backend": acc["backend"],
            "cost_class": acc["cost_class"],
            "models": sorted(list(acc["models"])),
            "calls": acc["calls"],
            "ok_rate": round(acc["ok"] / acc["calls"], 4) if acc["calls"] else 0.0,
            "tokens_in": acc["tokens_in"],
            "tokens_out": acc["tokens_out"],
            "last_seen": acc["last_seen"],
        })

    buckets.sort(key=lambda b: (b["backend"] or "", b["cost_class"]))

    offloaded_out = per_class["sunk"]["tokens_out"] + per_class["trial"]["tokens_out"]
    offload_ratio = round(offloaded_out / totals["tokens_out"], 4) if totals["tokens_out"] else None

    offloaded_in = per_class["sunk"]["tokens_in"] + per_class["trial"]["tokens_in"]
    usd = (offloaded_in * 3.0 + offloaded_out * 15.0) / 1_000_000.0

    # Same content-shaped digest scheme capacity.json uses: changes iff the
    # ledger's non-blank line count changes, stable across checkouts.
    corpus_digest = f"sha256:{hashlib.sha256(f'events.ndjson:{line_count}'.encode('utf-8')).hexdigest()}"

    return {
        "contract_version": "offload.v1",
        "evidence_watermark": newest_ts,
        "bucket_count": len(buckets),
        "totals": totals,
        "per_class": per_class,
        "offload_ratio": offload_ratio,
        "est_usd_saved": {
            "reference": "claude-sonnet",
            "input_per_mtok": 3.0,
            "output_per_mtok": 15.0,
            "usd": round(usd, 6)
        },
        "buckets": buckets,
        "corpus_digest": corpus_digest,
        "corpus_event_count": line_count,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hearth.projection.economics",
        description="Summarize HEARTH ledger economics (pure read).",
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args(argv[1:])

    print(json.dumps(summarize(args.ledger), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
