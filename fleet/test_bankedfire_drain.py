from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase, mock

from fleet import bankedfire_drain as drain
from hearth.backlog import dispatch as backlog_dispatch
from hearth.backlog import sources as backlog_sources
from hearth.toolsurface.task_lane import _ccmeta_header
from tools.workflow.validate_events import validate_event, validate_file


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

# B-04-R1 fixture. The ids above (`bbb_high`, ...) are shorthand and name NO
# combo, which is now the "no-combo" branch -- correct for them, but it means
# they can never exercise the capacity-artifact path. This table is the shape a
# real priced candidate has: `prefer_validation:<builder>|<model>|<backend>`
# (tools/workflow/project_experiments.py L290 + `_combo`), with the SAME builder
# the scripted conductor reports as the winner, which is the condition for the
# artifact to be written at all. Worth ordering is preserved so the same tests
# still pick the high one first, then `ccc_mid`.
_COMBO_BUILDER = "cc-builder-2"
_COMBO_MODEL = "qwen3-30b-a3b"
_COMBO_BACKEND = "omen-arc"
_COMBO_CANDIDATE = (f"prefer_validation:{_COMBO_BUILDER}|{_COMBO_MODEL}"
                    f"|{_COMBO_BACKEND}")
_COMBO_WORTH = {
    "contract_version": "candidate-worth.v1",
    "entries": [
        {"candidate_id": _COMBO_CANDIDATE, "worth_points": 10,
         "reason": "r", "author": "derek"},
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

    Every path is a temp path, INCLUDING all four backlog roots and the corpus:
    they default to hearth/var/... and <repo>/runs, and a tick that fell back to
    those defaults would read live state, create hearth/var, and (since B-04)
    write a dispatch record into the repo's own event corpus."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.arm_path = self.tmp / "arm.json"
        self.budget_path = _write_json(self.tmp / "budget.json", _GOOD_BUDGET)
        self.worth_path = _write_json(self.tmp / "worth.json", _WORTH)
        self.results_path = _write_json(self.tmp / "results.json", _EMPTY_RESULTS)
        self.queued_dir = self.tmp / "backlog" / "queued"
        self.dispatched_dir = self.tmp / "backlog" / "dispatched"
        self.done_dir = self.tmp / "backlog" / "done"
        self.refine_dir = self.tmp / "refine"
        self.corpus_root = self.tmp / "corpus"
        self.queued_dir.mkdir(parents=True)
        self.refine_dir.mkdir(parents=True)
        self.ledger = _FakeLedger()

    def _arm(self, scope: str = "all") -> None:
        drain.set_armed(True, "test", path=self.arm_path, scope=scope)

    def _use_combo_worth(self) -> str:
        """Swap in the combo-shaped worth table (see _COMBO_WORTH) and return
        the candidate id, which is also the experiment_id and the artifact key.

        Used by every test that asserts a capacity artifact IS written: the
        shorthand fixture ids name no combo, so under B-04-R1 they cannot.
        """
        _write_json(self.worth_path, _COMBO_WORTH)
        return _COMBO_CANDIDATE

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
            dispatched_dir=self.dispatched_dir,
            done_dir=self.done_dir,
            refine_dir=self.refine_dir,
            corpus_root=self.corpus_root,
            occupancy_check=lambda name: {"occupancy": "available"},
            acquire_lease=lambda name, pinned=False: _FakeLease(True),
            submit_task_fn=_fake_submit,
            task_status_fn=lambda plan_id: {"ok": True, "done": True},
            queue_status_fn=_idle_queue,
            ledger=self.ledger,
        )
        kwargs.update(overrides)
        return drain.run_tick(**kwargs)

    # -- shorthands the B-04 suites share ----------------------------------

    def _slot(self):
        return drain.load_arm_state(self.arm_path)["in_flight"]

    def _events(self, dispatch_id: str) -> list[dict]:
        path = backlog_dispatch.events_path(self.corpus_root, dispatch_id)
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _run_dirs(self) -> list[Path]:
        root = self.corpus_root / "runs" / backlog_dispatch.RUN_NAMESPACE
        return sorted(p for p in root.glob("*") if p.is_dir()) if root.is_dir() else []

    def _published_plans(self) -> list[Path]:
        return sorted((self.corpus_root / "runs").rglob("artifacts/experiments/*.json"))

    def _pending_plans(self) -> list[Path]:
        return sorted((self.corpus_root / "runs").rglob("artifacts/experiments/*.pending"))


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
        # The slot is a RECORD now, not a bare plan-id string: same assertion
        # ("something is in flight and we know what"), against the v2 contract.
        slot = self._slot()
        self.assertIsNotNone(slot)
        self.assertIsNotNone(slot["plan_id"])
        self.assertEqual(slot["experiment_id"], "bbb_high")

    def test_prior_dispatch_still_running_is_in_flight_noop(self) -> None:
        """A pre-B-04 arm file (bare last_dispatch_plan_id) still blocks.

        Adopting the legacy string rather than dropping it is the safe read: a
        forgotten slot means a second dispatch beside a live run."""
        self._arm()
        state = drain.load_arm_state(self.arm_path)
        state["last_dispatch_plan_id"] = "hearth-drain-prior-12345678"
        drain.save_arm_state(state, self.arm_path)
        report = self._tick(task_status_fn=lambda plan_id: {"ok": True, "done": False})
        self.assertEqual(report["reason"], "in-flight")

    def test_prior_dispatch_done_is_written_back_first_then_the_next_tick_dispatches(self) -> None:
        """A finished run is recorded BEFORE anything new is chosen.

        This is the tick that used to dispatch in the same breath as clearing
        the slot. It must not: the selection reads knowledge/, and knowledge/ is
        rebuilt from the corpus this write-back has only just appended to -- so
        dispatching in the same tick would re-pick the candidate that just
        finished. Same guarantee as before ("a finished run does not block the
        lane"), now spread across two ticks with the record in between."""
        self._arm()
        state = drain.load_arm_state(self.arm_path)
        state["last_dispatch_plan_id"] = "hearth-drain-prior-12345678"
        drain.save_arm_state(state, self.arm_path)
        first = self._tick(task_status_fn=lambda plan_id: {"ok": True, "done": True})
        self.assertTrue(first["reason"].startswith("observed:"))
        self.assertTrue(first["detail"]["legacy_slot"],
                        "a legacy slot has no run dir to write back into")
        self.assertIsNone(self._slot())
        second = self._tick()
        self.assertTrue(second["reason"].startswith("dispatched:"))

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

    def test_the_submitted_prompt_is_the_briefs_body_not_its_rendered_text(self) -> None:
        """B-03 sent brief.render(), so the inbox file carried TWO CCMETA
        headers (submit_task prepends its own). The conductor's _extract_ccmeta
        uses .search, so the first one -- submit_task's -- won and the brief's
        became inert body text. The body is what goes on the wire now."""
        self._arm("all")
        from hearth.backlog import authored_source
        self._add_authored(body="An authored brief body.")
        brief = authored_source(self.queued_dir).briefs[0]
        seen: dict = {}
        self._tick(submit_task_fn=lambda **kw: seen.update(kw) or {
            "ok": True, "plan_id": f"hearth-{kw['plan_id_hint']}-abcd1234"})
        self.assertEqual(seen["prompt"], brief.body)
        self.assertNotIn("<!-- CCMETA", seen["prompt"])
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


class _CycleHarness(_TickHarness):
    """One completed drain cycle, driven tick by tick with a scripted conductor.

    ``self.status`` is what ``task_status`` will say next; the tests move it
    from "still running" to "done" the way the conductor would."""

    def setUp(self) -> None:
        super().setUp()
        self.status: dict = {"ok": True, "done": False}
        self.submits: list[dict] = []

    def _submit(self, **kwargs) -> dict:
        self.submits.append(kwargs)
        plan_id = f"hearth-{kwargs['plan_id_hint'].lower().replace('_', '-')}-abcd1234"
        return {"ok": True, "plan_id": plan_id,
                "inbox_path": f"~/work/commandcenter/inbox/{plan_id}.md",
                "result_path": f"~/work/commandcenter/runs/{plan_id}/result.json"}

    def _tick(self, **overrides):
        kwargs = dict(submit_task_fn=self._submit,
                      task_status_fn=lambda plan_id: dict(self.status))
        kwargs.update(overrides)
        return super()._tick(**kwargs)

    def _finish_run(self, ok: bool = True, winner: str = "cc-builder-2") -> None:
        self.status = {"ok": True, "done": True,
                       "plan_id": self._slot()["plan_id"],
                       "result_path": self._slot()["result_path"],
                       "result": {"ok": ok, "winner": winner}}


class DispatchWriteBackTests(_CycleHarness):
    """The full cycle: dispatch -> in-flight -> observation -> next brief."""

    def test_one_cycle_writes_a_plan_an_event_a_slot_and_one_submit(self) -> None:
        self._arm("candidate")
        report = self._tick()
        self.assertTrue(report["reason"].startswith("dispatched:"))
        dispatch_id = report["detail"]["dispatch_id"]

        # the plan, published (the dispatch reached the conductor)
        plan_path = backlog_dispatch.plan_artifact_path(
            self.corpus_root, dispatch_id, "bbb_high")
        self.assertTrue(plan_path.is_file())
        self.assertEqual(self._pending_plans(), [])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["experiment_id"], "bbb_high")
        self.assertEqual(plan["derived_from_candidate"], "bbb_high")
        self.assertEqual(plan["contract_version"], "experiment-plan.v1")

        # the dispatch event, referencing it
        events = self._events(dispatch_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "work.accepted")
        self.assertEqual(events[0]["run_id"], f"hearth-drain/{dispatch_id}")
        self.assertEqual(events[0]["artifact_refs"][0]["artifact_type"],
                         "experiment_plan")

        # the slot, and exactly one submit
        slot = self._slot()
        self.assertEqual(slot["experiment_id"], "bbb_high")
        self.assertTrue(slot["submitted"])
        self.assertTrue(slot["inbox_path"])
        self.assertEqual(len(self.submits), 1)

    def test_the_second_tick_is_a_no_op_that_appends_nothing(self) -> None:
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        before = self._events(dispatch_id)
        report = self._tick()
        self.assertEqual(report["reason"], "in-flight")
        self.assertEqual(self._events(dispatch_id), before)
        self.assertEqual(len(self.submits), 1, "an in-flight tick must not dispatch")

    def test_the_finishing_tick_writes_one_observation_and_frees_the_slot(self) -> None:
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run(ok=True, winner="cc-builder-2")
        report = self._tick()

        self.assertEqual(report["reason"], "observed:succeeded")
        self.assertEqual(report["detail"]["observation_outcome"], "succeeded")
        events = self._events(dispatch_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["event_type"], "retrospective.created")
        self.assertEqual(events[1]["outcome"], "succeeded")
        self.assertEqual(events[1]["decision_id"], events[0]["decision_id"])
        self.assertEqual(events[1]["payload"]["experiment_id"], "bbb_high")
        self.assertTrue(backlog_dispatch.observed_marker_path(
            self.corpus_root, dispatch_id, "bbb_high").is_file())
        self.assertIsNone(self._slot())
        self.assertEqual(len(self.submits), 1, "a write-back tick never dispatches")

    def test_a_named_winner_also_lands_a_capacity_observation(self) -> None:
        # FIXTURE CHANGE (B-04-R1): the combo-shaped candidate, whose builder is
        # the winner the scripted conductor reports. Every assertion below is
        # the original one plus the two fields the repair adds.
        experiment_id = self._use_combo_worth()
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run(ok=True, winner=_COMBO_BUILDER)
        self._tick()
        path = backlog_dispatch.observation_artifact_path(
            self.corpus_root, dispatch_id, experiment_id)
        self.assertTrue(path.is_file())
        observation = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(observation["builder_id"], _COMBO_BUILDER)
        self.assertEqual(observation["outcome"], "success")
        self.assertEqual(observation["decision_id"], f"dec_{dispatch_id}")
        # The repair: a real combo, never None and never "unknown".
        self.assertEqual(observation["model_id"], _COMBO_MODEL)
        self.assertEqual(observation["backend"], _COMBO_BACKEND)
        self.assertNotIn(backlog_dispatch.CAPACITY_SKIP_FIELD,
                         self._events(dispatch_id)[1]["payload"])

    def test_a_run_with_no_winner_records_the_outcome_and_no_capacity_evidence(self) -> None:
        """No builder was named, so there is nothing to file evidence against.
        The outcome is still on the record -- as an event, not as a capacity
        observation about a combo nobody ran."""
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run(ok=True, winner=None)
        report = self._tick()
        self.assertEqual(report["reason"], "observed:no_winner")
        self.assertFalse(backlog_dispatch.observation_artifact_path(
            self.corpus_root, dispatch_id, "bbb_high").is_file())
        self.assertEqual(self._events(dispatch_id)[1]["outcome"], "no_winner")
        # B-04-R1: and the record now says WHY there is no artifact. bbb_high
        # names no combo, so the reason is no-combo, not the winner.
        self.assertEqual(self._events(dispatch_id)[1]["payload"]
                         [backlog_dispatch.CAPACITY_SKIP_FIELD],
                         backlog_dispatch.CAPACITY_SKIP_NO_COMBO)

    def test_a_failed_run_is_recorded_as_failed(self) -> None:
        # FIXTURE CHANGE (B-04-R1): combo-shaped candidate, and the winner is
        # now the combo's builder -- a run that FAILED on the combo under test
        # is still evidence about that combo, which is what this asserts.
        experiment_id = self._use_combo_worth()
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run(ok=False, winner=_COMBO_BUILDER)
        report = self._tick()
        self.assertEqual(report["reason"], "observed:failed")
        observation = json.loads(backlog_dispatch.observation_artifact_path(
            self.corpus_root, dispatch_id, experiment_id).read_text(encoding="utf-8"))
        self.assertEqual(observation["outcome"], "error")
        self.assertEqual(observation["model_id"], _COMBO_MODEL)
        self.assertEqual(observation["backend"], _COMBO_BACKEND)

    def test_a_winner_that_is_not_the_candidates_builder_files_no_evidence(self) -> None:
        """B-04-R1: the run happened, but on a machine the candidate is not
        asking about. Filing it against this combo would be a lie; the event
        records the outcome, the winner, and the reason there is no artifact."""
        experiment_id = self._use_combo_worth()
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run(ok=True, winner="cc-builder-9")
        report = self._tick()
        self.assertEqual(report["reason"], "observed:succeeded")
        self.assertFalse(backlog_dispatch.observation_artifact_path(
            self.corpus_root, dispatch_id, experiment_id).is_file())
        payload = self._events(dispatch_id)[1]["payload"]
        self.assertEqual(payload[backlog_dispatch.CAPACITY_SKIP_FIELD],
                         backlog_dispatch.CAPACITY_SKIP_WINNER_MISMATCH)
        self.assertEqual(payload["winner"], "cc-builder-9")
        self.assertEqual(payload["result_ok"], True)
        self.assertTrue(payload["result_path"])
        self.assertEqual(report["detail"][backlog_dispatch.CAPACITY_SKIP_FIELD],
                         backlog_dispatch.CAPACITY_SKIP_WINNER_MISMATCH)
        # The candidate is still SUPPRESSED: the experiment ran, and the plan
        # is published, so a result row exists (with outcome no_observation).
        self.assertEqual(self._slot(), None)
        self.assertEqual(len(self._published_plans()), 1)

    def test_an_authored_brief_files_no_capacity_evidence_at_all(self) -> None:
        """B-04-R1: an authored brief is a human work item, not a combo
        experiment. There is nothing for a capacity observation to be about,
        whoever won it."""
        self._arm("authored")
        self._add_authored()
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run(ok=True, winner="cc-builder-2")
        report = self._tick()
        self.assertEqual(report["reason"], "observed:succeeded")
        self.assertFalse(backlog_dispatch.observation_artifact_path(
            self.corpus_root, dispatch_id, "authored:0001-authored_thing").is_file())
        self.assertEqual(self._events(dispatch_id)[1]["payload"]
                         [backlog_dispatch.CAPACITY_SKIP_FIELD],
                         backlog_dispatch.CAPACITY_SKIP_NO_COMBO)
        plan = json.loads(self._published_plans()[0].read_text(encoding="utf-8"))
        self.assertIsNone(plan["subject"]["builder_id"])
        self.assertIsNone(plan["subject"]["model_id"])
        self.assertIsNone(plan["subject"]["backend"])

    def test_a_candidate_plan_subject_is_the_combo_the_candidate_names(self) -> None:
        """B-04-R1 scope 2, end to end: the plan the tick actually writes."""
        self._use_combo_worth()
        self._arm("candidate")
        self._tick()
        plan = json.loads(self._published_plans()[0].read_text(encoding="utf-8"))
        self.assertEqual(plan["subject"]["builder_id"], _COMBO_BUILDER)
        self.assertEqual(plan["subject"]["model_id"], _COMBO_MODEL)
        self.assertEqual(plan["subject"]["backend"], _COMBO_BACKEND)
        self.assertEqual(plan["experiment_type"], "prefer_validation")
        self.assertEqual(plan["derived_from_candidate"], _COMBO_CANDIDATE)

    def test_re_running_a_finished_cycle_appends_nothing_new(self) -> None:
        """Idempotency: the tick after the write-back starts a fresh dispatch,
        and the finished run's own dir is never written to again."""
        self._arm("candidate")
        first = self._tick()["detail"]["dispatch_id"]
        self._finish_run()
        self._tick()
        after_write_back = self._events(first)
        self.status = {"ok": True, "done": False}
        self._tick()
        self.assertEqual(self._events(first), after_write_back)

    def test_the_fourth_tick_selects_the_next_brief_once_the_rebuild_has_run(self) -> None:
        """The suppression is not magic: the drain writes the corpus, the
        rebuild turns it into an experiment_results row, and only THEN is the
        finished candidate ineligible. Here the rebuild step is stood in for by
        writing the row the projection produces; SuppressionLoopTests runs the
        real projector over the real events."""
        self._arm("candidate")
        self._tick()
        self._finish_run()
        self._tick()
        _write_json(self.results_path, {"results": [
            {"contract_version": "experiment-result.v1", "experiment_id": "bbb_high"}]})
        report = self._tick()
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertEqual(report["detail"]["source_ref"], "ccc_mid")

    def test_an_authored_brief_moves_queued_to_dispatched_to_done(self) -> None:
        self._arm("authored")
        path = self._add_authored()
        report = self._tick()
        self.assertEqual(report["detail"]["backlog_move"], "moved")
        self.assertFalse(path.exists())
        plan_id = self._slot()["plan_id"]
        self.assertTrue((self.dispatched_dir / f"{plan_id}.md").is_file())
        self._finish_run()
        self._tick()
        self.assertTrue((self.done_dir / f"{plan_id}.md").is_file())

    def test_a_candidate_dispatch_moves_no_files(self) -> None:
        self._arm("candidate")
        self._tick()
        self.assertFalse(self.dispatched_dir.exists())

    def test_every_appended_event_validates_against_the_corpus_schema(self) -> None:
        for scope, setup in (("candidate", lambda: None),
                             ("authored", self._add_authored),
                             ("refined", self._add_promoted_refine)):
            with self.subTest(scope=scope):
                self.setUp()
                self._arm(scope)
                setup()
                dispatch_id = self._tick()["detail"]["dispatch_id"]
                self._finish_run()
                self._tick()
                path = backlog_dispatch.events_path(self.corpus_root, dispatch_id)
                self.assertEqual(validate_file(path), [])
                for event in self._events(dispatch_id):
                    validate_event(event)

    def test_the_experiment_id_namespaces_authored_and_refined_briefs(self) -> None:
        self._arm("authored")
        self._add_authored()
        self.assertEqual(self._tick()["detail"]["experiment_id"],
                         "authored:0001-authored_thing")
        self.setUp()
        self._arm("refined")
        intent_id = self._add_promoted_refine()
        self.assertEqual(self._tick()["detail"]["experiment_id"],
                         f"refined:{intent_id}")

    def test_exactly_one_ccmeta_header_reaches_the_inbox(self) -> None:
        """The B-03 double-header bug, stated as the property that fixes it:
        the body submit_task writes must contain exactly one header, and that
        header must carry the brief's own requires/max_age_s."""
        self._arm("refined")
        self._add_promoted_refine()
        self._tick()
        kwargs = self.submits[0]
        body = _ccmeta_header(
            kwargs["builders"] or ["cc-builder-2", "cc-builder-3"],
            task_class=kwargs["task_class"], est_tokens=kwargs["est_tokens"],
            est_tokens_source="caller", requires=kwargs["requires"],
            max_age_s=kwargs["max_age_s"]) + kwargs["prompt"]
        self.assertEqual(body.count("<!-- CCMETA"), 1)
        header = json.loads(body.split("<!-- CCMETA\n", 1)[1].split("\n-->", 1)[0])
        self.assertEqual(header["requires"], ["docs/plan.md"])
        self.assertEqual(kwargs["requires"], ["docs/plan.md"])
        self.assertIsNone(kwargs["max_age_s"])
        self.assertNotIn("max_age_s", header)

    def test_the_tick_writes_nothing_outside_the_corpus_and_arm_roots(self) -> None:
        self._arm("candidate")
        self._tick()
        self.assertFalse(backlog_sources.DEFAULT_BACKLOG_ROOT.exists())
        self.assertFalse((drain.DEFAULT_CORPUS_ROOT / "runs"
                          / backlog_dispatch.RUN_NAMESPACE).exists())


