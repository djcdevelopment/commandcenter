"""Stream E1 tests: idle event types, operating-budget schema/validator, and budget_check.

Covers:
  - idle.observed and idle.ended fixture events validate via validate_events
  - operating-budget.v1 schema round-trip via validate_budget
  - budget_check truth table including suspended, unattended_dispatch_allowed==False,
    physical-null, sensor-null, within-limits, and over-limit cases
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from tools.workflow.validate_events import validate_file as validate_events_file
from tools.workflow.validate_budget import ValidationError, validate_budget, validate_file as validate_budget_file
from tools.workflow.budget_check import check

ROOT = Path(__file__).resolve().parents[2]
IDLE_FIXTURE = ROOT / "fixtures" / "workflow" / "runs" / "run_zzz_idle_001" / "events.jsonl"
BUDGET_FIXTURE = ROOT / "fixtures" / "workflow" / "budgets" / "example-budget.json"


# ---------------------------------------------------------------------------
# idle event fixture validation
# ---------------------------------------------------------------------------

class IdleEventFixtureTests(TestCase):

    def test_idle_fixture_validates_via_validate_events(self) -> None:
        errors = validate_events_file(IDLE_FIXTURE)
        self.assertEqual(errors, [], errors)

    def test_idle_fixture_contains_both_event_types(self) -> None:
        events = [json.loads(line) for line in IDLE_FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
        types = {e["event_type"] for e in events}
        self.assertIn("idle.observed", types)
        self.assertIn("idle.ended", types)

    def test_idle_fixture_has_no_capacity_observation_refs(self) -> None:
        """Sweep-hazard guard: must never add observation_count to the projection."""
        events = [json.loads(line) for line in IDLE_FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
        for event in events:
            for ref in event.get("artifact_refs") or []:
                self.assertNotEqual(ref.get("artifact_type"), "capacity_observation",
                                    "idle fixture must not contain capacity_observation refs")
                self.assertNotEqual(ref.get("artifact_type"), "scheduler_decision",
                                    "idle fixture must not contain scheduler_decision refs")

    def test_idle_event_payload_keys(self) -> None:
        events = [json.loads(line) for line in IDLE_FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
        for event in events:
            payload = event["payload"]
            for key in ("node_id", "source", "observed_at"):
                self.assertIn(key, payload, f"{event['event_type']} payload missing {key}")

    def test_idle_observed_source_is_valid_enum(self) -> None:
        events = [json.loads(line) for line in IDLE_FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
        for event in events:
            source = event["payload"].get("source")
            self.assertIn(source, ("session_end", "manual", "scheduler"),
                          f"source '{source}' not in allowed enum")


# ---------------------------------------------------------------------------
# operating-budget schema validation
# ---------------------------------------------------------------------------

def _base_budget() -> dict:
    return {
        "contract_version": "operating-budget.v1",
        "budget_id": "budget_test_001",
        "reason": "test budget for suite",
        "authored_by": "test",
        "unattended_dispatch_allowed": False,
        "suspended": False,
    }


class BudgetSchemaTests(TestCase):

    def test_example_fixture_passes_validate_budget(self) -> None:
        errors = validate_budget_file(BUDGET_FIXTURE)
        self.assertEqual(errors, [], errors)

    def test_minimal_valid_budget_passes(self) -> None:
        validate_budget(_base_budget())  # must not raise

    def test_full_valid_budget_passes(self) -> None:
        budget = {
            **_base_budget(),
            "node_id": "cc-builder-1",
            "max_gpu_temp_c": 85,
            "max_power_w": 300,
            "max_fan_rpm": 3000,
            "unattended_dispatch_allowed": True,
            "active_hours": {"start": "22:00", "end": "06:00"},
            "created": "2026-07-04T00:00:00Z",
            "suspended": False,
            "suspend_reason": None,
        }
        validate_budget(budget)  # must not raise

    def test_missing_reason_fails(self) -> None:
        budget = _base_budget()
        del budget["reason"]
        with self.assertRaises(ValidationError) as ctx:
            validate_budget(budget)
        self.assertIn("reason", str(ctx.exception))

    def test_missing_authored_by_fails(self) -> None:
        budget = _base_budget()
        del budget["authored_by"]
        with self.assertRaises(ValidationError) as ctx:
            validate_budget(budget)
        self.assertIn("authored_by", str(ctx.exception))

    def test_empty_reason_fails(self) -> None:
        budget = {**_base_budget(), "reason": ""}
        with self.assertRaises(ValidationError):
            validate_budget(budget)

    def test_extra_key_fails(self) -> None:
        budget = {**_base_budget(), "unknown_field": "oops"}
        with self.assertRaises(ValidationError) as ctx:
            validate_budget(budget)
        self.assertIn("unknown_field", str(ctx.exception))

    def test_wrong_contract_version_fails(self) -> None:
        budget = {**_base_budget(), "contract_version": "other.v1"}
        with self.assertRaises(ValidationError):
            validate_budget(budget)

    def test_null_max_fields_allowed(self) -> None:
        budget = {
            **_base_budget(),
            "max_gpu_temp_c": None,
            "max_power_w": None,
            "max_fan_rpm": None,
        }
        validate_budget(budget)  # null = unconstrained, must not raise

    def test_active_hours_missing_end_fails(self) -> None:
        budget = {**_base_budget(), "active_hours": {"start": "22:00"}}
        with self.assertRaises(ValidationError) as ctx:
            validate_budget(budget)
        self.assertIn("end", str(ctx.exception))

    def test_active_hours_bad_hhmm_fails(self) -> None:
        budget = {**_base_budget(), "active_hours": {"start": "25:00", "end": "06:00"}}
        with self.assertRaises(ValidationError):
            validate_budget(budget)

    def test_active_hours_null_allowed(self) -> None:
        budget = {**_base_budget(), "active_hours": None}
        validate_budget(budget)  # must not raise

    def test_suspended_false_with_reason_null_allowed(self) -> None:
        budget = {**_base_budget(), "suspend_reason": None}
        validate_budget(budget)  # must not raise


# ---------------------------------------------------------------------------
# budget_check truth table
# ---------------------------------------------------------------------------

def _base_check_budget(**kwargs) -> dict:
    b = {
        "contract_version": "operating-budget.v1",
        "budget_id": "bc_test",
        "reason": "check test",
        "authored_by": "test",
        "unattended_dispatch_allowed": True,
        "suspended": False,
        "max_gpu_temp_c": None,
        "max_power_w": None,
        "max_fan_rpm": None,
    }
    b.update(kwargs)
    return b


def _physical(**kwargs) -> dict:
    p = {
        "gpu_temp_c_peak": None,
        "gpu_temp_c_sustained_avg": None,
        "power_w_avg": None,
        "power_w_peak": None,
        "fan_rpm_avg": None,
    }
    p.update(kwargs)
    return p


class BudgetCheckTests(TestCase):

    # -- suspended --

    def test_suspended_blocks_immediately(self) -> None:
        b = _base_check_budget(suspended=True)
        result = check(b, _physical(gpu_temp_c_peak=50))
        self.assertFalse(result["permitted"])
        dims = [v["dimension"] for v in result["violated"]]
        self.assertIn("suspended", dims)

    def test_suspended_blocks_even_when_readings_fine(self) -> None:
        b = _base_check_budget(suspended=True, max_gpu_temp_c=85)
        result = check(b, _physical(gpu_temp_c_peak=50))
        self.assertFalse(result["permitted"])
        self.assertEqual(result["unmeasurable"], [])

    # -- unattended_dispatch_allowed == False --

    def test_unattended_not_allowed_blocks(self) -> None:
        b = _base_check_budget(unattended_dispatch_allowed=False)
        result = check(b, _physical(gpu_temp_c_peak=50))
        self.assertFalse(result["permitted"])
        dims = [v["dimension"] for v in result["violated"]]
        self.assertIn("unattended_dispatch_allowed", dims)

    # -- physical is None --

    def test_physical_none_no_constraints_permitted(self) -> None:
        b = _base_check_budget()  # all max_* are None
        result = check(b, None)
        self.assertTrue(result["permitted"])
        self.assertEqual(result["unmeasurable"], [])

    def test_physical_none_with_constrained_dimension_not_permitted(self) -> None:
        b = _base_check_budget(max_gpu_temp_c=85)
        result = check(b, None)
        self.assertFalse(result["permitted"])
        dims = [u["dimension"] for u in result["unmeasurable"]]
        self.assertIn("max_gpu_temp_c", dims)
        self.assertEqual(result["unmeasurable"][0]["reason"], "sensor absent for constrained dimension")

    def test_physical_none_all_three_constrained(self) -> None:
        b = _base_check_budget(max_gpu_temp_c=85, max_power_w=350, max_fan_rpm=3000)
        result = check(b, None)
        self.assertFalse(result["permitted"])
        dims = {u["dimension"] for u in result["unmeasurable"]}
        self.assertEqual(dims, {"max_gpu_temp_c", "max_power_w", "max_fan_rpm"})

    # -- sensor value is None (constrained but sensor absent) --

    def test_sensor_null_on_constrained_dimension_not_permitted(self) -> None:
        b = _base_check_budget(max_gpu_temp_c=85)
        result = check(b, _physical(gpu_temp_c_peak=None))
        self.assertFalse(result["permitted"])
        dims = [u["dimension"] for u in result["unmeasurable"]]
        self.assertIn("max_gpu_temp_c", dims)

    def test_sensor_null_power_peak_not_permitted(self) -> None:
        b = _base_check_budget(max_power_w=350)
        result = check(b, _physical(power_w_peak=None))
        self.assertFalse(result["permitted"])
        dims = [u["dimension"] for u in result["unmeasurable"]]
        self.assertIn("max_power_w", dims)

    def test_sensor_null_fan_rpm_not_permitted(self) -> None:
        b = _base_check_budget(max_fan_rpm=3000)
        result = check(b, _physical(fan_rpm_avg=None))
        self.assertFalse(result["permitted"])

    # -- within limits --

    def test_all_within_limits_permitted(self) -> None:
        b = _base_check_budget(max_gpu_temp_c=85, max_power_w=350, max_fan_rpm=3000)
        result = check(b, _physical(gpu_temp_c_peak=70, power_w_peak=250, fan_rpm_avg=2500))
        self.assertTrue(result["permitted"])
        self.assertEqual(result["violated"], [])
        self.assertEqual(result["unmeasurable"], [])

    def test_exactly_at_limit_permitted(self) -> None:
        b = _base_check_budget(max_gpu_temp_c=85)
        result = check(b, _physical(gpu_temp_c_peak=85))
        self.assertTrue(result["permitted"])

    # -- over limits --

    def test_gpu_temp_over_limit_not_permitted(self) -> None:
        b = _base_check_budget(max_gpu_temp_c=85)
        result = check(b, _physical(gpu_temp_c_peak=90))
        self.assertFalse(result["permitted"])
        dims = [v["dimension"] for v in result["violated"]]
        self.assertIn("max_gpu_temp_c", dims)
        violation = next(v for v in result["violated"] if v["dimension"] == "max_gpu_temp_c")
        self.assertEqual(violation["limit"], 85)
        self.assertEqual(violation["observed"], 90)

    def test_power_over_limit_not_permitted(self) -> None:
        b = _base_check_budget(max_power_w=350)
        result = check(b, _physical(power_w_peak=400))
        self.assertFalse(result["permitted"])

    def test_fan_rpm_over_limit_not_permitted(self) -> None:
        b = _base_check_budget(max_fan_rpm=3000)
        result = check(b, _physical(fan_rpm_avg=3500))
        self.assertFalse(result["permitted"])

    def test_multiple_violations_all_reported(self) -> None:
        b = _base_check_budget(max_gpu_temp_c=85, max_power_w=350)
        result = check(b, _physical(gpu_temp_c_peak=90, power_w_peak=400))
        self.assertFalse(result["permitted"])
        dims = {v["dimension"] for v in result["violated"]}
        self.assertIn("max_gpu_temp_c", dims)
        self.assertIn("max_power_w", dims)

    def test_unconstrained_dimension_not_in_result(self) -> None:
        # max_fan_rpm is None (unconstrained); should not appear in any result list
        b = _base_check_budget(max_gpu_temp_c=85, max_fan_rpm=None)
        result = check(b, _physical(gpu_temp_c_peak=70, fan_rpm_avg=9999))
        self.assertTrue(result["permitted"])
        all_dims = {x["dimension"] for x in result["violated"] + result["unmeasurable"]}
        self.assertNotIn("max_fan_rpm", all_dims)

    # -- sensor mapping correctness --

    def test_gpu_maps_to_peak_not_sustained(self) -> None:
        # max_gpu_temp_c must check gpu_temp_c_peak, not gpu_temp_c_sustained_avg
        b = _base_check_budget(max_gpu_temp_c=85)
        # peak over limit, sustained fine — must flag violation
        result = check(b, _physical(gpu_temp_c_peak=90, gpu_temp_c_sustained_avg=60))
        self.assertFalse(result["permitted"])

    def test_power_maps_to_peak_not_avg(self) -> None:
        b = _base_check_budget(max_power_w=350)
        # peak over limit, avg fine
        result = check(b, _physical(power_w_peak=400, power_w_avg=200))
        self.assertFalse(result["permitted"])

    def test_fan_maps_to_avg(self) -> None:
        b = _base_check_budget(max_fan_rpm=3000)
        result = check(b, _physical(fan_rpm_avg=2500))
        self.assertTrue(result["permitted"])
