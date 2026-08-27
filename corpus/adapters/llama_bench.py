"""Normalize ``llama-bench -o json`` output into ``bench-row.v1`` JSONL.

``llama-bench`` is the harness behind most of the published single-stream figures
in this lab, which makes it the format that lets a new measurement sit in the same
table as an old one. It already records its own engine build, placement flags and
per-repetition samples, so unlike the historical text tables this adapter needs a
descriptor only for machine identity and the model's corpus name.

A test object with ``n_gen == 0`` is a prefill (pp) measurement, one with
``n_prompt == 0`` is decode (tg), and one with both set is a combined ``-pg`` run.
Those become distinct ``workload`` values rather than being averaged together.

Usage::

    python -m corpus.adapters.llama_bench RAW.json \
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

ADAPTER_NAME = "llama-bench.json.v1"
ROW_CONTRACT = "bench-row.v1"

_REQUIRED_TEST_FIELDS = (
    "model_filename",
    "n_prompt",
    "n_gen",
    "n_depth",
    "avg_ts",
    "n_gpu_layers",
    "build_commit",
)


def _workload(test: dict[str, Any]) -> str:
    prompt = int(test["n_prompt"])
    gen = int(test["n_gen"])
    if prompt > 0 and gen == 0:
        return "prefill"
    if gen > 0 and prompt == 0:
        return "decode"
    if prompt > 0 and gen > 0:
        return "prefill+decode"
    raise AdapterError("test has neither n_prompt nor n_gen; it measures nothing")


def _validate_test(test: dict[str, Any], index: int) -> None:
    missing = [field for field in _REQUIRED_TEST_FIELDS if field not in test]
    if missing:
        raise AdapterError(f"test[{index}] is missing: {', '.join(missing)}")
    for field in ("n_prompt", "n_gen", "n_depth"):
        if not isinstance(test[field], int):
            raise AdapterError(f"test[{index}].{field} must be an integer")
    if not isinstance(test["avg_ts"], (int, float)) or isinstance(test["avg_ts"], bool):
        raise AdapterError(f"test[{index}].avg_ts must be a number")
    if float(test["avg_ts"]) <= 0:
        raise AdapterError(f"test[{index}].avg_ts is not a positive rate")


def adapt_json(payload: Any, descriptor: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized rows for one ``llama-bench -o json`` artifact."""

    _validate_descriptor(descriptor)
    if not isinstance(payload, list) or not payload:
        raise AdapterError("llama-bench json output must be a non-empty array of tests")
    if len(payload) != descriptor["expected_table_rows"]:
        raise AdapterError(
            f"expected {descriptor['expected_table_rows']} tests, found {len(payload)}"
        )

    device = descriptor["device"]
    model = descriptor["model"]
    source = descriptor["source"]
    expected_filename = model.get("filename")

    normalized: list[dict[str, Any]] = []
    for ordinal, test in enumerate(payload):
        if not isinstance(test, dict):
            raise AdapterError(f"test[{ordinal}] is not an object")
        _validate_test(test, ordinal)
        # Guard against pairing a descriptor with another arm's output: the
        # sweep runs several models through the same harness.
        if expected_filename and Path(str(test["model_filename"])).name != expected_filename:
            raise AdapterError(
                f"test[{ordinal}] ran {Path(str(test['model_filename'])).name!r}, "
                f"descriptor names {expected_filename!r}"
            )
        samples = test.get("samples_ts")
        if samples is not None and not isinstance(samples, list):
            raise AdapterError(f"test[{ordinal}].samples_ts must be an array when present")
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
                "engine": descriptor["engine"].get("name", "llama.cpp"),
                # llama-bench states its own build, so it is preferred over the
                # descriptor's claim about which binary ran.
                "engine_build": test.get("build_commit") or descriptor["engine"].get("build"),
                "model": model["name"],
                "model_quant": model.get("quant"),
                "model_params": model.get("params"),
                "model_size_bytes": test.get("model_size"),
                "model_architecture": model.get("architecture"),
                "n_prompt": int(test["n_prompt"]),
                "n_gen": int(test["n_gen"]),
                "n_depth": int(test["n_depth"]),
                "context_size": None,
                "concurrency": 1,
                "split_mode": test.get("split_mode") or descriptor.get("split_mode"),
                "tensor_split": test.get("tensor_split") or descriptor.get("tensor_split"),
                "flash_attn": bool(test["flash_attn"]) if test.get("flash_attn") is not None else None,
                "kv_type": test.get("type_k") or descriptor.get("kv_type"),
                "n_gpu_layers": test.get("n_gpu_layers"),
                "threads": test.get("n_threads"),
                "workload": _workload(test),
                "metric": "tokens_per_s",
                "value": float(test["avg_ts"]),
                "unit": "tokens/s",
                # avg_ts is the mean across repetitions, and llama-bench hands back
                # every sample, so the spread travels with the number.
                "stat": "mean",
                "stddev": test.get("stddev_ts"),
                "n_runs": len(samples) if samples else None,
                "samples": samples,
                "timestamp": test.get("test_time") or descriptor.get("timestamp"),
                "source": {
                    "adapter": ADAPTER_NAME,
                    "path": source["path"],
                    "ordinal": ordinal,
                    "note": source.get("note"),
                },
                "confidence": "measured",
                "adapter_config": {
                    "n_batch": test.get("n_batch"),
                    "n_ubatch": test.get("n_ubatch"),
                    "load_mode": test.get("load_mode"),
                    "devices": test.get("devices"),
                    "gpu_info": test.get("gpu_info"),
                    "build_number": test.get("build_number"),
                },
            }
        )

    ids = [row["row_id"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise AdapterError("adapter generated duplicate row IDs")
    return normalized


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="llama-bench -o json output")
    parser.add_argument("--descriptor", required=True, type=Path, help="run descriptor JSON")
    parser.add_argument("--output", type=Path, help="write JSONL here instead of stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        descriptor = json.loads(args.descriptor.read_text(encoding="utf-8"))
        payload = json.loads(args.source.read_text(encoding="utf-8"))
        rows = adapt_json(payload, descriptor)
        output = rows_to_jsonl(rows)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (AdapterError, OSError, json.JSONDecodeError) as exc:
        print(f"llama-bench adapter: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