class CrashMatrixTests(_CycleHarness):
    """Persist-first, proven by killing the tick at each numbered point.

    Every case is: crash at the point, then run the NEXT tick against the state
    that is actually on disk, and assert what it reconstructs. No sleeps, no
    timing -- ``crash_after`` raises deterministically."""

    def _crash(self, point: str, **overrides) -> None:
        with self.assertRaises(drain.InjectedCrash):
            self._tick(crash_after=point, **overrides)

    def test_the_crash_points_are_the_ones_the_tick_actually_offers(self) -> None:
        self.assertEqual(len(set(drain.CRASH_POINTS)), len(drain.CRASH_POINTS))

    def test_1_after_lease_nothing_is_written_and_the_next_tick_dispatches(self) -> None:
        self._arm("candidate")
        self._crash("lease")
        self.assertIsNone(self._slot())
        self.assertEqual(self._run_dirs(), [])
        self.assertEqual(self.submits, [])
        report = self._tick()
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertEqual(len(self._published_plans()), 1)
        self.assertEqual(len(self.submits), 1)

    def test_2_after_the_plan_artifact_the_orphan_is_inert(self) -> None:
        """RULE: an unpublished plan in a run dir with no slot is neither
        resumed nor deleted. It is the record that an attempt began, and it is
        invisible to the projection (the ref it would be found by was never
        written), so it cannot suppress its own candidate."""
        self._arm("candidate")
        self._crash("plan_artifact")
        self.assertIsNone(self._slot())
        self.assertEqual(len(self._pending_plans()), 1)
        self.assertEqual(self._published_plans(), [])
        self.assertEqual(self.submits, [])
        report = self._tick()
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertEqual(len(self._published_plans()), 1,
                         "exactly one plan is ever published for one dispatch")
        self.assertEqual(len(self.submits), 1)

    def test_3_after_the_dispatch_event_there_is_still_only_one_submit(self) -> None:
        self._arm("candidate")
        self._crash("dispatch_event")
        orphan = self._run_dirs()[0].name
        self.assertEqual(len(self._events(orphan)), 1)
        self.assertIsNone(self._slot())
        self.assertEqual(self.submits, [])

        report = self._tick()
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertEqual(len(self.submits), 1,
                         "the crashed attempt never submitted, so the total is one")
        self.assertEqual([kw["plan_id_hint"] for kw in self.submits],
                         ["candidate-bbb_high"])
        # The orphan run never published its plan, so the projection sees no
        # experiment for it and the candidate is not falsely suppressed.
        self.assertEqual(len(self._published_plans()), 1)
        self.assertNotIn(orphan, str(self._published_plans()[0]))

    def test_4_after_the_slot_the_next_tick_reconciles_never_submitted(self) -> None:
        self._arm("candidate")
        self._crash("slot")
        slot = self._slot()
        self.assertIsNotNone(slot)
        self.assertEqual(backlog_dispatch.in_flight_phase(slot),
                         backlog_dispatch.PHASE_PREPARED)
        dispatch_id = slot["dispatch_id"]

        report = self._tick()
        self.assertEqual(report["reason"], "reconciled:never-submitted")
        self.assertIsNone(self._slot())
        self.assertEqual(self.submits, [], "submit count 0 for that experiment")
        events = self._events(dispatch_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["outcome"], "reconciled_never_submitted")
        self.assertEqual(self._published_plans(), [],
                         "a dispatch that never happened must not suppress itself")
        # ...and the candidate is still selectable on the tick after that.
        self.assertEqual(self._tick()["detail"]["source_ref"], "bbb_high")

    def test_4b_after_the_attempt_flag_the_lane_fails_closed(self) -> None:
        """The one genuinely ambiguous point. The submit call was entered and
        never returned a plan_id, so whether the inbox write landed is unknown
        and no primitive can answer it. Holding the slot is the only safe
        answer: a second dispatch beside a live run is the failure this whole
        item exists to prevent."""
        self._arm("candidate")
        self._crash("submit_attempt")
        self.assertEqual(backlog_dispatch.in_flight_phase(self._slot()),
                         backlog_dispatch.PHASE_ATTEMPTED)
        for _ in range(3):
            report = self._tick()
            self.assertEqual(report["reason"], "in-flight:submit-outcome-unknown")
            self.assertIsNotNone(self._slot())
            self.assertEqual(self.submits, [])

    def test_5_after_submit_the_slot_holds_rather_than_re_dispatching(self) -> None:
        self._arm("candidate")
        self._crash("submit")
        self.assertEqual(len(self.submits), 1, "the brief DID reach the conductor")
        self.assertEqual(backlog_dispatch.in_flight_phase(self._slot()),
                         backlog_dispatch.PHASE_ATTEMPTED)
        report = self._tick()
        self.assertEqual(report["reason"], "in-flight:submit-outcome-unknown")
        self.assertEqual(len(self.submits), 1, "never a second dispatch")

    def test_6_after_the_slot_update_the_run_is_simply_in_flight(self) -> None:
        self._arm("candidate")
        self._crash("slot_submitted")
        slot = self._slot()
        self.assertEqual(backlog_dispatch.in_flight_phase(slot),
                         backlog_dispatch.PHASE_SUBMITTED)
        self.assertTrue(slot["inbox_path"])
        self.assertEqual(self._tick()["reason"], "in-flight")
        self.assertEqual(len(self.submits), 1)

    def test_6_the_unpublished_plan_is_published_by_the_write_back(self) -> None:
        self._arm("candidate")
        self._crash("slot_submitted")
        self.assertEqual(len(self._pending_plans()), 1)
        self.assertEqual(self._published_plans(), [])
        self._finish_run()
        report = self._tick()
        self.assertEqual(report["reason"], "observed:succeeded")
        self.assertTrue(report["detail"]["plan_published"])
        self.assertEqual(len(self._published_plans()), 1)
        self.assertEqual(self._pending_plans(), [])

    def test_7_after_submit_the_authored_file_moves_on_the_next_opportunity(self) -> None:
        self._arm("authored")
        path = self._add_authored()
        self._crash("publish")
        self.assertTrue(path.exists(), "mark_dispatched never ran")
        plan_id = self._slot()["plan_id"]
        self._finish_run()
        report = self._tick()
        self.assertEqual(report["detail"]["backlog_move"],
                         {"dispatched": "moved", "done": "moved"})
        self.assertFalse(path.exists())
        self.assertTrue((self.done_dir / f"{plan_id}.md").is_file())

    def test_8_a_crash_after_the_observation_never_appends_it_twice(self) -> None:
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run()
        self._crash("observation")
        self.assertEqual(len(self._events(dispatch_id)), 2)
        self.assertFalse(backlog_dispatch.observed_marker_path(
            self.corpus_root, dispatch_id, "bbb_high").is_file(),
            "the marker was not reached")
        self.assertIsNotNone(self._slot(), "the slot is cleared last")

        report = self._tick()
        self.assertEqual(report["reason"], "observed:succeeded")
        self.assertTrue(report["detail"]["observation_already_recorded"])
        self.assertEqual(len(self._events(dispatch_id)), 2,
                         "the deterministic event_id is the real guard")
        self.assertIsNone(self._slot())

    def test_8b_a_replayed_write_back_never_rewrites_the_capacity_artifact(self) -> None:
        """B-04-R1: the capacity artifact is written INSIDE the same guarded
        block as the observation event, before the append. A crash after the
        append must leave exactly one artifact, byte-identical on replay --
        otherwise a retried write-back would file the same evidence twice."""
        experiment_id = self._use_combo_worth()
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run(ok=True, winner=_COMBO_BUILDER)
        self._crash("observation")
        path = backlog_dispatch.observation_artifact_path(
            self.corpus_root, dispatch_id, experiment_id)
        self.assertTrue(path.is_file())
        before = path.read_bytes()

        report = self._tick()
        self.assertEqual(report["reason"], "observed:succeeded")
        self.assertTrue(report["detail"]["observation_already_recorded"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self._events(dispatch_id)), 2)
        self.assertEqual(
            len(list((self.corpus_root / "runs").rglob("artifacts/observations/*.json"))),
            1)

    def test_9_a_crash_after_the_marker_clears_the_slot_and_appends_nothing(self) -> None:
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run()
        self._crash("observed_marker")
        self.assertTrue(backlog_dispatch.observed_marker_path(
            self.corpus_root, dispatch_id, "bbb_high").is_file())
        self.assertIsNotNone(self._slot())

        report = self._tick()
        self.assertEqual(report["reason"], "observed:succeeded")
        self.assertEqual(len(self._events(dispatch_id)), 2)
        self.assertIsNone(self._slot())

    def test_10_after_the_slot_clear_the_lane_is_clean(self) -> None:
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        self._finish_run()
        self._tick()
        self.assertIsNone(self._slot())
        before = self._events(dispatch_id)
        _write_json(self.results_path, {"results": [
            {"contract_version": "experiment-result.v1", "experiment_id": "bbb_high"}]})
        self.status = {"ok": True, "done": False}
        report = self._tick()
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertEqual(report["detail"]["source_ref"], "ccc_mid")
        self.assertEqual(self._events(dispatch_id), before)

    def test_a_failed_submit_records_the_failure_and_frees_the_slot(self) -> None:
        self._arm("candidate")
        report = self._tick(submit_task_fn=lambda **kw: {"ok": False,
                                                         "error": "ssh timeout"})
        self.assertEqual(report["reason"], "no-op:dispatch-failed")
        self.assertIsNone(self._slot())
        dispatch_id = report["detail"]["dispatch_id"]
        events = self._events(dispatch_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["outcome"], "dispatch_failed")
        self.assertEqual(events[1]["payload"]["submit_error"], "ssh timeout")
        # The plan stays unpublished: nothing ran, so nothing may be suppressed.
        self.assertEqual(self._published_plans(), [])
        self.assertEqual(len(self._pending_plans()), 1)
        self.assertEqual(self._tick()["detail"]["source_ref"], "bbb_high",
                         "a failed dispatch leaves the candidate selectable")

    def test_every_crash_point_leaves_a_readable_arm_file(self) -> None:
        """The slot lives in the arm file, so a torn write there is a lost or
        phantom run, not a cosmetic problem."""
        for point in drain.CRASH_POINTS:
            with self.subTest(point=point):
                self.setUp()
                self._arm("candidate")
                if point in ("observation", "observed_marker"):
                    self._tick()
                    self._finish_run()
                with self.assertRaises(drain.InjectedCrash):
                    self._tick(crash_after=point)
                state = drain.load_arm_state(self.arm_path)
                self.assertTrue(state["armed"], f"{point} left the arm file unreadable")
                self.assertEqual(state["scope"], "candidate")


