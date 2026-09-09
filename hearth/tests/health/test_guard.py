"""The guard's stop rules and its staleness rule, ported with the guard.

These are the checks that turn a green run into a meaningful one. Every case here is a
place where a pass could otherwise mean nothing: a healthy port over a starved rung, a
sample taken before the work started, an unreadable reader. No network, no live rung --
every reader is injected.

Run: fleet-worker-node\\.venv-omen\\Scripts\\python.exe -m pytest hearth/tests/health/test_guard.py
"""
from __future__ import annotations

from unittest import TestCase

from hearth.health import guard

OK_HEALTH = {"http_status": 200, "body": '{"status":"ok"}'}
DEAD_HEALTH = {"error": "connection refused"}


def readers(state, health=OK_HEALTH):
    """Injected readers: a fixed rung state and a fixed /health answer."""
    return {"state_reader": lambda: dict(state), "health_reader": lambda: dict(health)}


def rung(verdict, age_s=None, **extra):
    state = {"verdict": verdict, "observed_tok_s": None, "frac_of_baseline": None,
             "baseline_tok_s": 106.0}
    if age_s is not None:
        state["observed_age_s"] = age_s
    state.update(extra)
    return state


class TestEnforce(TestCase):
    def test_degraded_is_a_stop_condition(self):
        record = {"verdict": "degraded", "fresh": True, "health": OK_HEALTH,
                  "rung": {"observed_tok_s": 10.91, "frac_of_baseline": 0.103,
                           "baseline_tok_s": 106.0}}
        with self.assertRaises(guard.ProductionDegraded) as caught:
            guard.enforce(record, "test")
        self.assertIn("degraded", str(caught.exception))
        self.assertIn("10.91", str(caught.exception))

    def test_stalled_and_unreachable_also_stop(self):
        for verdict in ("stalled", "unreachable"):
            with self.subTest(verdict=verdict):
                record = {"verdict": verdict, "fresh": True, "health": OK_HEALTH, "rung": {}}
                with self.assertRaises(guard.ProductionDegraded):
                    guard.enforce(record, "cell start")

    def test_warn_is_recorded_not_fatal(self):
        record = {"verdict": "warn", "fresh": True, "health": OK_HEALTH, "rung": {}}
        self.assertIs(guard.enforce(record, "test"), record)
        self.assertTrue(guard.serving(record))

    def test_a_healthy_port_does_not_rescue_a_degraded_rung(self):
        # The exact 2026-09-08 failure: :8082 answered 200 throughout the collapse.
        record = {"verdict": "degraded", "fresh": True, "health": OK_HEALTH, "rung": {}}
        with self.assertRaises(guard.ProductionDegraded):
            guard.enforce(record, "test")

    def test_dead_port_is_a_stop_condition(self):
        record = {"verdict": "at_rate", "fresh": True, "health": DEAD_HEALTH}
        with self.assertRaises(guard.ProductionDegraded) as caught:
            guard.enforce(record, "test")
        self.assertIn("health unavailable", str(caught.exception))


class TestStaleness(TestCase):
    def test_a_stale_sample_is_never_a_pass(self):
        record = {"verdict": "at_rate", "fresh": False, "health": OK_HEALTH, "rung": {}}
        self.assertIs(guard.enforce(record, "test"), record)  # not damage, so not an abort
        self.assertFalse(guard.serving(record))               # but not evidence of health

    def test_unreadable_guard_is_not_serving(self):
        self.assertFalse(guard.serving({"verdict": "unreadable", "fresh": False}))

    def test_sample_without_since_is_fresh_by_definition(self):
        record = guard.sample(clock=lambda: 1000.0, **readers(rung("at_rate", age_s=9999.0)))
        self.assertTrue(record["fresh"])
        self.assertTrue(guard.serving(record))

    def test_a_reading_older_than_since_is_not_fresh(self):
        # observed 600 s ago; the cell started 60 s ago -> the reading predates the cell.
        record = guard.sample(since=940.0, clock=lambda: 1000.0,
                              **readers(rung("at_rate", age_s=600.0)))
        self.assertEqual(record["observed_epoch"], 400.0)
        self.assertFalse(record["fresh"])
        self.assertFalse(guard.serving(record))

    def test_a_reading_after_since_is_fresh(self):
        record = guard.sample(since=900.0, clock=lambda: 1000.0,
                              **readers(rung("at_rate", age_s=60.0)))
        self.assertTrue(record["fresh"])
        self.assertTrue(guard.serving(record))

    def test_missing_age_cannot_be_fresh(self):
        record = guard.sample(since=900.0, clock=lambda: 1000.0, **readers(rung("at_rate")))
        self.assertFalse(record["fresh"])

    def test_an_unreadable_reader_is_recorded_not_raised(self):
        def boom():
            raise RuntimeError("keep-alive log unreadable")

        record = guard.sample(state_reader=boom, health_reader=lambda: dict(OK_HEALTH))
        self.assertEqual(record["verdict"], "unreadable")
        self.assertFalse(record["fresh"])
        self.assertIn("keep-alive", record["error"])


def stepping_clock(values):
    """A clock that walks ``values`` and then holds its last reading."""
    state = {"i": 0}

    def clock():
        i = state["i"]
        state["i"] = min(i + 1, len(values) - 1)
        return values[i]

    return clock


class TestWaitForFresh(TestCase):
    def test_polls_until_a_sample_lands_after_since(self):
        ages = iter([600.0, 600.0, 5.0])
        slept: list[float] = []
        record = guard.wait_for_fresh(
            since=995.0, phase="after cell", timeout_s=60.0, poll_s=1.0,
            sleep=slept.append, clock=lambda: 1000.0,
            state_reader=lambda: rung("at_rate", age_s=next(ages)),
            health_reader=lambda: dict(OK_HEALTH))
        self.assertTrue(record["fresh"])
        self.assertEqual(slept, [1.0, 1.0])

    def test_a_timeout_stays_not_fresh(self):
        record = guard.wait_for_fresh(
            since=999.0, phase="after cell", timeout_s=60.0, poll_s=1.0,
            sleep=lambda _s: None, clock=stepping_clock([1000.0, 1000.0, 1100.0]),
            state_reader=lambda: rung("at_rate", age_s=600.0),
            health_reader=lambda: dict(OK_HEALTH))
        self.assertFalse(record["fresh"])
        self.assertFalse(guard.serving(record))

    def test_a_stop_verdict_raises_even_while_waiting(self):
        with self.assertRaises(guard.ProductionDegraded):
            guard.wait_for_fresh(
                since=-10.0, phase="after cell", timeout_s=1.0, poll_s=0.1,
                sleep=lambda _s: None, clock=lambda: 0.0,
                state_reader=lambda: rung("degraded", age_s=1.0),
                health_reader=lambda: dict(OK_HEALTH))
