import copy
import json
from pathlib import Path

import pytest

from corpus.adapters.llama_batched_bench import AdapterError, adapt_text, rows_to_jsonl


CORPUS = Path(__file__).resolve().parent
FIXTURE = CORPUS / "fixtures" / "llama-batched-bench" / "expY-mistral24b-mmv8-rep1.txt"
DESCRIPTOR = CORPUS / "backfills" / "vulkancliff-expY-mistral24b-mmv8-rep1.json"


def load_inputs():
    return FIXTURE.read_text(encoding="utf-8"), json.loads(DESCRIPTOR.read_text(encoding="utf-8"))


def test_real_exp_y_backfill_emits_prefill_and_decode_rows():
    text, descriptor = load_inputs()

    rows = adapt_text(text, descriptor)

    assert len(rows) == 22
    assert len({row["row_id"] for row in rows}) == 22
    assert [row["source"]["ordinal"] for row in rows] == list(range(22))
    assert {row["workload"] for row in rows} == {"prefill", "decode"}
    assert all(row["contract_version"] == "bench-row.v1" for row in rows)
    assert all(row["n_depth"] is None for row in rows)


def test_ninth_thread_decode_is_preserved_without_inventing_depth():
    text, descriptor = load_inputs()

    rows = adapt_text(text, descriptor)
    ninth_decode = next(
        row for row in rows if row["workload"] == "decode" and row["concurrency"] == 9
    )

    assert ninth_decode["value"] == 19.71
    assert ninth_decode["n_kv"] == 5760
    assert ninth_decode["n_depth"] is None
    assert ninth_decode["context_size"] == 65536
    assert ninth_decode["flash_attn"] is True
    assert ninth_decode["device_count"] == 1


def test_ids_and_jsonl_are_deterministic():
    text, descriptor = load_inputs()

    first = adapt_text(text, descriptor)
    second = adapt_text(text, descriptor)

    assert rows_to_jsonl(first) == rows_to_jsonl(second)
    assert rows_to_jsonl(first).endswith("\n")


@pytest.mark.parametrize(
    "field", ["run_id", "hw_id", "platform", "expected_table_rows", "source"]
)
def test_missing_identity_field_fails_closed(field):
    text, descriptor = load_inputs()
    broken = copy.deepcopy(descriptor)
    del broken[field]

    with pytest.raises(AdapterError, match="descriptor"):
        adapt_text(text, broken)


def test_malformed_partial_table_fails_instead_of_emitting_partial_rows():
    text, descriptor = load_inputs()
    malformed = text.replace(
        "|   512 |    128 |    9 |   5760 |    5.089 |   905.56 |   58.453 |    19.71 |   63.541 |    90.65 |",
        "|   512 |    128 |    9 | broken |",
    )

    with pytest.raises(AdapterError, match="expected 10 cells"):
        adapt_text(malformed, descriptor)


def test_unsupported_table_header_fails_clearly():
    text, descriptor = load_inputs()

    with pytest.raises(AdapterError, match="table header"):
        adapt_text(text.replace("S_TG t/s", "S_TG req/s"), descriptor)


def test_truncated_table_fails_against_descriptor_row_count():
    text, descriptor = load_inputs()
    truncated = "\n".join(text.splitlines()[:-1])

    with pytest.raises(AdapterError, match="expected 11 table rows, parsed 10"):
        adapt_text(truncated, descriptor)
