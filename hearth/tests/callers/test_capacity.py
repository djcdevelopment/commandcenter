from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from hearth.projection.capacity import _percentile, build_capacity_document

_UNSET = object()  # "caller said nothing about error", distinct from an explicit None


def make_event(
    tool: str,
    ok: bool,
    duration_ms: int,
    tokens_out: int | None,
    *,
    ts: str = "2026-07-03T12:00:00+00:00",
    node: str = "omen",
    runner_class: str = "local",
    task_class: str | None = None,
    model: str | None = None,
    error: str | None = _UNSET,
) -> dict:
    # A real emitter that reports ok:false always names the failure -- across the
    # whole production ledger every genuine failure carries an error string, and
    # ledger.new_event now refuses to persist one that does not. So a failure
    # fixture defaults to a named error; pass error=None explicitly to build the
    # legacy "reports failure, names none" shape the projection treats as
    # indeterminate.
    if error is _UNSET:
        error = None if ok else "boom"
    event = {
        "schema": "hearth-event.v1",
        "event_id": f"he_{tool}_{duration_ms}_{ts}",
        "ts": ts,
        "caller": {"id": f"{runner_class}-1", "runner_class": runner_class, "node": node},
        "tool": tool,
        "ok": ok,
        "error": error,
        "duration_ms": duration_ms,
        "cost": {"tokens_in": 10, "tokens_out": tokens_out, "watt_s": None},
        "task_id": None,
    }
    if task_class is not None:
        event["task_class"] = task_class
    if model is not None:
        event["model"] = model
    return event


class PercentileTests(TestCase):
    def test_known_list_percentiles(self) -> None:
        values = [float(v) for v in range(1, 11)]  # 1..10
        self.assertEqual(_percentile(values, 0.50), 5.0)
        self.assertEqual(_percentile(values, 0.90), 9.0)
        self.assertEqual(_percentile([42.0], 0.50), 42.0)

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            _percentile([], 0.5)


