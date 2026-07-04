# PROVENANCE — runs/omen-worker-1-reearn

The omen-worker-1 capability re-earn build lap (dispatched 2026-07-04 once claudefarm1 was
recovered), and the finding that G3 is not close-able by build laps alone.

## The lap
CCMETA `{"builders": ["omen-worker-1", "cc-builder-2"], "promote": false}`, a small deterministic
task (slugify util + tests). **Both builders clean:** `agent_rc 0`, not timed out, assay `167/167`.
`omen-worker-1` = Ollama qwen3-coder:30b on OMEN's RTX 5070 (claudefarm1 shell). This is its
**second** real `build|ollama` success (after `obs_omen_debut_001`). Both signals are genuine
(no timeout, no infra failure), so — unlike the withheld wave-2 observations (ADR-0002) — they are
materialized and projected.

## Evidence
- `conductor/result.json` — raw conductor payload.
- `artifacts/obs_omen_worker_1_reearn_omen_worker_1.json` — omen-worker-1|qwen3-coder:30b|ollama, success.
- `artifacts/obs_omen_worker_1_reearn_cc_builder_2.json` — cc-builder-2|vllama-planner|openai, success.
- `events.jsonl` — links both.

## Belief-layer effect (re-projected 2026-07-04)
- Formed `success_invariant:model_portability:model_id=qwen3-coder:30b` (qwen3-coder succeeds across
  contexts). omen-worker-1's known_good reinforced. Corpus grew (no guard block).
- **G3 did NOT close.** `capabilities.json capability_count` stayed 0.

## Finding: why G3 didn't close (and why more laps won't help)
A capability (`capability.v1`) is only synthesized from a `success_invariant` association whose
invariant contains a **`task_kind`** (`synthesize_capabilities`, project_associations.py:219-220) —
a class-of-work qualification. The corpus now has ample repo-build successes:

| combo | workflows |
|---|---|
| cc-builder-2\|vllama-planner\|openai | 4 |
| cc-builder-1\|sonnet\|claude | 2 |
| omen-worker-1\|qwen3-coder:30b\|ollama | 2 |
| am4-worker-1\|vllama-planner\|openai | 1 |

That's 4 builders / 3 backends / multiple workflows — well past `ASSOCIATION_MIN_WORKFLOWS = 2` and
the "≥2 varied values in a dimension" check on its face. Yet the engine forms a *model_portability*
association, not a `task_kind=repo-build` success_invariant. **So the G3 blocker is not "more build
evidence" — it is the association engine's task_kind-invariant bucketing not triggering on this
evidence pattern.** That needs a read of `synthesize_associations` (project_associations.py ~91-150):
either the repo-build observations aren't bucketing into a shared task_kind invariant, or the
varied-value discipline is measured on a dimension these observations don't vary within a bucket.

**Recommendation:** treat G3 as a belief-layer investigation (why no task_kind=repo-build
capability forms from 4-builder/3-backend evidence), NOT a "fire another lap" task. The earlier
POUR-STATUS assumption ("second build workflow re-earns the capability") is corrected here.
