# DECISION-NEEDED-Ga — Candidate Worth Points

**Stream:** Ga — Learning-rate / worth-realized projector  
**Status:** Decision requested. Builder continues with all tasks that do not require these values.  
**Action needed:** Derek assigns `worth_points` (integer) and a `reason` for each candidate below,
then adds entries to `knowledge/candidate_worth.json`. Candidates without an entry are excluded
from the `worth_realized` numerator and reported in `unpriced_candidates`.

## Background

The `worth_realized` projector (`tools/workflow/project_learning.py`) sums `worth_points` over
experiments whose result shows `belief_changed == true` OR `gate_before != gate_after`, divided
by dispatch hours. The `worth` field on each candidate is prose — the projector's authored channel
is `knowledge/candidate_worth.json`, a Clause 2 overlay (will, with reason).

The plan originally referred to "15 current candidate_ids" — that number was `source_findings` in
the header of `experiment_candidates.json`. The actual candidate list is longer and has grown as
the corpus grew. All current candidates are listed below.

## Format for knowledge/candidate_worth.json entries

```json
{
  "candidate_id": "<id from list below>",
  "worth_points": <integer — your judgment of how many points this experiment is worth>,
  "reason": "<one sentence: why this number>",
  "author": "derek"
}
```

## All current candidates