class DisarmedWithInFlightTests(_CycleHarness):
    def test_disarm_leaves_the_run_alone_and_status_says_so(self) -> None:
        self._arm("candidate")
        self._tick()
        plan_id = self._slot()["plan_id"]
        drain.set_armed(False, "pulling the switch", path=self.arm_path)
        slot = self._slot()
        self.assertIsNotNone(slot, "the conductor owns that run; forgetting it "
                                   "would neither stop it nor find its result")
        self.assertEqual(slot["plan_id"], plan_id)
        status = drain.status_report(self.arm_path)
        self.assertTrue(status["disarmed_with_in_flight"])
        self.assertEqual(status["in_flight_phase"], backlog_dispatch.PHASE_SUBMITTED)

    def test_a_disarmed_tick_still_writes_back_a_finished_run(self) -> None:
        """Recording the truth is not dispatch."""
        self._arm("candidate")
        dispatch_id = self._tick()["detail"]["dispatch_id"]
        drain.set_armed(False, "pulling the switch", path=self.arm_path)
        self._finish_run()
        report = self._tick()
        self.assertEqual(report["reason"], "disarmed")
        self.assertTrue(report["detail"]["disarmed_with_in_flight"])
        self.assertEqual(report["detail"]["in_flight_action"], "observed:succeeded")
        self.assertEqual(len(self._events(dispatch_id)), 2)
        self.assertIsNone(self._slot())

    def test_a_disarmed_tick_never_dispatches_after_the_write_back(self) -> None:
        self._arm("candidate")
        self._tick()
        drain.set_armed(False, "pulling the switch", path=self.arm_path)
        self._finish_run()
        self._tick()
        report = self._tick()
        self.assertEqual(report["reason"], "disarmed")
        self.assertEqual(len(self.submits), 1)
        self.assertEqual(len(self._run_dirs()), 1)

    def test_a_disarmed_tick_with_a_running_job_reports_it_and_holds(self) -> None:
        self._arm("candidate")
        self._tick()
        drain.set_armed(False, "pulling the switch", path=self.arm_path)
        report = self._tick()
        self.assertEqual(report["reason"], "disarmed")
        self.assertEqual(report["detail"]["in_flight_action"], "in-flight")
        self.assertIsNotNone(self._slot())

    def test_a_disarmed_tick_leaves_a_legacy_slot_completely_alone(self) -> None:
        """The live arm file on OMEN is exactly this shape: v1, armed, carrying
        a July last_dispatch_plan_id, and loading as DISARMED for want of a
        scope. Resolving that slot would mean an SSH round trip and a rewrite of
        a human-authored file -- for no record at all, since there is no run
        directory behind it."""
        _write_json(self.arm_path, dict(_V1_ARM_FILE))
        before = self.arm_path.read_bytes()
        probed: list = []
        report = self._tick(task_status_fn=lambda p: probed.append(p) or
                            {"ok": True, "done": True})
        self.assertEqual(report["reason"], "disarmed")
        self.assertTrue(report["detail"]["disarmed_with_in_flight"])
        self.assertEqual(report["detail"]["in_flight_action"], "untouched:legacy-slot")
        self.assertEqual(probed, [], "a disarmed tick asks the conductor nothing")
        self.assertEqual(self.arm_path.read_bytes(), before,
                         "a disarmed tick must not repair the file")
        self.assertEqual(self._run_dirs(), [])

    def test_re_arming_migrates_the_legacy_slot_and_the_armed_tick_resolves_it(self) -> None:
        _write_json(self.arm_path, dict(_V1_ARM_FILE))
        drain.set_armed(True, "re-armed after the scope contract", path=self.arm_path,
                        scope="candidate")
        on_disk = json.loads(self.arm_path.read_text(encoding="utf-8"))
        self.assertNotIn("last_dispatch_plan_id", on_disk)
        self.assertEqual(on_disk["in_flight"]["plan_id"],
                         _V1_ARM_FILE["last_dispatch_plan_id"])
        report = self._tick(task_status_fn=lambda p: {"ok": True, "done": True})
        self.assertTrue(report["reason"].startswith("observed:"))
        self.assertTrue(report["detail"]["legacy_slot"])
        self.assertIsNone(self._slot())

    def test_status_on_a_clean_lane_reports_no_slot(self) -> None:
        self._arm("candidate")
        status = drain.status_report(self.arm_path)
        self.assertFalse(status["disarmed_with_in_flight"])
        self.assertIsNone(status["in_flight_phase"])


