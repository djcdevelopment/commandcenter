from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from hearth.projection.ledger_adapter import map_event, project_ledger
from tools.workflow.validate_events import ValidationError, validate_event, validate_file


def make_hearth_event(event_id: str, **overrides: object) -> dict:
    event = {
        "schema": "hearth-event.v1",
        "event_id": event_id,
        "ts": "2026-07-03T12:00:00+00:00",
        "caller": {"id": "claude-code-derek", "runner_class": "frontier", "node": "omen"},
        "tool": "record_event",
        "args_digest": "sha256:" + "a" * 64,
        "args_preview": '{"event": {"kind": "test"}}',
        "result_digest": "sha256:" + "b" * 64,
        "ok": True,
        "error": None,
        "duration_ms": 42,
        "cost": {"tokens_in": 100, "tokens_out": 25, "watt_s": None},
        "task_id": "task-001",
    }
    event.update(overrides)
    return event


def write_ndjson(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


class MapEventTests(TestCase):
    def test_maps_to_valid_workflow_event(self) -> None:
        workflow_event = map_event(make_hearth_event("he_001"))

        validate_event(workflow_event)  # existing machinery, raises on failure
        self.assertEqual(workflow_event["event_id"], "evt_hearth_he_001")
        self.assertEqual(workflow_event["event_type"], "work.accepted")
        self.assertEqual(workflow_event["timestamp"], "2026-07-03T12:00:00+00:00")
        self.assertEqual(workflow_event["actor"], {"type": "builder", "id": "claude-code-derek"})
        self.assertEqual(workflow_event["status"], "completed")
        self.assertEqual(workflow_event["outcome"], "success")
        self.assertEqual(workflow_event["segment_id"], "task-001")

    def test_carries_economics_into_payload(self) -> None:
        payload = map_event(make_hearth_event("he_002"))["payload"]

        self.assertEqual(payload["duration_ms"], 42)
        self.assertEqual(payload["cost"], {"tokens_in": 100, "tokens_out": 25, "watt_s": None})
        self.assertEqual(payload["tool"], "record_event")
        self.assertEqual(payload["runner_class"], "frontier")
        self.assertEqual(payload["node"], "omen")
        self.assertEqual(payload["args_digest"], "sha256:" + "a" * 64)

    def test_failed_call_maps_to_failed_status(self) -> None:
        workflow_event = map_event(
            make_hearth_event("he_003", ok=False, error="tool exploded")
        )

        validate_event(workflow_event)
        self.assertEqual(workflow_event["status"], "failed")
        self.assertEqual(workflow_event["outcome"], "failure")
        self.assertEqual(workflow_event["payload"]["error"], "tool exploded")

    def test_runner_class_maps_actor_type(self) -> None:
        local = make_hearth_event("he_004", caller={"id": "omen-worker-1", "runner_class": "local", "node": "omen"})
        human = make_hearth_event("he_005", caller={"id": "derek", "runner_class": "human", "node": "omen"})

        self.assertEqual(map_event(local)["actor"]["type"], "builder")
        self.assertEqual(map_event(human)["actor"]["type"], "operator")

    def test_rejects_unknown_schema_and_runner_class(self) -> None:
        with self.assertRaises(ValidationError):
            map_event(make_hearth_event("he_006", schema="hearth-event.v2"))
        with self.assertRaises(ValidationError):
            map_event(make_hearth_event("he_007", caller={"id": "x", "runner_class": "alien", "node": "n"}))


class ProjectLedgerTests(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.ledger = root / "ledger" / "events.ndjson"
        self.target = root / "runs" / "hearth-gateway" / "events.jsonl"
        self.cursor = root / "projection_cursor.json"

    def test_dry_run_writes_nothing(self) -> None:
        write_ndjson(self.ledger, [make_hearth_event("he_101"), make_hearth_event("he_102")])

        summary = project_ledger(self.ledger, self.target, self.cursor, dry_run=True)

        self.assertEqual(summary, {"processed": 2, "skipped": 0, "filtered": 0, "observations": 0,
                          "observations_deduped": 0, "errors": []})
        self.assertFalse(self.target.exists())
        self.assertFalse(self.cursor.exists())

    def test_appends_valid_workflow_events(self) -> None:
        write_ndjson(self.ledger, [make_hearth_event("he_101"), make_hearth_event("he_102", ok=False, error="boom")])

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual(summary["processed"], 2)
        self.assertEqual(validate_file(self.target), [])  # existing machinery
        lines = [json.loads(line) for line in self.target.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event_id"] for e in lines], ["evt_hearth_he_101", "evt_hearth_he_102"])

    def test_rerun_is_idempotent_via_cursor(self) -> None:
        write_ndjson(self.ledger, [make_hearth_event("he_101"), make_hearth_event("he_102")])
        project_ledger(self.ledger, self.target, self.cursor)

        second = project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual(second, {"processed": 0, "skipped": 2, "filtered": 0, "observations": 0,
                         "observations_deduped": 0, "errors": []})

        # ledger grows append-only; only the new event is processed
        with self.ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(make_hearth_event("he_103")) + "\n")
        third = project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual(third, {"processed": 1, "skipped": 2, "filtered": 0, "observations": 0,
                         "observations_deduped": 0, "errors": []})

        lines = self.target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)

    def test_corrupt_cursor_recovers_from_target_without_duplicating(self) -> None:
        """A NUL-filled cursor must not re-bridge rows the target already has.

        Regression for the 2026-08-29 outage: the cursor held 83 NUL bytes (a
        crash-during-write), load_cursor raised on it, and the bridge died before
        it could write a repaired cursor -- so every later run died identically and
        the corpus stopped advancing for nine days while all the surrounding health
        checks stayed green. Recovering from zero would have been just as wrong:
        append_event is unconditional, so it would have duplicated every row.
        """
        write_ndjson(self.ledger, [make_hearth_event("he_101"), make_hearth_event("he_102")])
        project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual(len(self.target.read_text(encoding="utf-8").splitlines()), 2)

        self.cursor.write_bytes(b"\x00" * 83)

        with self.ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(make_hearth_event("he_103")) + "\n")

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual(summary["errors"], [])
        self.assertIn("cursor_corrupt", summary)  # loud, not swallowed
        self.assertEqual(summary["cursor_recovered_from_target"], "he_102")
        self.assertEqual(summary["processed"], 1)  # only the new row
        self.assertEqual(summary["skipped"], 2)

        lines = [json.loads(line) for line in self.target.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["event_id"] for e in lines],
                         ["evt_hearth_he_101", "evt_hearth_he_102", "evt_hearth_he_103"])
        self.assertEqual(json.loads(self.cursor.read_text(encoding="utf-8"))["last_event_id"], "he_103")

    def test_corrupt_cursor_with_no_target_starts_from_zero(self) -> None:
        """No target means nothing was ever bridged, so zero is correct, not duplicative."""
        write_ndjson(self.ledger, [make_hearth_event("he_101")])
        self.cursor.parent.mkdir(parents=True, exist_ok=True)
        self.cursor.write_bytes(b"\x00" * 83)

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual(summary["errors"], [])
        self.assertIsNone(summary["cursor_recovered_from_target"])
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["skipped"], 0)

    def test_cursor_naming_an_absent_event_refuses_rather_than_duplicating(self) -> None:
        """A target/ledger mismatch must stop, not silently re-bridge everything."""
        write_ndjson(self.ledger, [make_hearth_event("he_101")])
        self.cursor.parent.mkdir(parents=True, exist_ok=True)
        self.cursor.write_text(json.dumps({"last_event_id": "he_from_another_ledger", "line": 7}),
                               encoding="utf-8")

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual(summary["processed"], 0)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("refusing to re-bridge from zero", summary["errors"][0])
        self.assertFalse(self.target.exists())

    def test_bad_lines_reported_and_good_lines_still_land(self) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(make_hearth_event("he_101")) + "\n")
            handle.write("not json at all\n")
            handle.write(json.dumps(make_hearth_event("he_103")) + "\n")

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual(summary["processed"], 2)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn(":2:", summary["errors"][0])

    def test_missing_ledger_reports_error(self) -> None:
        summary = project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(len(summary["errors"]), 1)


