"""Pacing, crossfade, and room-tone processing for podcast dialogue."""

from __future__ import annotations

import hashlib
from typing import Optional

import numpy as np


def generate_comfort_noise(
    length_samples: int,
    sample_rate: int = 24000,
    level_db: float = -62.0,
    *,
    seed: Optional[int] = None,
) -> np.ndarray:
    """Generate subtle, shaped comfort noise to prevent dead-drop digital silence.

    Draws from a local np.random.Generator rather than numpy's global singleton, so the
    two-thread xpu_dual synthesis cannot race on shared mutable state and a seeded call
    here can never perturb an unrelated one.

    seed=None (the production default) draws fresh entropy every call, so two episodes
    never get bit-identical room tone. Pass a seed to make a run reproducible -- see
    tools/audio-lab/pacing_bench.py, whose receipts record the seed they used.
    """
    if length_samples <= 0:
        return np.empty(0, dtype=np.float32)
    rng = np.random.default_rng(seed)
    amplitude = 10.0 ** (level_db / 20.0)
    noise = rng.normal(0, amplitude, length_samples).astype(np.float32)
    if length_samples > 4:
        noise = np.convolve(
            noise, np.ones(4, dtype=np.float32) / 4.0, mode="same"
        ).astype(np.float32)
    return noise


def apply_fades(
    audio: np.ndarray, sample_rate: int = 24000, fade_ms: float = 10.0
) -> np.ndarray:
    """Apply short cosine fade-in and fade-out to prevent clicks at chunk boundaries."""
    fade_len = int(fade_ms * sample_rate / 1000.0)
    if len(audio) < fade_len * 2 or fade_len <= 0:
        return audio
    result = audio.copy()
    ramp = 0.5 * (1.0 - np.cos(np.linspace(0, np.pi, fade_len)))
    result[:fade_len] *= ramp
    result[-fade_len:] *= ramp[::-1]
    return result


def _pause_seed(seed: int, index: int) -> int:
    """Derive a per-pause sub-seed, the same shape as execution.py's per-scene _seed.

    One raw seed reused for every pause would make each pause the identical noise loop --
    reproducible, but audibly wrong.
    """
    digest = hashlib.sha256(f"{seed}:{index}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def splice_dialogue(
    chunks: list[np.ndarray],
    pauses_ms: list[int],
    sample_rate: int = 24000,
    room_tone_db: Optional[float] = -62.0,
    fade_ms: float = 10.0,
    *,
    seed: Optional[int] = None,
) -> np.ndarray:
    """Assemble speaker turns with natural comfort noise and faded transitions.

    room_tone_db=None fills pauses with exact digital silence instead of comfort noise.
    seed=None keeps production nondeterministic per call; an explicit seed makes every
    pause in THIS call reproducible while still giving each its own texture.
    """
    if not chunks:
        return np.empty(0, dtype=np.float32)

    pieces = []
    pause_index = 0
    for i, chunk in enumerate(chunks):
        faded_chunk = (
            apply_fades(chunk, sample_rate=sample_rate, fade_ms=fade_ms)
            if fade_ms > 0
            else chunk
        )
        pieces.append(faded_chunk)
        if i < len(pauses_ms):
            pause_ms = pauses_ms[i]
            if pause_ms > 0:
                pause_samples = int(pause_ms * sample_rate / 1000.0)
                if room_tone_db is not None:
                    noise = generate_comfort_noise(
                        pause_samples,
                        sample_rate=sample_rate,
                        level_db=room_tone_db,
                        seed=None if seed is None else _pause_seed(seed, pause_index),
                    )
                else:
                    noise = np.zeros(pause_samples, dtype=np.float32)
                pieces.append(noise)
                pause_index += 1

    return np.concatenate(pieces)
