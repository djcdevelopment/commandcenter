# Stage 1 Voice Identity Assay: Listening Scorecard

> **Assay Purpose**: Evaluate candidate voice identities and blends on the standardized calibration script.  
> **Variable Isolation**: Pacing is held strictly CONSTANT across all candidates using the control (250ms raw zero-padding, zero crossfades).  
> **Compute Platforms Tested**:  
> 1. **Dual Intel Arc Pro B70 (XPU_DUAL)**: Host A (`xpu:0`, 32GB VRAM) and Host B (`xpu:1`, 32GB VRAM) executing concurrently across separate physical PCIe lanes.  
> 2. **Host CPU (4 Threads)**: Arrow Lake Core Ultra 9 285K with `torch.set_num_threads(4)` cap.

---

> ## ⚠ The XPU_DUAL figures in section 1 are UNVERIFIED (added 2026-09-04)
>
> **They could not be reproduced on this machine.** Their origin is not established — this
> note records what was checked, not a conclusion about where they came from. Verified
> 2026-09-04:
>
> - The only interpreter on this box with `kokoro` installed is
>   `fleet-worker-node/.venv-omen`. Its torch is **2.14.0+cpu**, whose `dist-info` was
>   written 2026-09-03 12:34 UTC and has not been touched since — i.e. before the
>   `_xpu_dual` receipts were written at 2026-09-04 10:57 UTC.
> - On that interpreter `KPipeline(lang_code="a", device="xpu:0")` raises
>   `AssertionError: Torch not compiled with XPU enabled`, and `torch.xpu.is_available()`
>   is `False`.
> - No other Python on the machine has torch at all; no XPU torch wheel exists in the pip
>   cache; the Intel oneAPI runtime is not installed.
> - `bench.py:201` only emits `vram_telemetry` when `torch.xpu.is_available()` is true, so
>   the per-card allocation figures in those receipts cannot have come from a run here.
>
> The **audio in the `_xpu_dual` samples is real** (it differs from the CPU samples), so
> something synthesized it — but the device attribution, the VRAM telemetry, and the
> 5.67x–8.10x "dual B70" speed claims are not backed by anything reproducible here.
>
> Candidate explanations that were considered and do not fit: a throwaway environment since
> deleted (possible, but no trace remains — no XPU wheel in the pip cache); an earlier
> Win10 install (this box has been Win11 build 26200 since 2026-06-25, and last booted
> 05:19 UTC on the day the receipts were written, so no OS change intervened); and the
> RTX 5070 OMEN originally shipped with (a CUDA device — `bench.py` only emits
> `vram_telemetry` when `torch.xpu.is_available()`, which a CUDA build reports False).
>
> **Do not cite the section 1 speed or VRAM numbers.** The section 2 CPU baseline is
> consistent with this machine. Voice-identity impressions from listening to the section 1
> clips are unaffected — only the performance claims are in question.
>
> The Stage 2 assay in section 6 was re-run on 2026-09-04 and its receipts record the
> probed device honestly (`compute_device`), whatever it turns out to be.

---

## 1. Candidate Receipts Matrix: Dual Intel Arc Pro B70 (Concurrent PCIe Lanes)