class HeartbeatFilterTests(TestCase):
    """96% of the live ledger is the lab watching itself. Bridging it poured an
    18 MB git-tracked heartbeat file into the evidence corpus and left
    corpus_event_count meaningless; these pin the filter that stops that."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.ledger = self.tmp / "ledger.ndjson"
        self.target = self.tmp / "runs" / "hearth-gateway" / "events.jsonl"
        self.cursor = self.tmp / "cursor.json"

    def test_watchdog_and_drain_callers_are_filtered(self) -> None:
        write_ndjson(self.ledger, [
            make_hearth_event("he_1", caller={"id": "mechnet-watchdog",
                                              "runner_class": "human", "node": "omen"},
                              tool="mechnet_watchdog.patrol_snapshot"),
            make_hearth_event("he_2", caller={"id": "bankedfire-drain",
                                              "runner_class": "human", "node": "omen"},
                              tool="bankedfire_drain.tick"),
            make_hearth_event("he_3"),  # real work: claude-code-derek
        ])

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["filtered"], 2)
        landed = [json.loads(line)["payload"]["hearth_event_id"]
                  for line in self.target.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(landed, ["he_3"])

    def test_probe_profile_is_filtered_regardless_of_caller(self) -> None:
        write_ndjson(self.ledger, [
            make_hearth_event("he_1", tool="kernel_status", profile="probe"),
            make_hearth_event("he_2", tool="local_generate", profile="unrestricted"),
        ])

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual((summary["processed"], summary["filtered"]), (1, 1))

    def test_filtered_rows_advance_the_cursor_so_they_are_never_reconsidered(self) -> None:
        """A filtered row is a decision, not a failure. If the cursor stalled on
        one, every later run would re-scan the whole heartbeat backlog."""
        write_ndjson(self.ledger, [
            make_hearth_event("he_1"),
            make_hearth_event("he_2", caller={"id": "mechnet-watchdog",
                                              "runner_class": "human", "node": "omen"}),
            make_hearth_event("he_3", caller={"id": "mechnet-watchdog",
                                              "runner_class": "human", "node": "omen"}),
        ])

        first = project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual((first["processed"], first["filtered"]), (1, 2))
        self.assertEqual(json.loads(self.cursor.read_text(encoding="utf-8"))["line"], 3)

        second = project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual((second["processed"], second["filtered"], second["skipped"]), (0, 0, 3))

    def test_malformed_row_does_not_advance_cursor_past_itself(self) -> None:
        """Filtering must not have made bad lines un-retryable: the cursor still
        stops at the last row that mapped or was deliberately filtered."""
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(make_hearth_event("he_1")) + "\n")
            handle.write("not json\n")

        summary = project_ledger(self.ledger, self.target, self.cursor)

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(json.loads(self.cursor.read_text(encoding="utf-8"))["line"], 1)

    def test_filter_can_be_disabled_for_full_fidelity(self) -> None:
        write_ndjson(self.ledger, [
            make_hearth_event("he_1", caller={"id": "mechnet-watchdog",
                                              "runner_class": "human", "node": "omen"}),
            make_hearth_event("he_2", profile="probe"),
        ])

        summary = project_ledger(self.ledger, self.target, self.cursor,
                                 filter_heartbeat=False)

        self.assertEqual((summary["processed"], summary["filtered"]), (2, 0))
        self.assertEqual(validate_file(self.target), [])

    def test_filtered_events_that_do_get_bridged_still_validate(self) -> None:
        write_ndjson(self.ledger, [make_hearth_event("he_1", tool="local_generate",
                                                     profile="unrestricted")])
        project_ledger(self.ledger, self.target, self.cursor)
        self.assertEqual(validate_file(self.target), [])


class CapacityObservationBridgeTests(TestCase):
    """The bridge must emit EVIDENCE, not just mirror rows.

    Every learning projector reads evidence exclusively through
    artifact_refs[].artifact_type (project_capacity.extract_observations), and counts a
    ref it cannot resolve on disk as `unresolved` rather than as an observation. Before
    this, map_event emitted no artifact_refs at all: 1,310 bridged rows yielded
    observation_count 27 and decision_count 0 while the corpus grew 1339 -> 1644.
    """

    def _inference_event(self, event_id: str = "he_inf", **overrides: object) -> dict:
        return make_hearth_event(
            event_id,
            tool="local_generate",
            model="qwen3-coder:30b",
            backend="omen-ollama",
            routed_by="pinned:omen-ollama",
            duration_ms=2000,
            cost={"tokens_in": 100, "tokens_out": 50, "watt_s": None},
            **overrides,
        )

    def test_inference_row_gains_a_resolvable_capacity_observation(self) -> None:
        from hearth.projection.ledger_adapter import build_capacity_observation

        workflow_event = map_event(self._inference_event())
        validate_event(workflow_event)
        refs = workflow_event["artifact_refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["artifact_type"], "capacity_observation")
        self.assertIn("artifacts/", refs[0]["path"])

        observation = build_capacity_observation(self._inference_event())
        self.assertEqual(observation["contract_version"], "capacity-observation.v1")
        self.assertEqual(observation["model_id"], "qwen3-coder:30b")
        self.assertEqual(observation["backend"], "omen-ollama")
        self.assertEqual(observation["observed"]["runtime_s"], 2.0)
        self.assertEqual(observation["observed"]["tokens_per_s"], 25.0)
        self.assertEqual(observation["outcome"], "success")

    def test_non_inference_row_emits_no_observation(self) -> None:
        from hearth.projection.ledger_adapter import build_capacity_observation

        self.assertIsNone(build_capacity_observation(make_hearth_event("he_plain")))
        self.assertEqual(map_event(make_hearth_event("he_plain"))["artifact_refs"], [])

    def test_observation_id_is_derived_so_reruns_are_idempotent(self) -> None:
        from hearth.projection.ledger_adapter import build_capacity_observation

        first = build_capacity_observation(self._inference_event("he_x"))
        second = build_capacity_observation(self._inference_event("he_x"))
        self.assertEqual(first["observation_id"], second["observation_id"])
        self.assertEqual(first, second)

    def test_bridge_writes_the_artifact_file_the_ref_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.ndjson"
            target = root / "runs" / "hearth-gateway" / "events.jsonl"
            write_ndjson(ledger, [self._inference_event("he_1"), make_hearth_event("he_2")])

            summary = project_ledger(ledger, target, root / "cursor.json")
            self.assertEqual(summary["errors"], [])
            self.assertEqual(summary["processed"], 2)
            self.assertEqual(summary["observations"], 1)

            events = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
            refs = [ref for event in events for ref in (event.get("artifact_refs") or [])]
            self.assertEqual(len(refs), 1)

            # The ref must resolve exactly the way project_capacity does.
            from tools.workflow.project_capacity import extract_observations
            observations, unresolved = extract_observations(events, target)
            self.assertEqual(unresolved, 0)
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0]["model_id"], "qwen3-coder:30b")

    def test_dispatch_time_observation_wins_and_is_not_double_counted(self) -> None:
        """Two producers describe the same call. The richer dispatch-time artifact wins;
        the bridge must decline that row even though the two spell the instant
        differently (`...Z` vs `...+00:00`)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.ndjson"
            target = root / "runs" / "hearth-gateway" / "events.jsonl"
            write_ndjson(ledger, [self._inference_event("he_dup", ts="2026-07-03T12:00:00Z")])

            dispatch = root / "runs" / "hearth-offload-claude" / "artifacts" / "2026-07-03" / "obs-d.json"
            dispatch.parent.mkdir(parents=True, exist_ok=True)
            dispatch.write_text(json.dumps({
                "contract_version": "capacity-observation.v1",
                "timestamp": "2026-07-03T12:00:00+00:00",
                "model_id": "qwen3-coder:30b",
                "backend": "omen-ollama",
            }), encoding="utf-8")

            summary = project_ledger(ledger, target, root / "cursor.json")
            self.assertEqual(summary["observations"], 0)
            self.assertEqual(summary["observations_deduped"], 1)
            events = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(events[0]["artifact_refs"], [])

    def test_failed_call_records_a_failure_observation(self) -> None:
        from hearth.projection.ledger_adapter import build_capacity_observation

        observation = build_capacity_observation(
            self._inference_event("he_bad", ok=False, error_code="timeout"))
        self.assertEqual(observation["outcome"], "failure")
        self.assertEqual(observation["failure_class"], "timeout")
