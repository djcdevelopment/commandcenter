from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from hearth.projection.economics import (
    DEFAULT_TOP_N,
    UNSTAMPED_KEY,
    build_offload_document,
    summarize,
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "offload.v1.schema.json"

# The marker a privacy proof needs: a string that exists ONLY in fields the
# offload projection must never read (args_preview, error). If it can be found
# anywhere in the serialized document, something started copying event fields
# through instead of counting them.
PROMPT_MARKER = "SECRET-PROMPT-MARKER-7f3a"


def make_offload_event(backend: str, ts: str, *, model: str = "qwen", ok: bool = True) -> dict:
    """An event build_offload_document actually counts: it filters to
    task_class=inference + tool=local_generate and buckets by backend."""
    return {
        "schema": "hearth-event.v1",
        "ts": ts,
        "task_class": "inference",
        "tool": "local_generate",
        "backend": backend,
        "model": model,
        "ok": ok,
        "cost": {"tokens_in": 10, "tokens_out": 20},
    }


def make_attributed_event(backend: str, ts: str, *, caller_id: str | None = None,
                          task_id: str | None = None, ok: bool = True,
                          tokens_in: int = 10, tokens_out: int = 20,
                          model: str = "qwen", args_preview: str | None = None,
                          error: str | None = None) -> dict:
    """An offload-counted event carrying the two attribution fields (C-06).

    `caller_id`/`task_id` left None means the KEY IS ABSENT from the event --
    the pre-stamping shape the ledger is full of, which must land under
    "(unstamped)" rather than disappearing.
    """
    event = make_offload_event(backend, ts, model=model, ok=ok)
    event["cost"] = {"tokens_in": tokens_in, "tokens_out": tokens_out}
    if caller_id is not None:
        event["caller"] = {"id": caller_id, "runner_class": "frontier", "node": "omen"}
    if task_id is not None:
        event["task_id"] = task_id
    if args_preview is not None:
        event["args_preview"] = args_preview
    if error is not None:
        event["error"] = error
    return event


def make_event(runner_class: str, tool: str, ok: bool, duration_ms: int, tokens_in: int, tokens_out: int) -> dict:
    return {
        "schema": "hearth-event.v1",
        "event_id": f"he_{runner_class}_{tool}_{duration_ms}",
        "ts": "2026-07-03T12:00:00+00:00",
        "caller": {"id": f"{runner_class}-1", "runner_class": runner_class, "node": "omen"},
        "tool": tool,
        "ok": ok,
        "duration_ms": duration_ms,
        "cost": {"tokens_in": tokens_in, "tokens_out": tokens_out, "watt_s": None},
    }


class EconomicsTests(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / "events.ndjson"

    def write_ledger(self, events: list[dict], extra_lines: list[str] | None = None) -> None:
        with self.ledger.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
            for line in extra_lines or []:
                handle.write(line + "\n")

    def test_summarize_buckets_by_runner_class_and_tool(self) -> None:
        self.write_ledger(
            [
                make_event("frontier", "run_tests", True, 1000, 500, 50),
                make_event("frontier", "run_tests", False, 2000, 700, 10),
                make_event("local", "run_tests", True, 4000, 900, 200),
                make_event("local", "record_event", True, 50, 100, 20),
            ]
        )

        summary = summarize(self.ledger)

        frontier = summary["per_runner_class"]["frontier"]
        self.assertEqual(frontier["calls"], 2)
        self.assertEqual(frontier["ok_rate"], 0.5)
        self.assertEqual(frontier["total_duration_ms"], 3000)
        self.assertEqual(frontier["tokens_in"], 1200)
        self.assertEqual(frontier["tokens_out"], 60)

        run_tests = summary["per_tool"]["run_tests"]
        self.assertEqual(run_tests["calls"], 3)
        self.assertEqual(run_tests["total_duration_ms"], 7000)

        self.assertEqual(summary["frontier_vs_local"]["local"]["calls"], 2)
        self.assertEqual(summary["frontier_vs_local"]["frontier"]["calls"], 2)
        self.assertEqual(summary["events"], 4)

    def test_missing_cost_fields_and_bad_lines_tolerated(self) -> None:
        event = make_event("local", "fs_read", True, 10, 0, 0)
        del event["cost"]
        self.write_ledger([event], extra_lines=["{ broken"])

        summary = summarize(self.ledger)

        self.assertEqual(summary["events"], 1)
        self.assertEqual(summary["parse_errors"], 1)
        self.assertEqual(summary["per_runner_class"]["local"]["tokens_in"], 0)

    def test_empty_or_missing_ledger(self) -> None:
        summary = summarize(self.ledger)  # never written
        self.assertEqual(summary["events"], 0)
        self.assertEqual(summary["frontier_vs_local"]["frontier"]["calls"], 0)

    def test_build_offload_document(self) -> None:
        from hearth.projection.economics import build_offload_document

        events = [
            {"schema": "hearth-event.v1", "ts": "2026-07-04T00:00:00Z", "task_class": "inference", "tool": "local_generate", "backend": "omen-ollama", "model": "qwen", "ok": True, "cost": {"tokens_in": 1000, "tokens_out": 2000}},
            {"schema": "hearth-event.v1", "ts": "2026-07-04T01:00:00Z", "task_class": "inference", "tool": "local_generate", "backend": None, "model": "gemini-1.5", "ok": True, "cost": {"tokens_in": 500, "tokens_out": 1000}},
            {"schema": "hearth-event.v1", "ts": "2026-07-04T02:00:00Z", "task_class": "inference", "tool": "local_generate", "backend": None, "model": "qwen2", "ok": False, "cost": {"tokens_in": None, "tokens_out": None}},
            {"schema": "hearth-event.v1", "ts": "2026-07-04T03:00:00Z", "task_class": "inference", "tool": "local_generate", "backend": "unknown-backend", "model": "gpt-4", "ok": True, "cost": {"tokens_in": 100, "tokens_out": 200}},
            # non-inference event to skip
            {"schema": "hearth-event.v1", "ts": "2026-07-04T04:00:00Z", "task_class": "other", "tool": "local_generate", "backend": "omen-ollama", "model": "qwen", "ok": True, "cost": {"tokens_in": 999, "tokens_out": 999}},
        ]
        self.write_ledger(events)

        doc = build_offload_document(self.ledger)
        self.assertEqual(doc["totals"]["calls"], 4)
        self.assertEqual(doc["totals"]["tokens_in"], 1600)
        self.assertEqual(doc["totals"]["tokens_out"], 3200)

        self.assertEqual(doc["per_class"]["sunk"]["tokens_out"], 2000)
        self.assertEqual(doc["per_class"]["trial"]["tokens_out"], 1000)
        self.assertEqual(doc["per_class"]["unknown"]["tokens_out"], 200)

        self.assertEqual(doc["offload_ratio"], round(3000 / 3200, 4))

        expected_usd = (1500 * 3.0 + 3000 * 15.0) / 1000000.0
        self.assertEqual(doc["est_usd_saved"]["usd"], round(expected_usd, 6))

        self.assertEqual(len(doc["buckets"]), 4)

    def test_real_usd_spent_prices_trial_calls_and_counts_unpriceable(self) -> None:
        """real_usd_spent is a floor over TRIAL calls only: a listed model with
        token counts is priced from GEMINI_PRICING_USD_PER_MTOK; an unlisted
        model or null tokens counts as unpriced rather than pricing as $0; sunk
        buckets report real_usd 0.0 (known free), never None (unknown)."""
        from hearth.projection.economics import build_offload_document
        from hearth.projection.gemini_pricing import GEMINI_PRICING_USD_PER_MTOK

        rates = GEMINI_PRICING_USD_PER_MTOK["gemini-3.5-flash"]
        events = [
            # trial, priced: listed model with real token counts
            {"schema": "hearth-event.v1", "ts": "2026-07-23T00:00:00Z", "task_class": "inference", "tool": "local_generate", "backend": "gcp-gemini", "model": "gemini-3.5-flash", "ok": True, "cost": {"tokens_in": 1_000_000, "tokens_out": 100_000}},
            # trial, unpriced: legacy model-fallback bucket, model not in the table
            {"schema": "hearth-event.v1", "ts": "2026-07-23T01:00:00Z", "task_class": "inference", "tool": "local_generate", "backend": None, "model": "gemini-1.5", "ok": True, "cost": {"tokens_in": 500, "tokens_out": 1000}},
            # trial, unpriced: listed model but null token counts (legacy zero-token row)
            {"schema": "hearth-event.v1", "ts": "2026-07-23T02:00:00Z", "task_class": "inference", "tool": "local_generate", "backend": "gcp-gemini", "model": "gemini-3.5-flash", "ok": True, "cost": {"tokens_in": None, "tokens_out": None}},
            # sunk: never enters real accounting, bucket reports 0.0
            {"schema": "hearth-event.v1", "ts": "2026-07-23T03:00:00Z", "task_class": "inference", "tool": "local_generate", "backend": "omen-ollama", "model": "qwen", "ok": True, "cost": {"tokens_in": 1000, "tokens_out": 2000}},
        ]
        self.write_ledger(events)

        doc = build_offload_document(self.ledger)

        expected = round((1_000_000 * rates["input"] + 100_000 * rates["output"]) / 1_000_000.0, 6)
        real = doc["real_usd_spent"]
        self.assertEqual(real["usd"], expected)
        self.assertEqual(real["priced_calls"], 1)
        self.assertEqual(real["unpriced_calls"], 2)
        self.assertIn("pricing_source", real)

        by_backend = {b["backend"]: b for b in doc["buckets"]}
        self.assertEqual(by_backend["gcp-gemini"]["real_usd"], expected)
        self.assertIsNone(by_backend["model:gemini-1.5"]["real_usd"])
        self.assertEqual(by_backend["omen-ollama"]["real_usd"], 0.0)


class TimestampOrderingTests(TestCase):
    """evidence_watermark / last_seen must be ordered by INSTANT, not by string.

    The twin of capacity.py's defect, in offload.v1. hearth-event.v1 permits three
    spellings of `ts` (`...:00Z`, `...:00+00:00`, `...:00.123Z`), and 'Z' (0x5A)
    sorts above both '+' (0x2B) and '.' (0x2E). Which mixtures actually go wrong is
    worth stating, because the two hazards are not equally severe:

    - Suffix alone (`Z` vs `+00:00`) is a TIE, not a wrong answer: a lexical
      compare only reaches the suffix when every character before it is equal, and
      an equal date+time+fraction IS the same instant. Lexical order breaks that
      tie arbitrarily; either answer names the right moment.
    - The fractional-second boundary is where ordering genuinely FLIPS:
      '...12:00:00Z' > '...12:00:00.123Z' lexically, so the strictly EARLIER
      instant wins. This holds across suffixes too
      ('...12:00:00Z' > '...12:00:00.123+00:00'), which is the combined case.

    So every test below that would fail on the old lexical code involves fractional
    seconds (or an unparseable ts); the suffix-only case is pinned as an invariant
    rather than as a bug repro. Each fixture keeps the date+time prefix IDENTICAL
    across the pair on purpose -- two different dates would pass on the buggy code,
    because the date decides before the suffix is ever reached.

    Both fields are load-bearing: corpus_guard.guard_write regression-guards on
    evidence_watermark, and dashboard.py renders it as "Offload watermark".

    The live ledger is uniform today (8,540 events, all fractional-Z, verified
    2026-07-18), so this is a LATENT defect: it fires the first time any emitter
    omits fractional seconds.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / "events.ndjson"

    def write_ledger(self, events: list[dict]) -> None:
        with self.ledger.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    def test_bare_z_must_not_beat_a_later_fractional_offset_ts(self) -> None:
        """Both hazards at once, and the sharpest case: a bare `Z` second beats a
        `.123+00:00` ts on BOTH the '.' and the '+' comparison, yet names the
        earlier instant. Fails on the old lexical code."""
        self.write_ledger([
            make_offload_event("omen-ollama", "2026-07-03T12:00:00Z"),
            make_offload_event("omen-ollama", "2026-07-03T12:00:00.123+00:00"),
        ])
        document = build_offload_document(self.ledger)
        self.assertEqual(document["evidence_watermark"], "2026-07-03T12:00:00.123+00:00")
        self.assertEqual(document["buckets"][0]["last_seen"], "2026-07-03T12:00:00.123+00:00")

    def test_fractional_seconds_beat_a_bare_earlier_second(self) -> None:
        """Hazard 2 alone: '...12:00:00Z' > '...12:00:00.123Z' lexically, so the
        EARLIER instant wins. Fractional seconds are a strictly later instant
        within the same second and must be picked. Fails on the old code."""
        self.write_ledger([
            make_offload_event("omen-ollama", "2026-07-03T12:00:00Z"),
            make_offload_event("omen-ollama", "2026-07-03T12:00:00.123Z"),
        ])
        document = build_offload_document(self.ledger)
        self.assertEqual(document["evidence_watermark"], "2026-07-03T12:00:00.123Z")
        self.assertEqual(document["buckets"][0]["last_seen"], "2026-07-03T12:00:00.123Z")

    def test_per_bucket_last_seen_orders_independently_of_the_document(self) -> None:
        """last_seen is tracked per bucket, so each bucket must resolve its own
        mixed-format max -- not inherit the document watermark. Both buckets are
        built so the fractional ts is the one that loses lexically. Fails on the
        old code."""
        self.write_ledger([
            make_offload_event("omen-ollama", "2026-07-03T12:00:00Z"),
            make_offload_event("omen-ollama", "2026-07-03T12:00:00.500Z"),
            make_offload_event("gcp-gemini", "2026-07-06T09:00:00Z", model="gemini"),
            make_offload_event("gcp-gemini", "2026-07-06T09:00:00.250+00:00", model="gemini"),
        ])
        document = build_offload_document(self.ledger)
        last_seen = {b["backend"]: b["last_seen"] for b in document["buckets"]}
        self.assertEqual(last_seen["omen-ollama"], "2026-07-03T12:00:00.500Z")
        self.assertEqual(last_seen["gcp-gemini"], "2026-07-06T09:00:00.250+00:00")
        self.assertEqual(document["evidence_watermark"], "2026-07-06T09:00:00.250+00:00")

    def test_unparseable_ts_cannot_win_the_watermark(self) -> None:
        """A ts that will not parse must be excluded from ordering, not allowed to
        win by sorting high as a string ('9' beats '2'). Fails on the old code."""
        self.write_ledger([
            make_offload_event("omen-ollama", "2026-07-03T12:00:00Z"),
            make_offload_event("omen-ollama", "99-not-a-timestamp"),
        ])
        document = build_offload_document(self.ledger)
        self.assertEqual(document["evidence_watermark"], "2026-07-03T12:00:00Z")
        self.assertEqual(document["buckets"][0]["last_seen"], "2026-07-03T12:00:00Z")
        self.assertEqual(document["buckets"][0]["calls"], 2)  # still counted

    def test_identical_instant_spelled_two_ways_resolves_to_that_instant(self) -> None:
        """Suffix-only mixture: the pair from the report. These are the SAME
        instant, so this pins an invariant rather than reproducing a flip --
        whichever spelling is emitted must parse back to that instant. (Passes on
        the old code too; kept so a future "normalize the format" change that
        shifted the instant would be caught.)"""
        self.write_ledger([
            make_offload_event("omen-ollama", "2026-07-03T12:00:00Z"),
            make_offload_event("omen-ollama", "2026-07-03T12:00:00+00:00"),
        ])
        watermark = build_offload_document(self.ledger)["evidence_watermark"]
        self.assertEqual(
            datetime.fromisoformat(watermark.replace("Z", "+00:00")),
            datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc),
        )

    def test_emitted_timestamp_is_the_winners_original_string(self) -> None:
        """The fix must not normalize the format -- the winning event's ts is
        emitted verbatim, so consumers see exactly what the ledger recorded and
        the document's timestamp spelling is unchanged by this repair."""
        self.write_ledger([
            make_offload_event("omen-ollama", "2026-07-03T12:00:00.123456Z"),
        ])
        document = build_offload_document(self.ledger)
        self.assertEqual(document["evidence_watermark"], "2026-07-03T12:00:00.123456Z")
        self.assertEqual(document["buckets"][0]["last_seen"], "2026-07-03T12:00:00.123456Z")

    def test_internal_ordering_key_does_not_leak_into_the_bucket(self) -> None:
        """last_seen_moment is a parsed-datetime ordering key. offload.json is
        serialized with json.dumps, which cannot encode a datetime -- so a leak
        would not merely be untidy, it would raise on write."""
        self.write_ledger([make_offload_event("omen-ollama", "2026-07-03T12:00:00.123Z")])
        document = build_offload_document(self.ledger)
        self.assertNotIn("last_seen_moment", document["buckets"][0])
        json.dumps(document)  # would raise TypeError if a datetime escaped


class AttributionDimensionTests(TestCase):
    """C-06: by_caller / by_task — "what did THIS caller / THIS session save?"

    The dimensions are read beside the totals, so the load-bearing property is
    not any single row: it is that the rows and the totals are the SAME
    arithmetic over the SAME events. Every test below either pins the row shape,
    or proves an event cannot go missing between the two.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / "events.ndjson"

    def write_ledger(self, events: list[dict]) -> None:
        with self.ledger.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    def test_row_shape_is_closed_and_carries_the_declared_fields(self) -> None:
        self.write_ledger([
            make_attributed_event("omen-arc", "2026-09-05T10:00:00Z",
                                  caller_id="claude-frontier", task_id="cc-1a2b3c4d",
                                  tokens_in=1000, tokens_out=2000),
        ])
        document = build_offload_document(self.ledger)

        expected_keys = {"key", "calls", "ok_rate", "tokens_in", "tokens_out",
                         "per_class", "est_usd_saved_usd", "last_seen"}
        for dimension, key in (("by_caller", "claude-frontier"), ("by_task", "cc-1a2b3c4d")):
            rows = document[dimension]
            self.assertEqual(len(rows), 1, dimension)
            row = rows[0]
            self.assertEqual(set(row), expected_keys, dimension)
            self.assertEqual(row["key"], key)
            self.assertEqual(row["calls"], 1)
            self.assertEqual(row["ok_rate"], 1.0)
            self.assertEqual(row["tokens_in"], 1000)
            self.assertEqual(row["tokens_out"], 2000)
            self.assertEqual(row["per_class"], {"sunk": 1, "trial": 0, "unknown": 0})
            self.assertEqual(row["last_seen"], "2026-09-05T10:00:00Z")
            # Same formula as the document's own est_usd_saved, over this row's
            # offloaded tokens: (1000*3.0 + 2000*15.0) / 1e6.
            self.assertEqual(row["est_usd_saved_usd"], round((1000 * 3.0 + 2000 * 15.0) / 1e6, 6))
            self.assertEqual(row["est_usd_saved_usd"], document["est_usd_saved"]["usd"])

        self.assertFalse(document["by_caller_truncated"])
        self.assertFalse(document["by_task_truncated"])
        self.assertEqual(document["by_caller_omitted"], 0)
        self.assertEqual(document["by_task_omitted"], 0)

    def test_repeated_stamped_calls_aggregate_into_one_row(self) -> None:
        """Three calls under one task_id are one session, not three rows."""
        self.write_ledger([
            make_attributed_event("omen-arc", "2026-09-05T10:00:00Z", caller_id="codex-cli",
                                  task_id="cc-deadbeef", tokens_in=100, tokens_out=200),
            make_attributed_event("omen-arc", "2026-09-05T11:00:00Z", caller_id="codex-cli",
                                  task_id="cc-deadbeef", tokens_in=300, tokens_out=400),
            make_attributed_event("gcp-gemini", "2026-09-05T12:00:00Z", caller_id="codex-cli",
                                  task_id="cc-deadbeef", ok=False, model="gemini-3.5-flash",
                                  tokens_in=50, tokens_out=0),
        ])
        document = build_offload_document(self.ledger)

        self.assertEqual(len(document["by_task"]), 1)
        row = document["by_task"][0]
        self.assertEqual(row["key"], "cc-deadbeef")
        self.assertEqual(row["calls"], 3)
        self.assertEqual(row["tokens_in"], 450)
        self.assertEqual(row["tokens_out"], 600)
        self.assertEqual(row["ok_rate"], round(2 / 3, 4))
        # per_class counts CALLS, and a failed call still names the rung it used.
        self.assertEqual(row["per_class"], {"sunk": 2, "trial": 1, "unknown": 0})
        # The newest ts in the row, not the newest in the document by accident.
        self.assertEqual(row["last_seen"], "2026-09-05T12:00:00Z")

    def test_unstamped_events_are_counted_under_one_key(self) -> None:
        """The pre-stamping ledger is the common case: absent, null and blank all
        mean "we do not know whose this was", and all three must still be counted."""
        absent = make_attributed_event("omen-arc", "2026-09-05T10:00:00Z")
        null_task = make_attributed_event("omen-arc", "2026-09-05T11:00:00Z")
        null_task["task_id"] = None
        null_task["caller"] = {"id": None, "runner_class": "frontier", "node": "omen"}
        blank_task = make_attributed_event("omen-arc", "2026-09-05T12:00:00Z",
                                           caller_id="   ", task_id="   ")
        self.write_ledger([absent, null_task, blank_task])

        document = build_offload_document(self.ledger)

        self.assertEqual([r["key"] for r in document["by_task"]], [UNSTAMPED_KEY])
        self.assertEqual([r["key"] for r in document["by_caller"]], [UNSTAMPED_KEY])
        self.assertEqual(document["by_task"][0]["calls"], 3)
        self.assertEqual(document["by_caller"][0]["calls"], 3)
        self.assertEqual(document["totals"]["calls"], 3)

    def test_sums_equal_totals_when_not_truncated(self) -> None:
        """The invariant the dimensions exist to keep: a mixed ledger of stamped
        and unstamped, ok and failed, sunk and trial events adds up both ways."""
        events = [
            make_attributed_event("omen-arc", "2026-09-05T10:00:00Z", caller_id="a",
                                  task_id="t1", tokens_in=10, tokens_out=20),
            make_attributed_event("omen-arc", "2026-09-05T10:01:00Z", caller_id="a",
                                  task_id="t2", tokens_in=30, tokens_out=40, ok=False),
            make_attributed_event("gcp-gemini", "2026-09-05T10:02:00Z", caller_id="b",
                                  task_id="t2", model="gemini-3.5-flash",
                                  tokens_in=50, tokens_out=60),
            make_attributed_event("unknown-backend", "2026-09-05T10:03:00Z", caller_id="b",
                                  model="gpt-4", tokens_in=70, tokens_out=80),
            make_attributed_event("omen-arc", "2026-09-05T10:04:00Z", tokens_in=90,
                                  tokens_out=100),
        ]
        self.write_ledger(events)

        document = build_offload_document(self.ledger)
        totals = document["totals"]

        for dimension in ("by_caller", "by_task"):
            rows = document[dimension]
            self.assertFalse(document[f"{dimension}_truncated"], dimension)
            self.assertEqual(sum(r["calls"] for r in rows), totals["calls"], dimension)
            self.assertEqual(sum(r["tokens_in"] for r in rows), totals["tokens_in"], dimension)
            self.assertEqual(sum(r["tokens_out"] for r in rows), totals["tokens_out"], dimension)
            # Class counts add up to the per_class call counts too.
            for cost_class in ("sunk", "trial", "unknown"):
                self.assertEqual(sum(r["per_class"][cost_class] for r in rows),
                                 document["per_class"][cost_class]["calls"],
                                 f"{dimension}/{cost_class}")

    def test_top_n_truncates_and_states_what_was_cut(self) -> None:
        """60 callers, 50 kept: truncation is a fact ON the document, not a
        silent shortening a reader would have to notice by counting."""
        events = []
        # Caller i makes (i+1) calls, so the ordering is unambiguous and the ten
        # QUIETEST callers are the ones that must fall off the end.
        for index in range(60):
            for call in range(index + 1):
                events.append(make_attributed_event(
                    "omen-arc", f"2026-09-05T10:{call:02d}:00Z",
                    caller_id=f"caller-{index:02d}", task_id=f"task-{index:02d}"))
        self.write_ledger(events)

        document = build_offload_document(self.ledger)

        for dimension in ("by_caller", "by_task"):
            rows = document[dimension]
            self.assertEqual(len(rows), DEFAULT_TOP_N, dimension)
            self.assertTrue(document[f"{dimension}_truncated"], dimension)
            self.assertEqual(document[f"{dimension}_omitted"], 10, dimension)
            # calls descending: the loudest caller (59 -> 60 calls) leads.
            self.assertEqual([r["calls"] for r in rows], sorted(
                (r["calls"] for r in rows), reverse=True), dimension)
            self.assertEqual(rows[0]["calls"], 60)
            self.assertEqual(rows[-1]["calls"], 11)
            # Truncated, the sums are deliberately SHORT of the totals -- which is
            # exactly why the omitted count has to be on the document.
            self.assertLess(sum(r["calls"] for r in rows), document["totals"]["calls"])

    def test_ties_break_on_key_so_the_document_is_deterministic(self) -> None:
        self.write_ledger([
            make_attributed_event("omen-arc", "2026-09-05T10:00:00Z", caller_id="zeta"),
            make_attributed_event("omen-arc", "2026-09-05T10:01:00Z", caller_id="alpha"),
            make_attributed_event("omen-arc", "2026-09-05T10:02:00Z", caller_id="mid"),
        ])
        first = build_offload_document(self.ledger)
        second = build_offload_document(self.ledger)
        self.assertEqual([r["key"] for r in first["by_caller"]], ["alpha", "mid", "zeta"])
        self.assertEqual(first, second)

    def test_top_n_is_configurable_and_must_be_a_positive_integer(self) -> None:
        self.write_ledger([
            make_attributed_event("omen-arc", "2026-09-05T10:00:00Z", caller_id="a"),
            make_attributed_event("omen-arc", "2026-09-05T10:01:00Z", caller_id="b"),
        ])
        narrowed = build_offload_document(self.ledger, top_n=1)
        self.assertEqual(len(narrowed["by_caller"]), 1)
        self.assertTrue(narrowed["by_caller_truncated"])
        self.assertEqual(narrowed["by_caller_omitted"], 1)

        for bad in (0, -1, "50", 1.5, True):
            with self.assertRaises(ValueError):
                build_offload_document(self.ledger, top_n=bad)

    def test_document_carries_no_prompt_text_or_arguments(self) -> None:
        """The privacy invariant, proved rather than promised: a ledger whose
        events carry a marker in args_preview and in error produces a document
        the marker cannot be found in, at any depth."""
        self.write_ledger([
            make_attributed_event(
                "omen-arc", "2026-09-05T10:00:00Z", caller_id="claude-frontier",
                task_id="cc-1a2b3c4d",
                args_preview=f'{{"prompt": "{PROMPT_MARKER}", "files": ["C:/work/secret.txt"]}}',
                error=f"RuntimeError: {PROMPT_MARKER} leaked"),
            make_attributed_event(
                "gcp-gemini", "2026-09-05T11:00:00Z", caller_id="codex-cli",
                task_id="cc-deadbeef", ok=False, model="gemini-3.5-flash",
                args_preview=PROMPT_MARKER, error=PROMPT_MARKER),
        ])

        serialized = json.dumps(build_offload_document(self.ledger))

        self.assertNotIn(PROMPT_MARKER, serialized)
        self.assertNotIn("args_preview", serialized)
        self.assertNotIn("secret.txt", serialized)
        self.assertNotIn("RuntimeError", serialized)
        # The rows are still there -- the events were counted, only their content
        # was left behind.
        document = json.loads(serialized)
        self.assertEqual(document["totals"]["calls"], 2)
        self.assertEqual({r["key"] for r in document["by_task"]}, {"cc-1a2b3c4d", "cc-deadbeef"})

    def test_document_validates_against_the_offload_v1_schema(self) -> None:
        """offload.v1 is `additionalProperties: false` at every level, so the new
        keys are only additive if the schema actually declares them."""
        import jsonschema

        events = [
            make_attributed_event("omen-arc", "2026-09-05T10:00:00Z", caller_id="a",
                                  task_id="t1"),
            make_attributed_event("gcp-gemini", "2026-09-05T10:01:00Z", caller_id="b",
                                  model="gemini-3.5-flash", ok=False),
            make_attributed_event("unknown-backend", "2026-09-05T10:02:00Z", model="gpt-4"),
        ]
        self.write_ledger(events)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        document = build_offload_document(self.ledger)
        jsonschema.Draft202012Validator(schema).validate(document)

        # And the maxItems bound is the same 50 the builder defaults to, so a
        # default-built document can never fail its own contract.
        self.assertEqual(schema["properties"]["by_caller"]["maxItems"], DEFAULT_TOP_N)
        self.assertEqual(schema["properties"]["by_task"]["maxItems"], DEFAULT_TOP_N)

    def test_empty_ledger_yields_empty_dimensions(self) -> None:
        document = build_offload_document(self.ledger)  # never written
        self.assertEqual(document["by_caller"], [])
        self.assertEqual(document["by_task"], [])
        self.assertFalse(document["by_caller_truncated"])
        self.assertEqual(document["by_task_omitted"], 0)

    def test_row_last_seen_orders_by_instant_like_the_buckets(self) -> None:
        """Same _parse_ts rule the buckets use: a bare `Z` must not beat a later
        fractional ts, and the winner's original spelling is emitted verbatim."""
        self.write_ledger([
            make_attributed_event("omen-arc", "2026-07-03T12:00:00Z", task_id="t1"),
            make_attributed_event("omen-arc", "2026-07-03T12:00:00.123+00:00", task_id="t1"),
            make_attributed_event("omen-arc", "99-not-a-timestamp", task_id="t1"),
        ])
        row = build_offload_document(self.ledger)["by_task"][0]
        self.assertEqual(row["last_seen"], "2026-07-03T12:00:00.123+00:00")
        self.assertEqual(row["calls"], 3)  # the unparseable ts is still counted
