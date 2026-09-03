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
    duration_s: float = 3.4,
) -> dict:
    """Queue a video animation job via ComfyUI (Wan2-I2V/LTX)."""
    with trace_span("hearth.job.video", attributes={"job.image_path": still_image_path}) as span:
        job_id = "video_" + new_invocation_id()
        span.set_attribute("job.id", job_id)
        
        path = Path(still_image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {still_image_path}")
            
        import shutil
        # ComfyUI loads inputs from its input directory usually, or from absolute paths depending on LoadImage custom nodes.
        # But standard LoadImage expects the file in ComfyUI/input.
        comfy_input_dir = Path(r"E:\Comfy-Desktop\ComfyUI-Installs\OMEN\ComfyUI\input")
        comfy_input_dir.mkdir(parents=True, exist_ok=True)
        
        dest_filename = f"{job_id}_{path.name}"
        dest_path = comfy_input_dir / dest_filename
        shutil.copy2(str(path), str(dest_path))
        
        # Submits to the standard ImageGen dispatcher which manages the B70 lane queue
        # In Phase 3, this leverages the existing .NET agent.
        from hearth.toolsurface.image_generate import submit_image
        
        receipt = submit_image(
            workflow_id="wan2-i2v",
            parameters={
                "input_image": dest_filename,
                "prompt": motion_prompt,
                "duration_seconds": duration_s,
                "fps": 24,
            },
            strategy="single",
            priority="normal"
        )
        
        # Override the job type explicitly to 'video' for tracking, but keep the underlying execution ledger job_id.
        # Actually, let's just return the receipt directly.
        return receipt
