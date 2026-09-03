"""Podcast contract generation and CPU synthesis."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

from hearth.mediagen.contracts import document_sha256, generate_contract
from hearth.observation.telemetry import trace_span
from hearth.schemas.validate import validate

_pipeline: Optional[Any] = None


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


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from kokoro import KPipeline
        _pipeline = KPipeline(lang_code="a", device="cpu")
    return _pipeline


def synthesize_podcast(contract: dict, output_path: Path) -> dict:
    """Synthesize a validated PodcastScript contract to a mono 24 kHz WAV."""
    import numpy as np
    import soundfile as sf

    validate(contract, schema_id="mediagen.podcast-script.v1")
    with trace_span(
        "hearth.mediagen.audio.synthesize",
        attributes={"script.turns": len(contract.get("turns", []))},
    ) as span:
        pipeline = _get_pipeline()
        chunks = []
        sample_rate = 24000
        speakers = contract["speakers"]
        for turn in contract["turns"]:
            voice_id = speakers[turn["speaker"]]["voice_id"]
            for _, _, audio in pipeline(turn["text"], voice=voice_id, speed=1.0):
                chunks.append(audio)
            pause_ms = turn.get("pause_after_ms", 250)
            if pause_ms:
                chunks.append(np.zeros(int(pause_ms * sample_rate / 1000), dtype=np.float32))
        if not chunks:
            raise ValueError("podcast contract produced no audio")
        audio = np.concatenate(chunks)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), audio, sample_rate, subtype="FLOAT")
        duration = len(audio) / sample_rate
        span.set_attribute("audio.duration_s", duration)
        return {
            "duration_seconds": duration, "sample_rate": sample_rate, "channels": 1,
            "file_size_bytes": output_path.stat().st_size,
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        }
