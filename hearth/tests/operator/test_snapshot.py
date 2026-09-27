"""Capacity snapshots: immutable, caller-neutral, two-horizon, and honest when blind.

The gate this suite guards: two cold agents must be able to cite the same
`snapshot_id`, see the same bytes, and never be told that a stale field is
fresh.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from datetime import timedelta
from unittest import mock

import jsonschema

from hearth.operator import core, history, inspection, paths
from hearth.operator.canonical import (canonical_json, identity_of, parse_rfc3339,
                                       rfc3339, utc_now)
from hearth.operator.identity import Identity, resolve_from_env
from hearth.tests.operator.support import (FakeDoor, OperatorTestCase, UNRESTRICTED_KEY,
                                           all_keys, fake_cli_runner, walk_fields)

SCHEMA = json.loads((paths.CONTRACTS / "capacity-snapshot.v1.schema.json")
                    .read_text(encoding="utf-8"))
BUNDLE_SCHEMA = json.loads((paths.CONTRACTS / "inspection-bundle.v1.schema.json")
                           .read_text(encoding="utf-8"))

# The D-106 classes, as decided. Read from the shipped config so a test failure
# means the config changed, not that two copies of the numbers disagree.
TTLS = paths.field_ttls()



def NO_LOCAL_PROBE():
    """The inspection falls back to the in-process probe for rungs the door did not cover;
    on omen-linux that probe reaches real endpoints (2026-09-27: am4-ollama/am4-dense answered
    from inside a door-down test). These tests fake the door, so they fake this too."""
    return mock.patch("hearth.toolsurface.scheduler.capture_resource_snapshot",
                      side_effect=RuntimeError("no in-process probe under test"))


class SnapshotTestCase(OperatorTestCase):
    """Compiles the real catalog once and captures against a fake outside world."""

    @classmethod
    def setUpClass(cls) -> None:
        from hearth.operator import catalog as catalog_mod

        cls.catalog = catalog_mod.compile_catalog()

    def capture(self, *, door=None, runner=None, now=None, local=False) -> dict:
        with NO_LOCAL_PROBE():
            return inspection.capture(self.catalog, door=door or FakeDoor(),
                                      cli_runner=runner or fake_cli_runner(),
                                      now=now, local=local)

    def refresh(self, *, door=None, runner=None, now=None) -> dict:
        moment = now or utc_now()
        with mock.patch.object(inspection, "utc_now", return_value=moment), NO_LOCAL_PROBE():
            return core.refresh(door=door or FakeDoor(),
                                cli_runner=runner or fake_cli_runner(),
                                now=moment, catalog=self.catalog)


class SnapshotShapeTests(SnapshotTestCase):
    def test_it_validates_against_its_schema(self) -> None:
        jsonschema.validate(self.capture(), SCHEMA)

    def test_kind_is_planning_and_the_window_is_300_seconds(self) -> None:
        snapshot = self.capture()
        self.assertEqual(snapshot["kind"], "planning")
        self.assertEqual(snapshot["planning_validity_s"], 300)
        observed = parse_rfc3339(snapshot["observed_at"])
        valid_until = parse_rfc3339(snapshot["planning_valid_until"])
        self.assertEqual(valid_until - observed, timedelta(seconds=300))

    def test_there_is_no_minimum_ttl_expiry_field(self) -> None:
        """D-106 replaced `expires_at = min(observed_at + ttl)` with two horizons;
        a 30-second occupancy TTL would otherwise expire the shared snapshot
        before any orchestrator finished deliberating."""
        self.assertNotIn("expires_at", set(all_keys(self.capture())))

    def test_every_dynamic_field_carries_the_D106_freshness_triple(self) -> None:
        snapshot = self.capture()
        fields = dict(walk_fields({key: value for key, value in snapshot.items()
                                   if key in ("door", "hosts", "gpus", "rungs", "models",
                                              "leases", "holds", "reachability",
                                              "trial_runway")}))
        self.assertGreater(len(fields), 20)
        for path, field in fields.items():
            with self.subTest(field=path):
                self.assertIn(field["ttl_s"], set(TTLS.values()))
                observed = parse_rfc3339(field["observed_at"])
                self.assertEqual(rfc3339(observed + timedelta(seconds=field["ttl_s"])),
                                 field["fresh_until"])
                self.assertTrue(field["source"])
                if field["value"] is None:
                    self.assertTrue(field.get("reason"),
                                    "a null value must say why it is null")

    def test_each_fact_sits_in_its_decided_freshness_class(self) -> None:
        snapshot = self.capture()
        self.assertEqual(snapshot["rungs"]["omen-arc"]["ready"]["ttl_s"], TTLS["readiness"])
        self.assertEqual(snapshot["rungs"]["omen-arc"]["residency"]["ttl_s"], TTLS["readiness"])
        self.assertEqual(snapshot["rungs"]["omen-arc"]["occupancy"]["ttl_s"], TTLS["occupancy"])
        self.assertEqual(snapshot["rungs"]["omen-arc"]["active_slots"]["ttl_s"], TTLS["occupancy"])
        self.assertEqual(snapshot["leases"]["ttl_s"], TTLS["occupancy"])
        self.assertEqual(snapshot["holds"]["ttl_s"], TTLS["occupancy"])
        self.assertEqual(snapshot["hosts"]["omen"]["reachable"]["ttl_s"], TTLS["reachability"])
        self.assertEqual(snapshot["trial_runway"]["budget_tokens"]["ttl_s"], TTLS["trial_runway"])
        self.assertEqual((120, 30, 300, 3600),
                         (TTLS["readiness"], TTLS["occupancy"], TTLS["reachability"],
                          TTLS["trial_runway"]))

    def test_it_records_what_the_door_reported(self) -> None:
        snapshot = self.capture()
        self.assertTrue(snapshot["door"]["reachable"]["value"])
        self.assertIn("hearth.toolsurface.inference",
                      snapshot["door"]["providers_mounted"]["value"])
        self.assertTrue(snapshot["rungs"]["omen-arc"]["ready"]["value"])
        self.assertEqual(snapshot["rungs"]["omen-arc"]["rung_state"]["value"]["verdict"],
                         "at_rate")
        self.assertEqual(snapshot["rungs"]["fx99-ollama"]["residency"]["value"],
                         ["qwen2.5-coder:7b"])
        self.assertEqual(snapshot["models"]["qwen2.5-coder:7b"]["resident_on"]["value"],
                         ["fx99-ollama"])
        self.assertTrue(snapshot["rungs"]["omen-arc"]["declared_live"]["value"])
        self.assertFalse(snapshot["hosts"]["am4"]["reachable"]["value"])

    def test_a_retired_rung_is_present_and_says_it_is_a_tombstone(self) -> None:
        snapshot = self.capture()
        tombstone = snapshot["rungs"]["omen-ollama"]["ready"]
        self.assertIsNone(tombstone["value"])
        self.assertIn("tombstone", tombstone["reason"])

    def test_no_caller_identity_appears_anywhere(self) -> None:
        """kernel_status returns the calling caller. If the snapshot copied a raw
        door result through, this is where it would show."""
        snapshot = self.capture()
        keys = set(all_keys(snapshot))
        for forbidden in ("caller", "caller_id", "profile", "authority", "key",
                          "api_key", "token", "capabilities_granted"):
            self.assertNotIn(forbidden, keys)
        text = canonical_json(snapshot).decode("utf-8")
        self.assertNotIn("fixture-unrestricted", text)
        self.assertNotIn("unrestricted", text)

    def test_the_identity_matches_the_content(self) -> None:
        snapshot = self.capture()
        self.assertEqual(snapshot["snapshot_id"], identity_of(snapshot, "snapshot_id"))


class SnapshotLifecycleTests(SnapshotTestCase):
    def test_refresh_writes_an_immutable_file_and_a_current_pointer(self) -> None:
        result = self.refresh()
        snapshot = result["snapshot"]
        path = result["path"]
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["snapshot_id"],
                         snapshot["snapshot_id"])
        current = json.loads(paths.current_path().read_text(encoding="utf-8"))
        self.assertEqual(current["capacity_snapshot"]["snapshot_id"], snapshot["snapshot_id"])
        self.assertEqual(current["catalog"]["catalog_version"], self.catalog["catalog_version"])

    def test_current_json_holds_no_authority_and_no_caller(self) -> None:
        self.refresh()
        current = json.loads(paths.current_path().read_text(encoding="utf-8"))
        self.assertEqual(set(current), {"contract_version", "catalog", "capacity_snapshot"})
        self.assertNotIn("authority", set(all_keys(current)))
        self.assertNotIn("caller", set(all_keys(current)))

    def test_inspect_twice_without_refresh_returns_the_same_snapshot_id(self) -> None:
        captured = self.refresh()["snapshot"]["snapshot_id"]
        first = core.inspect_bundle(Identity())
        second = core.inspect_bundle(Identity())
        self.assertEqual(first["capacity_snapshot"]["snapshot_id"], captured)
        self.assertEqual(second["capacity_snapshot"]["snapshot_id"], captured)
        self.assertEqual(first["capacity_snapshot"]["document"],
                         second["capacity_snapshot"]["document"])

    def test_two_callers_receive_identical_snapshots_and_different_authority(self) -> None:
        """The G1 gate in one test: shared environment, separate authority."""
        self.refresh()
        self.set_key(UNRESTRICTED_KEY)
        privileged = core.inspect_bundle(resolve_from_env())
        self.set_key(None)
        anonymous = core.inspect_bundle(resolve_from_env())

        self.assertEqual(privileged["capacity_snapshot"]["document"],
                         anonymous["capacity_snapshot"]["document"])
        self.assertEqual(privileged["catalog"], anonymous["catalog"])
        self.assertNotEqual(privileged["authority"], anonymous["authority"])
        self.assertEqual(anonymous["authority"]["caller"], None)
        self.assertEqual(privileged["authority"]["caller"]["id"], "fixture-unrestricted")

    def test_refresh_yields_a_new_id_and_appends_capacity_observed(self) -> None:
        first = self.refresh()
        later = utc_now() + timedelta(seconds=1)
        second = self.refresh(now=later)
        self.assertNotEqual(first["snapshot"]["snapshot_id"], second["snapshot"]["snapshot_id"])
        observed = [row for row in history.read_all()
                    if row["event_type"] == "capacity.observed"]
        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[1]["payload"]["snapshot_id"],
                         second["snapshot"]["snapshot_id"])
        self.assertEqual([row["sequence"] for row in history.read_all()], [1, 2])
        for row in observed:
            self.assertNotIn("caller", row["payload"])

    def test_an_unchanged_world_appends_no_invalidation(self) -> None:
        self.refresh()
        self.refresh(now=utc_now() + timedelta(seconds=1))
        self.assertEqual([row for row in history.read_all()
                          if row["event_type"] == "snapshot.invalidated"], [])

    def test_a_material_change_appends_snapshot_invalidated_naming_the_successor(self) -> None:
        first = self.refresh()
        changed = FakeDoor()
        changed.responses["capture_resource_snapshot"] = json.loads(
            json.dumps(changed.responses["capture_resource_snapshot"]))
        changed.responses["capture_resource_snapshot"]["omen-arc"]["ready"] = False
        second = self.refresh(door=changed, now=utc_now() + timedelta(seconds=1))

        invalidated = [row for row in history.read_all()
                       if row["event_type"] == "snapshot.invalidated"]
        self.assertEqual(len(invalidated), 1)
        payload = invalidated[0]["payload"]
        self.assertEqual(payload["snapshot_id"], first["snapshot"]["snapshot_id"])
        self.assertEqual(payload["successor_snapshot_id"], second["snapshot"]["snapshot_id"])
        self.assertIn("readiness", payload["changed_fields"])

    def test_the_previous_snapshot_file_is_not_touched_by_a_later_capture(self) -> None:
        first = self.refresh()
        before = hashlib.sha256(first["path"].read_bytes()).hexdigest()
        self.refresh(now=utc_now() + timedelta(seconds=1))
        after = hashlib.sha256(first["path"].read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_history_events_carry_their_own_identity(self) -> None:
        from hearth.operator.canonical import sha256_hex

        self.refresh()
        for event in history.read_all():
            preimage = {key: value for key, value in event.items() if key != "event_id"}
            self.assertEqual(event["event_id"], sha256_hex(canonical_json(preimage)))


class FreshnessTests(SnapshotTestCase):
    def test_a_field_past_fresh_until_is_reported_stale_without_editing_the_file(self) -> None:
        moment = utc_now()
        result = self.refresh(now=moment)
        path = result["path"]
        before = path.read_bytes()

        later = moment + timedelta(seconds=200)   # inside the 300 s planning window
        bundle = core.inspect_bundle(Identity(), now=later)
        freshness = bundle["presentation"]["freshness_now"]

        self.assertFalse(freshness["rungs.omen-arc.occupancy"]["fresh"], "30 s TTL")
        self.assertFalse(freshness["rungs.omen-arc.ready"]["fresh"], "120 s TTL")
        self.assertTrue(freshness["hosts.omen.reachable"]["fresh"], "300 s TTL")
        self.assertTrue(freshness["trial_runway.budget_tokens"]["fresh"], "3600 s TTL")
        self.assertEqual(freshness["rungs.omen-arc.ready"]["age_s"], 200)
        self.assertEqual(bundle["presentation"]["planning_window_remaining_s"], 100)

        self.assertEqual(path.read_bytes(), before,
                         "reading freshness must never rewrite the snapshot")
        self.assertNotIn("presentation", bundle["capacity_snapshot"]["document"])

    def test_the_bundle_validates_and_only_authority_and_presentation_differ(self) -> None:
        self.refresh()
        bundle = core.inspect_bundle(Identity())
        jsonschema.validate(bundle, BUNDLE_SCHEMA)
        self.assertEqual(bundle["capacity_snapshot"]["document"]["snapshot_id"],
                         bundle["capacity_snapshot"]["snapshot_id"])

    def test_presentation_never_enters_the_snapshot_identity(self) -> None:
        snapshot = self.capture()
        decorated = json.loads(json.dumps(snapshot))
        decorated["presentation"] = {"generated_at": rfc3339(utc_now())}
        self.assertEqual(identity_of(snapshot, "snapshot_id"),
                         identity_of(decorated, "snapshot_id"))


class RefusalTests(SnapshotTestCase):
    def test_a_missing_current_json_is_refused_with_the_remedy(self) -> None:
        with self.assertRaises(inspection.InspectError) as caught:
            core.inspect_bundle(Identity())
        message = str(caught.exception)
        self.assertIn("missing", message)
        self.assertIn("--refresh", message)

    def test_a_corrupt_current_json_is_refused_and_says_why(self) -> None:
        self.refresh()
        paths.current_path().write_text('{"catalog": {"catalog_version"', encoding="utf-8")
        with self.assertRaises(inspection.InspectError) as caught:
            core.inspect_bundle(Identity())
        self.assertIn("corrupt", str(caught.exception))

    def test_a_current_json_naming_a_missing_snapshot_is_refused(self) -> None:
        result = self.refresh()
        result["path"].unlink()
        with self.assertRaises(inspection.InspectError) as caught:
            core.inspect_bundle(Identity())
        self.assertIn(result["snapshot"]["snapshot_id"], str(caught.exception))

    def test_a_snapshot_whose_bytes_no_longer_match_its_identity_is_refused(self) -> None:
        result = self.refresh()
        document = json.loads(result["path"].read_text(encoding="utf-8"))
        document["snapshot_id"] = "0" * 64
        result["path"].write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(inspection.InspectError) as caught:
            core.inspect_bundle(Identity())
        self.assertIn("does not match", str(caught.exception))

    def test_an_expired_planning_window_is_refused_with_both_times(self) -> None:
        moment = utc_now() - timedelta(seconds=900)
        result = self.refresh(now=moment)
        with self.assertRaises(inspection.InspectError) as caught:
            core.inspect_bundle(Identity())
        message = str(caught.exception)
        self.assertIn("planning window has passed", message)
        self.assertIn(result["snapshot"]["observed_at"], message)
        self.assertIn(result["snapshot"]["planning_valid_until"], message)
        self.assertIn("--refresh", message)

    def test_inspect_never_captures_on_its_own(self) -> None:
        with self.assertRaises(inspection.InspectError):
            core.inspect_bundle(Identity())
        self.assertFalse(paths.snapshots_dir().exists(),
                         "a refused inspection must not have observed anything")
        self.assertEqual(history.read_all(), [])


class FaultInjectionTests(SnapshotTestCase):
    def test_a_door_that_is_down_still_produces_a_snapshot(self) -> None:
        down = FakeDoor(all_down="ConnectionRefusedError: [WinError 1225] 127.0.0.1:8710")
        result = self.refresh(door=down)
        snapshot = result["snapshot"]
        jsonschema.validate(snapshot, SCHEMA)

        self.assertFalse(snapshot["door"]["reachable"]["value"])
        self.assertIn("ConnectionRefused", snapshot["door"]["reachable"]["reason"])
        for name, rung in snapshot["rungs"].items():
            self.assertIsNone(rung["ready"]["value"], (name, rung["ready"]))
            self.assertTrue(rung["ready"]["reason"])
        self.assertIsNone(snapshot["leases"]["value"])
        self.assertTrue(snapshot["leases"]["reason"])
        # The facts that do not come from the door keep their real values.
        self.assertTrue(snapshot["hosts"]["omen"]["reachable"]["value"])
        self.assertEqual(snapshot["trial_runway"]["budget_tokens"]["value"], 200000000)
        self.assertTrue(result["path"].is_file())

    def test_one_probe_timing_out_nulls_only_its_own_fields(self) -> None:
        partial = FakeDoor(unavailable={"capture_resource_snapshot"})
        snapshot = self.capture(door=partial)
        jsonschema.validate(snapshot, SCHEMA)

        arc = snapshot["rungs"]["omen-arc"]
        self.assertIsNone(arc["ready"]["value"])
        self.assertIn("timed out", arc["ready"]["reason"])
        # Its neighbours are untouched, and each keeps its own fresh_until.
        self.assertTrue(snapshot["door"]["reachable"]["value"])
        self.assertTrue(arc["declared_live"]["value"])
        self.assertEqual(arc["rung_state"]["value"]["verdict"], "at_rate")
        self.assertTrue(snapshot["hosts"]["omen"]["services"]["value"]["hearth-gateway"])
        # Every other field keeps its own horizon rather than inheriting the
        # failure's: a blind probe does not shorten what the others measured.
        self.assertNotEqual(arc["ready"]["fresh_until"],
                            snapshot["hosts"]["omen"]["reachable"]["fresh_until"])
        self.assertNotEqual(arc["ready"]["fresh_until"], arc["occupancy"]["fresh_until"])
        self.assertEqual(arc["ready"]["ttl_s"], TTLS["readiness"])

    def test_a_reachability_sweep_that_fails_nulls_only_reachability(self) -> None:
        snapshot = self.capture(runner=fake_cli_runner(error="timed out after 30s"))
        jsonschema.validate(snapshot, SCHEMA)
        self.assertIsNone(snapshot["hosts"]["omen"]["reachable"]["value"])
        self.assertIn("timed out", snapshot["hosts"]["omen"]["reachable"]["reason"])
        self.assertIsNone(snapshot["reachability"]["sweep"]["value"])
        self.assertTrue(snapshot["rungs"]["omen-arc"]["ready"]["value"])

    def test_local_hold_probing_is_read_only_and_reports_the_sentinel(self) -> None:
        sentinel = paths.hearth_root() / "var" / "arc-maintenance.stop"
        sentinel.write_text("held", encoding="utf-8")
        before = sorted(p.name for p in (paths.hearth_root() / "var").iterdir())
        snapshot = self.capture(local=True)
        holds = {row["id"] for row in snapshot["holds"]["value"]}
        self.assertIn("arc-maintenance.stop", holds)
        self.assertEqual(sorted(p.name for p in (paths.hearth_root() / "var").iterdir()),
                         before, "a --local probe must not create or modify anything")


if __name__ == "__main__":
    unittest.main()
