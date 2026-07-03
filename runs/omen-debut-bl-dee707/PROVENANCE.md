# PROVENANCE — runs/omen-debut-bl-dee707/

Materialization of the REAL omen-worker-1 debut build workflow (conductor run `bl-20260702-dee707`),
approved as **D-A3-1** per `EVIDENCE-HUNT-A3.md` (T1). Every value below traces to staged, verified
evidence (`a3-staging/`, session 2192db2b), originally fetched read-only from
`claude@100.74.110.91:/home/claude/work/commandcenter/runs/bl-20260702-dee707/` plus corroborating
OMEN-local sources. No wall-clock timestamps were introduced (D18): every timestamp is an embedded
event/log/checkpoint time from the evidence itself.

## Evidence payload copied verbatim into this run dir

| File here | Staged source | Original location |
|---|---|---|
| `conductor/result.json` | `cand-t1-bl-dee707/bl-20260702-dee707/result.json` | conductor run dir |
| `conductor/nodes.json` | `cand-t1-bl-dee707/bl-20260702-dee707/nodes.json` | conductor run dir |
| `conductor/checkpoints/*.json` (3 files, intact chain `f22f194c` → `fa1f8ff2` → `80ecd061`) | `cand-t1-bl-dee707/bl-20260702-dee707/checkpoints/` | conductor run dir |

## events.jsonl — field-by-field

| Field | Value | Source |
|---|---|---|
| `workflow_id` | `wf-omen-debut-bl-dee707` | fresh id for this materialization (deliberately NOT the fixture id `wf_omen_debut`) |
| `run_id` | `omen-debut-bl-dee707` | this run dir name (pour-b2 mechanism) |
| evt `..._accepted` timestamp | `2026-07-02T10:38:05.037000+00:00` | conductor.log (staged `cand-t1-conductor-log`, lines 52–53): `2026-07-02 10:38:05,037 ... Initialized file checkpoint storage at .../bl-20260702-dee707/checkpoints` and `[bl-20260702-dee707] start trace=ae67e719...`. Log is UTC: its checkpoint-save line `10:38:09,339` matches checkpoint `f22f194c` embedded timestamp `10:38:09.339755+00:00` to <1 ms. |
| evt `..._retro` timestamp | `2026-07-02T10:45:55.071789+00:00` | final checkpoint `conductor/checkpoints/80ecd061-8331-4beb-a78d-00854791f4ba.json` `"timestamp"` field (agrees with conductor.log `10:45:55,072 ... DONE winner=omen-worker-1 promoted=True`) |
| `trace.trace_id` | `ae67e71992f5370c22893923ac4c27ce` | `conductor/result.json` `trace_id` (also embedded in every checkpoint traceparent) |
| actor | `system / cc-conductor` | the conductor produced result.json/checkpoints (fleet.json conductor identity) |
| `artifact_refs[0]` | `capacity_observation` → `runs/omen-debut-bl-dee707/artifacts/obs_omen_debut_001.json` | pour-b2 linkage mechanism (project_capacity.extract_observations resolves the path after `artifacts/` against this run dir) |
| payload summaries | scores/tiebreak/promotion facts | `conductor/result.json` (`assay.scoreboard[omen-worker-1]`, `assay.tiebreak`, `promotion`) |

## artifacts/obs_omen_debut_001.json — field-by-field