class BuildCapacityDocumentTests(TestCase):
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

    def test_empty_ledger_is_a_valid_empty_document(self) -> None:
        document = build_capacity_document(self.ledger)  # never written
        self.assertEqual(document["contract_version"], "capacity.v1")
        self.assertIsNone(document["evidence_watermark"])
        self.assertEqual(document["buckets"], [])

    def test_buckets_key_by_task_class_node_model_tool_with_nulls_for_legacy_events(self) -> None:
        self.write_ledger([
            make_event("run_tests", True, 1000, 50, task_class="build", model="qwen3-coder:30b"),
            make_event("run_tests", True, 2000, 60),  # legacy event: no task_class/model
        ])
        document = build_capacity_document(self.ledger)
        keys = {(b["task_class"], b["model"], b["tool"]) for b in document["buckets"]}
        self.assertIn(("build", "qwen3-coder:30b", "run_tests"), keys)
        self.assertIn((None, None, "run_tests"), keys)
        self.assertEqual(len(document["buckets"]), 2)

    def test_percentile_math_on_known_duration_list(self) -> None:
        durations = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
        self.write_ledger([make_event("run_tests", True, d, 10) for d in durations])
        document = build_capacity_document(self.ledger)
        bucket = document["buckets"][0]
        self.assertEqual(bucket["duration_ms"]["p50"], 500.0)
        self.assertEqual(bucket["duration_ms"]["p90"], 900.0)
        self.assertEqual(bucket["duration_ms"]["mean"], 550.0)
        self.assertEqual(bucket["duration_ms"]["max"], 1000.0)

    def test_ok_false_counts_toward_ok_rate_but_excluded_from_duration_percentiles(self) -> None:
        self.write_ledger([
            make_event("run_tests", True, 1000, 50),
            make_event("run_tests", False, 999999, None),
        ])
        document = build_capacity_document(self.ledger)
        bucket = document["buckets"][0]
        self.assertEqual(bucket["calls"], 2)
        self.assertEqual(bucket["ok_rate"], 0.5)
        self.assertEqual(bucket["indeterminate"], 0)
        self.assertEqual(bucket["duration_ms"]["p50"], 1000.0)
        self.assertEqual(bucket["duration_ms"]["max"], 1000.0)

    def test_ok_false_naming_no_error_is_indeterminate_not_a_failure(self) -> None:
        """The bankedfire_drain defect: 592 healthy idle no-op ticks emitted
        ok:false with error:null and dragged the bucket to ok_rate 0.0084,
        reading as a catastrophic outage. Such an event claims a failure while
        naming none, so it is evidence of nothing -- excluded from the rate
        rather than counted against it. Those events are immutable, so this
        projection-time reinterpretation is the only available repair."""
        self.write_ledger([
            make_event("bankedfire_drain.tick", True, 0, None),
            *[make_event("bankedfire_drain.tick", False, 0, None, error=None)
              for _ in range(9)],
        ])
        bucket = build_capacity_document(self.ledger)["buckets"][0]
        self.assertEqual(bucket["calls"], 10)      # liveness still visible
        self.assertEqual(bucket["indeterminate"], 9)
        self.assertEqual(bucket["ok_rate"], 1.0)   # NOT 0.1

    def test_a_named_failure_is_never_suppressed_as_indeterminate(self) -> None:
        """Guard on the predicate's blast radius: the watchdog's SSH timeouts
        name their error, so they must keep counting as the real failures they
        are. Only the unnamed shape is reinterpreted."""
        self.write_ledger([
            make_event("mechnet_watchdog.patrol_snapshot", True, 500, None),
            make_event("mechnet_watchdog.patrol_snapshot", False, 0, None,
                       error="TimeoutExpired: ssh claude@100.74.110.91"),
        ])
        bucket = build_capacity_document(self.ledger)["buckets"][0]
        self.assertEqual(bucket["ok_rate"], 0.5)
        self.assertEqual(bucket["indeterminate"], 0)

    def test_bucket_with_only_indeterminate_calls_reports_null_not_zero(self) -> None:
        """No determinate evidence must render as "never learned" (null), not as
        "always failed" (0.0) -- rendering it 0.0 is the very misreading this
        whole change exists to remove."""
        self.write_ledger([
            make_event("bankedfire_drain.tick", False, 0, None, error=None)
            for _ in range(4)
        ])
        bucket = build_capacity_document(self.ledger)["buckets"][0]
        self.assertEqual(bucket["calls"], 4)
        self.assertEqual(bucket["indeterminate"], 4)
        self.assertIsNone(bucket["ok_rate"])

    def test_malformed_lines_are_skipped_and_do_not_raise(self) -> None:
        self.write_ledger(
            [make_event("run_tests", True, 1000, 50)],
            extra_lines=["{ not json", "", "   "],
        )
        document = build_capacity_document(self.ledger)
        self.assertEqual(len(document["buckets"]), 1)
        self.assertEqual(document["buckets"][0]["calls"], 1)

    def test_tokens_out_per_s_only_when_tokens_out_and_duration_present(self) -> None:
        self.write_ledger([
            make_event("local_generate", True, 2000, 100),  # 50 tok/s
            make_event("local_generate", True, 1000, None),  # no tokens_out -> not sampled
        ])
        document = build_capacity_document(self.ledger)
        bucket = document["buckets"][0]
        self.assertEqual(bucket["tokens_out_per_s_p50"], 50.0)

    def test_evidence_watermark_is_newest_ts_seen(self) -> None:
        self.write_ledger([
            make_event("run_tests", True, 100, 10, ts="2026-07-01T00:00:00+00:00"),
            make_event("run_tests", True, 100, 10, ts="2026-07-03T00:00:00+00:00"),
            make_event("run_tests", True, 100, 10, ts="2026-07-02T00:00:00+00:00"),
        ])
        document = build_capacity_document(self.ledger)
        self.assertEqual(document["evidence_watermark"], "2026-07-03T00:00:00+00:00")


