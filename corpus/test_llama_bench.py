import copy
import json
from pathlib import Path

import pytest

from corpus.adapters.llama_bench import AdapterError, adapt_json, rows_to_jsonl


CORPUS = Path(__file__).resolve().parent
FIXTURE = CORPUS / "fixtures" / "llama-bench" / "qwen3-30b-a3b-control.json"
DESCRIPTOR = CORPUS / "backfills" / "llama-bench-control-qwen3-30b-20260827.json"


def load_inputs():
    return (
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        json.loads(DESCRIPTOR.read_text(encoding="utf-8")),
    )


def test_prefill_and_decode_become_separate_workloads():
    payload, descriptor = load_inputs()

    rows = adapt_json(payload, descriptor)

    assert len(rows) == 4
    assert [row["workload"] for row in rows] == ["prefill", "decode", "prefill", "decode"]
    assert len({row["row_id"] for row in rows}) == 4
    assert [row["source"]["ordinal"] for row in rows] == [0, 1, 2, 3]
    assert all(row["contract_version"] == "bench-row.v1" for row in rows)
    assert all(row["concurrency"] == 1 for row in rows)


def test_per_repetition_samples_travel_with_the_mean():
    payload, descriptor = load_inputs()

    decode = [row for row in adapt_json(payload, descriptor) if row["workload"] == "decode"][0]

    assert decode["value"] == 95.17
    assert decode["stat"] == "mean"
    assert decode["stddev"] == 0.71
    assert decode["n_runs"] == 3
    assert decode["samples"] == [94.6, 95.2, 95.7]
    # llama-bench states its own build; it outranks the descriptor's claim.
    assert decode["engine_build"] == "f413d64b"


def test_depth_is_carried_rather_than_inferred():
    payload, descriptor = load_inputs()

    rows = adapt_json(payload, descriptor)
    deep = [row for row in rows if row["n_depth"] == 8192]

    assert len(deep) == 2
    assert {row["workload"] for row in deep} == {"prefill", "decode"}
    assert all(row["n_gpu_layers"] == 99 for row in rows)


def test_every_generated_row_validates_against_the_bench_row_schema():
    jsonschema = pytest.importorskip("jsonschema")
    payload, descriptor = load_inputs()
    schema = json.loads((CORPUS / "schema" / "bench-row.v1.json").read_text(encoding="utf-8"))

    rows = adapt_json(payload, descriptor)
    validator = jsonschema.Draft202012Validator(schema)
    errors = [error.message for row in rows for error in validator.iter_errors(row)]

    assert errors == []


def test_ids_and_jsonl_are_deterministic():
    payload, descriptor = load_inputs()

    first = adapt_json(payload, descriptor)
    second = adapt_json(payload, descriptor)

    assert rows_to_jsonl(first) == rows_to_jsonl(second)
    assert rows_to_jsonl(first).endswith("\n")


@pytest.mark.parametrize("field", ["run_id", "hw_id", "platform", "expected_table_rows", "source"])
def test_missing_identity_field_fails_closed(field):
    payload, descriptor = load_inputs()
    broken = copy.deepcopy(descriptor)
    del broken[field]

    with pytest.raises(AdapterError, match="descriptor"):
        adapt_json(payload, broken)


def test_a_descriptor_paired_with_another_arms_output_fails_closed():
    # The sweep runs several models through one harness, so a mispaired
    # descriptor is a live risk, not a hypothetical one.
    payload, descriptor = load_inputs()
    broken = copy.deepcopy(payload)
    broken[0]["model_filename"] = "Qwen3.8-27B-Q4_K_M.gguf"

    with pytest.raises(AdapterError, match="descriptor names"):
        adapt_json(broken, descriptor)


def test_truncated_output_fails_against_the_descriptor_count():
    payload, descriptor = load_inputs()

    with pytest.raises(AdapterError, match="expected 4 tests, found 3"):
        adapt_json(payload[:-1], descriptor)


def test_a_test_that_measures_nothing_fails_closed():
    payload, descriptor = load_inputs()
    broken = copy.deepcopy(payload)
    broken[0]["n_prompt"] = 0
    broken[0]["n_gen"] = 0

    with pytest.raises(AdapterError, match="measures nothing"):
        adapt_json(broken, descriptor)


@pytest.mark.parametrize("value", [0, -1.0])
def test_a_non_positive_rate_is_not_a_measurement(value):
    payload, descriptor = load_inputs()
    broken = copy.deepcopy(payload)
    broken[1]["avg_ts"] = value

    with pytest.raises(AdapterError, match="not a positive rate"):
        adapt_json(broken, descriptor)


def test_missing_required_test_field_fails_closed():
    payload, descriptor = load_inputs()
    broken = copy.deepcopy(payload)
    del broken[2]["avg_ts"]

    with pytest.raises(AdapterError, match=r"test\[2\] is missing: avg_ts"):
        adapt_json(broken, descriptor)