class InFlightGateTests(_CycleHarness):
    def test_an_unreachable_conductor_holds_the_slot_instead_of_clearing_it(self) -> None:
        """The old behaviour cleared the slot on ok:false so the drain would not
        wedge on an SSH hiccup -- which let a SECOND dispatch start beside a
        live run. Unreachable is not resolved."""
        self._arm("candidate")
        self._tick()
        plan_id = self._slot()["plan_id"]
        report = self._tick(task_status_fn=lambda p: {"ok": False,
                                                      "error": "ssh exit 255"})
        self.assertEqual(report["reason"], "in-flight:status-unreachable")
        self.assertIn("ssh exit 255", report["detail"]["status_error"])
        self.assertEqual(self._slot()["plan_id"], plan_id)
        self.assertEqual(len(self.submits), 1)

    def test_a_raising_task_status_holds_the_slot_too(self) -> None:
        self._arm("candidate")
        self._tick()

        def boom(plan_id):
            raise TimeoutError("conductor unreachable")

        report = self._tick(task_status_fn=boom)
        self.assertEqual(report["reason"], "in-flight:status-unreachable")
        self.assertIn("TimeoutError", report["detail"]["status_error"])
        self.assertIsNotNone(self._slot())

    def test_a_nonsense_task_status_shape_holds_the_slot(self) -> None:
        self._arm("candidate")
        self._tick()
        report = self._tick(task_status_fn=lambda p: "not a dict")
        self.assertEqual(report["reason"], "in-flight:status-unreachable")
        self.assertIsNotNone(self._slot())

    def test_the_in_flight_gate_runs_before_the_queue_and_occupancy_probes(self) -> None:
        self._arm("candidate")
        self._tick()
        probed: list = []
        report = self._tick(queue_status_fn=lambda: probed.append("queue") or _idle_queue(),
                            occupancy_check=lambda n: probed.append("occ") or
                            {"occupancy": "available"})
        self.assertEqual(report["reason"], "in-flight")
        self.assertEqual(probed, [], "a held slot ends the tick before any probe")

    def test_a_missing_in_flight_key_on_a_valid_v2_file_is_an_empty_slot(self) -> None:
        _write_json(self.arm_path, {
            "contract_version": "bankedfire-drain-arm.v2", "armed": True,
            "scope": "candidate", "authored_by": "derek", "reason": "r",
            "updated": "2026-09-07T10:00:00Z"})
        self.assertIsNone(drain.load_arm_state(self.arm_path)["in_flight"])
        self.assertTrue(self._tick()["reason"].startswith("dispatched:"))

    def test_a_junk_in_flight_value_is_an_empty_slot(self) -> None:
        for junk in ("hearth-x-1", [], 0, {}):
            with self.subTest(junk=junk):
                self.setUp()
                _write_json(self.arm_path, {
                    "contract_version": "bankedfire-drain-arm.v2", "armed": True,
                    "scope": "candidate", "authored_by": "derek", "reason": "r",
                    "updated": "2026-09-07T10:00:00Z", "in_flight": junk})
                self.assertIsNone(drain.load_arm_state(self.arm_path)["in_flight"])


