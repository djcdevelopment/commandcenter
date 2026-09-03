from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from hearth.mediagen.jobspec import MediaArgumentError, validate_media_arguments


def operation(name: str, limit: int = 30 * 1024 * 1024):
    return SimpleNamespace(name=name, max_prompt_bytes=limit)


def test_document_is_packed_privately_and_public_state_uses_hash() -> None:
    text = "hello media"
    public, packed = validate_media_arguments(operation("media.pipeline"), {
        "document_text": text, "document_name": "source.md",
        "document_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "voice_profile": "alex_sam", "scene_count": 4,
    })
    assert "document_text" not in public
    assert public["scene_count"] == 4
    assert json.loads(packed)["document_text"] == text


def test_document_rejects_wrong_hash_and_scene_budget() -> None:
    with pytest.raises(MediaArgumentError):
        validate_media_arguments(operation("media.pipeline"), {
            "document_text": "hello", "document_name": "source.txt",
            "document_sha256": "0" * 64, "scene_count": 9,
        })


def test_animation_accepts_fixed_private_image_payload() -> None:
    content = b"image bytes"
    public, packed = validate_media_arguments(operation("media.animate"), {
        "motion_prompt": "slow pan", "source_image_b64": base64.b64encode(content).decode(),
        "source_image_name": "still.png", "source_media_type": "image/png",
        "source_artifact_id": None, "target_lane": "b70@bus9",
    })
    assert public["target_lane"] == "b70@bus9"
    assert public["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert json.loads(packed)["motion_prompt"] == "slow pan"


def test_animation_rejects_unknown_lane() -> None:
    with pytest.raises(MediaArgumentError):
        validate_media_arguments(operation("media.animate"), {
            "motion_prompt": "pan", "source_image_b64": base64.b64encode(b"x").decode(),
            "source_image_name": "still.png", "source_media_type": "image/png",
            "target_lane": "somewhere",
        })
