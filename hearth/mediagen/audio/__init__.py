"""Hearth Audio Synthesis subsystem for podcast and narration generation."""

from __future__ import annotations

from typing import Any

from .registry import VoiceProfile, VoiceProfileRegistry, get_voice_registry

__all__ = [
    "AudioSynthesizer",
    "VoiceProfile",
    "VoiceProfileRegistry",
    "get_voice_registry",
    "synthesize_script",
]

_LAZY = frozenset({"AudioSynthesizer", "synthesize_script"})


def __getattr__(name: str) -> Any:
    """Defer the synthesizer import so the voice registry stays cheap to reach.

    synthesizer.py imports torch, numpy and soundfile at module scope. Importing any
    submodule of this package runs this __init__ first, so an eager re-export would drag
    a GPU stack into hearth.mediagen.jobspec._document() -- argument validation that runs
    on the gateway request path, before a job is even durable, where an ImportError would
    surface as a crash rather than a MediaArgumentError. Guarded by
    tests/mediagen/test_audio.py::test_jobspec_validation_does_not_import_torch.
    """
    if name in _LAZY:
        from . import synthesizer

        return getattr(synthesizer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
