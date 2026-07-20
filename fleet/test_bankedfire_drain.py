from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from fleet import bankedfire_drain as drain


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _FakeLedger:
    """Captures append() calls without touching the real ledger/index files."""
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, event: dict) -> str:
        self.events.append(event)
        return f"fake-event-{len(self.events)}"


_GOOD_BUDGET = {
    "contract_version": "operating-budget.v1",
    "budget_id": "budget_test",
    "node_id": "omen",
    "max_gpu_temp_c": 83,
    "max_power_w": 250,
    "max_fan_rpm": None,
    "unattended_dispatch_allowed": True,
    "active_hours": None,
    "reason": "test budget",
    "authored_by": "derek",
    "suspended": False,
    "suspend_reason": None,
}

_WORTH = {
    "contract_version": "candidate-worth.v1",
    "entries": [
        {"candidate_id": "aaa_low", "worth_points": 2, "reason": "r", "author": "derek"},
        {"candidate_id": "bbb_high", "worth_points": 10, "reason": "r", "author": "derek"},
        {"candidate_id": "ccc_mid", "worth_points": 5, "reason": "r", "author": "derek"},
    ],
}

_EMPTY_RESULTS = {"contract_version": "experiment-results.v1", "results": []}


class ArmStateTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.arm_path = self.tmp / "arm.json"

    def test_missing_file_defaults_disarmed(self) -> None:
        state = drain.load_arm_state(self.arm_path)
        self.assertFalse(state["armed"])

    def test_corrupt_file_defaults_disarmed(self) -> None:
        self.arm_path.write_text("{not json", encoding="utf-8")
        state = drain.load_arm_state(self.arm_path)
        self.assertFalse(state["armed"])

    def test_set_armed_persists_authored_reason(self) -> None:
        drain.set_armed(True, "supervised test cycle", authored_by="derek", path=self.arm_path)
        state = drain.load_arm_state(self.arm_path)
        self.assertTrue(state["armed"])
        self.assertEqual(state["authored_by"], "derek")
        self.assertEqual(state["reason"], "supervised test cycle")
        self.assertIsNotNone(state["updated"])

    def test_disarm_after_arm(self) -> None:
        drain.set_armed(True, "arm", path=self.arm_path)
        drain.set_armed(False, "disarm", path=self.arm_path)
        state = drain.load_arm_state(self.arm_path)
        self.assertFalse(state["armed"])
        self.assertEqual(state["reason"], "disarm")


class BudgetGateTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.budget_path = self.tmp / "budget.json"

    def test_missing_budget_no_headroom(self) -> None:
        ok, detail = drain.check_budget(self.budget_path)
        self.assertFalse(ok)
        self.assertIn("error", detail)

    def test_good_budget_has_headroom(self) -> None:
        _write_json(self.budget_path, _GOOD_BUDGET)
        ok, detail = drain.check_budget(self.budget_path)
        self.assertTrue(ok)
        self.assertFalse(detail["thermal_wear_limits_live_checked"])

    def test_suspended_budget_fails(self) -> None:
        budget = {**_GOOD_BUDGET, "suspended": True}
        _write_json(self.budget_path, budget)
        ok, detail = drain.check_budget(self.budget_path)
        self.assertFalse(ok)
        self.assertIn("suspended", detail["fail_reason"])

    def test_unattended_disallowed_fails(self) -> None:
        budget = {**_GOOD_BUDGET, "unattended_dispatch_allowed": False}
        _write_json(self.budget_path, budget)
        ok, detail = drain.check_budget(self.budget_path)
        self.assertFalse(ok)

    def test_outside_active_hours_fails(self) -> None:
        from datetime import datetime, timezone
        budget = {**_GOOD_BUDGET, "active_hours": {"start": "09:00", "end": "17:00"}}
        _write_json(self.budget_path, budget)
        outside = datetime(2026, 7, 4, 3, 0, tzinfo=timezone.utc)
        ok, detail = drain.check_budget(self.budget_path, now=outside)
        self.assertFalse(ok)

    def test_inside_active_hours_passes(self) -> None:
        from datetime import datetime, timezone
        budget = {**_GOOD_BUDGET, "active_hours": {"start": "09:00", "end": "17:00"}}
        _write_json(self.budget_path, budget)
        inside = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        ok, _detail = drain.check_budget(self.budget_path, now=inside)
        self.assertTrue(ok)

    def test_invalid_budget_schema_fails(self) -> None:
        _write_json(self.budget_path, {"contract_version": "operating-budget.v1"})
        ok, detail = drain.check_budget(self.budget_path)
        self.assertFalse(ok)
        self.assertIn("error", detail)


class CandidateSelectionTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.worth_path = self.tmp / "worth.json"
        self.results_path = self.tmp / "results.json"
        _write_json(self.worth_path, _WORTH)
        _write_json(self.results_path, _EMPTY_RESULTS)

    def test_picks_highest_worth(self) -> None:
        candidate = drain.select_candidate(self.worth_path, self.results_path)
        self.assertEqual(candidate["candidate_id"], "bbb_high")

    def test_skips_already_run_candidates(self) -> None:
        _write_json(self.results_path, {"results": [{"candidate_id": "bbb_high"}]})
        candidate = drain.select_candidate(self.worth_path, self.results_path)
        self.assertEqual(candidate["candidate_id"], "ccc_mid")

    def test_none_left_returns_none(self) -> None:
        _write_json(self.results_path, {"results": [
            {"candidate_id": "aaa_low"}, {"candidate_id": "bbb_high"}, {"candidate_id": "ccc_mid"},
        ]})
        candidate = drain.select_candidate(self.worth_path, self.results_path)
        self.assertIsNone(candidate)

    def test_missing_files_return_none(self) -> None:
        candidate = drain.select_candidate(self.tmp / "nope.json", self.tmp / "nope2.json")
        self.assertIsNone(candidate)


class _FakeLease:
    def __init__(self, granted: bool, occupancy: str = "available") -> None:
        self.granted = granted
        self.occupancy_at_grant = occupancy


