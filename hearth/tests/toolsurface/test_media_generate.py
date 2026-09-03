from __future__ import annotations

import os
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hearth.toolsurface import media_generate


class FakeService:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, **kwargs):
        self.calls.append(kwargs)
        return {"job_id": "job_" + "a" * 32, "status": "queued"}


def test_submit_pipeline_packs_scoped_document_and_returns_immediately(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Source\nUseful details", encoding="utf-8")
    service = FakeService()
    previous = os.environ.get("HEARTH_SCOPE")
    os.environ["HEARTH_SCOPE"] = str(tmp_path)
    try:
        with patch.object(media_generate, "current_identity",
                          return_value=SimpleNamespace(caller_id="tester")), \
             patch.object(media_generate, "get_execution_service", return_value=service):
            result = media_generate.submit_media_pipeline(str(source))
    finally:
        if previous is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = previous
    assert result["status"] == "queued"
    assert service.calls[0]["operation_name"] == "media.pipeline"
    assert service.calls[0]["arguments"]["scene_count"] == 4


def test_submit_animation_validates_real_png(tmp_path: Path) -> None:
    source = tmp_path / "still.png"
    source.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZK4sAAAAASUVORK5CYII="
    ))
    service = FakeService()
    previous = os.environ.get("HEARTH_SCOPE")
    os.environ["HEARTH_SCOPE"] = str(tmp_path)
    try:
        with patch.object(media_generate, "current_identity",
                          return_value=SimpleNamespace(caller_id="tester")), \
             patch.object(media_generate, "get_execution_service", return_value=service), \
             patch("hearth.toolsurface.image_generate._workflow_registry", return_value={
                 "workflows": [{"id": "wan2-i2v", "enabled": True,
                                "allowed_lane_ids": ["b70@bus4", "b70@bus9"]}],
             }):
            result = media_generate.submit_video_animation(
                "slow pan", still_image_path=str(source), target_lane="b70@bus4"
            )
    finally:
        if previous is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = previous
    assert result["operation"] == "media.animate"
    assert service.calls[0]["arguments"]["source_media_type"] == "image/png"
