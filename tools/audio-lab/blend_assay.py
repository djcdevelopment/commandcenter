"""Audio Lab Assay: Heart + Emma Voice Blending on Intel Arc Pro B70 XPU.

Evaluates linear tensor interpolation between the user's top two female voice anchors:
- Clip 1: af_heart (American, conversational, warm)
- Clip 5: bf_emma (British, crisp, technical)

Holding pacing constant using the production P2 standard (-62 dB room tone, 10ms cosine fades)
on the standardized calibration script.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

repo_root = Path(__file__).resolve().parent.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import numpy as np
import soundfile as sf
import torch

from hearth.mediagen.audio.pacing import splice_dialogue

BLEND_CANDIDATES = {
    "01_anchor_pure_heart": {
        "title": "Anchor: Pure Heart (Control)",
        "alex_weights": {"af_heart": 1.0},
        "sam_weights": {"am_adam": 1.0},
        "description": "Baseline control from Clip 1 (af_heart + am_adam)",
    },
    "02_anchor_pure_emma": {
        "title": "Anchor: Pure Emma (Control)",
        "alex_weights": {"bf_emma": 1.0},
        "sam_weights": {"bm_george": 1.0},
        "description": "Baseline control from Clip 5 (bf_emma + bm_george)",
    },
    "03_blend_heart_emma_50_50": {
        "title": "Blend 50/50: Heart + Emma",
        "alex_weights": {"af_heart": 0.50, "bf_emma": 0.50},
        "sam_weights": {"am_adam": 1.0},
        "description": "Equal 50/50 blend of Heart and Emma paired with Adam",
    },
    "04_blend_heart_dominant_70_30": {
        "title": "Blend 70/30: Heart-Dominant",
        "alex_weights": {"af_heart": 0.70, "bf_emma": 0.30},
        "sam_weights": {"am_adam": 1.0},
        "description": "70% Heart, 30% Emma: American warmth with crisp British articulation",
    },
    "05_blend_emma_dominant_70_30": {
        "title": "Blend 70/30: Emma-Dominant",
        "alex_weights": {"af_heart": 0.30, "bf_emma": 0.70},
        "sam_weights": {"bm_george": 1.0},
        "description": "70% Emma, 30% Heart: British poise with American vocal dynamics",
    },
    "06_blend_heart_dominant_60_40": {
        "title": "Blend 60/40: Heart-Dominant",
        "alex_weights": {"af_heart": 0.60, "bf_emma": 0.40},
        "sam_weights": {"am_adam": 1.0},
        "description": "60% Heart, 40% Emma: Nuanced hybrid retaining American core",
    },
    "07_blend_dual_transatlantic_60_40": {
        "title": "Dual Transatlantic 60/40",
        "alex_weights": {"af_heart": 0.60, "bf_emma": 0.40},
        "sam_weights": {"am_adam": 0.60, "bm_george": 0.40},
        "description": "Transatlantic show pair: Alex (60 Heart / 40 Emma) + Sam (60 Adam / 40 George)",
    },
}


def build_voice_tensor(pipeline: Any, weights: dict[str, float]) -> torch.Tensor:
    """Linearly interpolate voice pack tensors by given weights."""
    tensors = []
    w_sum = sum(weights.values())
    for voice_name, raw_weight in weights.items():
        pack = pipeline.load_single_voice(voice_name)
        weight = raw_weight / w_sum
        tensors.append(pack * weight)
    return torch.sum(torch.stack(tensors), dim=0)


def run_blend_assay(
    device: str = "xpu:0",
    candidate_filter: str | None = None,
) -> list[dict[str, Any]]:
    from kokoro import KPipeline

    script_path = repo_root / "tools" / "audio-lab" / "test_scripts" / "calibration_sample.json"
    script_raw = script_path.read_text(encoding="utf-8")
    script_sha = hashlib.sha256(script_raw.encode("utf-8")).hexdigest()
    calibration = json.loads(script_raw)
    turns = calibration.get("turns", [])

    samples_dir = repo_root / "tools" / "audio-lab" / "samples" / "voice_blend"
    receipts_dir = repo_root / "tools" / "audio-lab" / "receipts" / "voice_blend"
    samples_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    print(f"Allocating Kokoro pipeline on {device}...")
    pipeline = KPipeline(lang_code="a", device=device)

    # Warm up pipeline with probe
    list(pipeline("Hardware probe.", voice="af_heart", speed=1.0))
    print("Pipeline probe verified.\n")

    results = []
    sample_rate = 24000
    fade_ms = 10.0
    room_tone_db = -62.0
    seed = 42

    for cid, spec in BLEND_CANDIDATES.items():
        if candidate_filter and candidate_filter not in cid:
            continue

        print(f"=== Running Assay: {cid} ===")
        print(f"  Title: {spec['title']}")
        print(f"  Alex weights: {spec['alex_weights']}")
        print(f"  Sam weights: {spec['sam_weights']}")

        v_alex = build_voice_tensor(pipeline, spec["alex_weights"])
        v_sam = build_voice_tensor(pipeline, spec["sam_weights"])

        turn_audios: list[np.ndarray] = []
        turn_pauses: list[int] = []

        t0 = time.perf_counter()
        for turn in turns:
            speaker = turn["speaker"]
            voice_tensor = v_alex if speaker == "host_a" else v_sam
            chunks = []
            for _, _, audio in pipeline(turn["text"], voice=voice_tensor, speed=1.0):
                chunks.append(audio)
            if chunks:
                turn_audios.append(np.concatenate(chunks))
                turn_pauses.append(turn.get("pause_after_ms", 250))

        wall_time = time.perf_counter() - t0

        final_audio = splice_dialogue(
            turn_audios,
            turn_pauses,
            sample_rate=sample_rate,
            room_tone_db=room_tone_db,
            fade_ms=fade_ms,
            seed=seed,
        )

        dur_s = len(final_audio) / sample_rate
        speed = dur_s / wall_time if wall_time > 0 else 0.0

        out_wav = samples_dir / f"{cid}.wav"
        sf.write(str(out_wav), final_audio, sample_rate, subtype="PCM_16")
        wav_bytes = out_wav.read_bytes()
        wav_sha = hashlib.sha256(wav_bytes).hexdigest()

        print(f"  Duration: {dur_s:.2f}s | Wall Time: {wall_time:.2f}s | Speed: {speed:.2f}x")
        print(f"  WAV: {out_wav.name} ({len(wav_bytes)} bytes, sha: {wav_sha[:16]}...)\n")

        receipt = {
            "candidate_id": cid,
            "title": spec["title"],
            "description": spec["description"],
            "calibration_script_sha256": script_sha,
            "compute_device": device,
            "alex_weights": spec["alex_weights"],
            "sam_weights": spec["sam_weights"],
            "pacing_parameters": {
                "fade_ms": fade_ms,
                "room_tone_db": room_tone_db,
                "comfort_noise_seed": seed,
            },
            "metrics": {
                "audio_duration_seconds": round(dur_s, 3),
                "wall_time_seconds": round(wall_time, 2),
                "speed_realtime": round(speed, 2),
                "file_size_bytes": len(wav_bytes),
                "artifact_sha256": wav_sha,
            },
            "artifact_path": str(out_wav),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        receipt_file = receipts_dir / f"{cid}.json"
        receipt_file.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        results.append(receipt)

    manifest = {
        "assay": "Heart + Emma Voice Blend Assay",
        "description": "Systematic linear interpolation of Kokoro voice embeddings for top female anchors",
        "calibration_script": "tools/audio-lab/test_scripts/calibration_sample.json",
        "compute_device": device,
        "pacing_standard": "P2_comfort_noise (-62 dB room tone, 10ms cosine fades, seed=42)",
        "candidates_count": len(results),
        "results": results,
    }

    manifest_file = receipts_dir / "voice_blend_manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest written to: {manifest_file}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Heart + Emma voice blend assay.")
    parser.add_argument("--device", default="xpu:0", help="Compute device (default: xpu:0)")
    parser.add_argument("--filter", default=None, help="Filter candidate id substring")
    args = parser.parse_args()

    print("=================================================================")
    print("  AUDIO LAB: HEART + EMMA VOICE BLENDING ASSAY                   ")
    print("=================================================================")
    run_blend_assay(device=args.device, candidate_filter=args.filter)


if __name__ == "__main__":
    main()