class BudgetAndOccupancyGateTests(_CycleHarness):
    """The gates, asserted on the tick (not just on check_budget), and asserted
    to write NOTHING when they refuse."""

    def _assert_refused(self, report, reason: str) -> None:
        self.assertEqual(report["reason"], reason)
        self.assertIsNone(self._slot())
        self.assertEqual(self._run_dirs(), [])
        self.assertEqual(self.submits, [])

    def test_a_suspended_budget_names_the_failing_field(self) -> None:
        self._arm("candidate")
        _write_json(self.budget_path, {**_GOOD_BUDGET, "suspended": True})
        report = self._tick()
        self._assert_refused(report, "no-budget")
        self.assertEqual(report["detail"]["budget_fail_field"], "suspended")

    def test_unattended_dispatch_not_allowed_names_its_field(self) -> None:
        self._arm("candidate")
        _write_json(self.budget_path, {**_GOOD_BUDGET,
                                       "unattended_dispatch_allowed": False})
        report = self._tick()
        self._assert_refused(report, "no-budget")
        self.assertEqual(report["detail"]["budget_fail_field"],
                         "unattended_dispatch_allowed")

    def test_outside_active_hours_names_its_field(self) -> None:
        # The tick's clock is injectable, so the window gate is deterministic
        # rather than "whatever time the suite happened to run".
        self._arm("candidate")
        _write_json(self.budget_path, {**_GOOD_BUDGET,
                                       "active_hours": {"start": "09:00", "end": "17:00"}})
        outside = datetime(2026, 7, 4, 3, 0, tzinfo=timezone.utc)
        report = self._tick(now=outside)
        self._assert_refused(report, "no-budget")
        self.assertEqual(report["detail"]["budget_fail_field"], "active_hours")

    def test_inside_active_hours_dispatches(self) -> None:
        self._arm("candidate")
        _write_json(self.budget_path, {**_GOOD_BUDGET,
                                       "active_hours": {"start": "09:00", "end": "17:00"}})
        inside = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        report = self._tick(now=inside)
        self.assertTrue(report["reason"].startswith("dispatched:"))
        events = self._events(report["detail"]["dispatch_id"])
        self.assertEqual(events[0]["timestamp"], "2026-07-04T12:00:00Z",
                         "the injected clock is the one the corpus records")

    def test_an_unreadable_budget_writes_nothing(self) -> None:
        self._arm("candidate")
        self.budget_path.unlink()
        report = self._tick()
        self._assert_refused(report, "no-budget")
        self.assertEqual(report["detail"]["budget_fail_field"], "unreadable")

    def test_a_busy_queue_writes_nothing(self) -> None:
        self._arm("candidate")
        self._assert_refused(self._tick(queue_status_fn=lambda: {**_idle_queue(),
                                                                "running": 1}), "busy")

    def test_an_unreadable_queue_writes_nothing(self) -> None:
        self._arm("candidate")
        self._assert_refused(
            self._tick(queue_status_fn=lambda: {"ok": False, "error": "ssh exit 255"}),
            "busy:queue-unreadable")

    def test_a_busy_rung_writes_nothing(self) -> None:
        self._arm("candidate")
        self._assert_refused(
            self._tick(occupancy_check=lambda n: {"occupancy": "busy"}), "busy")

    def test_a_refused_lease_writes_nothing(self) -> None:
        self._arm("candidate")
        self._assert_refused(
            self._tick(acquire_lease=lambda n, pinned=False: _FakeLease(False, "busy")),
            "busy")

    def test_a_disarmed_tick_writes_nothing(self) -> None:
        self._assert_refused(self._tick(), "disarmed")


