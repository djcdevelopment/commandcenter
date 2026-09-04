"""Audio Lab CLI benchmark: Stage 2 Pacing & Post-Processing Assay.

Evaluates pacing and post-processing variables against the zero-padding control
on the two Gate 1 finalists:
1. Finalist A: af_heart (Clip 1) + am_adam
2. Finalist B: bf_emma (Clip 5) + bm_george

Pacing Strategies Evaluated:
- P0_control_zeros: 250ms raw zero-padding, 0ms fade, no comfort noise (CONTROL).
- P1_edge_fades_only: 250ms zero-padding + 10ms cosine edge fades (isolating click suppression).
- P2_comfort_noise: 250ms pause filled with -62 dB comfort noise + 10ms fades (isolating ambient bed).

A fourth arm, P3_dynamic_cadence, was cut on 2026-09-04 as a NULL TREATMENT. It derived its
"dynamic" pause from the script's own pause_after_ms, but calibration_sample.json carries
250ms on all six turns -- so P3's pause schedule was identical to the fixed-250ms arms and
every variant came out to the same byte count (5,968,880 heart / 6,177,680 emma). It
differed from P2 only by an unseeded RNG draw, so scoring the two against each other would
have been scoring noise. Testing contextual cadence needs a script whose turns carry varied
pause intent (episode_01_script.json does), and is a separate assay.

The splice itself now calls hearth.mediagen.audio.pacing.splice_dialogue -- the same code
production runs -- rather than a lab-local reimplementation, so a good listening result
cannot bless pacing logic that never ships.

Comfort noise is seeded per run_id and the seed rides the receipt. That makes the SPLICE
reproducible, not the artifact: Kokoro synthesis varies run to run, so each receipt also
records turns_digest over the synthesized turns. Equal turns_digest + equal seed => equal
artifact_sha256; a differing artifact with a differing digest means synthesis moved, not
pacing. Within one run every strategy splices the same turns, which is what makes the
comparison controlled.

Runs on Dual Intel Arc Pro B70 GPUs (xpu_dual: Alex on xpu:0, Sam on xpu:1).
Emits durable JSON receipts to tools/audio-lab/receipts/pacing/ and updates scorecard.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add the lab directory and the repo root to sys.path so both local modules and the
# production hearth package import cleanly when this is run as a bare script.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
for _path in (SCRIPT_DIR, REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import numpy as np
import soundfile as sf
import torch

from kokoro import KPipeline

from hearth.mediagen.audio.pacing import splice_dialogue

# The two Gate 1 finalists selected by user
FINALISTS = {
    "finalist_heart": {
        "label": "Clip 1 Anchor (af_heart + am_adam)",
        "alex": "af_heart",
        "sam": "am_adam",
    },
    "finalist_emma": {
        "label": "Clip 5 Anchor (bf_emma + bm_george)",
        "alex": "bf_emma",
        "sam": "bm_george",
    },
}

# Every surviving arm holds the pause schedule fixed at 250ms; only fades and room tone
# vary. See the module docstring for why the dynamic-cadence arm was cut.
FIXED_PAUSE_MS = 250

PACING_STRATEGIES = {
    "P0_control_zeros": {
        "description": "Baseline control: 250ms raw digital silence, 0ms fade, no comfort noise",
        "fade_ms": 0.0,
        "room_tone_db": None,
    },
    "P1_edge_fades_only": {
        "description": "Click suppression: 250ms raw digital silence + 10ms cosine boundary fades",
        "fade_ms": 10.0,
        "room_tone_db": None,
    },
    "P2_comfort_noise": {
        "description": "Ambient bed: 250ms pause filled with -62 dB shaped comfort noise + 10ms fades",
        "fade_ms": 10.0,
        "room_tone_db": -62.0,
    },
}


def build_pipelines() -> tuple[str, dict]:
    """Probe for real XPU capability and report the device actually used.

    This used to hardcode xpu:0/xpu:1. That is not a safe assumption: as of 2026-09-04 the
    only interpreter on this box with kokoro installed (fleet-worker-node/.venv-omen)
    carries torch 2.14.0+cpu, where KPipeline(device='xpu:0') raises
    "Torch not compiled with XPU enabled". A bench must report the device it ran on, not
    the one it hoped for -- the receipt's compute_device field is evidence, not a label.
    """
    def _drain() -> None:
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            try:
                torch.xpu.empty_cache()
            except Exception:
                pass

    def _try(mode: str):
        """Build pipelines for a mode and PROVE them with a real synthesis."""
        if mode == "xpu_dual":
            pipes = {"host_a": KPipeline(lang_code="a", device="xpu:0"),
                     "host_b": KPipeline(lang_code="a", device="xpu:1")}
        elif mode == "xpu:0":
            pipe = KPipeline(lang_code="a", device="xpu:0")
            pipes = {"host_a": pipe, "host_b": pipe}
        else:
            torch.set_num_threads(4)
            pipe = KPipeline(lang_code="a", device="cpu")
            pipes = {"host_a": pipe, "host_b": pipe}

        def probe(role: str, voice: str):
            return [a for _, _, a in pipes[role]("Probe.", voice=voice, speed=1.0)]

        if mode == "xpu_dual":
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(probe, r, v)
                           for r, v in (("host_a", "af_heart"), ("host_b", "am_adam"))]
                for future in futures:
                    future.result()
        else:
            probe("host_a", "af_heart")
        return pipes

    has_xpu = hasattr(torch, "xpu") and torch.xpu.is_available()
    xpu_count = torch.xpu.device_count() if has_xpu else 0

    # A card being VISIBLE is not the card being USABLE, and torch.xpu.mem_get_info's
    # "free" is not what Level Zero will actually hand out. Measured 2026-09-04 with
    # ArcServe's llama-server resident on both B70s (~15 GB each): the dual path dies in
    # Kokoro's text-encoder LSTM with UR_RESULT_ERROR_OUT_OF_RESOURCES, and a single card
    # then dies loading a voice pack with UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY -- while
    # mem_get_info still reported 16.7 GB free. So every rung is proved by synthesizing,
    # and the receipt records the rung that actually worked.
    candidates = ["cpu"]
    if xpu_count >= 2:
        candidates = ["xpu_dual", "xpu:0", "cpu"]
    elif xpu_count == 1:
        candidates = ["xpu:0", "cpu"]

    for mode in candidates:
        try:
            pipes = _try(mode)
            print(f"  device probe: {mode} PROVED by synthesis")
            return mode, pipes
        except Exception as exc:
            print(f"  !! device probe {mode} FAILED ({type(exc).__name__}: {str(exc)[:90]})")
            _drain()
    raise RuntimeError("no usable synthesis device: every candidate failed its probe")


def run_seed_for(run_id: str) -> int:
    """Deterministic comfort-noise seed for a run, so its artifact_sha256 is reproducible."""
    return int.from_bytes(hashlib.sha256(run_id.encode("utf-8")).digest()[:8], "big")


def load_calibration_script() -> tuple[dict, str]:
    script_path = SCRIPT_DIR / "test_scripts" / "calibration_sample.json"
    raw = script_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return json.loads(raw), digest


def run_pacing_assay() -> list[dict]:
    pacing_samples_dir = SCRIPT_DIR / "samples" / "pacing"
    pacing_receipts_dir = SCRIPT_DIR / "receipts" / "pacing"
    pacing_samples_dir.mkdir(parents=True, exist_ok=True)
    pacing_receipts_dir.mkdir(parents=True, exist_ok=True)

    script_data, script_sha256 = load_calibration_script()
    turns = script_data["turns"]
    sample_rate = 24000

    device_mode, pipelines = build_pipelines()

    print("=" * 78)
    print("HEARTH AUDIO LAB -- STAGE 2: PACING & POST-PROCESSING ASSAY")
    print(f"Compute device: {device_mode}")
    print("Finalists Tested: Finalist Heart (Clip 1) & Finalist Emma (Clip 5)")
    print("Strategies: P0 (Control), P1 (Fades), P2 (Comfort Noise)")
    print("=" * 78)

    results = []

    for f_key, f_data in FINALISTS.items():
        alex_voice = f_data["alex"]
        sam_voice = f_data["sam"]
        print(f"\n========================================================")
        print(f"Synthesizing Raw Audio Chunks for: {f_data['label']}")
        print(f"Alex: {alex_voice} | Sam: {sam_voice}")
        print(f"========================================================")

        # 1. Synthesize all turns -- concurrently only when each host owns its own card.
        def synthesize_turn(turn_info: tuple[int, dict]) -> tuple[int, np.ndarray, int]:
            idx, turn = turn_info
            speaker = turn["speaker"]
            voice = alex_voice if speaker == "host_a" else sam_voice
            pipe = pipelines[speaker]
            chunks = []
            for _, _, audio in pipe(turn["text"], voice=voice, speed=1.0):
                chunks.append(audio)
            audio_cat = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
            return idx, audio_cat, turn.get("pause_after_ms", 250)

        indexed_turns = list(enumerate(turns))
        if device_mode == "xpu_dual":
            with ThreadPoolExecutor(max_workers=2) as pool:
                turn_results = list(pool.map(synthesize_turn, indexed_turns))
            turn_results.sort(key=lambda x: x[0])
        else:
            # host_a and host_b share one pipeline off the dual path; driving it from two
            # threads buys nothing and is not known to be safe.
            turn_results = [synthesize_turn(item) for item in indexed_turns]

        raw_audios = [r[1] for r in turn_results]
        # Kokoro synthesis is NOT deterministic run to run (verified 2026-09-04: the P0
        # control, whose only processing is zero-padding, produced different bytes on two
        # consecutive runs). So artifact_sha256 alone cannot be a reproducibility claim.
        # This digest pins the synthesized turns instead: equal turns_digest + equal
        # comfort_noise_seed => equal artifact_sha256, which IS checkable, and it separates
        # a synthesis difference from a pacing difference when two receipts disagree.
        turns_digest = hashlib.sha256(
            b"".join(np.ascontiguousarray(a, dtype=np.float32).tobytes() for a in raw_audios)
        ).hexdigest()
        # The script's own pause_after_ms is deliberately NOT used: this assay holds the
        # pause schedule constant so fades and room tone are the only variables.

        # 2. Evaluate each Pacing Strategy against the identical raw audio turns
        fixed_pauses_ms = [FIXED_PAUSE_MS] * len(raw_audios)

        for p_key, p_cfg in PACING_STRATEGIES.items():
            start_wall = time.perf_counter()
            fade_ms = p_cfg["fade_ms"]
            room_tone_db = p_cfg["room_tone_db"]

            run_id = f"{f_key}_{p_key}"
            seed = run_seed_for(run_id)

            # The production splice, not a lab copy -- so what scores well here is what ships.
            total_audio = splice_dialogue(
                raw_audios, fixed_pauses_ms, sample_rate=sample_rate,
                room_tone_db=room_tone_db, fade_ms=fade_ms, seed=seed,
            )
            audio_duration = len(total_audio) / float(sample_rate)
            wall_time = time.perf_counter() - start_wall

            output_wav = pacing_samples_dir / f"{run_id}.wav"
            sf.write(str(output_wav), total_audio, sample_rate, subtype="FLOAT")
            artifact_bytes = output_wav.read_bytes()
            artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()

            receipt = {
                "assay_stage": 2,
                "run_id": run_id,
                "finalist": f_key,
                "finalist_label": f_data["label"],
                "voice_spec": {"host_a": alex_voice, "host_b": sam_voice},
                "pacing_strategy": p_key,
                "strategy_description": p_cfg["description"],
                "fade_ms": fade_ms,
                "room_tone_db": room_tone_db,
                "pause_mode": f"fixed_{FIXED_PAUSE_MS}ms",
                "comfort_noise_seed": seed,
                "turns_digest": f"sha256:{turns_digest}",
                "compute_device": device_mode,
                "splice_impl": "hearth.mediagen.audio.pacing.splice_dialogue",
                "sample_rate": sample_rate,
                "audio_duration_s": round(audio_duration, 3),
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "script_digest": f"sha256:{script_sha256}",
                "artifact_path": str(output_wav),
                "artifact_bytes": len(artifact_bytes),
                "artifact_sha256": artifact_sha256,
            }

            receipt_path = pacing_receipts_dir / f"{run_id}.json"
            receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

            print(f"  [{p_key:<20}] Dur: {receipt['audio_duration_s']}s | Saved: {output_wav.name}")
            results.append(receipt)

    print("\n" + "=" * 78)
    print("STAGE 2 PACING ASSAY SUMMARY")
    print("=" * 78)
    for r in results:
        print(f"{r['run_id']:<36} | Dur: {r['audio_duration_s']:<6}s | {Path(r['artifact_path']).name}")
    print("=" * 78)

    append_pacing_section_to_scorecard(results)
    return results


SCORECARD_MARKER = "## 6. Stage 2 Pacing & Post-Processing Assay"


def append_pacing_section_to_scorecard(results: list[dict]) -> None:
    """Rewrite sections 6 and 7 of the scorecard from this run's receipts.

    DESTRUCTIVE BY DESIGN: everything from the section-6 marker onward is replaced, so a
    re-run after a human has filled in listening scores discards those ratings. It
    replaces rather than appends because appending duplicated sections 6 and 7 on every
    run. If you need to re-run once scores exist, copy the filled tables out first.
    """
    scorecard_path = SCRIPT_DIR / "LISTENING_SCORECARD.md"
    existing = scorecard_path.read_text(encoding="utf-8")

    marker_index = existing.find(SCORECARD_MARKER)
    if marker_index != -1:
        existing = existing[:marker_index].rstrip()
        if existing.endswith("---"):
            existing = existing[: -len("---")].rstrip()

    lines = [
        "",
        "---",
        "",
        "## 6. Stage 2 Pacing & Post-Processing Assay (Finalists)",
        "",
        "> **Assay Purpose**: Evaluate post-processing variations against the zero-padding control on the two Gate 1 finalists.",
        "> **Variables Tested**: Fade Click Suppression (10ms cosine ramp) and Ambient Room Tone (-62 dB comfort noise). Pause cadence is held CONSTANT at 250ms across every arm.",
        "> **Cut arm**: `P3_dynamic_cadence` was removed on 2026-09-04 as a null treatment -- it read its pauses from a calibration script whose turns are all 250ms, so it was byte-identical in length to the fixed arms and differed from `P2` only by an unseeded RNG draw. Contextual cadence needs a script with varied pause intent and is a separate assay.",
        "> **Reproducibility**: comfort noise is seeded per run id. Kokoro synthesis is NOT deterministic run to run, so `artifact_sha256` alone proves nothing across runs -- each receipt therefore also carries `turns_digest` (the synthesized turns it spliced) and `comfort_noise_seed`. Equal digest + equal seed => equal artifact. Within a single run all three strategies splice the SAME synthesized turns, which is what makes them a controlled comparison.",
        f"> **Compute device**: `{results[0]['compute_device'] if results else 'unknown'}` (probed at run time and recorded in every receipt). Pacing is post-processing applied to synthesized turns, so the treatments under test are device-independent -- the device affects synthesis speed only, not what you hear.",
        "",
        "### Pacing Receipts & Artifacts Matrix",
        "",
        "| Run ID | Finalist Anchor | Pacing Strategy | Fades | Room Tone | Cadence | Audio Dur | Artifact WAV | Receipt |",
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- |",
    ]

    for r in results:
        rid = r["run_id"]
        fin = r["finalist_label"]
        strat = r["pacing_strategy"]
        fades = f"{r['fade_ms']}ms" if r["fade_ms"] > 0 else "None"
        tone = f"{r['room_tone_db']} dB" if r["room_tone_db"] is not None else "Digital Silence"
        cad = r["pause_mode"]
        dur = f"{r['audio_duration_s']}s"
        art = f"[`{Path(r['artifact_path']).name}`](samples/pacing/{Path(r['artifact_path']).name})"
        rec = f"[`{rid}.json`](receipts/pacing/{rid}.json)"
        lines.append(f"| **`{rid}`** | {fin} | `{strat}` | {fades} | {tone} | {cad} | {dur} | {art} | {rec} |")

    lines.extend([
        "",
        "### Pacing Evaluation Scorecard",
        "",
        "Score each pacing variant from **1 (Poor)** to **5 (Natural / Seamless)**:",
        "- **Transition Seamlessness**: Smoothness of handovers between speakers (absence of boundary clicks or clipped vowels).",
        "- **Room Tone Naturalness**: Natural acoustic presence (absence of harsh digital silence dropouts vs intrusive hiss).",
        "- **Conversational Momentum**: Realistic back-and-forth conversational breathing and pacing.",
        "",
        "| Run ID | Strategy | Transition (1-5) | Room Tone (1-5) | Momentum (1-5) | Total (/15) | Defect / Boundary Notes |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :--- |",
    ])

    for r in results:
        lines.append(f"| **`{r['run_id']}`** | `{r['pacing_strategy']}` | | | | /15 | |")

    lines.extend([
        "",
        "---",
        "",
        "## 7. Gate 2 Decision: Default & Alternate Profile Promotion",
        "",
        "Select the **winning default profile** and **winning alternate profile** with their promoted pacing parameters:",
        "",
        "- [ ] **Default Show Profile**: `________________________` (e.g. `finalist_emma_P2_comfort_noise`)",
        "- [ ] **Alternate Show Profile**: `________________________` (e.g. `finalist_heart_P1_edge_fades_only`)",
        "",
        "*(Once selected, Slice 3 will integrate these exact winning behaviors into Hearth's `AudioSynthesizer` boundary).* ",
        "",
    ])

    updated = existing + "\n" + "\n".join(lines)
    scorecard_path.write_text(updated, encoding="utf-8")
    print(f"\nScorecard successfully updated with Stage 2 Pacing Assay at: {scorecard_path}")


if __name__ == "__main__":
    run_pacing_assay()
