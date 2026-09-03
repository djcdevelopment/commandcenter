"""MediaGen tool surface — entry point for multimodal media workloads.

Provides the `submit_podcast` and `submit_video_animation` tool functions
that will be exposed via MCP.  Each function validates its input contract
at the boundary before handing off to execution runtimes.

Image generation remains in `image_generate.py`; this module handles the
newer modalities (audio, video, composite) under the `hearth.mediagen`
namespace.
"""

from __future__ import annotations

from typing import Optional


def submit_podcast(
    document_path: str,
    title: Optional[str] = None,
    voice_profile: str = "alex_sam",
) -> dict:
    """Queue a podcast generation job: document → PodcastScript/v1 → Kokoro TTS → MP3.

    Args:
        document_path: Absolute path to the source document (PDF, TXT, MD).
        title: Optional episode title; auto-generated from document if omitted.
        voice_profile: Speaker pair identifier. Default "alex_sam"
            (af_heart + am_adam).

    Returns:
        Job submission receipt with job_id and initial status.

    Raises:
        NotImplementedError: Until Phase 2 execution is wired.
    """
    raise NotImplementedError(
        "submit_podcast is defined in Phase 1 (contracts); "
        "execution will be wired in Phase 2 (podcast vertical slice)"
    )


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
