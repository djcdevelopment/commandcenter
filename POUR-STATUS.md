# POUR-STATUS

## Streams

- `done`: `B2` on `stream/B2` at `90acc47`, merged to `master` and verified locally.
- `done`: `A1-remainder` on `stream/A1` at `abb75f2`, merged to `master` and verified locally.
- `done`: `B1` curated to `master` at `888a02d` (six constitutional HTML amendments).
- `done`: `C1` curated to `master` at `3d9cbad` (raw `model_*` telemetry fields; `model_residency` derived).
- `done`: `A2` curated to `master` at `cdad039` (corpus regression guard; opens G0).
- `in-flight`: none.
- `paused`: none.
- `blocked`: none.
- `not started`: none. Wave 1 complete.

## Pilot Landing: B2

- Conductor run: `pour-b2`
- Winner: `cc-builder-1`
- Winning assay: `pytest: 130/130 passed; imports: 0/0 ok; score=70; isolation=docker`
- Cycle time from conductor checkpoints: `2026-07-03T04:02:40.177585+00:00` -> `2026-07-03T04:09:32.603190+00:00` (`412.43s`)
- Evidence captured locally under `runs/pour-b2/`
- Local verification on landed branch:
  - `python -m unittest discover -s tests/workflow` -> `Ran 130 tests ... OK`
  - `python tools/workflow/check_doc_claims.py` -> `roadmap-first-capability WAIVED`, `candidates-present PASS`

## Landing: A1 remainder

- Conductor run: `pour-a1-r7`
- Winner: `cc-builder-2`
- Winning assay: `pytest: 130/130 passed; imports: 0/0 ok; score=70; isolation=docker`
- Cycle time from conductor checkpoints: `2026-07-03T05:51:33.372638+00:00` -> `2026-07-03T05:55:23.332966+00:00` (`229.96s`)
- Evidence captured locally under `runs/pour-a1-r7/`
- Local landing scope:
  - `tools/ops/push_backup.py`
  - `docs/ops-backup.md`
  - `BUILD-NOTES-A1.md`
- Local verification on landed branch:
  - `python tools/ops/push_backup.py --dry-run` -> sane stage/commit/push plan
  - `python -m unittest discover -s tests/workflow` -> `Ran 130 tests ... OK`

## Landing: B1 (curated)

- Curated locally from the approved STREAM B1 prompt; the fleet `pour-b1` winner rewrote the target HTML destructively instead of applying the six scoped edits.
- Landed on `master` at `888a02d`. `stream/B1` reference is held by a codex worktree and left untouched.
- Scope: `CAPABILITY-ROADMAP.html` (5 edits), `TWO-ECONOMIES-WIND-TUNNEL.html` (1 edit), `BUILD-NOTES-B1.md`.
- Verification: all six DoD grep targets match (one each; `re-derivation pending` x2); `HTMLParser` clean on both files; suite `Ran 130 tests ... OK` (untouched).

## Landing: C1 (curated)

- Curated locally from the approved STREAM C1 prompt; the fleet `pour-c1` winner touched the right files but under the wave's shared destructive `knowledge/*` rewrite.
- Landed on `master` at `3d9cbad`.
- Scope: `contracts/capacity-observation.v1.schema.json` (4 additive raw `model_*` fields; `model_residency` marked DERIVED), `docs/physical-telemetry-instrumentation-findings.md` (dated note), `tests/workflow/test_capacity_observation_schema.py` (new), `BUILD-NOTES-C1.md`.
- Verification: schema JSON parses; suite `Ran 141 tests ... OK` (130 baseline + 11 new).

## Landing: A2 (curated)

- Curated locally from the approved STREAM A2 prompt; the fleet `pour-a2` winner was the null-action false positive (only meaningful diff `retro.md`).
- Landed on `master` at `cdad039`.
- Scope: `tools/workflow/corpus_guard.py` (new), `guard_write` wired into all six projectors, `knowledge/corpus_regression_override.json` (authored, inactive), `tests/workflow/test_corpus_guard.py` (new), `DECISION-NEEDED-A2.md`, `BUILD-NOTES-A2.md`.
- `knowledge/*.json` deliberately NOT re-projected — the guard is code-only, and re-projecting the corpus is the incident operation itself.
- Verification: suite `Ran 148 tests ... OK` (141 + 7 new). Opens G0.

## Gates

- `G0`: `OPEN`
  - Check: `origin` configured = yes; `tools/workflow/corpus_guard.py` exists = yes (landed in A2 `cdad039`)
- `G1`: `CLOSED`
  - Check: `DECISIONS-D1.md` exists = no
- `G2`: `CLOSED`
  - Check: some `runs/*/artifacts/*.json` carries non-null `gpu_temp_c_peak` = no
- `G-budget`: `CLOSED`
  - Check: `operating-budget.v1` with `authored_by != "fixture"` exists = no
- `G3`: `CLOSED`
  - Check: `knowledge/capabilities.json` `capability_count >= 1` = no (`0`)

## Learning Metrics

- Landing `B2`
  - Findings: `18 -> 15` (`-3`)
  - Associations: `0 -> 0` (`+0`)
  - Capabilities: `0 -> 0` (`+0`)
  - Coverage gaps: `18 -> 26` (`+8`)
  - Experiment candidates: `15 -> 42` (`+27`)
  - Workflow test count vs baseline: `110 -> 130` (`+20`)
- Landing `A1-remainder`
  - Findings: `15 -> 15` (`+0`)
  - Associations: `0 -> 0` (`+0`)
  - Capabilities: `0 -> 0` (`+0`)
  - Coverage gaps: `26 -> 24` (`-2`)
  - Experiment candidates: `42 -> 40` (`-2`)
  - Workflow test count vs baseline: `110 -> 130` (`+20`)