class SuppressionLoopTests(_CycleHarness):
    """The loop closed with the REAL projector, over a temp corpus.

    ``project_experiments.materialize_experiments`` is what builds
    ``knowledge/experiment_results.json`` in production; it is called here
    unchanged, against the events and artifacts the drain actually wrote, into a
    temp knowledge dir. Nothing reads or writes the repo's own corpus."""

    def _rebuild(self) -> dict:
        from tools.workflow.project_experiments import (RESULTS_FILE,
                                                        materialize_experiments)
        event_files = sorted((self.corpus_root / "runs").rglob("events.jsonl"))
        knowledge = self.tmp / "knowledge"
        outputs = materialize_experiments(event_files, knowledge)
        self.rebuilt_results_path = knowledge / RESULTS_FILE
        return outputs[RESULTS_FILE]

    def test_a_completed_cycle_becomes_a_result_row_that_suppresses_it(self) -> None:
        # FIXTURE CHANGE (B-04-R1): a capacity observation only joins to the
        # plan when the run is evidence about the candidate's combo, so this
        # test now uses the combo-shaped candidate and the matching winner.
        # Every assertion is the original one, re-keyed to that id.
        experiment_id = self._use_combo_worth()
        self._arm("candidate")
        self._tick()
        self._finish_run(ok=True, winner=_COMBO_BUILDER)
        self._tick()

        results = self._rebuild()
        self.assertEqual(results["plan_count"], 1)
        self.assertEqual(results["unresolved_refs"], 0)
        row = results["results"][0]
        self.assertEqual(row["contract_version"], "experiment-result.v1")
        self.assertEqual(row["experiment_id"], experiment_id)
        self.assertEqual(row["outcome"], "success",
                         "the capacity observation joined to the plan")

        # The projected file is what the selection reads. Point the tick at it.
        self.assertIn(experiment_id,
                      backlog_sources.already_run_ids(results["results"]))
        report = self._tick(results_path=self.rebuilt_results_path)
        self.assertTrue(report["reason"].startswith("dispatched:"))
        self.assertEqual(report["detail"]["source_ref"], "ccc_mid",
                         "the finished candidate is ineligible after the rebuild")

    def test_a_run_that_never_reached_the_conductor_produces_no_result_row(self) -> None:
        """The .pending rule, proven through the projector: an unpublished plan
        leaves the artifact ref unresolved, so no row exists and the candidate
        is still selectable."""
        self._arm("candidate")
        with self.assertRaises(drain.InjectedCrash):
            self._tick(crash_after="slot")
        self._tick()  # reconciles never-submitted

        results = self._rebuild()
        self.assertEqual(results["plan_count"], 0)
        self.assertEqual(results["unresolved_refs"], 1,
                         "the aborted attempt is COUNTED, not silently dropped")
        self.assertEqual(backlog_sources.already_run_ids(results["results"]), set())
        report = self._tick(results_path=self.rebuilt_results_path)
        self.assertEqual(report["detail"]["source_ref"], "bbb_high")

    def test_a_failed_run_still_suppresses_the_candidate(self) -> None:
        """A dispatch that ran and failed HAS been tried; re-dispatching it every
        30 minutes forever is the bug this item exists to fix."""
        # FIXTURE CHANGE (B-04-R1): combo candidate + matching winner, so the
        # failure is still evidence about that combo and the row reads "error".
        experiment_id = self._use_combo_worth()
        self._arm("candidate")
        self._tick()
        self._finish_run(ok=False, winner=_COMBO_BUILDER)
        self._tick()
        results = self._rebuild()
        self.assertEqual(results["results"][0]["experiment_id"], experiment_id)
        self.assertEqual(results["results"][0]["outcome"], "error")
        self.assertEqual(self._tick(results_path=self.rebuilt_results_path)
                         ["detail"]["source_ref"], "ccc_mid")

    def test_a_run_with_no_winner_records_a_row_with_no_observation(self) -> None:
        self._arm("candidate")
        self._tick()
        self._finish_run(ok=True, winner=None)
        self._tick()
        results = self._rebuild()
        row = results["results"][0]
        self.assertEqual(row["experiment_id"], "bbb_high")
        self.assertEqual(row["outcome"], "no_observation")
        self.assertEqual(row["observation_ids"], [])

    def test_a_mismatched_winner_still_suppresses_with_a_no_observation_row(self) -> None:
        """B-04-R1's required outcome for a winner-mismatch: the experiment RAN,
        so the plan is published and the candidate becomes ineligible; but no
        capacity observation joined, so the row honestly reads no_observation
        rather than claiming evidence about a combo that never served it."""
        experiment_id = self._use_combo_worth()
        self._arm("candidate")
        self._tick()
        self._finish_run(ok=True, winner="cc-builder-9")
        self._tick()

        results = self._rebuild()
        self.assertEqual(results["plan_count"], 1)
        self.assertEqual(results["unresolved_refs"], 0)
        row = results["results"][0]
        self.assertEqual(row["experiment_id"], experiment_id)
        self.assertEqual(row["outcome"], "no_observation")
        self.assertEqual(row["observation_ids"], [])
        self.assertIn(experiment_id,
                      backlog_sources.already_run_ids(results["results"]))
        self.assertEqual(self._tick(results_path=self.rebuilt_results_path)
                         ["detail"]["source_ref"], "ccc_mid",
                         "a run that happened is still a run: it must not be "
                         "re-dispatched forever just because the winner differed")

    def test_two_cycles_produce_two_distinct_rows(self) -> None:
        self._arm("candidate")
        self._tick()
        self._finish_run()
        self._tick()
        first = self._rebuild()
        self.status = {"ok": True, "done": False}
        self._tick(results_path=self.rebuilt_results_path)
        self._finish_run()
        self._tick()
        second = self._rebuild()
        self.assertEqual(first["plan_count"], 1)
        self.assertEqual(second["plan_count"], 2)
        self.assertEqual({row["experiment_id"] for row in second["results"]},
                         {"bbb_high", "ccc_mid"})