class TimestampOrderingTests(TestCase):
    """evidence_watermark / last_seen must be ordered by INSTANT, not by string.

    hearth-event.v1 permits three spellings of `ts`
    (`...:00Z`, `...:00+00:00`, `...:00.123Z`), and 'Z' (0x5A) sorts above both
    '+' (0x2B) and '.' (0x2E). Precisely which mixtures go wrong is worth stating,
    because the two hazards are not equally severe:

    - Suffix alone (`Z` vs `+00:00`) is a TIE, not a wrong answer: a lexical
      compare only reaches the suffix when every character before it is equal, and
      an equal date+time+fraction IS the same instant. Lexical order breaks that
      tie arbitrarily; either answer names the right moment.
    - The fractional-second boundary is where ordering genuinely FLIPS:
      '...12:00:00Z' > '...12:00:00.123Z' lexically, so the strictly EARLIER
      instant wins. This holds across suffixes too
      ('...12:00:00Z' > '...12:00:00.123+00:00'), which is the combined case.

    So the tests that would fail on the old lexical code all involve fractional
    seconds; the suffix-only case is pinned below as an invariant rather than as a
    bug repro. evidence_watermark is load-bearing either way
    (corpus_guard.guard_write regression-guards on it, dashboard.py renders it).

    The live ledger is uniform today (8,530 events, all fractional-Z), so this is a
    LATENT defect: it fires the first time any emitter omits fractional seconds.
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
            make_event("run_tests", True, 100, 10, ts="2026-07-03T12:00:00Z"),
            make_event("run_tests", True, 100, 10, ts="2026-07-03T12:00:00.123+00:00"),
        ])
        document = build_capacity_document(self.ledger)
        self.assertEqual(document["evidence_watermark"], "2026-07-03T12:00:00.123+00:00")
        self.assertEqual(document["buckets"][0]["last_seen"], "2026-07-03T12:00:00.123+00:00")

    def test_identical_instant_spelled_two_ways_resolves_to_that_instant(self) -> None:
        """Suffix-only mixture: the pair from the report. These are the SAME
        instant, so this pins an invariant rather than reproducing a flip --
        whichever spelling is emitted must parse back to that instant. (Passes on
        the old code too; kept so a future "normalize the format" change that
        shifted the instant would be caught.)"""
        self.write_ledger([
            make_event("run_tests", True, 100, 10, ts="2026-07-03T12:00:00Z"),
            make_event("run_tests", True, 100, 10, ts="2026-07-03T12:00:00+00:00"),
        ])
        watermark = build_capacity_document(self.ledger)["evidence_watermark"]
        self.assertEqual(
            datetime.fromisoformat(watermark.replace("Z", "+00:00")),
            datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc),
        )

    def test_fractional_seconds_beat_a_bare_earlier_second(self) -> None:
        """Hazard 2: '...12:00:00Z' > '...12:00:00.123Z' lexically, so the EARLIER
        instant wins. Fractional seconds are a strictly later instant within the
        same second and must be picked."""
        self.write_ledger([
            make_event("run_tests", True, 100, 10, ts="2026-07-03T12:00:00Z"),
            make_event("run_tests", True, 100, 10, ts="2026-07-03T12:00:00.123Z"),
        ])
        document = build_capacity_document(self.ledger)
        self.assertEqual(document["evidence_watermark"], "2026-07-03T12:00:00.123Z")
        self.assertEqual(document["buckets"][0]["last_seen"], "2026-07-03T12:00:00.123Z")

    def test_per_bucket_last_seen_orders_independently_of_the_document(self) -> None:
        """last_seen is tracked per bucket, so each bucket must resolve its own
        mixed-format max -- not inherit the document watermark. Both buckets are
        built so the fractional ts is the one that loses lexically."""
        self.write_ledger([
            make_event("alpha", True, 100, 10, ts="2026-07-03T12:00:00Z"),
            make_event("alpha", True, 100, 10, ts="2026-07-03T12:00:00.500Z"),
            make_event("beta", True, 100, 10, ts="2026-07-06T09:00:00Z"),
            make_event("beta", True, 100, 10, ts="2026-07-06T09:00:00.250+00:00"),
        ])
        document = build_capacity_document(self.ledger)
        last_seen = {b["tool"]: b["last_seen"] for b in document["buckets"]}
        self.assertEqual(last_seen["alpha"], "2026-07-03T12:00:00.500Z")
        self.assertEqual(last_seen["beta"], "2026-07-06T09:00:00.250+00:00")
        self.assertEqual(document["evidence_watermark"], "2026-07-06T09:00:00.250+00:00")

    def test_emitted_timestamp_is_the_winners_original_string(self) -> None:
        """The fix must not normalize the format -- the winning event's ts is
        emitted verbatim, so consumers see exactly what the ledger recorded."""
        self.write_ledger([
            make_event("run_tests", True, 100, 10, ts="2026-07-03T12:00:00.123456Z"),
        ])
        document = build_capacity_document(self.ledger)
        self.assertEqual(document["evidence_watermark"], "2026-07-03T12:00:00.123456Z")

    def test_unparseable_ts_cannot_win_the_watermark(self) -> None:
        """A ts that will not parse must be excluded from ordering, not allowed to
        win by sorting high as a string ('9' beats '2')."""
        self.write_ledger([
            make_event("run_tests", True, 100, 10, ts="2026-07-03T12:00:00Z"),
            make_event("run_tests", True, 100, 10, ts="99-not-a-timestamp"),
        ])
        document = build_capacity_document(self.ledger)
        self.assertEqual(document["evidence_watermark"], "2026-07-03T12:00:00Z")
        self.assertEqual(document["buckets"][0]["last_seen"], "2026-07-03T12:00:00Z")
        self.assertEqual(document["buckets"][0]["calls"], 2)  # still counted

    def test_internal_ordering_key_does_not_leak_into_the_bucket(self) -> None:
        self.write_ledger([make_event("run_tests", True, 100, 10)])
        bucket = build_capacity_document(self.ledger)["buckets"][0]
        self.assertNotIn("last_seen_moment", bucket)
