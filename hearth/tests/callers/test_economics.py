from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from hearth.projection.economics import build_offload_document, summarize


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