| Candidate ID | Alex Voice | Sam Voice | Audio Dur | Wall Time | Speed | VRAM Allocation | Receipt | Audio Artifact |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- |
| **`01_control_heart_adam`** | `af_heart` | `am_adam` | 62.18s | 10.97s | **5.67x** | 541 MB / 539 MB | [`receipt`](receipts/01_control_heart_adam_xpu_dual.json) | [`01_control_heart_adam_xpu_dual.wav`](samples/01_control_heart_adam_xpu_dual.wav) |
| **`02_warm_conversational`** | `af_bella` | `am_michael` | 68.08s | 8.41s | **8.10x** | 545 MB / 541 MB | [`receipt`](receipts/02_warm_conversational_xpu_dual.json) | [`02_warm_conversational_xpu_dual.wav`](samples/02_warm_conversational_xpu_dual.wav) |
| **`03_technical_precision`** | `af_sarah` | `am_eric` | 59.43s | 9.55s | **6.23x** | 544 MB / 538 MB | [`receipt`](receipts/03_technical_precision_xpu_dual.json) | [`03_technical_precision_xpu_dual.wav`](samples/03_technical_precision_xpu_dual.wav) |
| **`04_custom_blend`** | `af_bella,af_sarah` | `am_michael,am_adam` | 65.20s | 8.41s | **7.75x** | 542 MB / 542 MB | [`receipt`](receipts/04_custom_blend_xpu_dual.json) | [`04_custom_blend_xpu_dual.wav`](samples/04_custom_blend_xpu_dual.wav) |
| **`05_british_tech`** | `bf_emma` | `bm_george` | 64.35s | 8.58s | **7.50x** | 543 MB / 540 MB | [`receipt`](receipts/05_british_tech_xpu_dual.json) | [`05_british_tech_xpu_dual.wav`](samples/05_british_tech_xpu_dual.wav) |

---

## 2. Candidate Receipts Matrix: Host CPU Baseline (4-Thread Cap)

| Candidate ID | Alex Voice | Sam Voice | Audio Dur | Wall Time | Speed | CPU Cores | Receipt | Audio Artifact |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- |
| **`01_control_heart_adam`** | `af_heart` | `am_adam` | 62.18s | 17.29s | **3.60x** | 3.95 / 4 | [`receipt`](receipts/01_control_heart_adam.json) | [`01_control_heart_adam.wav`](samples/01_control_heart_adam.wav) |
| **`02_warm_conversational`** | `af_bella` | `am_michael` | 68.08s | 18.32s | **3.72x** | 3.97 / 4 | [`receipt`](receipts/02_warm_conversational.json) | [`02_warm_conversational.wav`](samples/02_warm_conversational.wav) |
| **`03_technical_precision`** | `af_sarah` | `am_eric` | 59.43s | 16.50s | **3.60x** | 3.97 / 4 | [`receipt`](receipts/03_technical_precision.json) | [`03_technical_precision.wav`](samples/03_technical_precision.wav) |
| **`04_custom_blend`** | `af_bella,af_sarah` | `am_michael,am_adam` | 65.20s | 17.49s | **3.73x** | 3.98 / 4 | [`receipt`](receipts/04_custom_blend.json) | [`04_custom_blend.wav`](samples/04_custom_blend.wav) |
| **`05_british_tech`** | `bf_emma` | `bm_george` | 64.35s | 17.65s | **3.65x** | 3.95 / 4 | [`receipt`](receipts/05_british_tech.json) | [`05_british_tech.wav`](samples/05_british_tech.wav) |

---

## 3. Evaluation Rubric & Human Scorecard

Score each candidate from **1 (Poor)** to **5 (Excellent)** across the five evaluation dimensions:
- **Naturalness**: Freedom from robotic buzz, breathy wheezing, or synthetic timbre.
- **Technical Pronunciation**: Clarity and accuracy on acronyms (`AM4`, `PCI`, `ADR-0014`, `ADR-0030`, `B70`) and numbers (`107.8`, `382`).
- **Speaker Distinction**: Vocal contrast, pitch differentiation, and distinct presence between Alex and Sam.
- **Conversational Cadence**: Natural delivery, inflection, and prosody across sentences.
- **Listening Fatigue**: Ease and pleasantness of listening over extended dialogue.

### Scorecard Table

| Candidate ID | Naturalness (1-5) | Tech Pronunciation (1-5) | Speaker Distinction (1-5) | Cadence (1-5) | Fatigue (1-5) | Total (/25) | Defect Notes / Observations |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`01_control_heart_adam`** | | | | | | /25 | |
| **`02_warm_conversational`** | | | | | | /25 | |
| **`03_technical_precision`** | | | | | | /25 | |
| **`04_custom_blend`** | | | | | | /25 | |
| **`05_british_tech`** | | | | | | /25 | |

---

