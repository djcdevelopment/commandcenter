"""Audio Lab CLI benchmark: Stage 1 Voice Identity Assay.

Supports heterogeneous compute backends:
- Host CPU (with 4-thread cap to isolate local inference)
- Single Intel Arc Pro B70 GPU (xpu:0, 32GB VRAM)
- Dual Intel Arc Pro B70 GPUs (xpu:0 for Alex, xpu:1 for Sam concurrently!)

Isolates voice identity and tensor blends while holding pacing strictly CONSTANT
(using the baseline 250ms zero-padding control, zero crossfades, zero comfort noise).
Emits durable JSON receipts and a structured evaluation scorecard.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add parent directory to sys.path so imports work
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import numpy as np
import soundfile as sf
import torch

THREAD_CAP = 4
torch.set_num_threads(THREAD_CAP)

import kokoro
from kokoro import KPipeline

CANDIDATES = {
    "01_control_heart_adam": {
        "alex": "af_heart",
        "sam": "am_adam",
        "speed": 1.0,
        "description": "Baseline control (original test pipeline)",
    },
    "02_warm_conversational": {
        "alex": "af_bella",
        "sam": "am_michael",
        "speed": 1.0,
        "description": "Warm conversational delivery",
    },
    "03_technical_precision": {
        "alex": "af_sarah",
        "sam": "am_eric",
        "speed": 1.0,
        "description": "Crisp technical precision and authoritative delivery",
    },
    "04_custom_blend": {
        "alex": "af_bella,af_sarah",
        "sam": "am_michael,am_adam",
        "speed": 1.0,
        "description": "Interpolated tensor blends for distinct show personas",
    },
    "05_british_tech": {
        "alex": "bf_emma",
        "sam": "bm_george",
        "speed": 1.0,
        "description": "British clarity and engineering cadence",
    },
}


def load_calibration_script() -> tuple[dict, str]:
    script_path = SCRIPT_DIR / "test_scripts" / "calibration_sample.json"
    raw = script_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return json.loads(raw), digest


def get_pipeline_bundle(device_mode: str) -> dict[str, KPipeline]:
    if device_mode == "xpu_dual":
        print("  Allocating Dual Intel Arc Pro B70 lanes: Host A -> xpu:0 | Host B -> xpu:1")
        return {
            "host_a": KPipeline(lang_code="a", device="xpu:0"),
            "host_b": KPipeline(lang_code="a", device="xpu:1"),
        }
    elif device_mode in {"xpu", "xpu:0"}:
        print("  Allocating Intel Arc Pro B70 Card 0 (xpu:0)")
        p = KPipeline(lang_code="a", device="xpu:0")
        return {"host_a": p, "host_b": p}
    else:
        print(f"  Allocating Host CPU (Thread cap: {THREAD_CAP})")
        p = KPipeline(lang_code="a", device="cpu")
        return {"host_a": p, "host_b": p}


def run_assay(device_mode: str = "cpu", candidate_filter: str | None = None) -> list[dict]:
    samples_dir = SCRIPT_DIR / "samples"
    receipts_dir = SCRIPT_DIR / "receipts"
    samples_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    script_data, script_sha256 = load_calibration_script()
    turns = script_data["turns"]
    sample_rate = 24000

    print("=" * 76)
    print(f"HEARTH AUDIO LAB -- STAGE 1: VOICE IDENTITY ASSAY")
    print(f"Compute Mode: {device_mode.upper()} | Pacing Strategy: CONTROL (250ms raw zeros)")
    print(f"Script Digest: sha256:{script_sha256[:16]}... ({len(turns)} turns)")
    print("=" * 76)

    pipelines = get_pipeline_bundle(device_mode)
    assay_results = []

    candidates_to_run = (
        {candidate_filter: CANDIDATES[candidate_filter]}
        if candidate_filter and candidate_filter in CANDIDATES
        else CANDIDATES
    )

    for cid, config in candidates_to_run.items():
        alex_voice = config["alex"]
        sam_voice = config["sam"]
        speed = config.get("speed", 1.0)
        desc = config["description"]

        print(f"\nEvaluating Candidate: {cid}")
        print(f"  Alex: {alex_voice} | Sam: {sam_voice} | Speed: {speed}x")
        print(f"  Concept: {desc}")

        wall_start = time.perf_counter()
        cpu_start = time.process_time()

        turn_audios: list[np.ndarray] = []
        turn_pauses: list[int] = []

        if device_mode == "xpu_dual":
            # Dual-GPU parallel turn synthesis
            def synthesize_turn(turn_info: tuple[int, dict]) -> tuple[int, np.ndarray, int]:
                idx, turn = turn_info
                speaker = turn["speaker"]
                voice = alex_voice if speaker == "host_a" else sam_voice
                pipe = pipelines[speaker]
                chunks = []
                for _, _, audio in pipe(turn["text"], voice=voice, speed=speed):
                    chunks.append(audio)
                audio_cat = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
                return idx, audio_cat, turn.get("pause_after_ms", 250)

            with ThreadPoolExecutor(max_workers=2) as pool:
                indexed_turns = list(enumerate(turns))
                turn_results = list(pool.map(synthesize_turn, indexed_turns))
                turn_results.sort(key=lambda x: x[0])
                for _, turn_audio, pause_ms in turn_results:
                    turn_audios.append(turn_audio)
                    turn_pauses.append(pause_ms)
        else:
            pipe = pipelines["host_a"]
            for turn in turns:
                voice = alex_voice if turn["speaker"] == "host_a" else sam_voice
                text = turn["text"]
                pause_ms = turn.get("pause_after_ms", 250)

                chunks = []
                for _, _, audio in pipe(text, voice=voice, speed=speed):
                    chunks.append(audio)

                if chunks:
                    turn_audios.append(np.concatenate(chunks))
                    turn_pauses.append(pause_ms)

        # STAGE 1 VARIABLE ISOLATION: Pacing is held constant as raw zero-padding control
        spliced_chunks = []
        for i, turn_audio in enumerate(turn_audios):
            spliced_chunks.append(turn_audio)
            if i < len(turn_pauses):
                pause_samples = int(turn_pauses[i] * sample_rate / 1000.0)
                if pause_samples > 0:
                    spliced_chunks.append(np.zeros(pause_samples, dtype=np.float32))

        total_audio = np.concatenate(spliced_chunks)

        wall_time = time.perf_counter() - wall_start
        cpu_time = time.process_time() - cpu_start
        audio_duration = len(total_audio) / float(sample_rate)
        x_realtime = audio_duration / wall_time if wall_time > 0 else 0.0
        cpu_core_usage = cpu_time / wall_time if wall_time > 0 else 0.0

        device_tag = f"_{device_mode}" if device_mode != "cpu" else ""
        artifact_filename = f"{cid}{device_tag}.wav"
        output_wav = samples_dir / artifact_filename
        sf.write(str(output_wav), total_audio, sample_rate, subtype="FLOAT")
        artifact_bytes = output_wav.read_bytes()
        artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
        file_size_kb = len(artifact_bytes) / 1024.0

        # Memory telemetry
        vram_info = {}
        if hasattr(torch, "xpu") and torch.xpu.is_available() and "xpu" in device_mode:
            for d_idx in range(min(2, torch.xpu.device_count())):
                vram_info[f"xpu_{d_idx}_allocated_mb"] = round(torch.xpu.memory_allocated(d_idx) / (1024**2), 2)

        # Create durable JSON receipt
        receipt = {
            "candidate_id": cid,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "script_digest": f"sha256:{script_sha256}",
            "compute_device": device_mode,
            "backend": "kokoro",
            "backend_version": getattr(kokoro, "__version__", "0.9.4"),
            "voice_spec": {
                "host_a": alex_voice,
                "host_b": sam_voice,
            },
            "speed": speed,
            "pacing_strategy": "control_zero_padding_250ms",
            "crossfade_ms": 0.0,
            "room_tone_db": None,
            "sample_rate": sample_rate,
            "wall_time_s": round(wall_time, 3),
            "cpu_time_s": round(cpu_time, 3),
            "audio_duration_s": round(audio_duration, 3),
            "x_realtime": round(x_realtime, 2),
            "thread_cap": THREAD_CAP,
            "cpu_core_utilization": round(cpu_core_usage, 2),
            "vram_telemetry": vram_info,
            "artifact_path": str(output_wav),
            "artifact_bytes": len(artifact_bytes),
            "artifact_sha256": artifact_sha256,
        }

        receipt_filename = f"{cid}{device_tag}.json"
        receipt_path = receipts_dir / receipt_filename
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

        print(f"  -> Audio Duration: {receipt['audio_duration_s']}s")
        print(f"  -> Wall Time:      {receipt['wall_time_s']}s (Speed: {receipt['x_realtime']}x realtime)")
        if vram_info:
            print(f"  -> VRAM State:     {vram_info}")
        print(f"  -> Receipt:        {receipt_path.name}")
        print(f"  -> Audio File:     {output_wav.name} ({round(file_size_kb, 1)} KB)")

        assay_results.append(receipt)

    print("\n" + "=" * 76)
    print(f"STAGE 1 ASSAY SUMMARY MATRIX ({device_mode.upper()})")
    print("=" * 76)
    print(f"{'Candidate ID':<26} | {'Compute':<10} | {'Audio(s)':<8} | {'Speed':<14} | {'Artifact'}")
    print("-" * 76)
    for r in assay_results:
        print(
            f"{r['candidate_id']:<26} | {r['compute_device']:<10} | {r['audio_duration_s']:<8} | "
            f"{r['x_realtime']}x realtime | {Path(r['artifact_path']).name}"
        )
    print("=" * 76)

    generate_listening_scorecard(assay_results, device_mode=device_mode)
    return assay_results


def generate_listening_scorecard(results: list[dict], device_mode: str = "cpu") -> None:
    scorecard_path = SCRIPT_DIR / "LISTENING_SCORECARD.md"
    lines = [
        "# Stage 1 Voice Identity Assay: Listening Scorecard",
        "",
        "> **Assay Purpose**: Evaluate candidate voice identities and blends on the standardized calibration script.",
        "> **Variable Isolation**: Pacing is held strictly CONSTANT across all candidates using the control (250ms raw zero-padding, zero crossfades).",
        f"> **Compute Platform**: {device_mode.upper()} (Host CPU 4-thread cap or Dual Intel Arc Pro B70 32GB XPU lanes).",
        "",
        "---",
        "",
        "## 1. Candidate Overview & Receipts Matrix",
        "",
        "| Candidate ID | Compute | Alex Voice | Sam Voice | Audio Dur | Wall Time | x_Realtime | Receipt | Audio Artifact |",
        "| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- | :--- |",
    ]

    for r in results:
        cid = r["candidate_id"]
        dev = r["compute_device"]
        alex = r["voice_spec"]["host_a"]
        sam = r["voice_spec"]["host_b"]
        dur = f"{r['audio_duration_s']}s"
        wall = f"{r['wall_time_s']}s"
        spd = f"**{r['x_realtime']}x**"
        rec = f"[`{Path(r['artifact_path']).stem}.json`](receipts/{Path(r['artifact_path']).stem}.json)"
        art = f"[`{Path(r['artifact_path']).name}`](samples/{Path(r['artifact_path']).name})"
        lines.append(f"| **`{cid}`** | `{dev}` | `{alex}` | `{sam}` | {dur} | {wall} | {spd} | {rec} | {art} |")

    lines.extend([
        "",
        "---",
        "",
        "## 2. Evaluation Rubric & Human Scorecard",
        "",
        "Score each candidate from **1 (Poor)** to **5 (Excellent)** across the five evaluation dimensions:",
        "- **Naturalness**: Freedom from robotic buzz, breathy wheezing, or synthetic timbre.",
        "- **Technical Pronunciation**: Clarity and accuracy on acronyms (`AM4`, `PCI`, `ADR-0014`, `ADR-0030`, `B70`) and numbers (`107.8`, `382`).",
        "- **Speaker Distinction**: Vocal contrast, pitch differentiation, and distinct presence between Alex and Sam.",
        "- **Conversational Cadence**: Natural delivery, inflection, and prosody across sentences.",
        "- **Listening Fatigue**: Ease and pleasantness of listening over extended dialogue.",
        "",
        "### Scorecard Table",
        "",
        "| Candidate ID | Naturalness (1-5) | Tech Pronunciation (1-5) | Speaker Distinction (1-5) | Cadence (1-5) | Fatigue (1-5) | Total (/25) | Defect Notes / Observations |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])

    for r in results:
        cid = r["candidate_id"]
        lines.append(f"| **`{cid}`** | | | | | | /25 | |")

    lines.extend([
        "",
        "---",
        "",
        "## 3. Defect & Pronunciation Log",
        "",
        "Note specific timestamped defects or mispronunciations observed during listening:",
        "",
        "* **`01_control_heart_adam`**: *Baseline control. Note flat/breathy characteristics or turn transitions.*",
        "* **`02_warm_conversational`**: *Evaluate warmth vs clarity on technical terms.*",
        "* **`03_technical_precision`**: *Evaluate sharpness on acronyms vs potential stiffness.*",
        "* **`04_custom_blend`**: *Evaluate tensor interpolation coherence and timbre.*",
        "* **`05_british_tech`**: *Evaluate British inflection on American hardware terms.*",
        "",
        "---",
        "",
        "## 4. Gate 1 Decision: Finalist Selection",
        "",
        "Select the top **two finalists** to advance to **Slice 2 (Pacing & Post-Processing Assay)**:",
        "",
        "- [ ] **Finalist A**: `________________________`",
        "- [ ] **Finalist B**: `________________________`",
        "",
        "*(Once two finalists are selected, Slice 2 will run the pacing matrix: zero-padding control vs fades vs comfort noise vs dynamic gaps).* ",
        "",
    ])

    scorecard_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nListening Scorecard updated at: {scorecard_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hearth Audio Lab Assay")
    parser.add_argument("--device", choices=["cpu", "xpu", "xpu_dual"], default="cpu", help="Compute device")
    parser.add_argument("--candidate", type=str, default=None, help="Candidate ID filter")
    args = parser.parse_args()

    run_assay(device_mode=args.device, candidate_filter=args.candidate)
