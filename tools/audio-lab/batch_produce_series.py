"""Batch produce The Architecture of Sunk Compute podcast series on Intel Arc Pro B70 XPU."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

repo_root = Path(__file__).resolve().parent.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from hearth.mediagen.audio.synthesizer import synthesize_script
from hearth.mediagen.podcast import generate_podcast_script
from hearth.schemas.validate import validate

SERIES_CONFIG = [
    {
        "episode": "01",
        "file": "01-the-phantom-limbs-of-metal.md",
        "slug": "phantom_limbs",
        "fallback_title": "The Phantom Limbs of Metal",
    },
    {
        "episode": "02",
        "file": "02-when-green-lies-and-code-believes-it.md",
        "slug": "green_lies",
        "fallback_title": "When Green Lies and Code Believes It",
    },
    {
        "episode": "03",
        "file": "03-machine-lanes-dont-ride-the-cloud.md",
        "slug": "machine_lanes",
        "fallback_title": "Machine Lanes Don't Ride the Cloud",
    },
    {
        "episode": "04",
        "file": "04-the-silent-escalation-tax.md",
        "slug": "silent_escalation",
        "fallback_title": "The Silent Escalation Tax",
    },
    {
        "episode": "05",
        "file": "05-the-two-economies-doctrine.md",
        "slug": "two_economies",
        "fallback_title": "The Two Economies Doctrine",
    },
]

PROFILES = [
    ("british_tech", ["bf_emma", "bm_george"]),
    ("alex_sam", ["af_heart", "am_adam"]),
]


def ensure_script(
    source_dir: Path,
    scripts_dir: Path,
    cfg: dict[str, str],
    *,
    force_generate: bool = False,
) -> dict[str, Any]:
    """Ensure a valid podcast script contract exists for an episode."""
    script_file = scripts_dir / f"episode_{cfg['episode']}_script.json"
    source_file = source_dir / cfg["file"]

    source_text = source_file.read_text(encoding="utf-8")
    source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()

    if script_file.exists() and not force_generate:
        try:
            contract = json.loads(script_file.read_text(encoding="utf-8"))
            validate(contract, schema_id="mediagen.podcast-script.v1")
            if contract.get("source_document_sha256") == source_sha:
                print(f"  [Ep {cfg['episode']}] Script contract cached: {contract.get('title')}")
                return contract
            print(f"  [Ep {cfg['episode']}] Script source SHA mismatch, regenerating...")
        except Exception as exc:
            print(f"  [Ep {cfg['episode']}] Existing script invalid ({exc}), regenerating...")

    print(f"  [Ep {cfg['episode']}] Generating script via ArcServe Qwen3-30B...")
    contract = generate_podcast_script(source_text)

    # Normalize voice_ids to semantic roles to allow dynamic profile switching
    speakers = contract.setdefault("speakers", {})
    if "host_a" in speakers:
        speakers["host_a"]["voice_id"] = "host_a"
    if "host_b" in speakers:
        speakers["host_b"]["voice_id"] = "host_b"

    validate(contract, schema_id="mediagen.podcast-script.v1")
    script_file.write_text(json.dumps(contract, indent=2), encoding="utf-8")
    print(f"  [Ep {cfg['episode']}] Script saved to {script_file.name} ({len(contract.get('turns', []))} turns)")
    return contract


def synthesize_episode(
    contract: dict[str, Any],
    cfg: dict[str, str],
    out_dir: Path,
    receipts_dir: Path,
    *,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Synthesize golden master WAVs for an episode across selected profiles."""
    receipt_file = receipts_dir / f"golden_master_episode_{cfg['episode']}.json"
    existing_receipt: dict[str, Any] = {}
    if receipt_file.exists():
        try:
            existing_receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    artifacts: dict[str, Any] = existing_receipt.get("artifacts", {})
    compute_device = "xpu:0 (Intel Arc Pro B70)"

    for profile_name, _expected_voices in PROFILES:
        out_wav = out_dir / f"episode_{cfg['episode']}_{cfg['slug']}_{profile_name}.wav"
        if skip_existing and out_wav.exists() and profile_name in artifacts:
            print(f"  [Ep {cfg['episode']} - {profile_name}] Artifact cached: {out_wav.name}")
            continue

        print(f"  [Ep {cfg['episode']} - {profile_name}] Synthesizing on {compute_device}...")
        t0 = time.perf_counter()
        details = synthesize_script(contract, out_wav, profile_name=profile_name)
        wall_time = time.perf_counter() - t0
        speed = details["duration_seconds"] / wall_time if wall_time > 0 else 0.0

        if details.get("compute_device"):
            compute_device = details["compute_device"]

        print(
            f"  [Ep {cfg['episode']} - {profile_name}] Finished: {details['duration_seconds']:.2f}s audio "
            f"in {wall_time:.2f}s ({speed:.2f}x realtime)"
        )

        artifacts[profile_name] = {
            "path": str(out_wav),
            "profile": profile_name,
            "voices": details["voice_ids"],
            "duration_seconds": details["duration_seconds"],
            "sha256": details["sha256"],
            "file_size_bytes": details["file_size_bytes"],
            "synthesis_wall_time_s": round(wall_time, 2),
            "speed_realtime": round(speed, 2),
        }

    receipt = {
        "episode": cfg["episode"],
        "title": contract.get("title", cfg["fallback_title"]),
        "source_document_sha256": contract.get("source_document_sha256"),
        "compute_device": compute_device,
        "turns_count": len(contract.get("turns", [])),
        "artifacts": artifacts,
    }
    receipt_file.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"  [Ep {cfg['episode']}] Receipt saved: {receipt_file.name}")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch produce podcast series.")
    parser.add_argument("--force-generate", action="store_true", help="Force regenerate scripts from LLM")
    parser.add_argument("--force-synth", action="store_true", help="Force re-synthesize audio")
    parser.add_argument("--episode", type=str, default=None, help="Produce single episode (e.g. 02)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent.parent
    source_dir = Path(r"C:\work\writing\hearth-sunk-compute-cluster")
    scripts_dir = repo_root / "tools" / "audio-lab" / "test_scripts"
    out_dir = repo_root / "tools" / "audio-lab" / "samples" / "golden_master"
    receipts_dir = repo_root / "tools" / "audio-lab" / "receipts"

    scripts_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    print("=================================================================")
    print("  HEARTH MEDIAGEN: BATCH PRODUCTION OF SUNK COMPUTE SERIES       ")
    print("=================================================================")

    target_configs = SERIES_CONFIG
    if args.episode:
        target_configs = [c for c in SERIES_CONFIG if c["episode"] == args.episode]
        if not target_configs:
            raise ValueError(f"Unknown episode {args.episode}")

    all_receipts: list[dict[str, Any]] = []

    for cfg in target_configs:
        print(f"\n>>> Processing Episode {cfg['episode']}: {cfg['fallback_title']}")
        contract = ensure_script(
            source_dir, scripts_dir, cfg, force_generate=args.force_generate
        )
        receipt = synthesize_episode(
            contract, cfg, out_dir, receipts_dir, skip_existing=not args.force_synth
        )
        all_receipts.append(receipt)

    # Build series manifest across all 5 episodes
    manifest_episodes = []
    total_audio_s = 0.0
    total_wall_s = 0.0

    for cfg in SERIES_CONFIG:
        rf = receipts_dir / f"golden_master_episode_{cfg['episode']}.json"
        if rf.exists():
            ep_receipt = json.loads(rf.read_text(encoding="utf-8"))
            manifest_episodes.append(ep_receipt)
            for art in ep_receipt.get("artifacts", {}).values():
                total_audio_s += art.get("duration_seconds", 0.0)
                total_wall_s += art.get("synthesis_wall_time_s", 0.0)

    overall_speed = total_audio_s / total_wall_s if total_wall_s > 0 else 0.0

    manifest = {
        "series_title": "The Architecture of Sunk Compute",
        "description": "5-part technical deep-dive podcast series exploring sovereign compute, telemetry truth, and hardware topology.",
        "compute_device": "xpu:0 (Intel Arc Pro B70)",
        "pacing_standard": "P2_comfort_noise (-62 dB room tone bed, 10ms cosine edge fades)",
        "summary": {
            "episodes_count": len(manifest_episodes),
            "total_audio_seconds": round(total_audio_s, 2),
            "total_audio_minutes": round(total_audio_s / 60.0, 2),
            "total_synthesis_wall_time_s": round(total_wall_s, 2),
            "average_speed_realtime": round(overall_speed, 2),
        },
        "episodes": manifest_episodes,
    }

    manifest_file = receipts_dir / "series_sunk_compute_manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nSeries manifest written to: {manifest_file}")
    print(f"Total Audio: {manifest['summary']['total_audio_minutes']:.2f} min across {len(manifest_episodes)} episodes")
    print("=================================================================\n")


if __name__ == "__main__":
    main()
