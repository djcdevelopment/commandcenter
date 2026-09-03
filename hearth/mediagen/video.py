"""VisualStoryboard contract generation."""

from __future__ import annotations

from hearth.mediagen.contracts import document_sha256, generate_contract

FIXED_CLIP_DURATION = 81 / 24


def generate_visual_storyboard(document_text: str, *, scene_count: int = 4) -> dict:
    if not isinstance(scene_count, int) or isinstance(scene_count, bool) or not 1 <= scene_count <= 8:
        raise ValueError("scene_count must be between 1 and 8")
    digest = document_sha256(document_text)

    def check(contract: dict) -> None:
        if contract.get("source_document_sha256") != digest:
            raise ValueError("storyboard source_document_sha256 does not match input")
        scenes = contract.get("scenes") or []
        if len(scenes) != scene_count:
            raise ValueError(f"storyboard must contain exactly {scene_count} scenes")
        identifiers = [scene.get("scene_id") for scene in scenes]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("storyboard scene_id values must be unique")
        for scene in scenes:
            if scene.get("aspect_ratio", "16:9") != "16:9":
                raise ValueError("MediaGen v1 accepts only 16:9 storyboard scenes")
            if abs(float(scene["duration_seconds"]) - FIXED_CLIP_DURATION) > 0.0001:
                raise ValueError("MediaGen v1 scenes must be exactly 3.375 seconds")
            scene["target_lane"] = "any"

    return generate_contract(
        template_name="visual_storyboard_v1.jinja",
        schema_id="mediagen.visual-storyboard.v1",
        span_name="hearth.mediagen.contract.storyboard",
        system_prompt="You are a technical documentary storyboard artist. Output valid JSON only.",
        document_text=document_text,
        template_values={"scene_count": scene_count, "fixed_duration": FIXED_CLIP_DURATION},
        extra_validation=check,
    )