class CapacityProjectionTests(_CycleHarness):
    """B-04-R1's own proof, run through the REAL capacity projector.

    ``tools.workflow.project_capacity.materialize_knowledge`` is what builds
    ``knowledge/capacity_estimates.json`` / ``known_good_models.json`` in
    production. It is called here unchanged over the events and artifacts the
    drain actually wrote, into a temp knowledge dir. What is being proven is
    negative and specific: the combo key it derives
    (``project_capacity._combo_key``, L35-41) never contains the ``"unknown"``
    segment that a null model_id/backend would have produced.
    """

    def _project_capacity(self, tag: str) -> dict:
        from tools.workflow.project_capacity import (CAPACITY_ESTIMATES_FILE,
                                                     KNOWN_GOOD_FILE,
                                                     collect_event_files,
                                                     materialize_knowledge)
        # A fresh knowledge dir per call: the A2 regression guard compares a new
        # projection against the file already on disk, and these scenarios
        # deliberately project DIFFERENT corpora.
        knowledge = self.tmp / "knowledge-capacity" / tag
        event_files = collect_event_files([self.corpus_root / "runs"])
        outputs = materialize_knowledge(event_files, knowledge)
        self.capacity_knowledge_dir = knowledge
        return {"estimates": outputs[CAPACITY_ESTIMATES_FILE],
                "known_good": outputs[KNOWN_GOOD_FILE]}

    def _assert_no_unknown_segment(self, estimates: dict) -> None:
        for key, combo in estimates["combos"].items():
            self.assertNotIn(backlog_dispatch.UNKNOWN_COMBO_SEGMENT, key.split("|"),
                             f"combo key {key!r} carries the projection's "
                             f"'unknown' placeholder")
            for field in ("builder_id", "model_id", "backend"):
                self.assertNotEqual(combo[field],
                                    backlog_dispatch.UNKNOWN_COMBO_SEGMENT)

    def test_a_matching_winner_projects_the_candidates_own_combo(self) -> None:
        self._use_combo_worth()
        self._arm("candidate")
        self._tick()
        self._finish_run(ok=True, winner=_COMBO_BUILDER)
        self._tick()

        projected = self._project_capacity("match")
        estimates = projected["estimates"]
        self.assertEqual(estimates["observation_count"], 1)
        self.assertEqual(estimates["unresolved_observation_refs"], 0)
        key = f"{_COMBO_BUILDER}|{_COMBO_MODEL}|{_COMBO_BACKEND}"
        self.assertEqual(list(estimates["combos"]), [key],
                         "the projection must bucket this run under the combo "
                         "the candidate named, and nothing else")
        combo = estimates["combos"][key]
        self.assertEqual(combo["builder_id"], _COMBO_BUILDER)
        self.assertEqual(combo["model_id"], _COMBO_MODEL)
        self.assertEqual(combo["backend"], _COMBO_BACKEND)
        self.assertEqual(combo["samples"], 1)
        self.assertEqual(combo["successes"], 1)
        self._assert_no_unknown_segment(estimates)
        # ... and the derived belief file names the same real combo.
        entries = projected["known_good"]["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["builder_id"], entries[0]["model_id"],
                          entries[0]["backend"]),
                         (_COMBO_BUILDER, _COMBO_MODEL, _COMBO_BACKEND))

    def test_a_mismatched_winner_projects_nothing_at_all(self) -> None:
        self._use_combo_worth()
        self._arm("candidate")
        self._tick()
        self._finish_run(ok=True, winner="cc-builder-9")
        self._tick()

        estimates = self._project_capacity("mismatch")["estimates"]
        self.assertEqual(estimates["observation_count"], 0)
        self.assertEqual(estimates["combos"], {})
        self.assertEqual(estimates["unresolved_observation_refs"], 0,
                         "no artifact ref was written, so none is dangling")
        self._assert_no_unknown_segment(estimates)

    def test_an_authored_brief_projects_nothing_at_all(self) -> None:
        self._arm("authored")
        self._add_authored()
        self._tick()
        self._finish_run(ok=True, winner="cc-builder-2")
        self._tick()

        estimates = self._project_capacity("authored")["estimates"]
        self.assertEqual(estimates["observation_count"], 0)
        self.assertEqual(estimates["combos"], {})
        self._assert_no_unknown_segment(estimates)

    def test_the_pre_repair_shape_is_exactly_what_the_projector_would_bucket(self) -> None:
        """The bug, demonstrated rather than asserted about: an observation with
        a null model_id/backend -- what B-04 wrote -- lands in a
        <builder>|unknown|unknown bucket. This is why the artifact is now
        combo-gated and why build_capacity_observation refuses to build one."""
        from tools.workflow.project_capacity import reduce_capacity
        pre_repair = {"builder_id": _COMBO_BUILDER, "model_id": None,
                      "backend": None, "outcome": "success",
                      "timestamp": "2026-09-07T11:00:00Z"}
        self.assertEqual(list(reduce_capacity([pre_repair])),
                         [f"{_COMBO_BUILDER}|unknown|unknown"])
        # And the guard now makes that document unbuildable by the drain.
        with self.assertRaises(ValueError):
            backlog_dispatch.build_capacity_observation(
                "hearth-d-1", "x", timestamp="2026-09-07T11:00:00Z",
                builder_id=_COMBO_BUILDER, model_id=None, backend=None,
                succeeded=True)