| # | candidate_id | projector prose worth |
|---|---|---|
| 1 | `backend_comparison:mixtral:8x22b-instruct-v0.1-q2_K:ollama-cuda+ollama-vulkan-igpu+ollama-vulkan-mixed` | the model runs on multiple backends but beliefs formed on different workloads; controlled comparison replaces inference with measurement |
| 2 | `backend_comparison:qwen2.5:14b:ollama-cuda+ollama-cuda-contended+ollama-vulkan+ollama-vulkan-igpu+ollama-vulkan-mixed` | the model runs on multiple backends but beliefs formed on different workloads; controlled comparison replaces inference with measurement |
| 3 | `backend_comparison:qwen3-coder:30b:ollama+ollama-cuda+ollama-cuda-contended` | the model runs on multiple backends but beliefs formed on different workloads; controlled comparison replaces inference with measurement |
| 4 | `coverage_probe:single_workflow_evidence:failure_signature:failure_class=assay_test_failure\|backend=openai` | 2 consistent samples agree but all come from one workflow; association engine will not generalize from one workflow |
| 5 | `coverage_probe:single_workflow_evidence:failure_signature:failure_class=moe_offload_crash\|backend=vllm` | 3 consistent samples agree but all come from one workflow |
| 6 | `coverage_probe:single_workflow_evidence:model_portability:model_id=mixtral:8x22b-instruct-v0.1-q2_K` | 3 consistent samples agree but all come from one workflow |
| 7 | `coverage_probe:single_workflow_evidence:model_portability:model_id=qwen2.5:14b` | 5 consistent samples agree but all come from one workflow |
| 8 | `coverage_probe:single_workflow_evidence:model_portability:model_id=qwen3-30b-a3b-awq` | 3 consistent samples agree but all come from one workflow |
| 9 | `coverage_probe:single_workflow_evidence:model_portability:model_id=sonnet` | 1 consistent sample agrees but all come from one workflow |
| 10 | `coverage_probe:single_workflow_evidence:task_backend:task_kind=capacity-probe\|backend=ollama-cuda` | 3 consistent samples agree but all come from one workflow |
| 11 | `coverage_probe:single_workflow_evidence:task_backend:task_kind=capacity-probe\|backend=ollama-cuda-contended` | 2 consistent samples agree but all come from one workflow |
| 12 | `coverage_probe:single_workflow_evidence:task_backend:task_kind=capacity-probe\|backend=ollama-vulkan` | 1 consistent sample agrees but all come from one workflow |
| 13 | `coverage_probe:single_workflow_evidence:task_backend:task_kind=capacity-probe\|backend=ollama-vulkan-igpu` | 2 consistent samples agree but all come from one workflow |
| 14 | `coverage_probe:single_workflow_evidence:task_backend:task_kind=capacity-probe\|backend=ollama-vulkan-mixed` | 2 consistent samples agree but all come from one workflow |
| 15 | `coverage_probe:single_workflow_evidence:task_backend:task_kind=engine-start\|backend=vllm` | 3 consistent samples agree but all come from one workflow |
| 16 | `coverage_probe:single_workflow_evidence:task_backend:task_kind=repo-build\|backend=claude` | 1 consistent sample agrees but all come from one workflow |
| 17 | `coverage_probe:single_workflow_evidence:task_backend:task_kind=repo-build\|backend=ollama` | 1 consistent sample agrees but all come from one workflow |
| 18 | `coverage_probe:unmeasured_metrics:am4-worker-1\|vllama-planner\|openai` | predictions of these metrics can never be calibrated while they go unmeasured |
| 19 | `coverage_probe:unmeasured_metrics:cc-builder-1\|sonnet\|claude` | predictions of these metrics can never be calibrated while they go unmeasured |
| 20 | `coverage_probe:unmeasured_metrics:cc-builder-2\|vllama-planner\|openai` | predictions of these metrics can never be calibrated while they go unmeasured |
| 21 | `coverage_probe:unmeasured_metrics:omen-5070\|mixtral:8x22b-instruct-v0.1-q2_K\|ollama-cuda` | predictions of these metrics can never be calibrated while they go unmeasured |
| 22 | `coverage_probe:unmeasured_metrics:omen-5070\|mixtral:8x22b-instruct-v0.1-q2_K\|ollama-vulkan-igpu` | predictions of these metrics can never be calibrated while they go unmeasured |
| 23 | `coverage_probe:unmeasured_metrics:omen-5070\|mixtral:8x22b-instruct-v0.1-q2_K\|ollama-vulkan-mixed` | predictions of these metrics can never be calibrated while they go unmeasured |
| 24 | `coverage_probe:unmeasured_metrics:omen-5070\|qwen2.5:14b\|ollama-cuda` | predictions of these metrics can never be calibrated while they go unmeasured |
| 25 | `coverage_probe:unmeasured_metrics:omen-5070\|qwen2.5:14b\|ollama-cuda-contended` | predictions of these metrics can never be calibrated while they go unmeasured |
| 26 | `coverage_probe:unmeasured_metrics:omen-5070\|qwen2.5:14b\|ollama-vulkan` | predictions of these metrics can never be calibrated while they go unmeasured |
| 27 | `coverage_probe:unmeasured_metrics:omen-5070\|qwen2.5:14b\|ollama-vulkan-igpu` | predictions of these metrics can never be calibrated while they go unmeasured |
| 28 | `coverage_probe:unmeasured_metrics:omen-5070\|qwen2.5:14b\|ollama-vulkan-mixed` | predictions of these metrics can never be calibrated while they go unmeasured |
| 29 | `coverage_probe:unmeasured_metrics:omen-5070\|qwen3-coder:30b\|ollama-cuda` | predictions of these metrics can never be calibrated while they go unmeasured |
| 30 | `coverage_probe:unmeasured_metrics:omen-5070\|qwen3-coder:30b\|ollama-cuda-contended` | predictions of these metrics can never be calibrated while they go unmeasured |
| 31 | `coverage_probe:unmeasured_metrics:omen-worker-1\|qwen3-coder:30b\|ollama` | predictions of these metrics can never be calibrated while they go unmeasured |
| 32 | `coverage_probe:unmeasured_metrics:omen-wsl\|qwen3-30b-a3b-awq\|vllm` | predictions of these metrics can never be calibrated while they go unmeasured |
| 33 | `known_bad_retest:omen-wsl\|qwen3-30b-a3b-awq\|vllm` | the block is a belief not a fact; environment drift can invalidate it |
| 34 | `prefer_validation:cc-builder-1\|sonnet\|claude` | 2 more clean runs reach high confidence and earn a prefer rule (1/1 success today) |
| 35 | `prefer_validation:omen-5070\|mixtral:8x22b-instruct-v0.1-q2_K\|ollama-cuda` | 2 more clean runs reach high confidence and earn a prefer rule |
| 36 | `prefer_validation:omen-5070\|mixtral:8x22b-instruct-v0.1-q2_K\|ollama-vulkan-igpu` | 2 more clean runs reach high confidence and earn a prefer rule |
| 37 | `prefer_validation:omen-5070\|mixtral:8x22b-instruct-v0.1-q2_K\|ollama-vulkan-mixed` | 2 more clean runs reach high confidence and earn a prefer rule |
| 38 | `prefer_validation:omen-5070\|qwen2.5:14b\|ollama-cuda` | 2 more clean runs reach high confidence and earn a prefer rule |
| 39 | `prefer_validation:omen-5070\|qwen2.5:14b\|ollama-cuda-contended` | 2 more clean runs reach high confidence and earn a prefer rule |
| 40 | `prefer_validation:omen-5070\|qwen2.5:14b\|ollama-vulkan` | 2 more clean runs reach high confidence and earn a prefer rule |
| 41 | `prefer_validation:omen-5070\|qwen2.5:14b\|ollama-vulkan-igpu` | 2 more clean runs reach high confidence and earn a prefer rule |
| 42 | `prefer_validation:omen-5070\|qwen2.5:14b\|ollama-vulkan-mixed` | 2 more clean runs reach high confidence and earn a prefer rule |
| 43 | `prefer_validation:omen-5070\|qwen3-coder:30b\|ollama-cuda` | 2 more clean runs reach high confidence and earn a prefer rule |
| 44 | `prefer_validation:omen-5070\|qwen3-coder:30b\|ollama-cuda-contended` | 2 more clean runs reach high confidence and earn a prefer rule |
| 45 | `prefer_validation:omen-worker-1\|qwen3-coder:30b\|ollama` | 2 more clean runs reach high confidence and earn a prefer rule |
| 46 | `uncertain_resolution:am4-worker-1\|vllama-planner\|openai` | the combo is stuck in exploratory_only until evidence resolves it one way or the other |
| 47 | `uncertain_resolution:cc-builder-2\|vllama-planner\|openai` | the combo is stuck in exploratory_only until evidence resolves it one way or the other |

## Note on candidate count

The plan referenced "15 current candidate_ids" — that number (`source_findings: 15`) is the count
of findings that seeded the candidate list, not the candidates themselves. The actual corpus has
grown to 47 candidates across 5 experiment types. All 47 are listed above.
