import copy
import json
from pathlib import Path

import pytest

from corpus.adapters.qwen38_summary import AdapterError, adapt_summaries, rows_to_jsonl


CORPUS = Path(__file__).resolve().parent
FIXTURE = CORPUS / "fixtures" / "qwen38-summary" / "baseline-20260827.json"
DESCRIPTOR = CORPUS / "backfills" / "qwen38-campaign-baseline-20260827.json"


def load_inputs():
    return (
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        json.loads(DESCRIPTOR.read_text(encoding="utf-8")),
    )


def test_real_baseline_backfill_emits_one_row_per_metric():
    summaries, descriptor = load_inputs()

    rows = adapt_summaries(summaries, descriptor)

    assert len(rows) == 304
    assert len({row["row_id"] for row in rows}) == 304
    assert [row["source"]["ordinal"] for row in rows] == list(range(304))
    assert all(row["contract_version"] == "bench-row.v1" for row in rows)
    assert all(row["topology"] == "baseline-production" for row in rows)
    assert all(row["mtp_enabled"] is False for row in rows)


def test_depth_cliff_decode_p50_survives_normalization():
    summaries, descriptor = load_inputs()

    rows = adapt_summaries(summaries, descriptor)
    shallow = next(
        row
        for row in rows
        if row["concurrency"] == 1
        and row["n_prompt"] == 512
        and row["metric"] == "tokens_per_s"
        and row["stat"] == "p50"
    )

    assert shallow["value"] == 94.416027
    assert shallow["workload"] == "decode"
    assert shallow["device_count"] == 2
    assert shallow["confidence"] == "measured"


def test_energy_and_commit_rows_carry_units_and_derivation():
    summaries, descriptor = load_inputs()

    rows = adapt_summaries(summaries, descriptor)
    deep = [row for row in rows if row["concurrency"] == 1 and row["n_prompt"] == 32768]
    joules = next(row for row in deep if row["metric"] == "joules_per_successful_job")
    commit = next(row for row in deep if row["metric"] == "commit_headroom_bytes")

    assert joules["value"] == 5681.085888
    assert joules["unit"] == "J/job"
    assert joules["confidence"] == "derived"
    assert commit["value"] == 71854802862
    assert commit["stat"] == "min"


def test_ids_and_jsonl_are_deterministic():
    summaries, descriptor = load_inputs()

    first = adapt_summaries(summaries, descriptor)
    second = adapt_summaries(summaries, descriptor)

    assert rows_to_jsonl(first) == rows_to_jsonl(second)
    assert rows_to_jsonl(first).endswith("\n")


def test_every_generated_row_validates_against_the_bench_row_schema():
    jsonschema = pytest.importorskip("jsonschema")
    summaries, descriptor = load_inputs()
    schema = json.loads((CORPUS / "schema" / "bench-row.v1.json").read_text(encoding="utf-8"))

    rows = adapt_summaries(summaries, descriptor)
    validator = jsonschema.Draft202012Validator(schema)
    errors = [error.message for row in rows for error in validator.iter_errors(row)]

    assert errors == []


@pytest.mark.parametrize("field", ["run_id", "hw_id", "platform", "expected_table_rows", "source"])
def test_missing_identity_field_fails_closed(field):
    summaries, descriptor = load_inputs()
    broken = copy.deepcopy(descriptor)
    del broken[field]

    with pytest.raises(AdapterError, match="descriptor"):
        adapt_summaries(summaries, broken)


def test_configuration_count_mismatch_fails_closed():
    summaries, descriptor = load_inputs()

    with pytest.raises(AdapterError, match="expected 19 summary configurations, found 18"):
        adapt_summaries(summaries[:-1], descriptor)


def test_wrong_summary_contract_fails_closed():
    summaries, descriptor = load_inputs()
    broken = copy.deepcopy(summaries)
    broken[0]["contract_version"] = "qwen38-summary.v2"

    with pytest.raises(AdapterError, match="unsupported contract"):
        adapt_summaries(broken, descriptor)


def test_unknown_test_kind_fails_instead_of_guessing_workload():
    summaries, descriptor = load_inputs()
    broken = copy.deepcopy(summaries)
    broken[0]["test_kind"] = "stress"

    with pytest.raises(AdapterError, match="unknown test_kind"):
        adapt_summaries(broken, descriptor)


def test_missing_summary_field_fails_closed():
    summaries, descriptor = load_inputs()
    broken = copy.deepcopy(summaries)
    del broken[3]["jobs_per_hour"]

    with pytest.raises(AdapterError, match=r"summary\[3\] is missing: jobs_per_hour"):
        adapt_summaries(broken, descriptor)
