from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from hearth.mediagen.audio.pacing import apply_fades, generate_comfort_noise, splice_dialogue
from hearth.mediagen.audio.registry import (
    VoiceProfile, VoiceProfileRegistry, get_voice_registry,
)
from hearth.mediagen.jobspec import MediaArgumentError, validate_media_arguments

REPO_ROOT = Path(__file__).resolve().parents[3]


# --- registry ---------------------------------------------------------------------


def test_registry_recognizes_profile_voices_and_blends() -> None:
    registry = get_voice_registry()
    assert registry.is_recognized_voice("af_heart")
    assert registry.is_recognized_voice("af_bella,af_sarah")
    assert not registry.is_recognized_voice("af_bella,not_a_real_voice")


def test_registry_rejects_role_labels_and_non_strings() -> None:
    registry = get_voice_registry()
    assert not registry.is_recognized_voice("host_a")
    assert not registry.is_recognized_voice("host_b")
    assert not registry.is_recognized_voice(None)
    assert not registry.is_recognized_voice("")
    assert not registry.is_recognized_voice("   ")
    assert not registry.is_recognized_voice(42)


def test_registry_recognizes_cached_voices_no_profile_uses() -> None:
    # af_nicole and bm_daniel are in the Kokoro HF cache but back no profile; a contract
    # naming one is still legitimate.
    registry = get_voice_registry()
    assert registry.is_recognized_voice("af_nicole")
    assert registry.is_recognized_voice("bm_daniel")


def test_registering_a_profile_grows_the_recognized_set() -> None:
    registry = VoiceProfileRegistry()
    assert not registry.is_recognized_voice("zz_invented")
    registry.register(VoiceProfile(
        name="test_custom", description="t",
        host_a_voice="zz_invented", host_b_voice="am_adam",
    ))
    assert registry.is_recognized_voice("zz_invented")
    assert "test_custom" in registry.list_profiles()


# --- jobspec ----------------------------------------------------------------------


def _podcast_args(voice_profile: str) -> dict:
    text = "source"
    return {
        "document_text": text, "document_name": "source.md",
        "document_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "voice_profile": voice_profile,
    }


def _operation() -> SimpleNamespace:
    return SimpleNamespace(name="media.podcast", max_prompt_bytes=270336)


def test_jobspec_accepts_every_registered_profile() -> None:
    for name in get_voice_registry().list_profiles():
        public, packed = validate_media_arguments(_operation(), _podcast_args(name))
        assert public["_spec"]
        assert packed


def test_jobspec_rejects_unregistered_profile() -> None:
    with pytest.raises(MediaArgumentError):
        validate_media_arguments(_operation(), _podcast_args("no_such_profile"))