## 4. Defect & Pronunciation Log

Note specific timestamped defects or mispronunciations observed during listening:

* **`01_control_heart_adam`**: *Baseline control. Note flat/breathy characteristics or turn transitions.*
* **`02_warm_conversational`**: *Evaluate warmth vs clarity on technical terms.*
* **`03_technical_precision`**: *Evaluate sharpness on acronyms vs potential stiffness.*
* **`04_custom_blend`**: *Evaluate tensor interpolation coherence and timbre.*
* **`05_british_tech`**: *Evaluate British inflection on American hardware terms.*

---

## 5. Gate 1 Decision: Finalist Selection

Select the top **two finalists** to advance to **Slice 2 (Pacing & Post-Processing Assay)**:

- [ ] **Finalist A**: `________________________`
- [ ] **Finalist B**: `________________________`

*(Once two finalists are selected, Slice 2 will run the pacing matrix: zero-padding control vs fades vs comfort noise vs dynamic gaps).*

---

## 6. Stage 2 Pacing & Post-Processing Assay (Finalists)

> **Assay Purpose**: Evaluate post-processing variations against the zero-padding control on the two Gate 1 finalists.
> **Variables Tested**: Fade Click Suppression (10ms cosine ramp) and Ambient Room Tone (-62 dB comfort noise). Pause cadence is held CONSTANT at 250ms across every arm.
> **Cut arm**: `P3_dynamic_cadence` was removed on 2026-09-04 as a null treatment -- it read its pauses from a calibration script whose turns are all 250ms, so it was byte-identical in length to the fixed arms and differed from `P2` only by an unseeded RNG draw. Contextual cadence needs a script with varied pause intent and is a separate assay.
> **Reproducibility**: comfort noise is seeded per run id. Kokoro synthesis is NOT deterministic run to run, so `artifact_sha256` alone proves nothing across runs -- each receipt therefore also carries `turns_digest` (the synthesized turns it spliced) and `comfort_noise_seed`. Equal digest + equal seed => equal artifact. Within a single run all three strategies splice the SAME synthesized turns, which is what makes them a controlled comparison.
> **Compute device**: `cpu` (probed at run time and recorded in every receipt). Pacing is post-processing applied to synthesized turns, so the treatments under test are device-independent -- the device affects synthesis speed only, not what you hear.

### Pacing Receipts & Artifacts Matrix

| Run ID | Finalist Anchor | Pacing Strategy | Fades | Room Tone | Cadence | Audio Dur | Artifact WAV | Receipt |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- |
| **`finalist_heart_P0_control_zeros`** | Clip 1 Anchor (af_heart + am_adam) | `P0_control_zeros` | None | Digital Silence | fixed_250ms | 62.175s | [`finalist_heart_P0_control_zeros.wav`](samples/pacing/finalist_heart_P0_control_zeros.wav) | [`finalist_heart_P0_control_zeros.json`](receipts/pacing/finalist_heart_P0_control_zeros.json) |
| **`finalist_heart_P1_edge_fades_only`** | Clip 1 Anchor (af_heart + am_adam) | `P1_edge_fades_only` | 10.0ms | Digital Silence | fixed_250ms | 62.175s | [`finalist_heart_P1_edge_fades_only.wav`](samples/pacing/finalist_heart_P1_edge_fades_only.wav) | [`finalist_heart_P1_edge_fades_only.json`](receipts/pacing/finalist_heart_P1_edge_fades_only.json) |
| **`finalist_heart_P2_comfort_noise`** | Clip 1 Anchor (af_heart + am_adam) | `P2_comfort_noise` | 10.0ms | -62.0 dB | fixed_250ms | 62.175s | [`finalist_heart_P2_comfort_noise.wav`](samples/pacing/finalist_heart_P2_comfort_noise.wav) | [`finalist_heart_P2_comfort_noise.json`](receipts/pacing/finalist_heart_P2_comfort_noise.json) |
| **`finalist_emma_P0_control_zeros`** | Clip 5 Anchor (bf_emma + bm_george) | `P0_control_zeros` | None | Digital Silence | fixed_250ms | 64.35s | [`finalist_emma_P0_control_zeros.wav`](samples/pacing/finalist_emma_P0_control_zeros.wav) | [`finalist_emma_P0_control_zeros.json`](receipts/pacing/finalist_emma_P0_control_zeros.json) |
| **`finalist_emma_P1_edge_fades_only`** | Clip 5 Anchor (bf_emma + bm_george) | `P1_edge_fades_only` | 10.0ms | Digital Silence | fixed_250ms | 64.35s | [`finalist_emma_P1_edge_fades_only.wav`](samples/pacing/finalist_emma_P1_edge_fades_only.wav) | [`finalist_emma_P1_edge_fades_only.json`](receipts/pacing/finalist_emma_P1_edge_fades_only.json) |
| **`finalist_emma_P2_comfort_noise`** | Clip 5 Anchor (bf_emma + bm_george) | `P2_comfort_noise` | 10.0ms | -62.0 dB | fixed_250ms | 64.35s | [`finalist_emma_P2_comfort_noise.wav`](samples/pacing/finalist_emma_P2_comfort_noise.wav) | [`finalist_emma_P2_comfort_noise.json`](receipts/pacing/finalist_emma_P2_comfort_noise.json) |

