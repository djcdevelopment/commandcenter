"""Audio synthesis engine adapter for Intel Arc Pro B70 XPU and Host CPU."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import numpy as np
import soundfile as sf
import torch

from hearth.mediagen.audio.pacing import splice_dialogue
from hearth.mediagen.audio.registry import VoiceProfile, get_voice_registry

THREAD_CAP = 4


def _resolve_voice(
    role: str, speakers: dict, profile_voice: str, registry: VoiceProfileRegistry
) -> tuple[str, str, Optional[str]]:
    """Pick the Kokoro voice for one speaker: the contract wins if it names a real one.

    Returns (voice, source, warning). The prompt template emits the role labels
    "host_a"/"host_b" as voice_id rather than physical voices, so the profile path is the
    normal case and must stay quiet -- a signal that fires on every job is noise. A
    voice_id that is neither recognized nor its own role label means the model invented
    something, and that is worth a warning on the job.
    """
    declared = speakers.get(role, {}).get("voice_id")
    if registry.is_recognized_voice(declared):
        return declared, "contract", None
    if isinstance(declared, str) and declared.strip() and declared != role:
        return profile_voice, "profile", (
            f"{role}: contract voice_id {declared!r} is not a recognized Kokoro voice; "
            f"used the voice profile's {profile_voice!r} instead"
        )
    return profile_voice, "profile", None


class AudioSynthesizer:
    """Production audio synthesizer with automatic B70 GPU acceleration and CPU fallback."""

    def __init__(self) -> None:
        self._pipelines: dict[str, Any] = {}
        self._device_mode: Optional[str] = None

    def _init_pipelines(self) -> dict[str, Any]:
        if self._pipelines:
            return self._pipelines

        # Probe for Intel Arc Pro B70 XPU capability
        has_xpu = hasattr(torch, "xpu") and torch.xpu.is_available()
        xpu_count = torch.xpu.device_count() if has_xpu else 0

        from kokoro import KPipeline

        if has_xpu and xpu_count >= 2:
            self._device_mode = "xpu_dual"
            self._pipelines = {
                "host_a": KPipeline(lang_code="a", device="xpu:0"),
                "host_b": KPipeline(lang_code="a", device="xpu:1"),
            }
        elif has_xpu and xpu_count == 1:
            self._device_mode = "xpu:0"
            pipe = KPipeline(lang_code="a", device="xpu:0")
            self._pipelines = {"host_a": pipe, "host_b": pipe}
        else:
            self._device_mode = "cpu"
            torch.set_num_threads(THREAD_CAP)
            pipe = KPipeline(lang_code="a", device="cpu")
            self._pipelines = {"host_a": pipe, "host_b": pipe}

        return self._pipelines

    def synthesize(
        self,
        contract: dict,
        output_path: Path,
        *,
        profile_name: Optional[str] = None,
    ) -> dict:
        registry = get_voice_registry()
        profile: Optional[VoiceProfile] = (
            registry.get(profile_name) if profile_name else registry.get("alex_sam")
        )
        if profile is None:
            profile = registry.get("alex_sam")

        assert profile is not None
        pipelines = self._init_pipelines()
        speakers = contract.get("speakers", {})
        turns = contract.get("turns", [])
        sample_rate = 24000

        speed = profile.speed
        fade_ms = profile.fade_ms
        room_tone_db = profile.room_tone_db

        alex_voice, alex_from, alex_warning = _resolve_voice(
            "host_a", speakers, profile.host_a_voice, registry
        )
        sam_voice, sam_from, sam_warning = _resolve_voice(
            "host_b", speakers, profile.host_b_voice, registry
        )
        sources = {alex_from, sam_from}
        voice_source = sources.pop() if len(sources) == 1 else "mixed"
        voice_warnings = [w for w in (alex_warning, sam_warning) if w]

        turn_audios: list[np.ndarray] = []
        turn_pauses: list[int] = []

        if self._device_mode == "xpu_dual":
            def synth_turn(info: tuple[int, dict]) -> tuple[int, np.ndarray, int]:
                idx, turn = info
                speaker = turn["speaker"]
                voice = alex_voice if speaker == "host_a" else sam_voice
                pipe = pipelines[speaker]
                chunks = []
                for _, _, audio in pipe(turn["text"], voice=voice, speed=speed):
                    chunks.append(audio)
                cat = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
                return idx, cat, turn.get("pause_after_ms", 250)

            with ThreadPoolExecutor(max_workers=2) as pool:
                indexed_turns = list(enumerate(turns))
                turn_results = list(pool.map(synth_turn, indexed_turns))
                turn_results.sort(key=lambda x: x[0])
                for _, turn_audio, pause_ms in turn_results:
                    turn_audios.append(turn_audio)
                    turn_pauses.append(pause_ms)
        else:
            pipe = pipelines["host_a"]
            for turn in turns:
                speaker = turn["speaker"]
                voice = alex_voice if speaker == "host_a" else sam_voice
                chunks = []
                for _, _, audio in pipe(turn["text"], voice=voice, speed=speed):
                    chunks.append(audio)
                if chunks:
                    turn_audios.append(np.concatenate(chunks))
                    turn_pauses.append(turn.get("pause_after_ms", 250))

        if not turn_audios:
            raise ValueError("podcast contract produced no audio")

        total_audio = splice_dialogue(
            turn_audios,
            turn_pauses,
            sample_rate=sample_rate,
            room_tone_db=room_tone_db,
            fade_ms=fade_ms,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), total_audio, sample_rate, subtype="FLOAT")

        content_bytes = output_path.read_bytes()
        duration = len(total_audio) / float(sample_rate)

        return {
            "duration_seconds": duration,
            "sample_rate": sample_rate,
            "channels": 1,
            "file_size_bytes": len(content_bytes),
            "sha256": hashlib.sha256(content_bytes).hexdigest(),
            "voice_ids": [alex_voice, sam_voice],
            "voice_source": voice_source,
            "voice_warnings": voice_warnings,
        }


_SYNTHESIZER = AudioSynthesizer()


def synthesize_script(
    contract: dict,
    output_path: Path,
    *,
    profile_name: Optional[str] = None,
) -> dict:
    """Module-level entry point for podcast script synthesis."""
    return _SYNTHESIZER.synthesize(contract, output_path, profile_name=profile_name)
