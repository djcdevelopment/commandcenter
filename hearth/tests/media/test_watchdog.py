from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hearth.media import handoff
from hearth.media import watchdog


def healthy_tasks(_names=watchdog.TASK_NAMES):
    return {name: "Running" for name in watchdog.TASK_NAMES}


def healthy_processes():
    return {name: [index + 100] for index, name in enumerate(watchdog.TASK_NAMES)}


def healthy_agent():
    return handoff.AgentStatus(
        available=True,
        capable=True,
        detail="d3d11 device created",
        age_s=3.0,
        lanes=["b70@bus4", "b70@bus9"],
    )


def healthy_am4():
    return {
        "ok": True,
        "status": "ok",
        "rawMounted": True,
        "workWritable": True,
        "sessions": 5,
        "clips": 32,
    }


class PipelineWatchdogTests(unittest.TestCase):
    def test_pause_marker_reports_expected_hold_without_workers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "PAUSED"
            marker.write_text("work workstation in use\n", encoding="utf-8")
            report = watchdog.paused_report(marker, clock=lambda: 100.0)

        self.assertEqual("paused", report["status"])
        self.assertTrue(report["healthy"])
        self.assertTrue(report["paused"])
        self.assertEqual("work workstation in use", report["detail"])
        self.assertEqual([], report["actions"])

    def test_healthy_pipeline_is_a_no_op(self) -> None:
        starts = []
        restarts = []
        report = watchdog.inspect_and_heal(
            task_probe=healthy_tasks,
            process_probe=healthy_processes,
            task_start=starts.append,
            worker_restart=lambda name, pids: restarts.append((name, list(pids))),
            agent_probe=healthy_agent,
            am4_probe=healthy_am4,
            clock=lambda: 100.0,
        )
        self.assertTrue(report["healthy"])
        self.assertEqual("healthy", report["status"])
        self.assertEqual([], report["actions"])
        self.assertEqual([], starts)
        self.assertEqual([], restarts)

    def test_stopped_worker_is_started(self) -> None:
        starts = []
        states = healthy_tasks()
        states["BF6RenderBridge"] = "Ready"
        processes = healthy_processes()
        processes["BF6RenderBridge"] = []
        report = watchdog.inspect_and_heal(
            task_probe=lambda _names: states,
            process_probe=lambda: processes,
            task_start=starts.append,
            worker_restart=lambda _name, _pids: self.fail("agent restart was not needed"),
            agent_probe=healthy_agent,
            am4_probe=healthy_am4,
        )
        self.assertEqual(["BF6RenderBridge"], starts)
        self.assertEqual("healing", report["status"])
        self.assertEqual(
            [{"action": "start_task", "task": "BF6RenderBridge"}],
            report["actions"],
        )

    def test_stale_agent_heartbeat_restarts_only_the_agent(self) -> None:
        restarts = []
        stale = handoff.AgentStatus(
            available=False,
            capable=True,
            detail="render agent heartbeat is 1900s old",
            age_s=1900.0,
            lanes=["b70@bus4", "b70@bus9"],
        )
        report = watchdog.inspect_and_heal(
            task_probe=healthy_tasks,
            process_probe=healthy_processes,
            task_start=lambda _name: self.fail("all tasks were running"),
            worker_restart=lambda name, pids: restarts.append((name, list(pids))),
            agent_probe=lambda: stale,
            am4_probe=healthy_am4,
        )
        self.assertEqual([("BF6RenderAgent", [101])], restarts)
        self.assertEqual("healing", report["status"])

    def test_agent_started_this_tick_is_not_restarted_for_old_heartbeat(self) -> None:
        starts = []
        restarts = []
        states = healthy_tasks()
        states["BF6RenderAgent"] = "Ready"
        processes = healthy_processes()
        processes["BF6RenderAgent"] = []
        stale = handoff.AgentStatus(False, True, "stale", 120.0, [])
        watchdog.inspect_and_heal(
            task_probe=lambda _names: states,
            process_probe=lambda: processes,
            task_start=starts.append,
            worker_restart=lambda name, pids: restarts.append((name, list(pids))),
            agent_probe=lambda: stale,
            am4_probe=healthy_am4,
        )
        self.assertEqual(["BF6RenderAgent"], starts)
        self.assertEqual([], restarts)

    def test_live_but_incapable_agent_is_reported_without_restart_loop(self) -> None:
        restarts = []
        incapable = handoff.AgentStatus(True, False, "no D3D adapter", 2.0, [])
        report = watchdog.inspect_and_heal(
            task_probe=healthy_tasks,
            process_probe=healthy_processes,
            task_start=lambda _name: None,
            worker_restart=lambda name, pids: restarts.append((name, list(pids))),
            agent_probe=lambda: incapable,
            am4_probe=healthy_am4,
        )
        self.assertEqual([], restarts)
        self.assertEqual("unhealthy", report["status"])
        self.assertIn("not GPU-capable", report["errors"][0])

    def test_am4_mount_failure_is_observed_but_not_remotely_mutated(self) -> None:
        bad_am4 = healthy_am4()
        bad_am4.update(ok=False, rawMounted=False)
        report = watchdog.inspect_and_heal(
            task_probe=healthy_tasks,
            process_probe=healthy_processes,
            task_start=lambda _name: None,
            worker_restart=lambda _name, _pids: None,
            agent_probe=healthy_agent,
            am4_probe=lambda: bad_am4,
        )
        self.assertEqual("unhealthy", report["status"])
        self.assertEqual([], report["actions"])
        self.assertIn("rawMounted=False", report["errors"][0])

    def test_detached_worker_process_is_live_and_not_duplicated(self) -> None:
        states = healthy_tasks()
        states["BF6RenderBridge"] = "Ready"
        starts = []
        report = watchdog.inspect_and_heal(
            task_probe=lambda _names: states,
            process_probe=healthy_processes,
            task_start=starts.append,
            worker_restart=lambda _name, _pids: None,
            agent_probe=healthy_agent,
            am4_probe=healthy_am4,
        )
        self.assertTrue(report["healthy"])
        self.assertEqual([], starts)
        self.assertEqual("Ready", report["tasks"]["BF6RenderBridge"]["scheduler_state"])
        self.assertTrue(report["tasks"]["BF6RenderBridge"]["running"])

    def test_status_is_atomic_and_event_log_records_transitions_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = root / "status.json"
            events = root / "events.ndjson"
            healthy = {
                "status": "healthy",
                "healthy": True,
                "errors": [],
                "actions": [],
            }
            watchdog.persist_report(healthy, status_path=status, event_log_path=events)
            watchdog.persist_report(healthy, status_path=status, event_log_path=events)
            self.assertEqual(1, len(events.read_text(encoding="utf-8").splitlines()))
            self.assertEqual(healthy, json.loads(status.read_text(encoding="utf-8")))
            self.assertEqual([], list(root.glob("*.tmp")))

            failed = {
                "status": "unhealthy",
                "healthy": False,
                "errors": ["AM4 down"],
                "actions": [],
            }
            watchdog.persist_report(failed, status_path=status, event_log_path=events)
            self.assertEqual(2, len(events.read_text(encoding="utf-8").splitlines()))


if __name__ == "__main__":
    unittest.main()
