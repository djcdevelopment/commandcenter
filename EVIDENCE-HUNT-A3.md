# EVIDENCE-HUNT-A3 — Phase 1 Report (REPORT ONLY — nothing copied, nothing projected)

Date: 2026-07-03 · Stream A3 (widened, three target sets) · Hunt: cc-conductor + builder VMs + OMEN local/WSL ·
Every candidate adversarially provenance-checked against the fixture mimics. Staged copies:
`%TEMP%\claude\...\scratchpad\a3-staging\` (session 2192db2b).

## THE FRAMING FINDING — it governs everything below

**The pre-loss belief store (git `4cf048b`) was not built from real fleet runs for the lost lineage.**
The obs_201/202, obs_301–303, and obs_401 records were **fixture projections** poured into repo
`knowledge/` (violating the standing guardrail "real gates open on real fleet evidence only"):

- `prediction_bias` mean_signed_error **9984.0 is arithmetically locked to fixture values**:
  ((28.4×1024−18432)+(29.1×1024−20480))/2 = 9984.0 exactly. The `dec_assign_301/302` records exist only
  as fixtures whose predictions self-label `prediction_source: "fixture:optimistic"` / `"fixture:missing"`.
- obs_301–303 timestamps match fixture `run_vllm_001-003` **to the second**; obs_401 matches
  `run_hold_001` field-for-field. "builder-2" matches no fleet node that has ever existed.
- **claudefarm1 is a GPU-less Hyper-V VM** — it could never have run a `requires_gpu` vLLM probe. The
  real crash loop ran on **OMEN WSL, 2026-07-02T09:34–10:36Z** (proven below).
- **omen-worker-2 never existed**: no fleet.json entry (current or pre-reboot backup), no manifest, no
  conductor.log line, no run tree, and **zero `qwen3-coder:14b` loads across all five OMEN Ollama server
  logs**. The "medium confidence from 2 workflows" build|ollama capability was half fiction.

Consequence: the corpus-overwrite "loss" destroyed beliefs that were largely synthetic. The machine has
been right at every step — `capability_count: 0` was the honest reading. **Recovery must materialize the
REAL underlying events, not restore the fixture-shaped records.** After that, the store will be
100%-real-evidence-backed for the first time.

## T1 — omen-worker-1 debut: REAL, RECOVERABLE (fresh lineage, 1 workflow not 2)

| Candidate | What | Verdict |
|---|---|---|
| `cand-t1-bl-dee707` | Conductor run `bl-20260702-dee707`: result.json + nodes.json + intact 3-link checkpoint chain (10:38:05→10:45:55Z, trace `ae67e719…`). omen-worker-1 (openai runner → qwen3-coder:30b via omen.mshome.net:11434) **won the 5-way debiased tiebreak, promoted=True** | REAL |
| `cand-t1-conductor-log` | conductor.log lines agreeing with checkpoints to <1ms; "DONE winner=omen-worker-1 promoted=True" | REAL |
| `cand-t1-farmer-branch` | The winning commit `86c020d5` authored by `omen-worker-1 <omen-worker-1@commandcenter>` 76s before promote; diff matches assay retro verbatim | REAL |
| `cand-t1-fleetjson` + `cand-t1-omen-manifest` | 54.6 tok/s measured note (D19) + runner wiring | REAL |
| `cand-t1-ollama-lab` (`C:\work\tuning\ollama-backend-lab\`) | Raw benchmark corpus: 57.40 tok/s CUDA decode, RAM/VRAM deltas, full protocol — the physical basis | REAL |
| `cand-t1-ollama-serverlog` | qwen3-coder:30b loads on OMEN at 10:38:20–10:39:30Z — inside the run window | REAL |
| `cand-t1-hwbaseline-run` | Already in repo/git; honest ingest of the lab data — needs no recovery | REAL (derivative) |

**Recommendation:** materialize ONE fresh observation lineage (a `runs/omen-debut-bl-dee707/` run dir in
the pour-b2 pattern: events + capacity observation + conductor/ provenance) from the staged evidence.
The build|ollama capability then re-derives from **one real workflow** (low confidence, honestly) — a
second real omen-worker-1 dispatch upgrades it legitimately. **obs_202/omen-worker-2: declare
nonexistent. Do not re-earn a fiction.**

## T2 — vLLM MoE crash: PHENOMENON REAL, RECORDS SYNTHETIC — split verdict

| Candidate | What | Verdict |
|---|---|---|
| `cand-t2-wsl-vllm` | OMEN WSL engine telemetry: **56 ENGINE_CONTEXT entries, 2026-07-02T09:34→~10:36Z, ~66s apart = crash/restart loop**; vllm 0.24.0, Qwen3MoeForCausalLM, auto_awq, RTX 5070 12GB; the 19GB QuantTrio AWQ model in HF hub; `.triton` mtime = the blocked rung | REAL |
| `cand-t2-d19-d20-decisions` | Conductor `decisions.jsonl` D20: "vLLM cannot serve quantized MoE beyond VRAM on consumer cards — Marlin MoE GEMM requires quantization scales on-GPU, UVA offloader leaves them host-resident; crash loop ×12" | REAL |
| `cand-t23-transcript-75aa0abd` | Session transcript preserving pre-loss findings verbatim | SYNTHETIC-MIMIC (faithful capture of fixture-derived content) |

**Recommendation:** do NOT restore obs_301–303/dec_assign_301-302 (fixture narratives, wrong node, wrong
date). Instead materialize the REAL crash evidence — a `runs/vllm-moe-crash-omen-wsl-2026-07-02/` ingest
(node attribution: OMEN WSL / RTX 5070 12GB; failure_class `moe_offload_crash`; evidence: the 56-restart
telemetry + D20's diagnosis; carry D20's open-rung caveat re moe_wna16/triton untested). That yields a
real `known_bad` finding → `project_policy` re-emits the block **honestly**. The `+9984MB
adjust_prediction` rule: **never existed — do not restore**; re-earn via fresh flagged probes if
prediction-bias learning is wanted.

## T3 — reference-build/builder-2: NEVER EXISTED — declare unrecoverable

No conductor-native run state, no dispatch record, no worker remnant, no log line, anywhere. The only
"evidence" is fixture `run_hold_001` and its transcript echoes. **Nothing to recover.** If a
reference-build known_good is wanted, re-earn with a fresh flagged probe on a real node.

## Negative space & access notes (auditable)

- Searched: conductor home + repo (17 target strings), all 131 conductor run trees, stalled-runs, the
  161MB MAF capture, decisions.jsonl, conductor+api logs, fleet.json(+bak), farmer-repo branches, inbox;
  cc-builder-1/2/3/4 via hop; OMEN local (tuning lab, Ollama logs ×5, transcripts, git history); OMEN WSL
  (vllm config/cache/HF hub, booted read-only).
- **claudefarm1 UNREACHABLE** (doesn't resolve; TCP sweep of 172.30.0.0/20 found only builders 3/4 +
  conductor). It's the one plausible holder of anything missed — worth a follow-up pass when it's up,
  though the omen-worker-2 negative is already strong without it.
- AM4 Jaeger/OTel storage not searched this pass (time-box) — could hold debut-era traces.
- Disqualified mimic locations (found, excluded): the conductor's `~/work/commandcenter-ontology` checkout
  + its mirror, pour workspaces on builder VMs, `.codex-temp` inspect copies, capture.ndjson pour payloads.

## Decisions requested (Phase 2 does nothing until these are answered)

- **D-A3-1:** Approve T1 materialization (`runs/omen-debut-bl-dee707/` from staged conductor evidence)? →
  capability re-derives at 1 real workflow.
- **D-A3-2:** Approve T2 materialization (`runs/vllm-moe-crash-omen-wsl-2026-07-02/` from WSL telemetry +
  D20, node = OMEN WSL)? → known_bad + block rule return, honestly attributed.
- **D-A3-3:** Confirm: adjust_prediction (+9984MB) and obs_202/omen-worker-2 and obs_401/builder-2 are
  declared never-existed (no restore, no re-earn of fictions)?
- **D-A3-4:** Doc reconciliation follow-up: the roadmap S5b/S5 claims (and their B1 annotations saying
  "earned 2026-07-02, lost in overwrite") overstate history — the capability was never earned from 2 real
  workflows. Authorize a correction pass?
- **D-A3-5 (proposed new guardrail, "A4 — fixture taint"):** projectors refuse event sources located under
  `fixtures/` when `--out` targets the repo `knowledge/` dir. One `if` per projector + tests. This is the
  antibody for the original sin that created this whole incident chain.

*Phase 1 complete. Copying, materialization, and re-projection await Derek's approval, per stream protocol.*
