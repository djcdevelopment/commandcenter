"""Normalize ``llama-batched-bench`` text tables into ``bench-row.v1`` JSONL.

Historical benchmark files predate :mod:`corpus.runlog`, so the raw table does
not contain enough information to identify the machine, model, or run.  This
adapter therefore takes a small JSON descriptor alongside the raw text.  It
fails closed when identity fields or table cells are missing; a partial import
is more dangerous than no import in this corpus.

Usage::

    python -m corpus.adapters.llama_batched_bench RAW.txt \
        --descriptor run.json --output rows.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


ADAPTER_NAME = "llama-batched-bench.text.v1"
ROW_CONTRACT = "bench-row.v1"
DESCRIPTOR_CONTRACT = "bench-adapter-context.v1"

_HW_ID = re.compile(r"^hw-[0-9a-f]{12}$")
_CONFIG_LINE = re.compile(r"^llama_batched_bench:\s*(.+)$")
_CONFIG_ITEM = re.compile(r"([A-Za-z_]+)\s*=\s*([^,]+)")
_EXPECTED_COLUMNS = (
    "PP",
    "TG",
    "B",
    "N_KV",
    "T_PP s",
    "S_PP t/s",
    "T_TG s",
    "S_TG t/s",
    "T s",
    "S t/s",
)
_REQUIRED_DESCRIPTOR_FIELDS = (
    "contract_version",
    "run_id",
    "hw_id",
    "platform",
    "expected_table_rows",
    "device",
    "engine",
    "model",
    "source",
)


class AdapterError(ValueError):
    """Raised when an input cannot be normalized without guessing."""


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_config(line: str) -> dict[str, int | bool]:
    match = _CONFIG_LINE.match(line.strip())
    if not match:
        raise AdapterError("missing llama_batched_bench configuration header")

    parsed: dict[str, int | bool] = {}
    for key, raw_value in _CONFIG_ITEM.findall(match.group(1)):
        value = raw_value.strip()
        try:
            number = int(value)
        except ValueError as exc:
            raise AdapterError(f"non-integer configuration value for {key}: {value!r}") from exc
        parsed[key] = bool(number) if key in {"flash_attn", "is_pp_shared", "is_tg_separate"} else number

    required = {"n_kv_max", "n_batch", "n_ubatch", "flash_attn", "n_gpu_layers", "n_threads"}
    missing = sorted(required - parsed.keys())
    if missing:
        raise AdapterError(f"configuration header is missing: {', '.join(missing)}")
    return parsed


def _validate_descriptor(descriptor: dict[str, Any]) -> None:
    missing = [field for field in _REQUIRED_DESCRIPTOR_FIELDS if field not in descriptor]
    if missing:
        raise AdapterError(f"descriptor is missing: {', '.join(missing)}")
    if descriptor["contract_version"] != DESCRIPTOR_CONTRACT:
        raise AdapterError(
            f"unsupported descriptor contract: {descriptor['contract_version']!r}; "
            f"expected {DESCRIPTOR_CONTRACT!r}"
        )
    if not isinstance(descriptor["run_id"], str) or not descriptor["run_id"].strip():
        raise AdapterError("descriptor run_id must be a non-empty string")
    if not isinstance(descriptor["platform"], str) or not descriptor["platform"].strip():
        raise AdapterError("descriptor platform must be a non-empty string")
    if not isinstance(descriptor["hw_id"], str) or not _HW_ID.fullmatch(descriptor["hw_id"]):
        raise AdapterError("descriptor hw_id must match hw-<12 lowercase hex digits>")
    if (
        not isinstance(descriptor["expected_table_rows"], int)
        or descriptor["expected_table_rows"] < 1
    ):
        raise AdapterError("descriptor expected_table_rows must be a positive integer")

    nested_required = {
        "device": ("kind", "count"),
        "engine": ("name",),
        "model": ("name",),
        "source": ("path",),
    }
    for group, fields in nested_required.items():
        value = descriptor[group]
        if not isinstance(value, dict):
            raise AdapterError(f"descriptor {group} must be an object")
        absent = [field for field in fields if field not in value]
        if absent:
            raise AdapterError(f"descriptor {group} is missing: {', '.join(absent)}")
    if not isinstance(descriptor["device"]["count"], int) or descriptor["device"]["count"] < 1:
        raise AdapterError("descriptor device.count must be a positive integer")
    if not isinstance(descriptor["source"]["path"], str) or not descriptor["source"]["path"].strip():
        raise AdapterError("descriptor source.path must be a non-empty string")


def _parse_measurement_rows(lines: list[str]) -> list[dict[str, int | float]]:
    header_index: int | None = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith("|") and _table_cells(line) == list(_EXPECTED_COLUMNS):
            header_index = index
            break
    if header_index is None:
        raise AdapterError("benchmark table header was not found or has unsupported columns")

    rows: list[dict[str, int | float]] = []
    saw_separator = False
    for line_number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        stripped = line.strip()
        if not stripped:
            if rows:
                break
            continue
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = _table_cells(stripped)
        if all(cell and set(cell) <= {"-", ":"} for cell in cells):
            saw_separator = True
            continue
        if len(cells) != len(_EXPECTED_COLUMNS):
            raise AdapterError(
                f"line {line_number}: expected {len(_EXPECTED_COLUMNS)} cells, found {len(cells)}"
            )
        try:
            rows.append(
                {
                    "n_prompt": int(cells[0]),
                    "n_gen": int(cells[1]),
                    "concurrency": int(cells[2]),
                    "n_kv": int(cells[3]),
                    "t_pp_s": float(cells[4]),
                    "s_pp_t_s": float(cells[5]),
                    "t_tg_s": float(cells[6]),
                    "s_tg_t_s": float(cells[7]),
                    "t_total_s": float(cells[8]),
                    "s_total_t_s": float(cells[9]),
                }
            )
        except ValueError as exc:
            raise AdapterError(f"line {line_number}: non-numeric benchmark cell") from exc

    if not saw_separator:
        raise AdapterError("benchmark table separator was not found")
    if not rows:
        raise AdapterError("benchmark table contains no measurement rows")
    return rows


def _row_id(run_id: str, source_path: str, ordinal: int) -> str:
    normalized_path = source_path.replace("\\", "/")
    identity = f"{run_id}\0{normalized_path}\0{ordinal}".encode("utf-8")
    return f"row-{hashlib.sha256(identity).hexdigest()[:16]}"


def _base_row(
    descriptor: dict[str, Any],
    config: dict[str, int | bool],
    measurement: dict[str, int | float],
    ordinal: int,
) -> dict[str, Any]:
    device = descriptor["device"]
    engine = descriptor["engine"]
    model = descriptor["model"]
    source = descriptor["source"]
    return {
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
        "model": model["name"],
        "model_quant": model.get("quant"),
        "model_params": model.get("params"),
        "model_size_bytes": model.get("size_bytes"),
        "model_architecture": model.get("architecture"),
        "n_prompt": measurement["n_prompt"],
        "n_gen": measurement["n_gen"],
        "n_depth": None,
        "n_kv": measurement["n_kv"],
        "context_size": config["n_kv_max"],
        "concurrency": measurement["concurrency"],
        "split_mode": descriptor.get("split_mode"),
        "tensor_split": descriptor.get("tensor_split"),
        "flash_attn": config["flash_attn"],
        "kv_type": descriptor.get("kv_type"),
        "n_gpu_layers": config["n_gpu_layers"],
        "threads": config["n_threads"],
        "metric": "tokens_per_s",
        "unit": "tokens/s",
        "stat": "single",
        "stddev": None,
        "n_runs": 1,
        "samples": None,
        "timestamp": descriptor.get("timestamp"),
        "source": {
            "adapter": ADAPTER_NAME,
            "path": source["path"],
            "ordinal": ordinal,
            "note": source.get("note"),
        },
        "confidence": "measured",
        "adapter_config": {
            "n_batch": config["n_batch"],
            "n_ubatch": config["n_ubatch"],
            "is_pp_shared": config.get("is_pp_shared"),
            "is_tg_separate": config.get("is_tg_separate"),
        },
    }


def adapt_text(text: str, descriptor: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized rows for one raw benchmark text artifact."""

    _validate_descriptor(descriptor)
    lines = text.splitlines()
    config_line = next((line for line in lines if _CONFIG_LINE.match(line.strip())), None)
    if config_line is None:
        raise AdapterError("missing llama_batched_bench configuration header")
    config = _parse_config(config_line)
    measurements = _parse_measurement_rows(lines)
    if len(measurements) != descriptor["expected_table_rows"]:
        raise AdapterError(
            f"expected {descriptor['expected_table_rows']} table rows, parsed {len(measurements)}"
        )

    normalized: list[dict[str, Any]] = []
    ordinal = 0
    for measurement in measurements:
        for workload, value_key, duration_key in (
            ("prefill", "s_pp_t_s", "t_pp_s"),
            ("decode", "s_tg_t_s", "t_tg_s"),
        ):
            row = _base_row(descriptor, config, measurement, ordinal)
            row["workload"] = workload
            row["value"] = measurement[value_key]
            row["duration_s"] = measurement[duration_key]
            normalized.append(row)
            ordinal += 1

    ids = [row["row_id"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise AdapterError("adapter generated duplicate row IDs")
    return normalized


def rows_to_jsonl(rows: Iterable[dict[str, Any]]) -> str:
    """Serialize rows deterministically, including a final newline."""

    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="llama-batched-bench text output")
    parser.add_argument("--descriptor", required=True, type=Path, help="historical run descriptor JSON")
    parser.add_argument("--output", type=Path, help="write JSONL here instead of stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        descriptor = json.loads(args.descriptor.read_text(encoding="utf-8"))
        rows = adapt_text(args.source.read_text(encoding="utf-8"), descriptor)
        output = rows_to_jsonl(rows)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (AdapterError, OSError, json.JSONDecodeError) as exc:
        print(f"llama-batched-bench adapter: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
