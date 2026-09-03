from __future__ import annotations

from unittest.mock import patch

import pytest

from hearth.mediagen.video import FIXED_CLIP_DURATION, generate_visual_storyboard


def test_storyboard_locks_scene_count_duration_and_scheduler_lane() -> None:
    captured = {}

    def fake_generate_contract(**kwargs):
        captured.update(kwargs)
        contract = {
            "source_document_sha256": __import__("hashlib").sha256(b"doc").hexdigest(),
            "scenes": [{"scene_id": "scene_001", "duration_seconds": FIXED_CLIP_DURATION,
                        "aspect_ratio": "16:9", "target_lane": "b70@bus9"}],
        }
        kwargs["extra_validation"](contract)
        return contract

    with patch("hearth.mediagen.video.generate_contract", side_effect=fake_generate_contract):
        value = generate_visual_storyboard("doc", scene_count=1)
    assert value["scenes"][0]["target_lane"] == "any"
    assert captured["template_values"]["scene_count"] == 1


def test_storyboard_rejects_out_of_budget_count() -> None:
    with pytest.raises(ValueError):
        generate_visual_storyboard("doc", scene_count=9)
