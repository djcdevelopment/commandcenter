from __future__ import annotations

import hashlib
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hearth.execution import ArtifactStore, CapacityLeaseStore, ExecutionLedger, ExecutionService
from hearth.execution.operations import load_operations
from hearth.mediagen.execution import MediaGenerationSubsystem


def test_contract_generation_waits_for_llm_tenancy() -> None:
    class Session:
        def __init__(self) -> None:
            self.states = [
                {"active": True, "state": "imagegen"},
                {"active": False, "state": "llm"},
            ]
            self.verified = 0

        def status(self) -> dict:
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return {"session": state}

        def verify_arcserve(self) -> bool:
            self.verified += 1
            return True

    session = Session()
    service = SimpleNamespace(_image_dispatcher=SimpleNamespace(session=session))
    subsystem = MediaGenerationSubsystem(service=service, autostart=False)
    with patch("hearth.mediagen.execution.time.sleep"):
        subsystem._wait_for_arcserve(threading.Event())
    assert session.verified == 1


def _wait(service: ExecutionService, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = service.get_job(job_id)
        if state and state["status"] in {"succeeded", "failed", "cancelled"}:
            return state
        time.sleep(0.02)
    raise AssertionError("MediaGen job did not finish")


def test_podcast_runs_on_delegated_worker_and_records_result(tmp_path: Path) -> None:
    service = ExecutionService(
        ledger=ExecutionLedger(tmp_path / "ledger"),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        leases=CapacityLeaseStore(tmp_path / "coordination.sqlite"),
        operations=load_operations(), generate=lambda **_: {"ok": True},
        recover_pending=False, workers=1,
    )
    subsystem = MediaGenerationSubsystem(service=service)
    service._media_dispatcher = subsystem

    def fake_script(text: str) -> dict:
        return {
            "schema": "mediagen.podcast-script.v1", "version": "1.0.0", "title": "T",
            "source_document_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "speakers": {
                "host_a": {"name": "Alex", "voice_id": "af_heart", "persona": "A"},
                "host_b": {"name": "Sam", "voice_id": "am_adam", "persona": "B"},
            },
            "turns": [{"speaker": "host_a", "text": "Hi"},
                      {"speaker": "host_b", "text": "Hello"}],
        }

    def fake_synthesize(_contract: dict, output: Path) -> dict:
        output.write_bytes(b"RIFF-test-wav")
        return {"duration_seconds": 1.0, "sample_rate": 24000, "channels": 1,
                "file_size_bytes": output.stat().st_size,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}

    text = "source"
    try:
        with patch("hearth.mediagen.execution.OUTPUT_DIR", tmp_path / "outputs"), \
             patch.object(subsystem, "_wait_for_arcserve"), \
             patch("hearth.mediagen.execution.podcast.generate_podcast_script",
                   side_effect=fake_script), \
             patch("hearth.mediagen.execution.podcast.synthesize_podcast",
                   side_effect=fake_synthesize):
            state = service.submit(
                operation_name="media.podcast",
                arguments={"document_text": text, "document_name": "source.md",
                           "document_sha256": hashlib.sha256(text.encode()).hexdigest(),
                           "voice_profile": "alex_sam"},
                principal={"type": "hearth_caller", "id": "test", "authenticated": True},
                source={"transport": "test", "adapter": "pytest"},
            )
            final = _wait(service, state["job_id"])
        assert final["status"] == "succeeded", final.get("reason")
        assert any(item.get("role") == "checkpoint" for item in final["artifacts"])
        assert any(item.get("media_type") == "audio/wav" for item in final["artifacts"])
        assert final["result_artifact_id"].startswith("art_")
    finally:
        service.close()


def test_cancel_before_dispatch_is_terminal(tmp_path: Path) -> None:
    service = ExecutionService(
        ledger=ExecutionLedger(tmp_path / "ledger"),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        leases=CapacityLeaseStore(tmp_path / "coordination.sqlite"),
        operations=load_operations(), generate=lambda **_: {"ok": True},
        recover_pending=False, workers=1,
    )
    subsystem = MediaGenerationSubsystem(service=service, autostart=False)
    service._media_dispatcher = subsystem
    text = "source"
    try:
        state = service.submit(
            operation_name="media.podcast",
            arguments={"document_text": text, "document_name": "source.md",
                       "document_sha256": hashlib.sha256(text.encode()).hexdigest(),
                       "voice_profile": "alex_sam"},
            principal={"type": "hearth_caller", "id": "test", "authenticated": True},
            source={"transport": "test", "adapter": "pytest"},
        )
        result = subsystem.cancel(state["job_id"], reason="test cancellation")
        assert result["status"] == "cancelled"
    finally:
        service.close()
