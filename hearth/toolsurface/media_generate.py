"""MediaGen tool surface — entry point for multimodal media workloads.

Provides the `submit_podcast` and `submit_video_animation` tool functions
that will be exposed via MCP.  Each function validates its input contract
at the boundary before handing off to execution runtimes.

Image generation remains in `image_generate.py`; this module handles the
newer modalities (audio, video, composite) under the `hearth.mediagen`
namespace.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from hearth.execution.ids import new_invocation_id
from hearth.mediagen import podcast
from hearth.observation.telemetry import trace_span


def submit_podcast(
    document_path: str,
    title: Optional[str] = None,
    voice_profile: str = "alex_sam",
) -> dict:
    """Queue a podcast generation job: document -> PodcastScript/v1 -> Kokoro TTS -> MP3/WAV."""
    with trace_span("hearth.job.podcast", attributes={"job.document_path": document_path}) as span:
        job_id = "podcast_" + new_invocation_id()
        span.set_attribute("job.id", job_id)
        
        path = Path(document_path)
        if not path.is_file():
            raise FileNotFoundError(f"Document not found: {document_path}")
            
        doc_text = path.read_text(encoding="utf-8", errors="replace")
        
        # 1. Generate and Validate Contract
        script_contract = podcast.generate_podcast_script(doc_text)
        if title:
            script_contract["title"] = title
            
        # 2. Execute on CPU
        output_file = podcast.OUTPUT_DIR / f"podcast_{job_id}.wav"
        artifact = podcast.synthesize_podcast(script_contract, output_file)
        
        # 3. Fill out artifact metadata
        artifact.update({
            "schema": "mediagen.media-artifact.v1",
            "version": "1.0.0",
            "artifact_id": f"art_{job_id}",
            "media_type": "audio/wav",
            "codec": "pcm_f32le",
            "source_contract_schema": "mediagen.podcast-script.v1",
        })
        # Add trace ID for Jaeger
        from hearth.observation.telemetry import get_current_traceparent
        tp = get_current_traceparent()
        if tp:
            artifact["trace_id"] = tp.split("-")[1]
            
        # Validate final artifact contract
        from hearth.schemas.validate import validate
        validate(artifact, schema_id="mediagen.media-artifact.v1")
        
        return {
            "ok": True,
            "job_id": job_id,
            "status": "succeeded",
            "artifact": artifact,
            "output_path": str(output_file)
        }


def submit_video_animation(
    still_image_path: str,
    motion_prompt: str,
    duration_s: float = 4.0,
) -> dict:
    """Queue a video animation job: concept still → I2V animation → MP4.

    Args:
        still_image_path: Absolute path to the source keyframe image.
        motion_prompt: Text description of the desired camera motion / animation.
        duration_s: Target clip duration in seconds (1.0–16.0, default 4.0).

    Returns:
        Job submission receipt with job_id and initial status.

    Raises:
        NotImplementedError: Until Phase 3 execution is wired.
    """
    raise NotImplementedError(
        "submit_video_animation is defined in Phase 1 (contracts); "
        "execution will be wired in Phase 3 (video vertical slice)"
    )