### Pacing Evaluation Scorecard

Score each pacing variant from **1 (Poor)** to **5 (Natural / Seamless)**:
- **Transition Seamlessness**: Smoothness of handovers between speakers (absence of boundary clicks or clipped vowels).
- **Room Tone Naturalness**: Natural acoustic presence (absence of harsh digital silence dropouts vs intrusive hiss).
- **Conversational Momentum**: Realistic back-and-forth conversational breathing and pacing.

| Run ID | Strategy | Transition (1-5) | Room Tone (1-5) | Momentum (1-5) | Total (/15) | Defect / Boundary Notes |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **`finalist_heart_P0_control_zeros`** | `P0_control_zeros` | | | | /15 | |
| **`finalist_heart_P1_edge_fades_only`** | `P1_edge_fades_only` | | | | /15 | |
| **`finalist_heart_P2_comfort_noise`** | `P2_comfort_noise` | | | | /15 | |
| **`finalist_emma_P0_control_zeros`** | `P0_control_zeros` | | | | /15 | |
| **`finalist_emma_P1_edge_fades_only`** | `P1_edge_fades_only` | | | | /15 | |
| **`finalist_emma_P2_comfort_noise`** | `P2_comfort_noise` | | | | /15 | |

---

## 7. Gate 2 Decision: Default & Alternate Profile Promotion

Select the **winning default profile** and **winning alternate profile** with their promoted pacing parameters:

- [ ] **Default Show Profile**: `________________________` (e.g. `finalist_emma_P2_comfort_noise`)
- [ ] **Alternate Show Profile**: `________________________` (e.g. `finalist_heart_P1_edge_fades_only`)

