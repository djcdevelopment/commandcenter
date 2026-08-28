from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from hearth.execution import ExecutionLedger
from hearth.integrations.bf6_workflows import BF6WorkflowGateway, callback_signature


def test_intake_correlation_signed_terminal_and_idempotency() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        ledger = ExecutionLedger(Path(temporary) / "execution")
        forwarded = []

        def forward(url, body):
            forwarded.append((url, body))
            return {
                "external_run_id": "hatchet-run-123",
                "details_url": "http://hatchet/runs/hatchet-run-123",
            }

        gateway = BF6WorkflowGateway(
            ledger=ledger,
            forward=forward,
            orchestrator_url="http://orchestrator",
            callback_base_url="http://hearth",
        )
        request = {
            "session_id": "20260828T120000Z-deadbeef",
            "segment_path": "raw/20260828T120000Z-deadbeef/segment-001.mkv",
            "media_namespace": "integration",
            "render_mode": "software",
            "skip_model_signals": True,
            "idempotency_key": "integration:bf6:one",
        }
        caller = SimpleNamespace(id="derek", runner_class="human")
        accepted = gateway.submit(request, caller=caller)
        duplicate = gateway.submit(request, caller=caller)
        assert duplicate["idempotent"] is True
        assert duplicate["job_id"] == accepted["job_id"]
        assert len(forwarded) == 1
        correlation = forwarded[0][1]["correlation"]
        assert forwarded[0][1]["schema_version"] == 2
        assert correlation["hearth_job_id"] == accepted["job_id"]
        assert correlation["hearth_request_id"] == accepted["request_id"]

        receipt = {
            "schema_version": 1,
            "outcome": "succeeded",
            "hearth_request_id": accepted["request_id"],
            "hearth_job_id": accepted["job_id"],
            "session_id": request["session_id"],
            "external_run_id": "hatchet-run-123",
            "details_url": "http://hatchet/runs/hatchet-run-123",
            "trace_id": None,
            "artifact_refs": [{
                "artifact_id": "clip-r0-horizontal",
                "kind": "horizontal",
                "path": "drafts/20260828T120000Z-deadbeef/clip.mp4",
                "sha256": "a" * 64,
                "size": 1234,
            }],
            "completed_at": "2026-08-28T12:30:00Z",
            "error_summary": None,
        }
        body = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
        terminal = gateway.receive_terminal(
            accepted["job_id"], body, callback_signature(body, "test-secret"), secret="test-secret"
        )
        repeated = gateway.receive_terminal(
            accepted["job_id"], body, callback_signature(body, "test-secret"), secret="test-secret"
        )
        assert terminal == {"accepted": True, "idempotent": False, "status": "succeeded"}
        assert repeated == {"accepted": True, "idempotent": True, "status": "succeeded"}
        state = ledger.get_job(accepted["job_id"])
        assert state["status"] == "succeeded"
        assert state["external_run_id"] == "hatchet-run-123"
        assert len(state["artifacts"]) == 1
        assert state["deliveries"][0]["adapter"] == "bf6-review"


def test_manual_render_has_revision_attempt_idempotency_and_render_endpoint() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        ledger = ExecutionLedger(Path(temporary) / "execution")
        forwarded = []

        def forward(url, body):
            forwarded.append((url, body))
            return {
                "external_run_id": "hatchet-render-123",
                "details_url": "http://hatchet/runs/hatchet-render-123",
            }

        gateway = BF6WorkflowGateway(
            ledger=ledger, forward=forward, orchestrator_url="http://orchestrator"
        )
        clip = {
            "clip_id": "session-clip",
            "session_id": "session",
            "segment_path": "raw/session/segment.mkv",
            "segment_paths": ["raw/session/segment.mkv"],
            "start_seconds": 1.0,
            "end_seconds": 4.0,
            "clip_revision": 2,
            "render_attempts": 3,
        }
        request = {
            "session_id": "session",
            "media_namespace": "integration",
            "clip": clip,
            "render_attempt": 3,
        }
        caller = SimpleNamespace(id="bf6-dispatcher", runner_class="local")
        accepted = gateway.submit_render(request, caller=caller)
        repeated = gateway.submit_render(request, caller=caller)

        assert accepted["job_id"] == repeated["job_id"]
        assert repeated["idempotent"] is True
        assert len(forwarded) == 1
        assert forwarded[0][0].endswith("/v1/workflows/bf6/renders")
        assert forwarded[0][1]["schema_version"] == 2
        assert forwarded[0][1]["render_attempt"] == 3