class BenignNoOpCLITests(TestCase):
    """`python -m fleet.bankedfire_drain --json` under a temp HEARTH_ROOT."""

    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.live_arm = drain.DEFAULT_ARM_STATE_PATH
        self.live_arm_existed = self.live_arm.exists()
        self.live_corpus = drain.DEFAULT_CORPUS_ROOT / "runs" / backlog_dispatch.RUN_NAMESPACE
        self.live_corpus_existed = self.live_corpus.exists()

    def _run(self, argv):
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, {"HEARTH_ROOT": str(self.tmp)}), \
                contextlib.redirect_stdout(buffer):
            code = drain.main(argv)
        return code, buffer.getvalue()

    def test_the_arm_path_follows_hearth_root(self) -> None:
        with mock.patch.dict(os.environ, {"HEARTH_ROOT": str(self.tmp)}):
            self.assertEqual(drain.default_arm_state_path(),
                             self.tmp.resolve() / "var" / drain.ARM_STATE_FILENAME)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(drain.default_arm_state_path(),
                             drain.DEFAULT_ARM_STATE_PATH)

    def test_no_arm_file_is_a_disarmed_no_op_that_exits_zero(self) -> None:
        code, out = self._run(["--json"])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["reason"], "disarmed")
        self.assertFalse(report["detail"]["armed"])

    def test_the_no_op_creates_nothing_outside_the_temp_root(self) -> None:
        self._run(["--json"])
        self.assertEqual(self.live_arm.exists(), self.live_arm_existed,
                         "the live arm file must not be created or removed")
        self.assertEqual(self.live_corpus.exists(), self.live_corpus_existed,
                         "the live drain corpus must not be created")
        self.assertTrue((self.tmp / "var" / "ledger").is_dir(),
                        "the tick's ledger row landed inside the temp root")

    def test_status_reports_the_derived_flags(self) -> None:
        code, out = self._run(["--status"])
        self.assertEqual(code, 0)
        status = json.loads(out)
        self.assertFalse(status["armed"])
        self.assertIsNone(status["in_flight_phase"])
        self.assertFalse(status["disarmed_with_in_flight"])


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