class _TickHarness(TestCase):
    """Shared run_tick fixture. Holds no tests of its own so subclasses do not
    re-run each other's."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.arm_path = self.tmp / "arm.json"
        self.budget_path = _write_json(self.tmp / "budget.json", _GOOD_BUDGET)
        self.worth_path = _write_json(self.tmp / "worth.json", _WORTH)
        self.results_path = _write_json(self.tmp / "results.json", _EMPTY_RESULTS)
        self.ledger = _FakeLedger()

    def _tick(self, **overrides):
        kwargs = dict(
            arm_state_path=self.arm_path,
            budget_path=self.budget_path,
            worth_path=self.worth_path,
            results_path=self.results_path,
            occupancy_check=lambda name: {"occupancy": "available"},
            acquire_lease=lambda name, pinned=False: _FakeLease(True),
            submit_task_fn=lambda prompt, plan_id_hint=None, task_class=None: {
                "ok": True, "plan_id": f"hearth-{plan_id_hint}-abcd1234",
            },
            task_status_fn=lambda plan_id: {"ok": True, "done": True},
            ledger=self.ledger,
        )
        kwargs.update(overrides)
        return drain.run_tick(**kwargs)


class RunTickTests(_TickHarness):
    def test_disarmed_is_noop(self) -> None:
        report = self._tick()
        self.assertEqual(report["reason"], "disarmed")
        self.assertEqual(len(self.ledger.events), 1)
        self.assertEqual(self.ledger.events[0]["tool"], "bankedfire_drain.tick")

    def test_armed_busy_occupancy_is_noop(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        report = self._tick(occupancy_check=lambda name: {"occupancy": "busy"})
        self.assertEqual(report["reason"], "busy")

    def test_armed_unknown_occupancy_is_noop_busy(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        report = self._tick(occupancy_check=lambda name: {"occupancy": "unknown"})
        self.assertEqual(report["reason"], "busy")

    def test_armed_no_budget_is_noop(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        _write_json(self.budget_path, {**_GOOD_BUDGET, "suspended": True})
        report = self._tick()
        self.assertEqual(report["reason"], "no-budget")

    def test_armed_no_candidates_is_noop(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        _write_json(self.results_path, {"results": [
            {"candidate_id": "aaa_low"}, {"candidate_id": "bbb_high"}, {"candidate_id": "ccc_mid"},
        ]})
        report = self._tick()
        self.assertEqual(report["reason"], "no-candidates")

    def test_armed_idle_budget_ok_dispatches_highest_worth(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        report = self._tick()
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertIn("bbb_high", report["detail"]["candidate"]["candidate_id"])
        state = drain.load_arm_state(self.arm_path)
        self.assertIsNotNone(state["last_dispatch_plan_id"])

    def test_prior_dispatch_still_running_is_in_flight_noop(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        state = drain.load_arm_state(self.arm_path)
        state["last_dispatch_plan_id"] = "hearth-drain-prior-12345678"
        drain.save_arm_state(state, self.arm_path)
        report = self._tick(task_status_fn=lambda plan_id: {"ok": True, "done": False})
        self.assertEqual(report["reason"], "in-flight")

    def test_prior_dispatch_done_clears_slot_and_allows_new_dispatch(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        state = drain.load_arm_state(self.arm_path)
        state["last_dispatch_plan_id"] = "hearth-drain-prior-12345678"
        drain.save_arm_state(state, self.arm_path)
        report = self._tick(task_status_fn=lambda plan_id: {"ok": True, "done": True})
        self.assertTrue(report["reason"].startswith("dispatched:"))

    def test_lease_refused_is_noop_busy(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        report = self._tick(acquire_lease=lambda name, pinned=False: _FakeLease(False, "busy"))
        self.assertEqual(report["reason"], "busy")

    def test_every_tick_ledgers_exactly_once(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        self._tick()
        self.assertEqual(len(self.ledger.events), 1)
        self.assertEqual(self.ledger.events[0]["tool"], "bankedfire_drain.tick")

    def test_dispatch_failure_reported_as_noop(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        report = self._tick(submit_task_fn=lambda prompt, plan_id_hint=None, task_class=None: {
            "ok": False, "error": "ssh timeout",
        })
        self.assertEqual(report["reason"], "no-op:dispatch-failed")

    def test_dispatch_is_tagged_as_a_proofing_run(self) -> None:
        # Drain dispatches are retests/experiments on idle sunk compute, not
        # production work — the task_class tag is what lets ledger consumers
        # (capacity buckets, scheduler hindsight) keep them out of real-work data.
        drain.set_armed(True, "test", path=self.arm_path)
        seen: dict = {}

        def capture(prompt, plan_id_hint=None, task_class=None):
            seen["task_class"] = task_class
            return {"ok": True, "plan_id": f"hearth-{plan_id_hint}-abcd1234"}

        report = self._tick(submit_task_fn=capture)
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertEqual(seen["task_class"], "proofing")


class LedgerSemanticsTests(_TickHarness):
    """`ok` means "this tick did its job", NOT "this tick dispatched".

    The drain is armed and fires every 1800s; on an idle fleet a benign no-op is
    the overwhelmingly common branch. Keying ok on "dispatched" made 592 healthy
    ticks project ok_rate 0.0084 into knowledge/capacity.json, which reads as a
    catastrophic outage and already produced one wrong diagnosis."""

    def _event(self):
        self.assertEqual(len(self.ledger.events), 1)
        return self.ledger.events[0]

    def test_benign_noops_are_ok_true_and_name_their_branch(self) -> None:
        cases = {
            "disarmed": dict(),
            "busy": dict(occupancy_check=lambda name: {"occupancy": "busy"}),
            "in-flight": dict(task_status_fn=lambda plan_id: {"ok": True, "done": False}),
        }
        for outcome, overrides in cases.items():
            with self.subTest(outcome=outcome):
                self.setUp()
                if outcome != "disarmed":
                    drain.set_armed(True, "test", path=self.arm_path)
                if outcome == "in-flight":
                    state = drain.load_arm_state(self.arm_path)
                    state["last_dispatch_plan_id"] = "hearth-prior-run"
                    drain.save_arm_state(state, self.arm_path)
                self._tick(**overrides)
                event = self._event()
                self.assertTrue(event["ok"], f"{outcome} is a healthy no-op")
                self.assertEqual(event["outcome"], outcome)
                self.assertIsNone(event["error"], "a benign no-op names no error")

    def test_no_budget_and_no_candidates_are_ok_true(self) -> None:
        drain.set_armed(True, "test", path=self.arm_path)
        _write_json(self.budget_path, {**_GOOD_BUDGET, "suspended": True})
        self._tick()
        event = self._event()
        self.assertTrue(event["ok"])
        self.assertEqual(event["outcome"], "no-budget")

        self.setUp()
        drain.set_armed(True, "test", path=self.arm_path)
        _write_json(self.results_path, {"results": [
            {"candidate_id": "aaa_low"}, {"candidate_id": "bbb_high"},
            {"candidate_id": "ccc_mid"},
        ]})
        self._tick()
        event = self._event()
        self.assertTrue(event["ok"])
        self.assertEqual(event["outcome"], "no-candidates")

    def test_dispatch_is_ok_true_with_an_id_free_outcome_label(self) -> None:
        """`outcome` is what the projection buckets on, so it must stay
        low-cardinality -- the plan_id belongs in `reason`, not here."""
        drain.set_armed(True, "test", path=self.arm_path)
        report = self._tick()
        event = self._event()
        self.assertTrue(event["ok"])
        self.assertEqual(event["outcome"], "dispatched")
        self.assertIn("bbb_high", report["reason"])
        self.assertNotIn("bbb_high", event["outcome"])

    def test_a_real_malfunction_is_still_ok_false_and_names_itself(self) -> None:
        """The one branch that genuinely failed must stay legible as a failure,
        otherwise this change would trade a false alarm for a blind spot."""
        drain.set_armed(True, "test", path=self.arm_path)
        self._tick(submit_task_fn=lambda prompt, plan_id_hint=None, task_class=None: {
            "ok": False, "error": "ssh timeout",
        })
        event = self._event()
        self.assertFalse(event["ok"])
        self.assertEqual(event["outcome"], "dispatch-failed")
        self.assertEqual(event["error"], "ssh timeout")

    def test_no_tick_ever_emits_the_incoherent_ok_false_without_error(self) -> None:
        """The precise defect signature: 592 of 597 historical drain events said
        ok:false while naming no error. No branch may reproduce it."""
        branches = [
            dict(),
            dict(occupancy_check=lambda name: {"occupancy": "busy"}),
            dict(occupancy_check=lambda name: {"occupancy": "unknown"}),
            dict(submit_task_fn=lambda prompt, plan_id_hint=None, task_class=None: {
                "ok": False, "error": "ssh timeout"}),
            dict(submit_task_fn=lambda prompt, plan_id_hint=None, task_class=None: {"ok": False}),
        ]
        for i, overrides in enumerate(branches):
            with self.subTest(branch=i):
                self.setUp()
                drain.set_armed(True, "test", path=self.arm_path)
                self._tick(**overrides)
                event = self._event()
                if not event["ok"]:
                    self.assertTrue(event["error"],
                                    "ok:false must always name its failure")


class CLITests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.arm_path = self.tmp / "arm.json"

    def test_arm_and_disarm_via_helpers_round_trip(self) -> None:
        drain.set_armed(True, "cli arm", authored_by="derek", path=self.arm_path)
        self.assertTrue(drain.load_arm_state(self.arm_path)["armed"])
        drain.set_armed(False, "cli disarm", authored_by="derek", path=self.arm_path)
        self.assertFalse(drain.load_arm_state(self.arm_path)["armed"])
