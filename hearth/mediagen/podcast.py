"""Podcast contract generation and CPU synthesis."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from hearth.mediagen.contracts import document_sha256, generate_contract
from hearth.observation.telemetry import trace_span
from hearth.schemas.validate import validate


def generate_podcast_script(document_text: str) -> dict:
    digest = document_sha256(document_text)

    def check(contract: dict) -> None:
        if contract.get("source_document_sha256") != digest:
            raise ValueError("podcast contract source_document_sha256 does not match input")

    return generate_contract(
        template_name="podcast_script_v1.jinja",
        schema_id="mediagen.podcast-script.v1",
        span_name="hearth.mediagen.contract.podcast",
        system_prompt="You are a senior technical podcast producer. Output valid JSON only.",
        document_text=document_text,
        extra_validation=check,
    )


def synthesize_podcast(
    contract: dict, output_path: Path, *, profile_name: Optional[str] = None
) -> dict:
    """Synthesize a validated PodcastScript contract to a mono 24 kHz WAV."""
    from hearth.mediagen.audio.synthesizer import synthesize_script

    validate(contract, schema_id="mediagen.podcast-script.v1")
    with trace_span(
        "hearth.mediagen.audio.synthesize",
        attributes={"script.turns": len(contract.get("turns", []))},
    ) as span:
        details = synthesize_script(contract, output_path, profile_name=profile_name)
        span.set_attribute("audio.duration_s", details["duration_seconds"])
        if details.get("voice_source"):
            span.set_attribute("audio.voice_source", details["voice_source"])
        if details.get("voice_ids"):
            span.set_attribute("audio.voice_ids", details["voice_ids"])
        return details