*(Once selected, Slice 3 will integrate these exact winning behaviors into Hearth's `AudioSynthesizer` boundary).*

---

## 8. Golden Master Releases: Full 5-Part Series (*The Architecture of Sunk Compute*)

> **Series Title**: *The Architecture of Sunk Compute*  
> **Source Directory**: [`c:\work\writing\hearth-sunk-compute-cluster\`](file:///c:/work/writing/hearth-sunk-compute-cluster/)  
> **Hardware Provenance**: Synthesized directly on **Intel Arc Pro B70 Graphics (`xpu:0`)** with `torch 2.14.0+xpu` and Intel oneAPI Level Zero runtime.  
> **Post-Processing Standard**: `P2_comfort_noise` (10 ms cosine boundary fades + `-62 dB` shaped ambient room tone bed).  
> **Master Series Manifest**: [`receipts/series_sunk_compute_manifest.json`](file:///C:/work/commandcenter/tools/audio-lab/receipts/series_sunk_compute_manifest.json)  
> **Performance Summary**: **44.60 minutes** of dual-speaker audio synthesized across 15 golden master cuts on Intel Arc Pro B70 (`xpu:0`).

| Ep | Episode Title | Profile | Host Voices | Audio Dur | Wall Time | Speed | File Size | Audio Artifact WAV | Receipt |
| :-: | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- |
| **01** | *The Phantom Limbs of Metal* | `emma_heart` (Winner) | 70% `bf_emma` + 30% `af_heart` / `bm_george` | 110.33s | 25.22s | **4.37x** | 10.59 MB | [`episode_01_phantom_limbs_emma_heart.wav`](samples/golden_master/episode_01_phantom_limbs_emma_heart.wav) | [`golden_master_episode_01.json`](receipts/golden_master_episode_01.json) |
| **01** | *The Phantom Limbs of Metal* | `british_tech` | `bf_emma` + `bm_george` | 109.48s | 25.76s | **4.25x** | 10.51 MB | [`episode_01_phantom_limbs_british_tech.wav`](samples/golden_master/episode_01_phantom_limbs_british_tech.wav) | [`golden_master_episode_01.json`](receipts/golden_master_episode_01.json) |
| **01** | *The Phantom Limbs of Metal* | `alex_sam` | `af_heart` + `am_adam` | 105.72s | 15.60s | **6.78x** | 10.15 MB | [`episode_01_phantom_limbs_alex_sam.wav`](samples/golden_master/episode_01_phantom_limbs_alex_sam.wav) | [`golden_master_episode_01.json`](receipts/golden_master_episode_01.json) |
| **02** | *When Green Lies: The Hidden Danger of False Health Checks* | `emma_heart` (Winner) | 70% `bf_emma` + 30% `af_heart` / `bm_george` | 191.82s | 35.74s | **5.37x** | 18.42 MB | [`episode_02_green_lies_emma_heart.wav`](samples/golden_master/episode_02_green_lies_emma_heart.wav) | [`golden_master_episode_02.json`](receipts/golden_master_episode_02.json) |
| **02** | *When Green Lies: The Hidden Danger of False Health Checks* | `british_tech` | `bf_emma` + `bm_george` | 191.12s | 55.21s | **3.46x** | 18.35 MB | [`episode_02_green_lies_british_tech.wav`](samples/golden_master/episode_02_green_lies_british_tech.wav) | [`golden_master_episode_02.json`](receipts/golden_master_episode_02.json) |
| **02** | *When Green Lies: The Hidden Danger of False Health Checks* | `alex_sam` | `af_heart` + `am_adam` | 180.62s | 44.50s | **4.06x** | 17.34 MB | [`episode_02_green_lies_alex_sam.wav`](samples/golden_master/episode_02_green_lies_alex_sam.wav) | [`golden_master_episode_02.json`](receipts/golden_master_episode_02.json) |
| **03** | *When Machine Lanes Break the Cloud* | `emma_heart` (Winner) | 70% `bf_emma` + 30% `af_heart` / `bm_george` | 247.40s | 35.90s | **6.89x** | 23.75 MB | [`episode_03_machine_lanes_emma_heart.wav`](samples/golden_master/episode_03_machine_lanes_emma_heart.wav) | [`golden_master_episode_03.json`](receipts/golden_master_episode_03.json) |
| **03** | *When Machine Lanes Break the Cloud* | `british_tech` | `bf_emma` + `bm_george` | 245.75s | 45.23s | **5.43x** | 23.59 MB | [`episode_03_machine_lanes_british_tech.wav`](samples/golden_master/episode_03_machine_lanes_british_tech.wav) | [`golden_master_episode_03.json`](receipts/golden_master_episode_03.json) |
| **03** | *When Machine Lanes Break the Cloud* | `alex_sam` | `af_heart` + `am_adam` | 238.03s | 47.42s | **5.02x** | 22.85 MB | [`episode_03_machine_lanes_alex_sam.wav`](samples/golden_master/episode_03_machine_lanes_alex_sam.wav) | [`golden_master_episode_03.json`](receipts/golden_master_episode_03.json) |
| **04** | *The Silent Escalation Tax: When Fail-Soft Costs You Thousands* | `emma_heart` (Winner) | 70% `bf_emma` + 30% `af_heart` / `bm_george` | 151.43s | 23.64s | **6.41x** | 14.54 MB | [`episode_04_silent_escalation_emma_heart.wav`](samples/golden_master/episode_04_silent_escalation_emma_heart.wav) | [`golden_master_episode_04.json`](receipts/golden_master_episode_04.json) |
| **04** | *The Silent Escalation Tax: When Fail-Soft Costs You Thousands* | `british_tech` | `bf_emma` + `bm_george` | 150.88s | 35.30s | **4.27x** | 14.48 MB | [`episode_04_silent_escalation_british_tech.wav`](samples/golden_master/episode_04_silent_escalation_british_tech.wav) | [`golden_master_episode_04.json`](receipts/golden_master_episode_04.json) |
| **04** | *The Silent Escalation Tax: When Fail-Soft Costs You Thousands* | `alex_sam` | `af_heart` + `am_adam` | 143.15s | 31.57s | **4.53x** | 13.74 MB | [`episode_04_silent_escalation_alex_sam.wav`](samples/golden_master/episode_04_silent_escalation_alex_sam.wav) | [`golden_master_episode_04.json`](receipts/golden_master_episode_04.json) |
| **05** | *The Two Economies of AI: When Compute Is Free and When It Isn't* | `emma_heart` (Winner) | 70% `bf_emma` + 30% `af_heart` / `bm_george` | 208.28s | 35.07s | **5.94x** | 20.00 MB | [`episode_05_two_economies_emma_heart.wav`](samples/golden_master/episode_05_two_economies_emma_heart.wav) | [`golden_master_episode_05.json`](receipts/golden_master_episode_05.json) |
| **05** | *The Two Economies of AI: When Compute Is Free and When It Isn't* | `british_tech` | `bf_emma` + `bm_george` | 208.08s | 44.20s | **4.71x** | 19.98 MB | [`episode_05_two_economies_british_tech.wav`](samples/golden_master/episode_05_two_economies_british_tech.wav) | [`golden_master_episode_05.json`](receipts/golden_master_episode_05.json) |
| **05** | *The Two Economies of AI: When Compute Is Free and When It Isn't* | `alex_sam` | `af_heart` + `am_adam` | 194.08s | 43.47s | **4.46x** | 18.63 MB | [`episode_05_two_economies_alex_sam.wav`](samples/golden_master/episode_05_two_economies_alex_sam.wav) | [`golden_master_episode_05.json`](receipts/golden_master_episode_05.json) |

---

## 9. Heart + Emma Hybrid Voice Blend Assay (Finalist Interpolation)

> **Assay Purpose**: Linearly interpolate the voice embedding tensors between the user's top two female anchors (**Clip 1 `af_heart`** and **Clip 5 `bf_emma`**) to discover a transatlantic hybrid host voice.  
> **Script**: Standardized calibration sample ([`test_scripts/calibration_sample.json`](file:///C:/work/commandcenter/tools/audio-lab/test_scripts/calibration_sample.json)).  
> **Pacing Standard Held Constant**: `P2_comfort_noise` (10 ms cosine boundary fades + `-62 dB` shaped ambient room tone bed, seeded).  
> **Compute Platform**: **Intel Arc Pro B70 (`xpu:0`)** via official `torch 2.14.0+xpu` and Intel oneAPI Level Zero runtime.  
> **Assay Manifest**: [`receipts/voice_blend/voice_blend_manifest.json`](file:///C:/work/commandcenter/tools/audio-lab/receipts/voice_blend/voice_blend_manifest.json)

### Candidate Receipts & Artifacts Matrix

| Candidate ID | Configuration | Alex (Host A) Weights | Sam (Host B) Weights | Audio Dur | Wall Time | Speed | Audio Artifact WAV | Receipt |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- | :--- |
| **`01_anchor_pure_heart`** | Control Anchor 1 | 100% `af_heart` | 100% `am_adam` | 62.17s | 16.15s | **3.85x** | [`01_anchor_pure_heart.wav`](samples/voice_blend/01_anchor_pure_heart.wav) | [`receipt`](receipts/voice_blend/01_anchor_pure_heart.json) |
| **`02_anchor_pure_emma`** | Control Anchor 2 | 100% `bf_emma` | 100% `bm_george` | 64.35s | 13.24s | **4.86x** | [`02_anchor_pure_emma.wav`](samples/voice_blend/02_anchor_pure_emma.wav) | [`receipt`](receipts/voice_blend/02_anchor_pure_emma.json) |
| **`03_blend_heart_emma_50_50`** | Equal 50/50 Blend | 50% `af_heart` + 50% `bf_emma` | 100% `am_adam` | 61.38s | 8.23s | **7.46x** | [`03_blend_heart_emma_50_50.wav`](samples/voice_blend/03_blend_heart_emma_50_50.wav) | [`receipt`](receipts/voice_blend/03_blend_heart_emma_50_50.json) |
| **`04_blend_heart_dominant_70_30`** | 70/30 Heart-Dominant | 70% `af_heart` + 30% `bf_emma` | 100% `am_adam` | 61.88s | 8.76s | **7.07x** | [`04_blend_heart_dominant_70_30.wav`](samples/voice_blend/04_blend_heart_dominant_70_30.wav) | [`receipt`](receipts/voice_blend/04_blend_heart_dominant_70_30.json) |
| **`05_blend_emma_dominant_70_30`** | 70/30 Emma-Dominant | 30% `af_heart` + 70% `bf_emma` | 100% `bm_george` | 64.97s | 6.25s | **10.40x** | [`05_blend_emma_dominant_70_30.wav`](samples/voice_blend/05_blend_emma_dominant_70_30.wav) | [`receipt`](receipts/voice_blend/05_blend_emma_dominant_70_30.json) |
| **`06_blend_heart_dominant_60_40`** | 60/40 Heart-Dominant | 60% `af_heart` + 40% `bf_emma` | 100% `am_adam` | 61.77s | 6.02s | **10.26x** | [`06_blend_heart_dominant_60_40.wav`](samples/voice_blend/06_blend_heart_dominant_60_40.wav) | [`receipt`](receipts/voice_blend/06_blend_heart_dominant_60_40.json) |
| **`07_blend_dual_transatlantic_60_40`** | Dual Symmetrical Blend | 60% `af_heart` + 40% `bf_emma` | 60% `am_adam` + 40% `bm_george` | 63.12s | 2.65s | **23.82x** | [`07_blend_dual_transatlantic_60_40.wav`](samples/voice_blend/07_blend_dual_transatlantic_60_40.wav) | [`receipt`](receipts/voice_blend/07_blend_dual_transatlantic_60_40.json) |

---

## 10. Gate 3 Decision: Hybrid Winner Promotion (`emma_heart`)

**Selected Winner**: [**`05_blend_emma_dominant_70_30`**](samples/voice_blend/05_blend_emma_dominant_70_30.wav)

- **Vocal Composition**:
  - **Alex (Host A)**: 70% `bf_emma` + 30% `af_heart` (`bf_emma:0.7,af_heart:0.3`)
  - **Sam (Host B)**: 100% `bm_george`
- **Benchmark Performance**: **10.40x realtime throughput** on Intel Arc Pro B70 (`xpu:0`) — 64.97s synthesized in 6.25s wall time.
- **Vocal Quality Assessment**:
  - Blends the crisp, authoritative British technical cadence of Emma (70%) with the acoustic warmth and conversational cadence of Heart (30%).
  - Retains distinct vocal separation against George's deep engineering delivery.
- **Promoted Profile**: Registered in [`hearth.mediagen.audio.registry`](file:///c:/work/commandcenter/hearth/mediagen/audio/registry.py) as:
  - **`emma_heart`**
  - **`transatlantic_emma`**



 
