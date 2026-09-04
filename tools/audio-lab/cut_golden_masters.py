"""Synthesize Episode 01 Golden Masters on Intel Arc Pro B70 XPU."""

from __future__ import annotations

import json
import time
from pathlib import Path

from hearth.mediagen.audio.synthesizer import synthesize_script

script_path = Path(r"C:\work\commandcenter\tools\audio-lab\test_scripts\episode_01_script.json")
contract = json.loads(script_path.read_text(encoding="utf-8"))

out_dir = Path(r"C:\work\commandcenter\tools\audio-lab\samples\golden_master")
out_dir.mkdir(parents=True, exist_ok=True)

# 1. British Tech (Emma + George)
out_wav_emma = out_dir / "episode_01_phantom_limbs_british_tech.wav"
t0 = time.perf_counter()
details_emma = synthesize_script(contract, out_wav_emma, profile_name="british_tech")
t_emma = time.perf_counter() - t0
speed_emma = details_emma["duration_seconds"] / t_emma
print("=== EPISODE 01: BRITISH TECH (Emma & George) ===")
print(f"Duration: {details_emma['duration_seconds']:.2f}s | Wall time: {t_emma:.2f}s ({speed_emma:.1f}x speed)")
print(f"Voices: {details_emma['voice_ids']} (source: {details_emma['voice_source']})")
print(f"WAV: {out_wav_emma} ({details_emma['file_size_bytes']} bytes, sha256: {details_emma['sha256'][:16]}...)")

# 2. Alex & Sam (Heart & Adam)
out_wav_heart = out_dir / "episode_01_phantom_limbs_alex_sam.wav"
t0 = time.perf_counter()
details_heart = synthesize_script(contract, out_wav_heart, profile_name="alex_sam")
t_heart = time.perf_counter() - t0
speed_heart = details_heart["duration_seconds"] / t_heart
print("\n=== EPISODE 01: ALEX & SAM (Heart & Adam) ===")
print(f"Duration: {details_heart['duration_seconds']:.2f}s | Wall time: {t_heart:.2f}s ({speed_heart:.1f}x speed)")
print(f"Voices: {details_heart['voice_ids']} (source: {details_heart['voice_source']})")
print(f"WAV: {out_wav_heart} ({details_heart['file_size_bytes']} bytes, sha256: {details_heart['sha256'][:16]}...)")

receipt = {
    "episode": "01",
    "title": contract.get("title"),
    "source_document_sha256": contract.get("source_document_sha256"),
    "compute_device": "xpu:0 (Intel Arc Pro B70)",
    "artifacts": {
        "british_tech": {
            "path": str(out_wav_emma),
            "profile": "british_tech",
            "voices": details_emma["voice_ids"],
            "duration_seconds": details_emma["duration_seconds"],
            "sha256": details_emma["sha256"],
            "file_size_bytes": details_emma["file_size_bytes"],
            "synthesis_wall_time_s": round(t_emma, 2),
            "speed_realtime": round(speed_emma, 2),
        },
        "alex_sam": {
            "path": str(out_wav_heart),
            "profile": "alex_sam",
            "voices": details_heart["voice_ids"],
            "duration_seconds": details_heart["duration_seconds"],
            "sha256": details_heart["sha256"],
            "file_size_bytes": details_heart["file_size_bytes"],
            "synthesis_wall_time_s": round(t_heart, 2),
            "speed_realtime": round(speed_heart, 2),
        },
    },
}

receipt_path = Path(r"C:\work\commandcenter\tools\audio-lab\receipts\golden_master_episode_01.json")
receipt_path.parent.mkdir(parents=True, exist_ok=True)
receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
print(f"\nReceipt written to: {receipt_path}")