## Doc Claims

| claim_id | expected | actual | result |
|---|---:|---:|---|
| `roadmap-first-capability` | `>=1` | `0` | `WAIVED` |
| `candidates-present` | `>=1` | `40` | `PASS` |

## Notes

- The pilot required two minimal conductor adapters, committed there immediately:
  - `0c30d3f` `feat(conductor): allow per-request target repos`
  - `b122562` `feat(conductor): allow per-request builder subsets`
- The daemon path is now validated with a real two-builder inbox run. The failed single-builder A1 probes (`pour-a1-r3` through `pour-a1-r6`) were invalid plans; the conductor requires at least two fan-out targets.
- The local re-projection over `runs/` is a direct function of the current evidence corpus: `runs/omen-5070-hwbaseline-2026-07-02`, `runs/pour-b2`, and `runs/pour-a1-r7` are present under `runs/`.
- `runs/pour-b2/conductor/` preserves the raw conductor `result.json`, `nodes.json`, and checkpoint evidence used to materialize the pilot observations.
- `runs/pour-a1-r7/conductor/` preserves the raw conductor `result.json`, `nodes.json`, and checkpoint evidence for the A1 remainder landing.

## Checkpoint

- Wave 1 complete. All five streams (`B2`, `A1-remainder`, `B1`, `C1`, `A2`) are landed on `master`. `B1`/`C1`/`A2` were curator passes from the approved prompts after the assay mis-selected the fleet winners — the instrument finding and a proposed stream-scoped acceptance assay are in `ASSAY-ACCEPTANCE-GAP-2026-07-03.html`. Test baseline `110 → 148`. `G0` open.

## External Audit — 2026-07-03 (independent pass over the pilot landing, e41fd6b)

**Verdict on the pilot: CLEAN.** Every mechanically checkable claim in this file verified true (130/130 + the 110 baseline re-proven at `4cf048b`; cycle time reproduces to the microsecond from the committed checkpoint timestamps; all belief-count deltas exact; gates correct). Determinism confirmed the hard way: re-running the six-projector chain over `runs/` left `knowledge/` byte-identical. Process conformant to `CODEX-POUR-ORCHESTRATOR.md` on every locally checkable point, including pilot-before-wave-1. Conductor-side claims (adapter commits, VM execution, assay runs) are internally consistent but rest on captured-copy evidence — not independently attestable from this box.

**⚠ FINDING THE POUR DOES NOT YET KNOW: the vLLM crash gate is gone and projection cannot restore it.**
- Yesterday's overwrite removed `block:claudefarm1|qwen3-30b-a3b-awq|vllm` (and the `+9984MB` `adjust_prediction` rule) from `policy.json` (`rules: []` at `4cf048b`; removal recorded in `policy_audit.ndjson` at watermark `2026-07-02T06:55:00Z`). The hand-restore fixed `findings.json` but never re-ran `project_policy`.
- The pilot re-projection then deleted the `known_bad:claudefarm1|qwen3-30b-a3b-awq|vllm` finding itself (3/3 `moe_offload_crash`) **plus the 14 experiment candidates that were the re-earn demand for the dropped beliefs** (incl. `known_bad_retest` for this exact combo). The system forgot the knowledge AND the appetite to reacquire it — the new gaps/candidates all concern surviving/new evidence; zero reference the dropped subjects. (Design note: S8 coverage cannot state a blind spot about a combo with zero evidence in the corpus; the A2 guard — now landed — is the mitigation for future shrinks, but it does not retro-flag this one.)
- **Operational hold requested:** do not schedule `claudefarm1 + qwen3-30b-a3b-awq + vllm` (or MoE-offload vLLM combos generally) without an experiment flag until the gate is re-derived. Nothing in `policy.json` currently blocks it.
- Also dropped, same mechanism: `known_good:omen-worker-1|qwen3-coder:30b|ollama`, `known_good:omen-worker-2|qwen3-coder:14b|ollama` (the two halves of the lost `build|ollama` capability evidence), `known_good:builder-2|claude-opus-4.8|local`, `prediction_bias:qwen3-30b-a3b-awq:expected_peak_ram_mb`, and both `recommendation` findings. All survive only in git history at `4cf048b`.
- **Recovery paths, constitutional order:** (1) run the WIDENED stream A3 evidence hunt (its grep targets now include the vllm-probe and hold-probe lineages — see `FLEET-WORK-PLAN.html`, updated 2026-07-03); `pour-b2` proved conductor checkpoints/`result.json` are materializable into observations, so odds are fair. The `prediction_bias` finding additionally needs the dispatch decision records (`dec_assign_301/302`) — the hardest ask. (2) Whatever A3 cannot find gets re-earned: three fresh flagged vLLM probes re-derive the known_bad honestly. (3) Never hand-write any of it back into `knowledge/`.

**Minor items from the deliverable review (non-blocking):** `check_doc_claims.py` aborts with an uncaught traceback on a type-mismatched claim or malformed waiver date instead of a FAIL row (exit code still nonzero — gate cannot pass silently); per-builder `runtime_s` left null in pour observations although `elapsed_s` sits in `result.json` (available fidelity uncaptured — worth fixing in the evidence adapter before wave 2); conductor checkpoint message payloads are pickled base64 blobs, opaque to future replay; the `e1f8644` commit title says "HTML doc-claim checker" though nothing parses HTML (naming only). The fleet VM's one real spec deviation (local-time instead of UTC waiver expiry) was caught and fixed by the orchestrator in `90acc47` — the cross-model review loop worked.
