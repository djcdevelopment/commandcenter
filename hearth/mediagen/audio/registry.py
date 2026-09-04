"""Voice profile registry for semantic role to physical voice mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


# Voice packs proven present in this machine's Kokoro HF cache
# (models--hexgrad--Kokoro-82M/snapshots/*/voices/*.pt), verified 2026-09-04.
#
# Kokoro ships no offline voice catalog -- KPipeline.load_single_voice resolves a name by
# downloading voices/<id>.pt from HuggingFace on demand -- so there is nothing to
# introspect and a shape heuristic like ^[ab][fm]_\w+$ would accept a plausible but
# nonexistent name whose failure surfaces as a 404 deep inside the TTS thread. Hence a
# maintained list: add an entry only once the pack has actually been driven through
# KPipeline. Registering a VoiceProfile extends the recognized set too, so this constant
# only needs to carry voices no profile happens to use.
_KNOWN_KOKORO_VOICES = frozenset({
    "af_bella", "af_heart", "af_nicole", "af_sarah",
    "am_adam", "am_eric", "am_michael",
    "bf_emma", "bm_daniel", "bm_george",
})


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    description: str
    host_a_voice: str
    host_b_voice: str
    speed: float = 1.0
    fade_ms: float = 10.0
    room_tone_db: float = -62.0


# Empirically validated profiles from Stage 1 & Stage 2 assays
_DEFAULT_PROFILES = {
    "alex_sam": VoiceProfile(
        name="alex_sam",
        description="Default show profile: American conversational tone (af_heart + am_adam) with click suppression and comfort bed",
        host_a_voice="af_heart",
        host_b_voice="am_adam",
        speed=1.0,
        fade_ms=10.0,
        room_tone_db=-62.0,
    ),
    "heart_adam": VoiceProfile(
        name="heart_adam",
        description="Alias for alex_sam: af_heart + am_adam with click suppression and comfort bed",
        host_a_voice="af_heart",
        host_b_voice="am_adam",
        speed=1.0,
        fade_ms=10.0,
        room_tone_db=-62.0,
    ),
    "british_tech": VoiceProfile(
        name="british_tech",
        description="Alternate show profile: British engineering cadence (bf_emma + bm_george) with click suppression and comfort bed",
        host_a_voice="bf_emma",
        host_b_voice="bm_george",
        speed=1.0,
        fade_ms=10.0,
        room_tone_db=-62.0,
    ),
    "emma_george": VoiceProfile(
        name="emma_george",
        description="Alias for british_tech: bf_emma + bm_george with click suppression and comfort bed",
        host_a_voice="bf_emma",
        host_b_voice="bm_george",
        speed=1.0,
        fade_ms=10.0,
        room_tone_db=-62.0,
    ),
    "bella_michael": VoiceProfile(
        name="bella_michael",
        description="Warm conversational delivery pairing af_bella with am_michael",
        host_a_voice="af_bella",
        host_b_voice="am_michael",
        speed=1.0,
        fade_ms=10.0,
        room_tone_db=-62.0,
    ),
    "warm_conversational": VoiceProfile(
        name="warm_conversational",
        description="Alias for bella_michael",
        host_a_voice="af_bella",
        host_b_voice="am_michael",
        speed=1.0,
        fade_ms=10.0,
        room_tone_db=-62.0,
    ),
    "sarah_eric": VoiceProfile(
        name="sarah_eric",
        description="Crisp technical precision pairing af_sarah with am_eric",
        host_a_voice="af_sarah",
        host_b_voice="am_eric",
        speed=1.0,
        fade_ms=10.0,
        room_tone_db=-62.0,
    ),
    "technical_precision": VoiceProfile(
        name="technical_precision",
        description="Alias for sarah_eric",
        host_a_voice="af_sarah",
        host_b_voice="am_eric",
        speed=1.0,
        fade_ms=10.0,
        room_tone_db=-62.0,
    ),
    "custom_blend": VoiceProfile(
        name="custom_blend",
        description="Interpolated tensor blend (af_bella,af_sarah + am_michael,am_adam)",
        host_a_voice="af_bella,af_sarah",
        host_b_voice="am_michael,am_adam",
        speed=1.0,
        fade_ms=10.0,
        room_tone_db=-62.0,
    ),
}


def _split_voice(voice: str) -> list[str]:
    """Split a Kokoro voice spec into its parts (a blend is comma-delimited)."""
    return [part.strip() for part in voice.split(",") if part.strip()]


class VoiceProfileRegistry:
    def __init__(self, profiles: Optional[Mapping[str, VoiceProfile]] = None) -> None:
        self._profiles = dict(profiles or _DEFAULT_PROFILES)

    def register(self, profile: VoiceProfile) -> None:
        self._profiles[profile.name] = profile

    def get(self, name: str) -> Optional[VoiceProfile]:
        return self._profiles.get(name)

    def list_profiles(self) -> list[str]:
        return sorted(self._profiles.keys())

    def recognized_voices(self) -> frozenset[str]:
        """Every physical Kokoro voice id this registry will accept from a contract.

        The maintained inventory unioned with the voices of every registered profile, so
        registering a profile extends what a contract may name.
        """
        names = set(_KNOWN_KOKORO_VOICES)
        for profile in self._profiles.values():
            for voice in (profile.host_a_voice, profile.host_b_voice):
                names.update(_split_voice(voice))
        return frozenset(names)

    def is_recognized_voice(self, voice_id: object) -> bool:
        """True if voice_id names a real voice, or a blend of them (``af_bella,af_sarah``)."""
        if not isinstance(voice_id, str) or not voice_id.strip():
            return False
        parts = _split_voice(voice_id)
        if not parts:
            return False
        recognized = self.recognized_voices()
        return all(part in recognized for part in parts)


_REGISTRY = VoiceProfileRegistry()


def get_voice_registry() -> VoiceProfileRegistry:
    return _REGISTRY
