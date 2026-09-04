import json
import tempfile
import unittest
from pathlib import Path

from hearth.projection.public_portfolio import (
    PublicProjectionError,
    build_snapshot,
    validate_public_snapshot,
)


def _gateway_event(tool: str, ts: str, **overrides) -> dict:
    event = {
        "schema": "hearth-event.v1",
        "event_id": "private-event-id",
        "ts": ts,
        "caller": {"id": "secret-host", "runner_class": "local", "node": "10.0.0.8"},
        "tool": tool,
        "args_preview": r'{"path":"C:\\Users\\derek\\private.txt"}',
        "result_digest": "private-result",
        "ok": True,
        "error": None,
        "duration_ms": 10,
        "cost": {"tokens_in": None, "tokens_out": None, "watt_s": None},
        "task_id": "private-task",
    }
    event.update(overrides)
    return event


def _execution_event(sequence: int, event_type: str, ts: str, **overrides) -> dict:
    event = {
        "schema": "hearth-execution-event.v1",
        "sequence": sequence,
        "event_id": f"evt_{sequence:032x}",
        "timestamp": ts,
        "event_type": event_type,
        "request_id": f"req_{'a' * 32}",
        "job_id": f"job_{'b' * 32}",
        "invocation_id": None,
        "principal": None,
        "source": None,
        "operation": None,
        "desired": None,
        "observed": None,
        "artifacts": [],
        "reason": r"failed at C:\Users\derek\secret.txt",
    }
    event.update(overrides)
    return event


class PublicPortfolioProjectionTests(unittest.TestCase):
    def _ledgers(self, root: Path) -> tuple[Path, Path]:
        gateway = root / "gateway.ndjson"
        gateway_events = [
            _gateway_event("kernel_status", "2026-08-31T01:00:00Z"),
            *[
                _gateway_event(
                    "local_generate",
                    f"2026-09-0{1 + (index % 2)}T02:00:00Z",
                    backend="omen-arc",
                    cost={"tokens_in": 100, "tokens_out": 20, "watt_s": None},
                )
                for index in range(12)
            ],
            _gateway_event(
                "mechnet_watchdog.rung_state",
                "2026-09-02T03:00:00Z",
                outcome="at_rate",
            ),
        ]
        gateway.write_text("\n".join(json.dumps(event) for event in gateway_events) + "\n", encoding="utf-8")

        execution = root / "execution.ndjson"
        execution_events = [
            _execution_event(
                1,
                "request.accepted",
                "2026-09-01T01:00:00Z",
                desired={"idempotency_key": "secret-key"},
            ),
            _execution_event(2, "invocation.started", "2026-09-01T01:00:01Z", invocation_id=f"inv_{'c' * 31}1"),
            _execution_event(3, "invocation.failed", "2026-09-01T01:00:02Z", invocation_id=f"inv_{'c' * 31}1"),
            _execution_event(4, "invocation.started", "2026-09-01T01:00:03Z", invocation_id=f"inv_{'c' * 31}2"),
            _execution_event(5, "invocation.succeeded", "2026-09-01T01:00:04Z", invocation_id=f"inv_{'c' * 31}2"),
            _execution_event(
                6,
                "artifact.recorded",
                "2026-09-01T01:00:05Z",
                artifacts=[{
                    "artifact_id": f"art_{'d' * 32}",
                    "sha256": "e" * 64,
                    "size": 42,
                    "media_type": "text/plain",
                    "filename": "secret.txt",
                }],
            ),
            _execution_event(7, "job.succeeded", "2026-09-01T01:00:06Z"),
            _execution_event(8, "delivery.projected", "2026-09-01T01:00:07Z"),
        ]
        execution.write_text("\n".join(json.dumps(event) for event in execution_events) + "\n", encoding="utf-8")
        return gateway, execution

    def test_projection_emits_only_aggregates_and_fixed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway, execution = self._ledgers(Path(tmp))
            snapshot = build_snapshot(gateway, execution, exporter_revision="test")
        self.assertEqual(snapshot["gateway"]["events"], 14)
        self.assertEqual(snapshot["gateway"]["inference"]["local_calls"], 12)
        self.assertEqual(snapshot["gateway"]["inference"]["tokens_in"], 1200)
        self.assertEqual(snapshot["execution"]["retried_jobs"], 1)
        self.assertEqual(snapshot["execution"]["recovered_jobs"], 1)
        self.assertEqual(snapshot["execution"]["artifacts_recorded"], 1)
        self.assertTrue(snapshot["execution"]["projection_replay_verified"])
        self.assertEqual(snapshot["mechnet"]["snapshot_state"], "at_rate")
        rendered = json.dumps(snapshot, ensure_ascii=False)
        for private in ("secret-host", "10.0.0.8", "Users", "private-task", "secret.txt", "secret-key"):
            self.assertNotIn(private, rendered)
        validate_public_snapshot(snapshot)

    def test_small_weekly_cells_are_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway, execution = self._ledgers(Path(tmp))
            snapshot = build_snapshot(gateway, execution, exporter_revision="test")
        first_week = snapshot["weekly"][0]
        self.assertIsNone(first_week["operations"])
        self.assertGreater(first_week["suppressed_cells"], 0)

    def test_non_contiguous_execution_stream_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway, execution = self._ledgers(Path(tmp))
            events = [json.loads(line) for line in execution.read_text(encoding="utf-8").splitlines()]
            events[2]["sequence"] = 99
            execution.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(PublicProjectionError, "not contiguous"):
                build_snapshot(gateway, execution, exporter_revision="test")

    def test_invalid_execution_lifecycle_fails_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway, execution = self._ledgers(Path(tmp))
            events = [json.loads(line) for line in execution.read_text(encoding="utf-8").splitlines()]
            events[2]["invocation_id"] = f"inv_{'f' * 32}"
            execution.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(PublicProjectionError, "replay failed"):
                build_snapshot(gateway, execution, exporter_revision="test")

    def test_digest_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gateway, execution = self._ledgers(Path(tmp))
            snapshot = build_snapshot(gateway, execution, exporter_revision="test")
        snapshot["gateway"]["events"] += 1
        with self.assertRaisesRegex(PublicProjectionError, "digest"):
            validate_public_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