def test_jobspec_validation_does_not_import_torch() -> None:
    """Argument validation runs on the gateway request path -- it must not load a GPU stack.

    hearth.mediagen.jobspec imports the voice registry, which runs
    hearth/mediagen/audio/__init__.py; that module defers the synthesizer (and so torch,
    numpy and soundfile) behind a PEP 562 __getattr__ precisely so this stays true.
    """
    script = (
        "import hashlib, sys\n"
        "from types import SimpleNamespace\n"
        "from hearth.mediagen.jobspec import validate_media_arguments\n"
        "text = 'source'\n"
        "validate_media_arguments(\n"
        "    SimpleNamespace(name='media.podcast', max_prompt_bytes=270336),\n"
        "    {'document_text': text, 'document_name': 'source.md',\n"
        "     'document_sha256': hashlib.sha256(text.encode()).hexdigest(),\n"
        "     'voice_profile': 'alex_sam'})\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] == 'torch')\n"
        "assert not leaked, leaked\n"
        "print('OK')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        timeout=120, cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


# --- pacing -----------------------------------------------------------------------


def test_apply_fades_ramps_edges_without_changing_length() -> None:
    audio = np.ones(2400, dtype=np.float32)
    faded = apply_fades(audio, sample_rate=24000, fade_ms=10.0)
    assert len(faded) == len(audio)
    assert faded[0] < 0.01 and faded[-1] < 0.01
    assert faded[len(faded) // 2] == pytest.approx(1.0)
    # the source array must not be mutated in place
    assert audio[0] == pytest.approx(1.0)


def test_apply_fades_is_a_noop_when_audio_is_shorter_than_the_ramp() -> None:
    audio = np.ones(4, dtype=np.float32)
    assert np.array_equal(apply_fades(audio, fade_ms=10.0), audio)


def test_comfort_noise_is_reproducible_with_a_seed() -> None:
    a = generate_comfort_noise(4800, seed=42)
    b = generate_comfort_noise(4800, seed=42)
    c = generate_comfort_noise(4800, seed=43)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_comfort_noise_is_fresh_when_unseeded() -> None:
    assert not np.array_equal(generate_comfort_noise(4800), generate_comfort_noise(4800))


def test_comfort_noise_does_not_disturb_the_global_rng() -> None:
    np.random.seed(1234)
    before = np.random.random()
    np.random.seed(1234)
    generate_comfort_noise(4800, seed=7)
    assert np.random.random() == before


def test_comfort_noise_level_tracks_the_requested_db() -> None:
    quiet = generate_comfort_noise(48000, level_db=-62.0, seed=1)
    loud = generate_comfort_noise(48000, level_db=-20.0, seed=1)
    assert float(np.abs(quiet).max()) < float(np.abs(loud).max())
    assert len(generate_comfort_noise(0)) == 0


def test_splice_dialogue_fills_pauses_with_silence_or_room_tone() -> None:
    chunks = [np.ones(2400, dtype=np.float32), np.ones(2400, dtype=np.float32)]
    pauses = [250, 0]

    silent = splice_dialogue(chunks, pauses, room_tone_db=None, fade_ms=0.0)
    toned = splice_dialogue(chunks, pauses, room_tone_db=-62.0, fade_ms=0.0, seed=99)

    expected = 2400 + int(250 * 24000 / 1000) + 2400
    assert len(silent) == expected == len(toned)
    gap = slice(2400, 2400 + int(250 * 24000 / 1000))
    assert not np.any(silent[gap])
    assert np.any(toned[gap])


def test_splice_dialogue_gives_each_pause_its_own_texture_under_one_seed() -> None:
    chunks = [np.ones(240, dtype=np.float32) for _ in range(3)]
    spliced = splice_dialogue(chunks, [250, 250, 0], fade_ms=0.0, seed=5)
    gap_len = int(250 * 24000 / 1000)
    first = spliced[240:240 + gap_len]
    second = spliced[240 + gap_len + 240:240 + gap_len + 240 + gap_len]
    assert not np.array_equal(first, second)


def test_splice_dialogue_is_reproducible_and_handles_empty_input() -> None:
    chunks = [np.ones(240, dtype=np.float32), np.ones(240, dtype=np.float32)]
    a = splice_dialogue(chunks, [250, 0], seed=11)
    b = splice_dialogue(chunks, [250, 0], seed=11)
    assert np.array_equal(a, b)
    assert len(splice_dialogue([], [])) == 0


# --- synthesizer ------------------------------------------------------------------

pytest.importorskip("torch")
pytest.importorskip("soundfile")


class _FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    def __call__(self, text, voice, speed=1.0):
        self.calls.append((text, voice, speed))
        yield None, None, np.zeros(2400, dtype=np.float32)


def _contract(host_a_voice: str, host_b_voice: str) -> dict:
    return {
        "schema": "mediagen.podcast-script.v1", "version": "1.0.0", "title": "T",
        "source_document_sha256": hashlib.sha256(b"x").hexdigest(),
        "speakers": {
            "host_a": {"name": "Alex", "voice_id": host_a_voice, "persona": "A"},
            "host_b": {"name": "Sam", "voice_id": host_b_voice, "persona": "B"},
        },
        "turns": [{"speaker": "host_a", "text": "Hi", "pause_after_ms": 250},
                  {"speaker": "host_b", "text": "Hello", "pause_after_ms": 250}],
    }


def _synthesize(contract: dict, tmp_path: Path) -> tuple[dict, _FakePipeline]:
    from hearth.mediagen.audio.synthesizer import AudioSynthesizer

    fake = _FakePipeline()
    synth = AudioSynthesizer()
    synth._device_mode = "cpu"
    with patch.object(synth, "_init_pipelines", return_value={"host_a": fake, "host_b": fake}):
        details = synth.synthesize(contract, tmp_path / "out.wav", profile_name="alex_sam")
    return details, fake


def test_contract_voice_wins_when_it_names_a_real_voice(tmp_path: Path) -> None:
    details, fake = _synthesize(_contract("af_bella", "am_eric"), tmp_path)
    assert {voice for _, voice, _ in fake.calls} == {"af_bella", "am_eric"}
    assert details["voice_ids"] == ["af_bella", "am_eric"]
    assert details["voice_source"] == "contract"
    assert details["voice_warnings"] == []


def test_role_labels_resolve_to_the_profile_without_warning(tmp_path: Path) -> None:
    # This is what the prompt template emits today, so it must stay quiet.
    details, fake = _synthesize(_contract("host_a", "host_b"), tmp_path)
    assert {voice for _, voice, _ in fake.calls} == {"af_heart", "am_adam"}
    assert details["voice_ids"] == ["af_heart", "am_adam"]
    assert details["voice_source"] == "profile"
    assert details["voice_warnings"] == []


def test_an_invented_voice_id_is_loud(tmp_path: Path) -> None:
    details, _ = _synthesize(_contract("af_madeup", "host_b"), tmp_path)
    assert details["voice_ids"] == ["af_heart", "am_adam"]
    assert details["voice_source"] == "profile"
    assert len(details["voice_warnings"]) == 1
    assert "af_madeup" in details["voice_warnings"][0]


def test_one_real_voice_and_one_role_label_is_mixed(tmp_path: Path) -> None:
    details, _ = _synthesize(_contract("af_bella", "host_b"), tmp_path)
    assert details["voice_ids"] == ["af_bella", "am_adam"]
    assert details["voice_source"] == "mixed"
    assert details["voice_warnings"] == []


def test_synthesis_result_validates_against_the_media_artifact_schema(tmp_path: Path) -> None:
    """MediaArtifact.v1 is additionalProperties:false and _media_contract spreads
    **details, so every schema-bound key the synthesizer returns must be declared."""
    from hearth.schemas.validate import validate

    details, _ = _synthesize(_contract("af_bella", "am_eric"), tmp_path)
    details.pop("voice_warnings")
    contract = {
        "schema": "mediagen.media-artifact.v1", "version": "1.0.0",
        "artifact_id": "art_test", "media_type": "audio/wav",
        "source_contract_schema": "mediagen.podcast-script.v1",
        **details, "codec": "pcm_f32le",
    }
    validate(contract, schema_id="mediagen.media-artifact.v1")
