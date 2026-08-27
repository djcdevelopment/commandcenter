"""Normalize ``qwen38-summary.v1`` configuration aggregates into ``bench-row.v1`` JSONL.

The qwen38 campaign persists one immutable request row per attempt and derives
per-configuration summaries (``qwen38_campaign.py summarize``).  The summaries
are the corpus-worthy layer: the ``bench-row.v1`` metric enum was widened
(jobs_per_hour, output_tokens_per_s, joules_per_successful_job, fairness_cv,
p95 stats, ...) exactly for this shape.  Raw request rows stay campaign-local
and are referenced through ``source.path``.

Like the sibling ``llama_batched_bench`` adapter this one takes a
``bench-adapter-context.v1`` descriptor for machine/model identity and fails
closed on anything it would otherwise have to guess.  ``expected_table_rows``
counts summary configurations here.

Usage::

    python -m corpus.adapters.qwen38_summary SUMMARY.json \
        --descriptor run.json --output rows.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from corpus.adapters.llama_batched_bench import (
    AdapterError,
    _row_id,
    _validate_descriptor,
    rows_to_jsonl,
)

ADAPTER_NAME = "qwen38-summary.json.v1"
ROW_CONTRACT = "bench-row.v1"
SUMMARY_CONTRACT = "qwen38-summary.v1"

_REQUIRED_SUMMARY_FIELDS = (
    "contract_version",
    "candidate",
    "topology",
    "mtp_enabled",
    "test_kind",
    "concurrency",
    "requests",
    "valid_requests",
    "valid_rate",
    "wall_s",
    "jobs_per_hour",
)

# workload for whole-request metrics, by campaign test_kind.
_TEST_KIND_WORKLOAD = {
    "performance": "prefill+decode",
    "assay": "quality",
    "soak": "soak",
}

_GIB = 1024**3


def _validate_summary(summary: dict[str, Any], index: int) -> None:
    missing = [field for field in _REQUIRED_SUMMARY_FIELDS if field not in summary]
    if missing:
        raise AdapterError(f"summary[{index}] is missing: {', '.join(missing)}")
    if summary["contract_version"] != SUMMARY_CONTRACT:
        raise AdapterError(
            f"summary[{index}] has unsupported contract {summary['contract_version']!r}; "
            f"expected {SUMMARY_CONTRACT!r}"
        )
    for field in ("concurrency", "requests", "valid_requests"):
        if not isinstance(summary[field], int):
            raise AdapterError(f"summary[{index}].{field} must be an integer")
    for field in ("valid_rate", "wall_s", "jobs_per_hour"):
        if not isinstance(summary[field], (int, float)) or isinstance(summary[field], bool):
            raise AdapterError(f"summary[{index}].{field} must be a number")


def _metric_specs(summary: dict[str, Any], workload: str) -> list[dict[str, Any]]:
    """(metric, value, unit, stat, workload, confidence) rows; None values are skipped."""

    def spec(metric: str, value: Any, unit: str, stat: str, *, workload_override: str | None = None,
             confidence: str = "measured") -> dict[str, Any]:
        return {
            "metric": metric,
            "value": value,
            "unit": unit,
            "stat": stat,
            "workload": workload_override or workload,
            "confidence": confidence,
        }

    commit_headroom_gb = summary.get("minimum_commit_headroom_gb")
    specs = [
        spec("jobs_per_hour", summary.get("jobs_per_hour"), "jobs/hour", "single", confidence="derived"),
        spec(
            "output_tokens_per_s",
            summary.get("successful_output_tokens_per_s"),
            "tokens/s",
            "single",
            confidence="derived",
        ),
        spec("success_rate", summary.get("valid_rate"), "ratio", "single", confidence="derived"),
        spec("e2el_s", summary.get("latency_p50_s"), "s", "p50"),
        spec("e2el_s", summary.get("latency_p95_s"), "s", "p95"),
        spec("e2el_s", summary.get("latency_p99_s"), "s", "p99"),
        spec("ttft_s", summary.get("ttft_p50_s"), "s", "p50"),
        spec("ttft_s", summary.get("ttft_p95_s"), "s", "p95"),
        spec("ttft_s", summary.get("ttft_p99_s"), "s", "p99"),
        spec("tokens_per_s", summary.get("decode_rate_p50_tokens_per_s"), "tokens/s", "p50", workload_override="decode"),
        spec("tokens_per_s", summary.get("decode_rate_p95_tokens_per_s"), "tokens/s", "p95", workload_override="decode"),
        spec("acceptance_rate", summary.get("mtp_acceptance_rate"), "ratio", "single", confidence="derived"),
        spec("fairness_cv", summary.get("client_goodput_fairness_cv"), "cv", "single", confidence="derived"),
        spec(
            "joules_per_successful_job",
            summary.get("joules_per_successful_job"),
            "J/job",
            "single",
            confidence="derived",
        ),
        spec("power_w", summary.get("average_power_w"), "W", "mean"),
        spec("temperature_c", summary.get("maximum_temperature_c"), "C", "max"),
        spec(
            "commit_headroom_bytes",
            int(commit_headroom_gb * _GIB) if commit_headroom_gb is not None else None,
            "bytes",
            "min",
        ),
    ]
    return [item for item in specs if item["value"] is not None]


def adapt_summaries(summaries: list[dict[str, Any]], descriptor: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized rows for one campaign summary artifact."""

    _validate_descriptor(descriptor)
    if not isinstance(summaries, list) or not summaries:
        raise AdapterError("summary artifact must be a non-empty JSON array")
    if len(summaries) != descriptor["expected_table_rows"]:
        raise AdapterError(
            f"expected {descriptor['expected_table_rows']} summary configurations, found {len(summaries)}"
        )

    device = descriptor["device"]
    engine = descriptor["engine"]
    model = descriptor["model"]
    source = descriptor["source"]
    peak_shared_growth_gb_key = "peak_shared_growth_gb"

    normalized: list[dict[str, Any]] = []
    ordinal = 0
    for index, summary in enumerate(summaries):
        _validate_summary(summary, index)
        test_kind = str(summary["test_kind"])
        workload = _TEST_KIND_WORKLOAD.get(test_kind)
        if workload is None:
            raise AdapterError(f"summary[{index}] has unknown test_kind {test_kind!r}")
        shared_growth_gb = summary.get(peak_shared_growth_gb_key)
        for item in _metric_specs(summary, workload):
            normalized.append(
                {
                    "contract_version": ROW_CONTRACT,
                    "row_id": _row_id(descriptor["run_id"], source["path"], ordinal),
                    "run_id": descriptor["run_id"],
                    "hw_id": descriptor["hw_id"],
                    "hw_label": descriptor.get("hw_label"),
                    "host": descriptor.get("host"),
                    "platform": descriptor["platform"],
                    "era": descriptor.get("era"),
                    "device_kind": device["kind"],
                    "device_count": device["count"],
                    "engine": engine["name"],
                    "engine_build": engine.get("build"),
                    "model": str(summary["candidate"]),
                    "model_quant": summary.get("model_quant") or model.get("quant"),
                    "model_params": model.get("params"),
                    "model_architecture": model.get("architecture"),
                    "n_prompt": summary.get("requested_prompt_tokens"),
                    "n_gen": None,
                    "n_depth": None,
                    "context_size": None,
                    "concurrency": summary["concurrency"],
                    "split_mode": descriptor.get("split_mode"),
                    "tensor_split": descriptor.get("tensor_split"),
                    "flash_attn": descriptor.get("flash_attn"),
                    "kv_type": descriptor.get("kv_type"),
                    "topology": summary["topology"],
                    "placement": summary.get("placement"),
                    "mtp_enabled": bool(summary["mtp_enabled"]),
                    "artifact_revision": summary.get("artifact_revision"),
                    "shared_memory_growth_bytes": (
                        int(shared_growth_gb * _GIB) if shared_growth_gb is not None else None
                    ),
                    "workload": item["workload"],
                    "metric": item["metric"],
                    "value": item["value"],
                    "unit": item["unit"],
                    "stat": item["stat"],
                    "stddev": None,
                    "n_runs": summary["valid_requests"],
                    "samples": None,
                    "timestamp": descriptor.get("timestamp"),
                    "source": {
                        "adapter": ADAPTER_NAME,
                        "path": source["path"],
                        "ordinal": ordinal,
                        "note": source.get("note"),
                    },
                    "confidence": item["confidence"],
                    "adapter_config": {
                        "test_kind": test_kind,
                        "slot_depth": summary.get("slot_depth"),
                        "parallel_slots": summary.get("parallel_slots"),
                        "requests": summary["requests"],
                        "valid_requests": summary["valid_requests"],
                        "wall_s": summary["wall_s"],
                        "run_ids": summary.get("run_ids"),
                    },
                }
            )
            ordinal += 1

    if not normalized:
        raise AdapterError("no metric rows could be generated from the summary artifact")
    ids = [row["row_id"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise AdapterError("adapter generated duplicate row IDs")
    return normalized


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="qwen38-summary.v1 JSON artifact")
    parser.add_argument("--descriptor", required=True, type=Path, help="run descriptor JSON")
    parser.add_argument("--output", type=Path, help="write JSONL here instead of stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        descriptor = json.loads(args.descriptor.read_text(encoding="utf-8"))
        summaries = json.loads(args.source.read_text(encoding="utf-8"))
        rows = adapt_summaries(summaries, descriptor)
        output = rows_to_jsonl(rows)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (AdapterError, OSError, json.JSONDecodeError) as exc:
        print(f"qwen38-summary adapter: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
