from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from fleet import bankedfire_drain as drain
from hearth.backlog import sources as backlog_sources
from hearth.toolsurface.task_lane import _ccmeta_header


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


# A synthetic copy of the v1 arm-file SHAPE (contract bankedfire-drain-arm.v1,
# the schema _default_arm_state emitted before the scope key existed) with a
# synthetic plan id. The live hearth/var/bankedfire_drain_arm.json is never read
# by these tests -- the point is the CONTRACT, and a fixture that reaches into
# hearth/var would both create it and couple the suite to one machine's state.
_V1_ARM_FILE = {
    "contract_version": "bankedfire-drain-arm.v1",
    "armed": True,
    "authored_by": "derek",
    "reason": "supervised drain cycle",
    "updated": "2026-07-04T12:00:00Z",
    "last_dispatch_plan_id": "hearth-drain-synthetic-00000000",
}


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


class ArmScopeContractTests(TestCase):
    """v2: the arm file says WHICH sources unattended dispatch may draw from."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.arm_path = self.tmp / "arm.json"

    def test_a_v1_armed_file_with_no_scope_loads_as_disarmed(self) -> None:
        """No silent grandfathering. The v1 file authorized 'run priced
        candidates'; reading it as scope=all would widen that, without anyone
        deciding to, into 'run anything anyone drops in a directory'."""
        _write_json(self.arm_path, _V1_ARM_FILE)
        state = drain.load_arm_state(self.arm_path)
        self.assertFalse(state["armed"])
        self.assertEqual(state["reason"], drain.MISSING_SCOPE_REASON)
        self.assertIn('--arm "<reason>" --scope all', state["reason"])
        self.assertEqual(state["disarmed_by"], "missing-scope")
        self.assertIsNone(state["scope"])

    def test_loading_a_v1_file_does_not_rewrite_it(self) -> None:
        _write_json(self.arm_path, _V1_ARM_FILE)
        before = self.arm_path.read_bytes()
        drain.load_arm_state(self.arm_path)
        self.assertEqual(self.arm_path.read_bytes(), before,
                         "a read must not repair the file behind the human's back")

    def test_a_v1_file_that_is_already_disarmed_keeps_its_own_reason(self) -> None:
        _write_json(self.arm_path, {**_V1_ARM_FILE, "armed": False,
                                    "reason": "paused for the rebuild"})
        state = drain.load_arm_state(self.arm_path)
        self.assertFalse(state["armed"])
        self.assertEqual(state["reason"], "paused for the rebuild")

    def test_an_unknown_scope_loads_as_disarmed(self) -> None:
        _write_json(self.arm_path, {**_V1_ARM_FILE, "scope": "everything"})
        state = drain.load_arm_state(self.arm_path)
        self.assertFalse(state["armed"])
        self.assertEqual(state["disarmed_by"], "unknown-scope")
        self.assertIn("everything", state["reason"])

    def test_arm_without_scope_writes_scope_all_explicitly(self) -> None:
        drain.set_armed(True, "supervised cycle", path=self.arm_path)
        on_disk = json.loads(self.arm_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["scope"], "all")
        self.assertEqual(on_disk["contract_version"], "bankedfire-drain-arm.v2")
        self.assertTrue(drain.load_arm_state(self.arm_path)["armed"])

    def test_arm_with_an_explicit_scope_writes_it(self) -> None:
        drain.set_armed(True, "candidates only", path=self.arm_path, scope="candidate")
        self.assertEqual(json.loads(self.arm_path.read_text(encoding="utf-8"))["scope"],
                         "candidate")
        self.assertEqual(drain.load_arm_state(self.arm_path)["scope"], "candidate")

    def test_arming_with_an_unknown_scope_is_refused_and_writes_nothing(self) -> None:
        with self.assertRaises(ValueError):
            drain.set_armed(True, "typo", path=self.arm_path, scope="everythng")
        self.assertFalse(self.arm_path.exists())

    def test_every_scope_the_chooser_knows_can_be_armed(self) -> None:
        from hearth.backlog.select import SCOPES
        self.assertEqual(set(drain.ARM_SCOPES), set(SCOPES))
        for scope in drain.ARM_SCOPES:
            with self.subTest(scope=scope):
                drain.set_armed(True, "cycle", path=self.arm_path, scope=scope)
                self.assertEqual(drain.load_arm_state(self.arm_path)["scope"], scope)

    def test_rearming_clears_the_disarmed_by_marker(self) -> None:
        _write_json(self.arm_path, _V1_ARM_FILE)
        self.assertEqual(drain.load_arm_state(self.arm_path)["disarmed_by"],
                         "missing-scope")
        drain.set_armed(True, "re-armed after the scope contract", path=self.arm_path)
        state = drain.load_arm_state(self.arm_path)
        self.assertTrue(state["armed"])
        self.assertNotIn("disarmed_by", state)


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

    def test_skips_a_candidate_recorded_under_experiment_id(self) -> None:
        """The re-exported selector carries the fix.

        experiment_results.json rows are experiment-result.v1 and carry
        experiment_id, NOT candidate_id -- the old comparison could never skip
        one, so a completed candidate stayed dispatchable forever."""
        rows = [{"contract_version": "experiment-result.v1", "experiment_id": "bbb_high"}]
        _write_json(self.results_path, {"results": rows})
        self.assertEqual(drain.select_candidate(self.worth_path,
                                                self.results_path)["candidate_id"],
                         "ccc_mid")
        self.assertNotIn("bbb_high", {r.get("candidate_id") for r in rows},
                         "the old candidate_id-to-candidate_id comparison saw nothing")

    def test_select_candidate_is_the_backlog_source_not_a_second_copy(self) -> None:
        self.assertIs(drain.select_candidate, backlog_sources.select_candidate)


class _FakeLease:
    def __init__(self, granted: bool, occupancy: str = "available") -> None:
        self.granted = granted
        self.occupancy_at_grant = occupancy


def _fake_submit(**kwargs) -> dict:
    """Stand-in for submit_task. Accepts exactly what Brief.submit_kwargs emits."""
    return {"ok": True, "plan_id": f"hearth-{kwargs['plan_id_hint']}-abcd1234"}


def _idle_queue() -> dict:
    return {"ok": True, "queued": 0, "running": 0, "running_undispatched": 0,
            "done": 12, "hearth_queued": 0}


class _TickHarness(TestCase):
    """Shared run_tick fixture. Holds no tests of its own so subclasses do not
    re-run each other's.

    Every path is a temp path, INCLUDING the two backlog roots: the authored
    queue and the refine store default to hearth/var/..., and a tick that fell
    back to those defaults would both read live state and create hearth/var."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.arm_path = self.tmp / "arm.json"
        self.budget_path = _write_json(self.tmp / "budget.json", _GOOD_BUDGET)
        self.worth_path = _write_json(self.tmp / "worth.json", _WORTH)
        self.results_path = _write_json(self.tmp / "results.json", _EMPTY_RESULTS)
        self.queued_dir = self.tmp / "backlog" / "queued"
        self.refine_dir = self.tmp / "refine"
        self.queued_dir.mkdir(parents=True)
        self.refine_dir.mkdir(parents=True)
        self.ledger = _FakeLedger()

    def _arm(self, scope: str = "all") -> None:
        drain.set_armed(True, "test", path=self.arm_path, scope=scope)

    def _add_authored(self, name: str = "0001-authored_thing.md",
                      body: str = "An authored brief body.") -> Path:
        path = self.queued_dir / name
        path.write_text(_ccmeta_header(["cc-builder-2"], task_class="build") + body,
                        encoding="utf-8")
        return path

    def _add_promoted_refine(self, intent_id: str = "refine-alpha-11111111",
                             final: str = "The refined final text.") -> str:
        _write_json(self.refine_dir / f"{intent_id}.json",
                    {"contract_version": "commander-refine.v1", "intent_id": intent_id,
                     "idea": "refined idea", "final": final, "ok": True})
        backlog_sources.promote_refine(intent_id, refine_dir=self.refine_dir,
                                       task_class="build", requires=["docs/plan.md"],
                                       now="2026-09-06T10:00:00Z")
        return intent_id

    def _tick(self, **overrides):
        kwargs = dict(
            arm_state_path=self.arm_path,
            budget_path=self.budget_path,
            worth_path=self.worth_path,
            results_path=self.results_path,
            queued_dir=self.queued_dir,
            refine_dir=self.refine_dir,
            occupancy_check=lambda name: {"occupancy": "available"},
            acquire_lease=lambda name, pinned=False: _FakeLease(True),
            submit_task_fn=_fake_submit,
            task_status_fn=lambda plan_id: {"ok": True, "done": True},
            queue_status_fn=_idle_queue,
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
        self._arm()
        report = self._tick(occupancy_check=lambda name: {"occupancy": "busy"})
        self.assertEqual(report["reason"], "busy")

    def test_armed_unknown_occupancy_is_noop_busy(self) -> None:
        self._arm()
        report = self._tick(occupancy_check=lambda name: {"occupancy": "unknown"})
        self.assertEqual(report["reason"], "busy")

    def test_armed_no_budget_is_noop(self) -> None:
        self._arm()
        _write_json(self.budget_path, {**_GOOD_BUDGET, "suspended": True})
        report = self._tick()
        self.assertEqual(report["reason"], "no-budget")

    def test_armed_no_candidates_is_noop(self) -> None:
        self._arm()
        _write_json(self.results_path, {"results": [
            {"candidate_id": "aaa_low"}, {"candidate_id": "bbb_high"}, {"candidate_id": "ccc_mid"},
        ]})
        report = self._tick()
        self.assertEqual(report["reason"], "no-candidates")

    def test_armed_idle_budget_ok_dispatches_highest_worth(self) -> None:
        self._arm()
        report = self._tick()
        self.assertTrue(report["reason"].startswith("dispatched:"))
        # source_ref is the candidate_id verbatim -- the tick detail names WHICH
        # backlog item was chosen, which is what detail["candidate"] used to say.
        self.assertIn("bbb_high", report["detail"]["source_ref"])
        state = drain.load_arm_state(self.arm_path)
        self.assertIsNotNone(state["last_dispatch_plan_id"])

    def test_prior_dispatch_still_running_is_in_flight_noop(self) -> None:
        self._arm()
        state = drain.load_arm_state(self.arm_path)
        state["last_dispatch_plan_id"] = "hearth-drain-prior-12345678"
        drain.save_arm_state(state, self.arm_path)
        report = self._tick(task_status_fn=lambda plan_id: {"ok": True, "done": False})
        self.assertEqual(report["reason"], "in-flight")

    def test_prior_dispatch_done_clears_slot_and_allows_new_dispatch(self) -> None:
        self._arm()
        state = drain.load_arm_state(self.arm_path)
        state["last_dispatch_plan_id"] = "hearth-drain-prior-12345678"
        drain.save_arm_state(state, self.arm_path)
        report = self._tick(task_status_fn=lambda plan_id: {"ok": True, "done": True})
        self.assertTrue(report["reason"].startswith("dispatched:"))

    def test_lease_refused_is_noop_busy(self) -> None:
        self._arm()
        report = self._tick(acquire_lease=lambda name, pinned=False: _FakeLease(False, "busy"))
        self.assertEqual(report["reason"], "busy")

    def test_every_tick_ledgers_exactly_once(self) -> None:
        self._arm()
        self._tick()
        self.assertEqual(len(self.ledger.events), 1)
        self.assertEqual(self.ledger.events[0]["tool"], "bankedfire_drain.tick")

    def test_dispatch_failure_reported_as_noop(self) -> None:
        self._arm()
        report = self._tick(submit_task_fn=lambda **kw: {"ok": False, "error": "ssh timeout"})
        self.assertEqual(report["reason"], "no-op:dispatch-failed")

    def test_dispatch_is_tagged_as_a_proofing_run(self) -> None:
        # Drain dispatches are retests/experiments on idle sunk compute, not
        # production work — the task_class tag is what lets ledger consumers
        # (capacity buckets, scheduler hindsight) keep them out of real-work data.
        self._arm()
        seen: dict = {}

        def capture(**kw):
            seen.update(kw)
            return {"ok": True, "plan_id": f"hearth-{kw['plan_id_hint']}-abcd1234"}

        report = self._tick(submit_task_fn=capture)
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertEqual(seen["task_class"], "proofing")

    def test_dispatch_stamps_a_derived_est_tokens(self) -> None:
        # Token hole #1 (M3): the drain is a submit_task call site, so it must
        # stamp est_tokens itself -- derived from the brief it actually sends,
        # with the proofing allowance -- and ledger the same number.
        from hearth.toolsurface.task_lane import estimate_tokens
        self._arm()
        seen: dict = {}

        def capture(**kw):
            seen.update(kw)
            return {"ok": True, "plan_id": f"hearth-{kw['plan_id_hint']}-abcd1234",
                    "est_tokens": kw["est_tokens"], "est_tokens_source": "caller"}

        report = self._tick(submit_task_fn=capture)
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertIsInstance(seen["est_tokens"], int)
        self.assertGreater(seen["est_tokens"], 0)
        self.assertEqual(seen["est_tokens"], estimate_tokens(seen["prompt"], "proofing"))
        # detail is what _record_tick hands the ledger as `result` (the ledger
        # keeps a digest, so the tick report is the readable copy).
        self.assertEqual(report["detail"]["est_tokens"], seen["est_tokens"])
        self.assertEqual(len(self.ledger.events), 1)

    def test_failed_dispatch_still_ledgers_the_estimate(self) -> None:
        self._arm()
        report = self._tick(submit_task_fn=lambda **kw: {"ok": False, "error": "ssh timeout"})
        self.assertEqual(report["reason"], "no-op:dispatch-failed")
        self.assertGreater(report["detail"]["est_tokens"], 0)


class IdleGateTests(_TickHarness):
    """The gate that used to be decorative.

    ``check_occupancy`` returns "available" unconditionally for a backend with
    no registered probe, and the am4 probes were deleted on 2026-08-21 — so the
    old ``am4-oxen`` gate answered idle without measuring anything. The gate is
    now two questions (conductor queue, then the omen-arc probe) and both must
    say idle."""

    def _seen_backend(self) -> list:
        seen: list = []
        self._arm()
        self._tick(occupancy_check=lambda name: (seen.append(name),
                                                 {"occupancy": "available"})[1])
        return seen

    def test_the_tick_asks_occupancy_about_omen_arc_never_am4_oxen(self) -> None:
        self.assertEqual(drain.DRAIN_BACKEND, "omen-arc")
        seen = self._seen_backend()
        self.assertEqual(seen, ["omen-arc"])
        self.assertNotIn("am4-oxen", seen)

    def test_the_lease_is_taken_on_the_same_rung(self) -> None:
        self._arm()
        leased: list = []
        self._tick(acquire_lease=lambda name, pinned=False: (leased.append(name),
                                                             _FakeLease(True))[1])
        self.assertEqual(leased, ["omen-arc"])

    def test_a_running_conductor_job_is_busy(self) -> None:
        self._arm()
        report = self._tick(queue_status_fn=lambda: {**_idle_queue(), "running": 1})
        self.assertEqual(report["reason"], "busy")
        self.assertEqual(report["detail"]["busy_reason"], "conductor-queue-not-idle")

    def test_a_queued_conductor_job_is_busy(self) -> None:
        self._arm()
        report = self._tick(queue_status_fn=lambda: {**_idle_queue(), "queued": 1})
        self.assertEqual(report["reason"], "busy")

    def test_queue_status_ok_false_is_busy_queue_unreadable(self) -> None:
        self._arm()
        report = self._tick(queue_status_fn=lambda: {"ok": False, "error": "ssh exit 255"})
        self.assertEqual(report["reason"], "busy:queue-unreadable")
        self.assertIn("ssh exit 255", report["detail"]["queue_error"])

    def test_queue_status_raising_is_busy_queue_unreadable_and_never_dispatches(self) -> None:
        self._arm()
        submits: list = []

        def boom():
            raise TimeoutError("conductor unreachable")

        report = self._tick(queue_status_fn=boom,
                            submit_task_fn=lambda **kw: submits.append(kw) or {"ok": True})
        self.assertEqual(report["reason"], "busy:queue-unreadable")
        self.assertIn("TimeoutError", report["detail"]["queue_error"])
        self.assertEqual(submits, [], "an unreadable queue must never dispatch")
        self.assertEqual(len(self.ledger.events), 1)

    def test_queue_status_without_integer_counts_is_busy_queue_unreadable(self) -> None:
        """Fail closed: a shape we do not understand is not 'idle by default'."""
        for bad in ({"ok": True}, {"ok": True, "queued": None, "running": 0},
                    {"ok": True, "queued": "0", "running": "0"}, "not a dict"):
            with self.subTest(queue=bad):
                self.setUp()
                self._arm()
                report = self._tick(queue_status_fn=lambda bad=bad: bad)
                self.assertEqual(report["reason"], "busy:queue-unreadable")

    def test_both_idle_proceeds_to_a_dispatch(self) -> None:
        self._arm()
        report = self._tick()
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertEqual(report["detail"]["queue"], {"queued": 0, "running": 0})
        self.assertEqual(report["detail"]["occupancy"], "available")

    def test_queue_unreadable_is_a_benign_outcome_with_its_own_label(self) -> None:
        # ok means "this tick did its job" (it fail-closed), and the condition
        # stays visible because it carries a distinct low-cardinality outcome.
        self._arm()
        self._tick(queue_status_fn=lambda: {"ok": False, "error": "ssh exit 255"})
        event = self.ledger.events[0]
        self.assertTrue(event["ok"])
        self.assertEqual(event["outcome"], "busy:queue-unreadable")
        self.assertIsNone(event["error"])
        self.assertIn("busy:queue-unreadable", drain.BENIGN_OUTCOMES)


class BacklogSelectionTests(_TickHarness):
    """Selection now comes from hearth.backlog.select_next, scoped by the file."""

    def test_an_authored_brief_beats_the_highest_worth_candidate(self) -> None:
        self._arm("all")
        self._add_authored()
        report = self._tick()
        self.assertEqual(report["detail"]["source"], "authored")
        self.assertEqual(report["detail"]["source_ref"], "0001-authored_thing.md")
        self.assertEqual(report["detail"]["slug"], "0001-authored_thing")

    def test_a_refined_brief_beats_a_candidate(self) -> None:
        self._arm("all")
        intent_id = self._add_promoted_refine()
        report = self._tick()
        self.assertEqual(report["detail"]["source"], "refined")
        self.assertEqual(report["detail"]["source_ref"], intent_id)

    def test_an_unpromoted_refine_intent_is_not_selected(self) -> None:
        self._arm("all")
        _write_json(self.refine_dir / "refine-beta-22222222.json",
                    {"intent_id": "refine-beta-22222222", "final": "nope"})
        report = self._tick()
        self.assertEqual(report["detail"]["source"], "candidate")

    def test_the_arm_files_scope_restricts_the_selection(self) -> None:
        self._arm("candidate")
        self._add_authored()
        report = self._tick()
        self.assertEqual(report["detail"]["scope"], "candidate")
        self.assertEqual(report["detail"]["source"], "candidate")
        self.assertEqual(report["detail"]["source_ref"], "bbb_high")

    def test_a_narrow_scope_with_nothing_in_it_is_no_candidates(self) -> None:
        self._arm("authored")
        report = self._tick()
        self.assertEqual(report["reason"], "no-candidates")
        self.assertEqual(report["detail"]["backlog_counts"]["candidate"], 3,
                         "the candidate source still has work; the scope excluded it")

    def test_the_submitted_prompt_is_the_briefs_rendered_text(self) -> None:
        self._arm("all")
        self._add_authored(body="An authored brief body.")
        seen: dict = {}
        self._tick(submit_task_fn=lambda **kw: seen.update(kw) or {
            "ok": True, "plan_id": f"hearth-{kw['plan_id_hint']}-abcd1234"})
        from hearth.backlog import authored_source
        brief = authored_source(self.queued_dir).briefs[0]
        self.assertEqual(seen["prompt"], brief.render())
        self.assertEqual(seen["task_class"], "build")

    def test_requires_and_max_age_ride_the_submit_call(self) -> None:
        self._arm("refined")
        self._add_promoted_refine()
        seen: dict = {}
        self._tick(submit_task_fn=lambda **kw: seen.update(kw) or {
            "ok": True, "plan_id": f"hearth-{kw['plan_id_hint']}-abcd1234"})
        self.assertEqual(seen["requires"], ["docs/plan.md"])

    def test_a_candidate_dispatch_declares_its_proposal_deliverable(self) -> None:
        self._arm("candidate")
        seen: dict = {}
        self._tick(submit_task_fn=lambda **kw: seen.update(kw) or {
            "ok": True, "plan_id": f"hearth-{kw['plan_id_hint']}-abcd1234"})
        self.assertEqual(seen["requires"], ["proposals/bbb_high.md"])
        self.assertEqual(seen["plan_id_hint"], "candidate-bbb_high")

    def test_a_broken_authored_brief_is_skipped_without_crashing_the_tick(self) -> None:
        self._arm("all")
        (self.queued_dir / "broken.md").write_text("no header", encoding="utf-8")
        report = self._tick()
        self.assertEqual(report["detail"]["source"], "candidate",
                         "the tick falls through to the next source, it does not crash")
        self.assertEqual(len(self.ledger.events), 1)

    def test_no_candidates_reports_why_each_source_was_empty(self) -> None:
        self._arm("all")
        _write_json(self.worth_path, {"entries": []})
        (self.queued_dir / "broken.md").write_text("no header", encoding="utf-8")
        report = self._tick()
        self.assertEqual(report["reason"], "no-candidates")
        reasons = [r["reason"] for r in report["detail"]["backlog_rejected"]]
        self.assertTrue(any("unparseable" in r for r in reasons))
        self.assertEqual(report["detail"]["backlog_counts"],
                         {"authored": 0, "refined": 0, "candidate": 0})

    def test_a_v1_arm_file_produces_exactly_one_disarmed_tick_and_no_submit(self) -> None:
        _write_json(self.arm_path, _V1_ARM_FILE)
        submits: list = []
        report = self._tick(submit_task_fn=lambda **kw: submits.append(kw) or {"ok": True})
        self.assertEqual(report["reason"], "disarmed")
        self.assertEqual(report["detail"]["disarmed_by"], "missing-scope")
        self.assertEqual(report["detail"]["arm_reason"], drain.MISSING_SCOPE_REASON)
        self.assertEqual(submits, [])
        self.assertEqual(len(self.ledger.events), 1)
        self.assertEqual(self.ledger.events[0]["outcome"], "disarmed")
        self.assertTrue(self.ledger.events[0]["ok"])

    def test_an_unknown_scope_in_the_file_produces_one_disarmed_tick(self) -> None:
        _write_json(self.arm_path, {**_V1_ARM_FILE, "scope": "everything"})
        submits: list = []
        report = self._tick(submit_task_fn=lambda **kw: submits.append(kw) or {"ok": True})
        self.assertEqual(report["reason"], "disarmed")
        self.assertEqual(report["detail"]["disarmed_by"], "unknown-scope")
        self.assertEqual(submits, [])
        self.assertEqual(len(self.ledger.events), 1)

    def test_a_completed_candidate_is_skipped_by_its_experiment_id(self) -> None:
        """End-to-end proof of the selection bug: an experiment-result.v1 row is
        the only shape the results file actually holds."""
        self._arm("candidate")
        _write_json(self.results_path, {"results": [
            {"contract_version": "experiment-result.v1", "experiment_id": "bbb_high"}]})
        report = self._tick()
        self.assertEqual(report["detail"]["source_ref"], "ccc_mid")

    def test_the_tick_never_touches_the_default_backlog_roots(self) -> None:
        self._arm("all")
        self._tick()
        self.assertFalse(backlog_sources.DEFAULT_BACKLOG_ROOT.exists())


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
                    self._arm()
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
        self._arm()
        _write_json(self.budget_path, {**_GOOD_BUDGET, "suspended": True})
        self._tick()
        event = self._event()
        self.assertTrue(event["ok"])
        self.assertEqual(event["outcome"], "no-budget")

        self.setUp()
        self._arm()
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
        self._arm()
        report = self._tick()
        event = self._event()
        self.assertTrue(event["ok"])
        self.assertEqual(event["outcome"], "dispatched")
        self.assertIn("bbb_high", report["reason"])
        self.assertNotIn("bbb_high", event["outcome"])

    def test_a_real_malfunction_is_still_ok_false_and_names_itself(self) -> None:
        """The one branch that genuinely failed must stay legible as a failure,
        otherwise this change would trade a false alarm for a blind spot."""
        self._arm()
        self._tick(submit_task_fn=lambda **kw: {"ok": False, "error": "ssh timeout"})
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
            dict(queue_status_fn=lambda: {"ok": False, "error": "ssh exit 255"}),
            dict(queue_status_fn=lambda: {**_idle_queue(), "running": 3}),
            dict(submit_task_fn=lambda **kw: {"ok": False, "error": "ssh timeout"}),
            dict(submit_task_fn=lambda **kw: {"ok": False}),
        ]
        for i, overrides in enumerate(branches):
            with self.subTest(branch=i):
                self.setUp()
                self._arm()
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

    def test_the_cli_offers_exactly_the_scopes_the_chooser_knows(self) -> None:
        # The CLI is the only way a human writes the scope, so its --scope
        # choices must BE the chooser's enum, not a hand-kept copy that can drift.
        # (main() is deliberately not invoked here: every arm/disarm branch in it
        # writes the DEFAULT arm-state path -- the live hearth/var file.)
        from hearth.backlog.select import SCOPES
        self.assertEqual(set(drain.ARM_SCOPES), set(SCOPES))
        self.assertEqual(drain.DEFAULT_SCOPE, "all")
        self.assertIn(drain.DEFAULT_SCOPE, drain.ARM_SCOPES)
