"""`outcome` separates WHICH branch a call took from WHETHER it worked.

Background: `ok` was doing two incompatible jobs. For an inference call it means
"succeeded"; for a timer tick bankedfire_drain read it as "dispatched work", so
592 healthy idle no-ops recorded ok:false and the tool projected an ok_rate of
0.0084 into knowledge/capacity.json -- indistinguishable from a real outage, and
it already caused one wrong diagnosis (a claimed shared root cause with an
omen-ollama outage, since falsified).

Covers: `outcome` round-trips additively; legacy events without it still
validate; and the invariant that an ok:false event must always name its failure.
"""

import json
import tempfile
import unittest
from pathlib import Path

from hearth.kernel.ledger import Ledger, new_event, validate_event

CALLER = {"id": "dev-local", "runner_class": "human", "node": "omen"}


class OutcomeFieldTest(unittest.TestCase):
    def test_outcome_defaults_to_null_and_validates(self):
        event = new_event(CALLER, "ping")
        self.assertIsNone(event["outcome"])
        validate_event(event)

    def test_outcome_round_trips_through_ndjson(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger")
            ledger.append(new_event(CALLER, "bankedfire_drain.tick",
                                    ok=True, outcome="no-candidates"))
            line = json.loads(
                (Path(tmp) / "ledger" / "events.ndjson").read_text(
                    encoding="utf-8").splitlines()[0])
        self.assertEqual(line["outcome"], "no-candidates")
        self.assertTrue(line["ok"])

    def test_legacy_event_without_outcome_still_validates(self):
        """Additive, exactly like task_class/model before it: the ~8.5k events
        already on disk carry no `outcome` key and must keep validating."""
        event = new_event(CALLER, "ping")
        del event["outcome"]
        validate_event(event)

    def test_outcome_must_be_a_string_or_null(self):
        event = new_event(CALLER, "ping")
        event["outcome"] = 42
        with self.assertRaises(Exception):
            validate_event(event)


class OkFalseMustNameItsFailureTest(unittest.TestCase):
    """ok:false with error:null is structurally incoherent -- the emitter claims
    something broke while declining to say what. That exact shape is what made
    the drain's 592 benign ticks unreadable, so it must not be persistable."""

    def test_unnamed_failure_is_stamped_rather_than_stored_blank(self):
        event = new_event(CALLER, "some_tool", ok=False)
        self.assertFalse(event["ok"])
        self.assertTrue(event["error"], "an ok:false event must name its failure")

    def test_a_named_failure_is_left_exactly_as_given(self):
        event = new_event(CALLER, "some_tool", ok=False, error="TimeoutExpired: ssh")
        self.assertEqual(event["error"], "TimeoutExpired: ssh")

    def test_success_is_not_given_a_spurious_error(self):
        self.assertIsNone(new_event(CALLER, "some_tool", ok=True)["error"])

    def test_normalisation_never_raises_since_callers_swallow_exceptions(self):
        """Every _record* helper in fleet/ wraps ledger appends in a best-effort
        try/except, so raising here would silently DROP the audit line this
        invariant exists to protect. Stamping keeps the line."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger")
            ledger.append(new_event(CALLER, "some_tool", ok=False))
            lines = (Path(tmp) / "ledger" / "events.ndjson").read_text(
                encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
