# PROVENANCE — runs/vllm-moe-crash-omen-wsl-2026-07-02/

Materialized under **D-A3-2** (EVIDENCE-HUNT-A3.md, T2) from staged real evidence:
`a3-staging/cand-t2-wsl-vllm/` (OMEN WSL vLLM engine telemetry) and
`a3-staging/cand-t2-d19-d20-decisions/` (conductor decisions.jsonl D20 excerpt).
No projector was run as part of this materialization; `knowledge/` untouched.

## NODE-ATTRIBUTION CORRECTION (read this first)

The pre-loss fixture records (obs_301–303 / dec_assign_301-302) claimed this
phenomenon occurred on **claudefarm1 on 2026-06-30**. That was fixture fiction:
claudefarm1 is a GPU-less Hyper-V VM and could never have run a `requires_gpu`
vLLM probe. The real crash loop ran on **OMEN WSL (NVIDIA GeForce RTX 5070
12GB, WSL2), 2026-07-02T09:34–10:36Z** — see EVIDENCE-HUNT-A3.md ("THE FRAMING
FINDING" and T2). This run dir is the honest re-materialization: same real
phenomenon, corrected node, real embedded timestamps. The fixture-shaped
records are NOT restored.

## What physically happened (the evidence)

`wsl/usage_stats.json` — byte-for-byte copy of the staged OMEN WSL vLLM usage
telemetry — contains **56 ENGINE_CONTEXT entries** with epoch-nanosecond
`log_time` values spanning `1782984890860285952` (2026-07-02T09:34:50.860286Z)
to `1782988566969476096` (2026-07-02T10:36:06.969476Z), ~66 s apart: an
engine-start → crash → restart loop. Every entry reports `vllm_version 0.24.0`,
`model_architecture Qwen3MoeForCausalLM`, `gpu_type NVIDIA GeForce RTX 5070`
(`gpu_memory_per_device` ≈ 12 GB), `platform ...WSL2...`. Entry composition:

- entries 1–24: `quantization auto_awq`, `enforce_eager false`
- entries 25–53: `quantization auto_awq`, `enforce_eager true` (D20: "--enforce-eager does not help — kernel-level check")
- entries 54–56: `quantization moe_wna16`, `enforce_eager true` — the **open rung**, a DIFFERENT failure mode (see caveat below)

The engine never reached serving in any attempt — hence honest nulls in every
`observed` block. Diagnosis (`wsl/d20-decision.jsonl`, conductor decision D20,
recorded 2026-07-02): **Marlin MoE GEMM requires quantization scales on-GPU;
the UVA weight-offloader leaves them host-resident** ("b_scales is not on
GPU"); crash loop; llama.cpp-family (Ollama) is the OMEN engine for
beyond-VRAM models.

## D20 open-rung caveat — untested, not impossible

The `--quantization moe_wna16` path routes through triton kernels that MAY
tolerate UVA host memory. It was never actually tested: it fails earlier in
fresh WSL for want of a C compiler (blocked on `sudo apt install
build-essential`). The final 3 telemetry entries are those compiler-blocked
attempts. Any policy/block derived from this run applies to the **Marlin
auto_awq offload path**; the moe_wna16/triton rung remains an open experiment,
not a proven dead end.

## events.jsonl — field trace

Two minimal events, validated by `tools.workflow.validate_events`:

| Field | Value | Source |
|---|---|---|
| `evt_vllm_moe_crash_accepted.timestamp` | `2026-07-02T09:34:50.860286Z` | `log_time` of the FIRST ENGINE_CONTEXT entry (`1782984890860285952` ns), integer epoch-ns → ISO-8601 Z |
| `evt_vllm_moe_crash_retro.timestamp` | `2026-07-02T10:36:06.969476Z` | `log_time` of the LAST ENGINE_CONTEXT entry (`1782988566969476096` ns) |
| `workflow_id` | `wf-vllm-moe-crash-omen-wsl-2026-07-02` | fresh id per D-A3-2 (deliberately NOT `wf_vllm_probe`, the fixture lineage) |
| `run_id` | `vllm-moe-crash-omen-wsl-2026-07-02` | this run dir |
| `actor` | `system / omen-wsl` | corrected node attribution (telemetry `platform` + `gpu_type`) |
| `artifact_refs` | 3 × `capacity_observation` | the three observations below; `artifact_id` included per `contracts/workflow-event.schema.json` (note: the pour-b2 reference omits it; the runtime validator checks neither) |

## artifacts/ — three observations, three DISTINCT real restart entries

Sampling rule: first, middle, and last of the **53 auto_awq entries**. Three
unanimous same-failure-class samples are what the confidence ladder requires
(`project_findings.HIGH_CONFIDENCE_MIN_SAMPLES = 3`, unanimity = single
failure_class) — and three distinct engine-start-then-crash events are three
honest samples of the phenomenon.

**Deviation from the letter of the sampling spec, for honesty:** the spec said
"first / middle / last entry". The literal last 3 entries of the telemetry are
`moe_wna16` open-rung attempts (compiler-blocked — a different failure mode
that D20 explicitly marks untested). Labeling one of them
`moe_offload_crash` would misattribute evidence, so the last **auto_awq**
entry (#53 of 56) is used instead. The events.jsonl window still spans the full
loop including the moe_wna16 tail, and the retrospective payload says so.

| Observation | Telemetry entry | `log_time` (ns) | `timestamp` | entry particulars |
|---|---|---|---|---|
| `obs_vllm_crash_001` | 1st of 56 (1st auto_awq) | `1782984890860285952` | `2026-07-02T09:34:50.860286Z` | `enforce_eager=false` |
| `obs_vllm_crash_002` | 27th of 56 (middle auto_awq) | `1782986621870501120` | `2026-07-02T10:03:41.870501Z` | `enforce_eager=true` |
| `obs_vllm_crash_003` | 53rd of 56 (last auto_awq) | `1782988316771380992` | `2026-07-02T10:31:56.771381Z` | `enforce_eager=true` |

Field-by-field (identical across the three except `observation_id`,
`timestamp`, and the per-entry particulars embedded in `workload_shape.notes`):

| Field | Value | Source |
|---|---|---|
| `contract_version` | `capacity-observation.v1` | `contracts/capacity-observation.v1.schema.json` |
| `decision_id` | `null` | no scheduler-decision.v1 record exists for this loop (D20 is a human decision log entry, carried in `wsl/d20-decision.jsonl`, not a scheduler prediction — no predictions are fabricated) |
| `timestamp` | per row above | that entry's `log_time`, integer epoch-ns → ISO-8601 Z (D18: telemetry-embedded time, no wall clock) |
| `builder_id` | `omen-wsl` | corrected node (telemetry `platform` = WSL2 kernel `6.18.33.2-microsoft-standard-WSL2`; NOT claudefarm1) |
| `model_id` | `qwen3-30b-a3b-awq` | telemetry `model_architecture Qwen3MoeForCausalLM` + `quantization auto_awq`; the QuantTrio Qwen3-Coder-30B-A3B AWQ checkpoint in the WSL HF hub (staged `hf-hub-model-listing.txt`; D20 "vLLM 0.24 same model class (QuantTrio AWQ)") |
| `backend` | `vllm` | telemetry `vllm_version 0.24.0` |
| `hardware_profile_id` | `nvidia-geforce-rtx-5070\|wsl2` | telemetry `gpu_type` + `platform` (WSL2) |
| `workload_shape.task_kind` | `engine-start` | what each entry IS: an engine start attempt |
| `workload_shape.estimated_context_tokens` | `null` | no workload estimate existed (max_model_len is engine config, not a workload estimate) |
| `workload_shape.requires_gpu` | `true` | telemetry `cuda_runtime 13.0`, `gpu_count 1`; vLLM CUDA engine |
| `workload_shape.notes` | crash-loop context + this entry's `log_time_ns` and `enforce_eager` | ties each observation to its distinct telemetry entry |
| `observed.*` | all `null` (incl. `physical: null`) | the engine never served — honest nulls, nothing measured. `model_residency` never set (derived-by-projection field) |
| `outcome` | `oom_crash` | schema enum member for the VRAM-constraint failure family. Nuance: the proximate crash is a kernel assertion ("b_scales is not on GPU"), which exists only because the model exceeds 12 GB VRAM and weights were offloaded; the precise mechanism is carried in `failure_class` |
| `failure_class` | `moe_offload_crash` | D20 diagnosis (Marlin MoE GEMM scales-on-GPU vs UVA host-resident offload) |
| `promotion_status` | `null` | nothing was promoted or held; the probe never produced a candidate |

## wsl/ — provenance payload

| File | Source | Fidelity |
|---|---|---|
| `usage_stats.json` | staged `cand-t2-wsl-vllm/usage_stats.json` (from OMEN WSL vLLM usage-stats store) | byte-for-byte copy |
| `vllm-model_executor-models-qwen3_moe-Qwen3MoeForCausalLM.json` | staged model-info cache record | byte-for-byte copy |
| `d20-decision.jsonl` | line 20 of the conductor `decisions.jsonl`, as staged in `cand-t2-d19-d20-decisions/decisions-D19-D20.jsonl` | grep line-number prefix (`20:`) stripped; JSON payload otherwise byte-identical (verified parseable) |

## What this run re-derives (when the orchestrator re-projects)

3 observations, one combo `omen-wsl|qwen3-30b-a3b-awq|vllm`: 0 successes /
3 failures, single failure_class `moe_offload_crash` → `known_bad` entry
(0.6 confidence at 3 samples) → high-confidence unanimous finding →
`project_policy` re-emits the scheduling block **honestly attributed**.
The fixture-era `+9984MB adjust_prediction` rule is intentionally NOT
re-derivable from this run: no scheduler decisions, no predictions — that rule
never existed (D-A3-3).