| Field | Value | Source |
|---|---|---|
| `contract_version` | `capacity-observation.v1` | contracts/capacity-observation.v1.schema.json |
| `observation_id` | `obs_omen_debut_001` | fresh id (deliberately NOT the fixture id `obs_201`) |
| `decision_id` | `null` | no staged scheduler-decision artifact exists for this run — honest null |
| `workflow_id` / `run_id` | `wf-omen-debut-bl-dee707` / `omen-debut-bl-dee707` | this materialization (see above) |
| `timestamp` | `2026-07-02T10:45:55.071789+00:00` | run-completion time: final checkpoint `80ecd061` embedded `"timestamp"` |
| `builder_id` | `omen-worker-1` | `result.json` `winner`, `builds["omen-worker-1"]`; conductor.log `DONE winner=omen-worker-1 promoted=True`; winning commit `86c020d5` authored `omen-worker-1 <omen-worker-1@commandcenter>` (staged patch, `cand-t1-farmer-branch`) |
| `model_id` | `qwen3-coder:30b` | `result.json` `builds["omen-worker-1"].runner_model`; fleet.json omen-worker-1 `_note`; omen-worker-1 manifest `runtime.runner` |
| `backend` | `ollama` | fleet.json `_note` "Ollama qwen3-coder:30b at omen.mshome.net:11434"; manifest `runner: openai -> omen.mshome.net:11434 qwen3-coder:30b` (openai-compat client over the Ollama service; backend recorded as the serving engine) |
| `hardware_profile_id` | `nvidia-geforce-rtx-5070\|13.1` | staged `cand-t1-ollama-lab/logs/cuda.err.log` (same OMEN Ollama host/day): `description="NVIDIA GeForce RTX 5070" ... driver=13.1`; normalized lowercase, spaces→`-`, `name\|driver` per FLEET-WORK-PLAN convention |
| `workload_shape.task_kind` | `repo-build` | pour-b2 convention for conductor build runs; the run built/pushed branch `ccfarm/bl-20260702-dee707/omen-worker-1/lap1` (`result.json`) |
| `workload_shape.estimated_context_tokens` | `null` | no staged source gives a number — honest null |
| `workload_shape.requires_gpu` | `null` | no staged source states the task itself required a GPU — honest null |
| `workload_shape.notes` | 54.6 = client-measured (D19); 57.40 = local CUDA basis | fleet.json omen-worker-1 `_note` "(54.6 tok/s measured)"; `cand-t1-ollama-lab/hardware-baseline-omen-5070-2026-07-02.md` line 35 & `hardware-capability-matrix.md` line 80: qwen3-coder:30b CUDA decode 57.40 tok/s |
| `observed.runtime_s` | `470.035` | run window from the checkpoint chain context: start = checkpoint-storage initialization `2026-07-02 10:38:05,037` (conductor.log line 52, the creation of this checkpoint chain) → final checkpoint `80ecd061` `10:45:55.071789Z`. 10:45:55.071789 − 10:38:05.037 = 470.034789 s, rounded to ms. (Strict first-checkpoint-FILE `f22f194c` 10:38:09.339755 → last gives 465.732 s; the earlier bound is the chain's own initialization instant, kept because it is the run start the same log ties to the chain.) |
| `observed.ttft_s` | `null` | not measured in any staged source — honest null |
| `observed.tokens_per_s` | `54.6` | fleet.json omen-worker-1 `_note`: "(54.6 tok/s measured)" — client-side measured figure per D19; deliberately NOT the 57.40 local CUDA number (see notes) |
| `observed.ram_gb_peak` / `vram_gb_peak` / `context_tokens` | `null` | no staged source gives per-THIS-run numbers (lab RAM/VRAM deltas are separate bench runs, not this build) — honest nulls |
| `observed.physical` | `null` | no per-run physical telemetry summary staged; raw model_* sensor fields therefore null-by-omission and `model_residency` NEVER producer-set (schema: derived-only). In-window corroboration (not a summary metric): OMEN Ollama server.log shows qwen3-coder:30b template-selection/load activity 2026-07-02T03:38:20→03:41:44-07:00 = 10:38:20→10:41:44Z, inside the run window (staged `cand-t1-ollama-serverlog`) |
| `outcome` | `success` | `result.json` `builds["omen-worker-1"]`: ok=true, status=ok, agent_rc=0, done_signal=true; assay grade A score 99 |
| `failure_class` | `null` | success — no failure |
| `promotion_status` | `approved` | `result.json` `promotion.promoted=true`, ff-only to main `8fdff18d…` → `86c020d5…`; conductor.log `promote: fast-forwarded main …` + `promoted=True` |

## Corroboration chain (why this run is REAL, not fixture-shaped)

1. Checkpoint chain intact and self-consistent: `f22f194c` (prev=null, 10:38:09.339755) → `fa1f8ff2`
   (10:44:57.807730) → `80ecd061` (10:45:55.071789), all `workflow_name=bl-20260702-dee707`, same
   `graph_signature_hash`, traceparents embed trace `ae67e719…`.
2. conductor.log agrees with the checkpoint files to <1 ms on every save line.
3. Winning commit `86c020d5d66e896018105252e6797c59d0a5685c` authored by
   `omen-worker-1 <omen-worker-1@commandcenter>` at `2026-07-02T10:44:41Z` — 74 s before the promote
   line — and the promotion.commit in result.json matches it exactly.
4. OMEN Ollama server.log shows the exact model (`qwen3-coder:30b`) active inside the run window.
5. The physical basis for the throughput class is the same-day OMEN CUDA lab corpus
   (`C:\work\tuning\ollama-backend-lab\`), staged as `cand-t1-ollama-lab`.
