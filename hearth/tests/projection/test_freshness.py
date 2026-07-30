from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.projection.freshness import (
    StaleProjectionError,
    bridge_backlog,
    check_freshness,
    corpus_watermark,
    enforce_freshness,
    format_report,
    ledger_head,
    parse_instant,
)

LEDGER_HEAD_TS = "2026-07-30T07:44:45.291635Z"
FRESH_TS = "2026-07-30T05:00:00Z"          # ~2.7h behind head: inside the 10h default
STALE_TS = "2026-07-04T05:50:00+00:00"     # the real 26-day freeze, 625.9h behind


def _ledger_line(event_id: str, ts: str) -> str:
    return json.dumps({
        "schema": "hearth-event.v1", "event_id": event_id, "ts": ts,
        "caller": {"id": "dev-local", "runner_class": "human", "node": "omen"},
        "tool": "kernel_status", "ok": True,
    })


def _workflow_line(event_id: str, ts: str) -> str:
    return json.dumps({
        "event_id": event_id, "event_type": "work.accepted", "timestamp": ts,
        "workflow_id": "wf-hearth-gateway", "run_id": "hearth-gateway",
        "actor": {"type": "operator", "id": "dev-local"}, "status": "completed",
    })


class _FreshnessFixture:
    """Shared temp-dir fixture. Deliberately NOT a TestCase: the cases below mix it in
    beside TestCase so each runs only its own tests, instead of re-running an
    inherited suite."""

    def setUp(self) -> None:
        self.root = Path(mkdtemp()).resolve()
        self.ledger = self.root / "ledger.ndjson"
        self.cursor = self.root / "cursor.json"
        self.knowledge = self.root / "knowledge"
        self.runs = self.root / "runs"
        self.knowledge.mkdir()
        (self.runs / "hearth-gateway").mkdir(parents=True)

        self.ledger.write_text(
            "\n".join(_ledger_line(f"e{n}", LEDGER_HEAD_TS if n == 2 else "2026-07-30T04:00:00Z")
                      for n in range(3)) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_corpus(self, ts: str) -> None:
        (self.runs / "hearth-gateway" / "events.jsonl").write_text(
            _workflow_line("evt_1", ts) + "\n", encoding="utf-8")

    def _write_doc(self, name: str, document: dict) -> None:
        (self.knowledge / name).write_text(json.dumps(document), encoding="utf-8")

    def _check(self, **overrides) -> dict:
        kwargs = dict(knowledge_dir=self.knowledge, ledger_path=self.ledger,
                      sources=[self.runs], cursor_path=self.cursor)
        kwargs.update(overrides)
        return check_freshness(**kwargs)


class FreshnessTestCase(_FreshnessFixture, TestCase):
    # --- primitives ---------------------------------------------------------

    def test_parse_instant_accepts_every_spelling_live_in_the_repo(self) -> None:
        """All four spellings coexist in the corpus; a guard fooled by a format
        is not a guard."""
        instants = [parse_instant(raw) for raw in (
            "2026-07-30T05:00:00Z", "2026-07-30T05:00:00+00:00",
            "2026-07-30T05:00:00.000000Z", "2026-07-30T05:00:00.000000+00:00",
            "2026-07-30T05:00:00",  # naive -> assumed UTC
        )]
        self.assertTrue(all(i is not None for i in instants))
        self.assertEqual(len(set(instants)), 1)

    def test_parse_instant_rejects_garbage(self) -> None:
        for raw in (None, "", "   ", "not-a-date", 12345, {}):
            self.assertIsNone(parse_instant(raw))

    def test_ledger_head_takes_the_max_not_the_last_line(self) -> None:
        head, lines = ledger_head(self.ledger)
        self.assertEqual(lines, 3)
        self.assertEqual(head, parse_instant(LEDGER_HEAD_TS))

    def test_ledger_head_of_missing_file_is_none(self) -> None:
        self.assertEqual(ledger_head(self.root / "nope.ndjson"), (None, 0))

    def test_bridge_backlog_counts_whole_ledger_when_cursor_absent(self) -> None:
        self.assertEqual(bridge_backlog(self.cursor, 20110), (20110, 0))

    def test_bridge_backlog_uses_cursor_line(self) -> None:
        self.cursor.write_text(json.dumps({"last_event_id": "e0", "line": 3}), encoding="utf-8")
        self.assertEqual(bridge_backlog(self.cursor, 20110), (20107, 3))

    def test_bridge_backlog_treats_corrupt_cursor_as_zero(self) -> None:
        self.cursor.write_text("{not json", encoding="utf-8")
        self.assertEqual(bridge_backlog(self.cursor, 10), (10, 0))

    def test_corpus_watermark_is_max_across_source_roots(self) -> None:
        self._write_corpus(FRESH_TS)
        self.assertEqual(corpus_watermark([self.runs]), parse_instant(FRESH_TS))

    # --- the regression this guard exists for -------------------------------

    def test_the_actual_26_day_freeze_is_reported_stale(self) -> None:
        """The real 2026-07-04 -> 2026-07-30 failure: cursor stuck near zero, the
        corpus frozen, documents stamping a 26-day-old evidence watermark, and a
        rebuild that reported ok every time."""
        self.cursor.write_text(json.dumps({"last_event_id": "e0", "line": 3}), encoding="utf-8")
        self._write_corpus(STALE_TS)
        self._write_doc("policy.json", {"evidence_watermark": STALE_TS})
        self._write_doc("capabilities.json",
                        {"evidence_watermark": STALE_TS, "capability_count": 0})

        report = self._check(max_bridge_backlog=0)

        self.assertFalse(report["ok"])
        self.assertIn("corpus", report["stale"])
        self.assertIn("policy.json", report["stale"])
        self.assertIn("capabilities.json", report["stale"])
        corpus_check = next(c for c in report["checks"] if c["name"] == "corpus")
        self.assertGreater(corpus_check["lag_hours"], 600)

    def test_bridge_backlog_alone_fails_the_check(self) -> None:
        """Most direct statement of the bug: unprocessed ledger lines. Fresh
        corpus and fresh documents must not excuse a stalled bridge."""
        self._write_corpus(LEDGER_HEAD_TS)
        self._write_doc("policy.json", {"evidence_watermark": LEDGER_HEAD_TS})

        report = self._check(max_bridge_backlog=0)

        self.assertFalse(report["ok"])
        self.assertEqual(report["stale"], ["bridge"])

    def test_everything_current_passes(self) -> None:
        self.cursor.write_text(json.dumps({"last_event_id": "e2", "line": 3}), encoding="utf-8")
        self._write_corpus(LEDGER_HEAD_TS)
        self._write_doc("policy.json", {"evidence_watermark": FRESH_TS})

        report = self._check()

        self.assertTrue(report["ok"], format_report(report))
        self.assertEqual(report["stale"], [])

    def test_lag_just_inside_and_just_outside_the_threshold(self) -> None:
        self.cursor.write_text(json.dumps({"last_event_id": "e2", "line": 3}), encoding="utf-8")
        self._write_corpus(LEDGER_HEAD_TS)
        self._write_doc("policy.json", {"evidence_watermark": "2026-07-30T02:00:00Z"})

        self.assertTrue(self._check(max_lag_hours=6.0)["ok"])
        self.assertIn("policy.json", self._check(max_lag_hours=5.0)["stale"])

    # --- honesty properties -------------------------------------------------

    def test_document_without_watermark_is_unchecked_not_fresh(self) -> None:
        """findings.json / known_bad_models.json stamp no watermark. They must be
        reported as unverifiable, never counted as passing."""
        self.cursor.write_text(json.dumps({"last_event_id": "e2", "line": 3}), encoding="utf-8")
        self._write_corpus(LEDGER_HEAD_TS)
        self._write_doc("findings.json", {"contract_version": "findings.v1"})

        report = self._check()

        self.assertIn("findings.json", report["unchecked"])
        self.assertNotIn("findings.json", report["stale"])
        check = next(c for c in report["checks"] if c["name"] == "findings.json")
        self.assertFalse(check["checked"])

    def test_missing_ledger_fails_rather_than_passing_vacuously(self) -> None:
        report = self._check(ledger_path=self.root / "absent.ndjson")
        self.assertFalse(report["ok"])
        self.assertIn("ledger", report["stale"])

    def test_unreadable_document_is_stale_not_skipped(self) -> None:
        self.cursor.write_text(json.dumps({"last_event_id": "e2", "line": 3}), encoding="utf-8")
        self._write_corpus(LEDGER_HEAD_TS)
        (self.knowledge / "broken.json").write_text("{ truncated", encoding="utf-8")

        report = self._check()

        self.assertFalse(report["ok"])
        self.assertIn("broken.json", report["stale"])

    def test_empty_corpus_is_stale_not_fresh(self) -> None:
        self.cursor.write_text(json.dumps({"last_event_id": "e2", "line": 3}), encoding="utf-8")
        report = self._check()
        self.assertIn("corpus", report["stale"])

    def test_enforce_raises_with_the_report_in_the_message(self) -> None:
        self._write_corpus(STALE_TS)
        with self.assertRaises(StaleProjectionError) as caught:
            enforce_freshness(knowledge_dir=self.knowledge, ledger_path=self.ledger,
                              sources=[self.runs], cursor_path=self.cursor)
        self.assertIn("corpus", str(caught.exception))

    def test_enforce_returns_report_when_fresh(self) -> None:
        self.cursor.write_text(json.dumps({"last_event_id": "e2", "line": 3}), encoding="utf-8")
        self._write_corpus(LEDGER_HEAD_TS)
        report = enforce_freshness(knowledge_dir=self.knowledge, ledger_path=self.ledger,
                                   sources=[self.runs], cursor_path=self.cursor)
        self.assertTrue(report["ok"])


class AckTestCase(_FreshnessFixture, TestCase):
    """A permanently-red guard is a guard nobody reads -- which is how eleven
    projections stayed frozen for 26 days while the rebuild reported ok. These pin the
    mechanism that lets the guard say "stale, known cause, tracked, expires on this
    date" WITHOUT going quiet about anything new."""

    ACK_EXPIRY = "2026-08-31T00:00:00Z"     # after LEDGER_HEAD_TS
    EARLY_EXPIRY = "2026-07-20T00:00:00Z"   # before LEDGER_HEAD_TS

    def _ack(self, entries: list[dict], active: bool = True) -> None:
        (self.knowledge / "freshness_ack.json").write_text(json.dumps({
            "contract_version": "freshness-ack.v1", "active": active,
            "author": "derek", "created": "2026-07-30", "acknowledged": entries,
        }), encoding="utf-8")

    def _stale_doc_setup(self) -> None:
        """The real situation: bridge and corpus current, one document 26 days stale."""
        self.cursor.write_text(json.dumps({"last_event_id": "e2", "line": 3}), encoding="utf-8")
        self._write_corpus(LEDGER_HEAD_TS)
        self._write_doc("policy.json", {"evidence_watermark": STALE_TS})

    def _entry(self, **overrides) -> dict:
        entry = {
            "name": "policy.json",
            "watermark": STALE_TS,
            "reason": "Structurally stale until ADR-0027 option B lands",
            "blocked_on": "docs/adr/0027-gateway-dispatches-are-not-observations-yet.md",
            "expires_at_ledger_head": self.ACK_EXPIRY,
        }
        entry.update(overrides)
        return entry

    # --- the load-bearing properties ----------------------------------------

    def test_matching_ack_moves_document_from_stale_to_acknowledged(self) -> None:
        self._stale_doc_setup()
        self._ack([self._entry()])

        report = self._check()

        self.assertTrue(report["ok"], format_report(report))
        self.assertEqual(report["acknowledged"], ["policy.json"])
        self.assertNotIn("policy.json", report["stale"])
        check = next(c for c in report["checks"] if c["name"] == "policy.json")
        # The document IS stale; only the bucket its name lands in changes.
        self.assertTrue(check["stale"])
        self.assertTrue(check["acknowledged"])

    def test_ack_does_not_mask_new_drift(self) -> None:
        """THE load-bearing test. An ack pinned to one watermark must stop applying the
        moment the document stamps a different one -- even another stale one."""
        self._stale_doc_setup()
        self._ack([self._entry()])
        self._write_doc("policy.json", {"evidence_watermark": "2026-07-11T00:00:00+00:00"})

        report = self._check()

        self.assertFalse(report["ok"])
        self.assertIn("policy.json", report["stale"])
        self.assertEqual(report["acknowledged"], [])
        self.assertIn("re-decide", report["ack_expired"][0]["note"])

    def test_ack_does_not_mask_a_regressed_watermark(self) -> None:
        """A from-zero rebuild bypasses corpus_guard, so a document CAN regress. The
        ack must not keep quiet about that either."""
        self._stale_doc_setup()
        self._ack([self._entry()])
        self._write_doc("policy.json", {"evidence_watermark": "2026-06-01T00:00:00+00:00"})

        report = self._check()

        self.assertFalse(report["ok"])
        self.assertIn("policy.json", report["stale"])

    def test_ack_pin_compares_parsed_instants_not_strings(self) -> None:
        self._stale_doc_setup()  # document stamps 2026-07-04T05:50:00+00:00
        self._ack([self._entry(watermark="2026-07-04T05:50:00Z")])

        self.assertTrue(self._check()["ok"])

    def test_ack_expires_against_ledger_head_not_wall_clock(self) -> None:
        """Proven by moving the LEDGER HEAD across the boundary while the ack file
        stays byte-identical -- no wall-clock dependence is possible."""
        self._stale_doc_setup()
        self._ack([self._entry(expires_at_ledger_head=self.EARLY_EXPIRY)])

        report = self._check()
        self.assertFalse(report["ok"])
        self.assertIn("expired", report["ack_expired"][0]["note"])

        # Rewind only the ledger to before the expiry; the ack file is untouched.
        self.ledger.write_text(_ledger_line("e0", "2026-07-15T00:00:00Z") + "\n",
                               encoding="utf-8")
        self.cursor.write_text(json.dumps({"last_event_id": "e0", "line": 1}), encoding="utf-8")
        self._write_corpus("2026-07-15T00:00:00Z")
        self.assertTrue(self._check()["ok"])

    # --- fail-closed properties ---------------------------------------------

    def test_ack_without_expiry_is_refused(self) -> None:
        self._stale_doc_setup()
        entry = self._entry()
        del entry["expires_at_ledger_head"]
        self._ack([entry])

        report = self._check()

        self.assertFalse(report["ok"])
        self.assertIn("no expires_at_ledger_head", report["ack_expired"][0]["note"])

    def test_inactive_ack_does_not_apply(self) -> None:
        self._stale_doc_setup()
        self._ack([self._entry()], active=False)

        report = self._check()

        self.assertFalse(report["ok"])
        self.assertIn("policy.json", report["stale"])

    def test_ack_of_bridge_is_a_hard_error(self) -> None:
        self._stale_doc_setup()
        self._ack([self._entry(name="bridge")])

        report = self._check()

        self.assertFalse(report["ok"])
        self.assertTrue(report["ack_errors"])
        self.assertIn("never", report["ack_errors"][0])

    def test_ack_of_corpus_is_a_hard_error(self) -> None:
        self._stale_doc_setup()
        self._ack([self._entry(name="corpus")])

        report = self._check()

        self.assertFalse(report["ok"])
        self.assertTrue(report["ack_errors"])

    def test_unreadable_ack_fails_closed_even_when_nothing_is_stale(self) -> None:
        self.cursor.write_text(json.dumps({"last_event_id": "e2", "line": 3}), encoding="utf-8")
        self._write_corpus(LEDGER_HEAD_TS)
        (self.knowledge / "freshness_ack.json").write_text("{ truncated", encoding="utf-8")

        report = self._check()

        self.assertFalse(report["ok"])
        self.assertTrue(report["ack_errors"])
        self.assertEqual(report["stale"], [])

    def test_missing_ledger_ignores_any_ack_file(self) -> None:
        self._stale_doc_setup()
        self._ack([self._entry()])

        report = self._check(ledger_path=self.root / "absent.ndjson")

        self.assertFalse(report["ok"])
        self.assertIn("ledger", report["stale"])
        self.assertEqual(report["acknowledged"], [])

    # --- hygiene ------------------------------------------------------------

    def test_ack_file_is_not_itself_scanned_as_a_document(self) -> None:
        """Each entry carries a 'watermark' key, so _document_watermark would other-
        wise report the ack file stale for the very watermark it acknowledges."""
        self._stale_doc_setup()
        self._ack([self._entry()])

        report = self._check()

        self.assertNotIn("freshness_ack.json", [c["name"] for c in report["checks"]])
        self.assertNotIn("freshness_ack.json", report["stale"])
        self.assertNotIn("freshness_ack.json", report["unchecked"])

    def test_control_files_are_not_reported_unchecked(self) -> None:
        self._stale_doc_setup()
        self._write_doc("corpus_regression_override.json", {"active": False})
        self._write_doc("policy_overrides.json", {})

        report = self._check()

        self.assertNotIn("corpus_regression_override.json", report["unchecked"])
        self.assertNotIn("policy_overrides.json", report["unchecked"])

    def test_absent_ack_file_changes_nothing(self) -> None:
        self._stale_doc_setup()

        report = self._check()

        self.assertFalse(report["ok"])
        self.assertEqual(report["acknowledged"], [])
        self.assertEqual(report["ack_errors"], [])

    def test_honor_acks_false_is_the_strict_audit_view(self) -> None:
        self._stale_doc_setup()
        self._ack([self._entry()])

        self.assertTrue(self._check()["ok"])
        strict = self._check(honor_acks=False)
        self.assertFalse(strict["ok"])
        self.assertIn("policy.json", strict["stale"])

    def test_format_report_never_says_bare_ok_while_acks_are_in_force(self) -> None:
        self._stale_doc_setup()
        self._ack([self._entry()])

        text = format_report(self._check())

        self.assertIn("ACKNOWLEDGED", text)
        self.assertIn("ACK", text)
        self.assertIn("ADR-0027", text)
        self.assertIn(self.ACK_EXPIRY, text)
        self.assertNotIn("freshness: OK (", text)

    def test_check_freshness_writes_nothing(self) -> None:
        """Pins the read-only-reporter property, and with it the deliberate divergence
        from corpus_regression_override's auto-deactivation."""
        self._stale_doc_setup()
        self._ack([self._entry()])
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
                  for p in sorted(self.knowledge.iterdir())}

        self._check()

        after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
                 for p in sorted(self.knowledge.iterdir())}
        self.assertEqual(before, after)

    def test_enforce_does_not_raise_when_all_staleness_is_acknowledged(self) -> None:
        self._stale_doc_setup()
        self._ack([self._entry()])

        report = enforce_freshness(knowledge_dir=self.knowledge, ledger_path=self.ledger,
                                   sources=[self.runs], cursor_path=self.cursor)

        self.assertTrue(report["ok"])

    def test_enforce_still_raises_on_unacknowledged_staleness(self) -> None:
        self._stale_doc_setup()
        self._write_doc("capabilities.json", {"evidence_watermark": STALE_TS})
        self._ack([self._entry()])

        with self.assertRaises(StaleProjectionError) as caught:
            enforce_freshness(knowledge_dir=self.knowledge, ledger_path=self.ledger,
                              sources=[self.runs], cursor_path=self.cursor)

        self.assertIn("capabilities.json", str(caught.exception))
