from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from tools.workflow.append_event import append_event
from tools.workflow.materialize_run import materialize_run
from tools.workflow.project_policy import EXPERIMENT_FLAG, evaluate

# Arbitrary-at-birth baselines, recorded per the arbitrary-but-traced decision rule (D18).
PREFER_SCORE_BONUS = 0.1        # a prefer rule nudges ranking; it never gates
CAPABILITY_MATCH_BONUS = 0.15   # per matched capability requirement; outranks a mere preference
                                # because qualification is derived from cross-workflow evidence

DEFAULT_TASK_KIND = "reference-build"
WORKLOAD_CONTEXT_TOKENS = 8192

# Reference candidate pools. happy/hold keep the original single local builder; the policy-*
# scenarios dispatch against the known-bad vLLM MoE combo so the gates have something to gate;
# capability-pool pits a higher-base-score unqualified candidate against a qualified one.
CANDIDATE_POOLS = {
    "default": [
        {"builder_id": "builder-1", "model_id": "claude-opus-4.8", "backend": "local",
         "base_score": 1.0, "predictions": {}, "frontier": True},
    ],
    "known-bad-only": [
        {"builder_id": "claudefarm1", "model_id": "qwen3-30b-a3b-awq", "backend": "vllm",
         "base_score": 0.74, "frontier": False,
         "predictions": {"expected_generation_tokens_per_second": 42.0, "tokens_per_s": 42.0,
                         "expected_peak_ram_mb": 18432.0}},
    ],
    "capability-pool": [
        {"builder_id": "builder-1", "model_id": "claude-opus-4.8", "backend": "local",
         "base_score": 0.9, "predictions": {}, "frontier": True},
        {"builder_id": "omen-worker-1", "model_id": "qwen3-coder:30b", "backend": "ollama",
         "base_score": 0.8, "frontier": False,
         "predictions": {"expected_generation_tokens_per_second": 54.0, "tokens_per_s": 54.0}},
    ],
}

FRONTIER_DISABLED_REASON = "frontier disabled by operator"

SCENARIOS = {
    "happy": {"pool": "default", "experiment_flag": False, "requires_policy": False},
    "hold": {"pool": "default", "experiment_flag": False, "requires_policy": False},
    "policy-blocked": {"pool": "known-bad-only", "experiment_flag": False, "requires_policy": True},
    "policy-experiment": {"pool": "known-bad-only", "experiment_flag": True, "requires_policy": True},
    "policy-adjusted": {"pool": "default", "experiment_flag": False, "requires_policy": True},
    "policy-suspended": {"pool": "known-bad-only", "experiment_flag": False, "requires_policy": True},
    "capability-directed": {"pool": "capability-pool", "experiment_flag": False, "requires_policy": False,
                            "requires_capabilities": True, "task_kind": "build"},
}


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_policy_rules(policy_path: Path | None) -> list[dict]:
    if policy_path is None:
        return []
    return json.loads(policy_path.read_text(encoding="utf-8-sig"))["rules"]


def load_capabilities(capabilities_path: Path | None) -> list[dict] | None:
    if capabilities_path is None:
        return None
    return json.loads(capabilities_path.read_text(encoding="utf-8-sig"))["capabilities"]


def _capability_requirements(capabilities: list[dict], task_kind: str) -> list[dict]:
    """The requirements are DERIVED, not hand-authored: a capability whose invariant matches the
    workload's task_kind exists only because past traces of this class of work succeeded — trace
    history states what this work needs."""
    return [capability for capability in capabilities
            if capability["invariant"].get("task_kind") == task_kind]


def _capability_matches(candidate: dict, capability: dict, estimated_context_tokens: int | None) -> bool:
    combo = {"builder_id": candidate["builder_id"], "model_id": candidate["model_id"],
             "backend": candidate["backend"]}
    if combo not in capability["qualified_resources"]:
        return False
    if capability["qualification_status"] != "qualified":
        return False  # a stale qualification does not satisfy a requirement; it proposes an experiment
    max_context = capability["operating_envelope"].get("max_context_tokens")
    if estimated_context_tokens is not None and max_context is not None \
            and estimated_context_tokens > max_context:
        return False  # outside the measured envelope is untested ground, not qualified ground
    return True


def schedule(candidate_pool: list[dict], task_kind: str, rules: list[dict],
             experiment_flag: bool = False, capabilities: list[dict] | None = None,
             estimated_context_tokens: int | None = WORKLOAD_CONTEXT_TOKENS,
             disable_frontier: bool = False) -> dict:
    """Policy- and capability-aware assignment: every candidate goes through policy.evaluate();
    blocked combos never dispatch without the experiment flag. When capabilities are loaded the
    scheduler asks what capabilities this work requires, then which candidate satisfies them —
    qualified capabilities are scheduled, not machines — and the SchedulerDecision explains both.
    disable_frontier is an operator-level kill switch (a token budget ran dry mid-pour): frontier
    candidates are excluded before policy even runs, so a local-model candidate wins by default
    rather than the run stalling for lack of frontier quota."""
    required = _capability_requirements(capabilities, task_kind) if capabilities is not None else []
    considered = []
    verdicts = []
    capability_annotations = []
    for candidate in candidate_pool:
        if disable_frontier and candidate.get("frontier"):
            considered.append({
                "builder_id": candidate["builder_id"],
                "model_id": candidate["model_id"],
                "backend": candidate["backend"],
                "filters_passed": False,
                "score": None,
                "selected": False,
                "rejected_reason": FRONTIER_DISABLED_REASON,
            })
            verdicts.append(None)
            capability_annotations.append({
                "builder_id": candidate["builder_id"],
                "model_id": candidate["model_id"],
                "backend": candidate["backend"],
                "matched": [],
                "missing": [capability["capability_id"] for capability in required],
            })
            continue
        verdict = evaluate(rules, builder_id=candidate["builder_id"], model_id=candidate["model_id"],
                           backend=candidate["backend"], task_kind=task_kind,
                           experiment_flag=experiment_flag)
        gating_ids = [match["policy_id"] for match in verdict["matched_rules"]
                      if match["status"] == "active" and match["effect"] in ("block", "quarantine")]
        matched = [capability["capability_id"] for capability in required
                   if _capability_matches(candidate, capability, estimated_context_tokens)]
        missing = [capability["capability_id"] for capability in required
                   if capability["capability_id"] not in matched]
        score = round(candidate["base_score"]
                      + (PREFER_SCORE_BONUS if verdict["preferred"] else 0.0)
                      + CAPABILITY_MATCH_BONUS * len(matched), 2)
        considered.append({
            "builder_id": candidate["builder_id"],
            "model_id": candidate["model_id"],
            "backend": candidate["backend"],
            "filters_passed": verdict["allowed"],
            "score": score,
            "selected": False,
            "rejected_reason": None if verdict["allowed"] else "policy: " + ", ".join(gating_ids),
        })
        verdicts.append(verdict)
        capability_annotations.append({
            "builder_id": candidate["builder_id"],
            "model_id": candidate["model_id"],
            "backend": candidate["backend"],
            "matched": matched,
            "missing": missing,
        })

    blocked = [{"builder_id": entry["builder_id"], "model_id": entry["model_id"],
                "backend": entry["backend"],
                "policy_ids": [] if verdict is None else
                              [match["policy_id"] for match in verdict["matched_rules"]
                               if match["status"] == "active" and match["effect"] in ("block", "quarantine")]}
               for entry, verdict in zip(considered, verdicts) if not entry["filters_passed"]]

    allowed = [index for index, entry in enumerate(considered) if entry["filters_passed"]]
    if not allowed:
        if disable_frontier and all(entry["rejected_reason"] == FRONTIER_DISABLED_REASON for entry in considered):
            reason = f"frontier disabled by operator and no local-model candidate is registered " \
                     f"in this pool ({len(considered)} candidate(s))"
        else:
            reason = f"policy blocked all {len(considered)} candidate(s)"
        return {"selected": None, "candidates_considered": considered, "candidates_blocked": blocked,
                "decision_reason": reason,
                "policy_influence": None, "capability_influence": None, "candidate": None}

    winner = max(allowed, key=lambda index: considered[index]["score"])
    considered[winner]["selected"] = True
    verdict = verdicts[winner]
    winner_capabilities = capability_annotations[winner]

    reason_parts = []
    if rules:
        reason_parts.append(f"policy-aware selection: {len(allowed)}/{len(considered)} candidate(s) allowed")
        if verdict["requires_experiment_flag"]:
            reason_parts.append("experiment dispatch through an open gate")
    if capabilities is not None:
        reason_parts.append(f"capability match {len(winner_capabilities['matched'])}/{len(required)} "
                            "on the selected candidate: qualified capabilities are scheduled, not machines")
    if not reason_parts:
        reason_parts.append("only local reference builder registered")

    capability_influence = None
    if capabilities is not None:
        capability_influence = {
            "capabilities_evaluated": True,
            "requirements_source": "derived from trace history: capabilities whose invariant matches "
                                   "the workload task_kind",
            "capabilities_required": [capability["capability_id"] for capability in required],
            "capabilities_matched": winner_capabilities["matched"],
            "capabilities_missing": winner_capabilities["missing"],
            "candidates": capability_annotations,
        }

    return {
        "selected": {"builder_id": considered[winner]["builder_id"],
                     "model_id": considered[winner]["model_id"],
                     "backend": considered[winner]["backend"]},
        "candidate": candidate_pool[winner],
        "candidates_considered": considered,
        "candidates_blocked": blocked,
        "decision_reason": "; ".join(reason_parts),
        "policy_influence": {
            "policy_evaluated": bool(rules),
            "experiment_flag": experiment_flag,
            "requires_experiment_flag": verdict["requires_experiment_flag"],
            "matched_rules": verdict["matched_rules"],
            "adjustments_applied": [],  # filled in when predictions are written
            "prediction_adjustments": verdict["prediction_adjustments"],
            "candidates_blocked": blocked,
        },
        "capability_influence": capability_influence,
    }


EXPERIMENT_TYPE_BY_FINDING = {
    "known_bad": "known_bad_retest",
    "regression": "regression_probe",
    "uncertain": "uncertain_resolution",
}
_GATE_SEVERITY = {"block": 2, "quarantine": 1, "exploratory_only": 0}


def build_experiment_plan(run_id: str, workflow_id: str, selection: dict, rules: list[dict]) -> dict | None:
    """An experiment is a gated dispatch performed intentionally to gain evidence. When the flag
    opens a gate, the intent goes on the record BEFORE the outcome exists: which belief is being
    tested, what evidence is sought, and what risk that purchase costs."""
    influence = selection.get("policy_influence")
    if not influence or not influence["experiment_flag"]:
        return None
    gated = [match for match in influence["matched_rules"]
             if match["status"] == "active" and match["effect"] in _GATE_SEVERITY]
    if not gated:
        return None
    opened = max(gated, key=lambda match: _GATE_SEVERITY[match["effect"]])
    rule = next(r for r in rules if r["policy_id"] == opened["policy_id"])
    override = rule.get("override") or {}
    selected = selection["selected"]
    label = " + ".join(selected[field] for field in ("builder_id", "model_id", "backend"))
    return {
        "contract_version": "experiment-plan.v1",
        "experiment_id": "exp_assign_001",
        "experiment_type": EXPERIMENT_TYPE_BY_FINDING.get(rule["finding_type"], "known_bad_retest"),
        "workflow_id": workflow_id,
        "run_id": run_id,
        "decision_id": "dec_assign_001",
        "timestamp": "2026-07-02T15:00:25Z",
        "subject": {"builder_id": selected["builder_id"], "model_id": selected["model_id"],
                    "backend": selected["backend"], "task_kind": "reference-build", "metric": None},
        "target_finding_id": rule["derived_from_finding"],
        "derived_from_candidate": None,
        "gate_opened": {"policy_id": rule["policy_id"], "effect": rule["effect"],
                        "flag": override.get("flag") or EXPERIMENT_FLAG,
                        "semantics": override.get("semantics")},
        "reason": f"intentional dispatch through the {rule['effect']} gate on {label}: the belief rests on "
                  f"{rule['evidence_summary']} (last observed {rule['last_observed']}); untested is not impossible",
        "evidence_sought": f"a success contradicts '{rule['evidence_summary']}' and reclassifies the combo; "
                           "another failure raises confidence in the gate",
        "risk_accepted": f"one dispatch slot and a likely failure ({rule['evidence_summary']}); the gate stays "
                         "closed to normal work while the experiment runs",
    }


def apply_prediction_adjustments(predictions: dict, policy_influence: dict | None) -> dict:
    """Bias corrections land BEFORE the SchedulerDecision is written, and each one is recorded
    with its before/after so the correction is visible, not silent."""
    if not policy_influence:
        return predictions
    for adjustment in policy_influence.pop("prediction_adjustments", []):
        metric = adjustment["metric"]
        if predictions.get(metric) is None:
            continue
        before = predictions[metric]
        predictions[metric] = round(before + adjustment["additive_correction"], 2)
        policy_influence["adjustments_applied"].append({
            "policy_id": adjustment["policy_id"],
            "metric": metric,
            "additive_correction": adjustment["additive_correction"],
            "before": before,
            "after": predictions[metric],
        })
    return predictions


def _event(
    event_id: str,
    event_type: str,
    timestamp: str,
    workflow_id: str,
    run_id: str,
    actor: dict,
    status: str,
    payload: dict,
    **extra: object,
) -> dict:
    event = {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "actor": actor,
        "status": status,
        "payload": payload,
    }
    event.update(extra)
    return event


def _base_predictions(scenario: str) -> dict:
    return {
        "runtime_s": 190.0,
        "ttft_s": None,
        "tokens_per_s": 40.0,
        "ram_gb_peak": None,
        "vram_gb_peak": None,
        "assay_pass_probability": None,
        "promotion_probability": None,
        "expected_model_load_ms": None,
        "expected_ttft_ms": None,
        "expected_prompt_tokens_per_second": None,
        "expected_generation_tokens_per_second": 40.0,
        "expected_peak_ram_mb": None,
        "expected_peak_vram_mb": None,
        "expected_assay_outcome": "passed",
        "expected_promotion_status": "held" if scenario == "hold" else "approved",
        "confidence": 0.6,
        "prediction_source": "reference-heuristic",
    }


def build_scheduler_decision(run_id: str, workflow_id: str, scenario: str, selection: dict,
                             task_kind: str = DEFAULT_TASK_KIND) -> dict:
    predictions = {**_base_predictions(scenario), **(selection["candidate"].get("predictions") or {})}
    predictions = apply_prediction_adjustments(predictions, selection["policy_influence"])
    return {
        "contract_version": "scheduler-decision.v1",
        "decision_id": "dec_assign_001",
        "workflow_id": workflow_id,
        "run_id": run_id,
        "timestamp": "2026-07-02T15:00:25Z",
        "workload_shape": {
            "task_kind": task_kind,
            "estimated_context_tokens": WORKLOAD_CONTEXT_TOKENS,
            "requires_gpu": False,
            "notes": "reference workflow, local execution only",
        },
        "candidates_considered": selection["candidates_considered"],
        "selected": selection["selected"],
        "decision_reason": selection["decision_reason"],
        "predictions": predictions,
        "evidence_refs": [],
        "policy_influence": selection["policy_influence"],
        "capability_influence": selection.get("capability_influence"),
    }


def build_capacity_observation(run_id: str, workflow_id: str, scenario: str, selected: dict,
                               task_kind: str = DEFAULT_TASK_KIND) -> dict:
    return {
        "contract_version": "capacity-observation.v1",
        "observation_id": "obs_001",
        "decision_id": "dec_assign_001",
        "workflow_id": workflow_id,
        "run_id": run_id,
        "timestamp": "2026-07-02T15:03:20Z",
        "builder_id": selected["builder_id"],
        "model_id": selected["model_id"],
        "backend": selected["backend"],
        "workload_shape": {
            "task_kind": task_kind,
            "estimated_context_tokens": WORKLOAD_CONTEXT_TOKENS,
            "requires_gpu": False,
            "notes": None,
        },
        "observed": {
            "runtime_s": 190.0,
            "ttft_s": None,
            "tokens_per_s": 40.0,
            "ram_gb_peak": None,
            "vram_gb_peak": None,
            "context_tokens": 8192,
        },
        "outcome": "success",
        "failure_class": None,
        "promotion_status": "held" if scenario == "hold" else "approved",
    }


def build_scenario_events(run_id: str, workflow_id: str, scenario: str, selection: dict | None = None,
                          experiment_plan: dict | None = None) -> list[dict]:
    selected = (selection or {}).get("selected") or {"builder_id": "builder-1", "model_id": "claude-opus-4.8"}
    decision_reason = (selection or {}).get("decision_reason") or "only local reference builder registered"
    builder_actor = {"type": "builder", "id": selected["builder_id"], "model_id": selected["model_id"]}
    assignment_refs = [
        {
            "artifact_id": f"art_decision_{run_id}",
            "artifact_type": "scheduler_decision",
            "path": f"runs/{run_id}/artifacts/decisions/dec_assign_001.json",
        }
    ]
    if experiment_plan is not None:
        assignment_refs.append(
            {
                "artifact_id": f"art_experiment_{run_id}",
                "artifact_type": "experiment_plan",
                "path": f"runs/{run_id}/artifacts/experiments/{experiment_plan['experiment_id']}.json",
            }
        )
    base = [
        _event(
            "evt_001",
            "work.accepted",
            "2026-07-02T15:00:00Z",
            workflow_id,
            run_id,
            {"type": "system", "id": "commandcenter"},
            "accepted",
            {"source": "inbox", "input_ref": f"inbox/{run_id}.md"},
        ),
        _event(
            "evt_002",
            "planning.started",
            "2026-07-02T15:00:05Z",
            workflow_id,
            run_id,
            {"type": "planner", "id": "planner-1", "model_id": "claude-opus-4.8"},
            "in_progress",
            {"plan_strategy": "single-pass"},
        ),
        _event(
            "evt_003",
            "planning.completed",
            "2026-07-02T15:00:20Z",
            workflow_id,
            run_id,
            {"type": "planner", "id": "planner-1", "model_id": "claude-opus-4.8"},
            "completed",
            {"estimated_builders": 1},
            outcome="success",
            artifact_refs=[
                {
                    "artifact_id": f"art_plan_{run_id}",
                    "artifact_type": "plan",
                    "path": f"runs/{run_id}/artifacts/PLAN.md",
                }
            ],
        ),
        _event(
            "evt_004",
            "builder.assigned",
            "2026-07-02T15:00:25Z",
            workflow_id,
            run_id,
            builder_actor,
            "assigned",
            {"assignment_scope": "workflow"},
            lap_id="lap_001",
            decision_id="dec_assign_001",
            decision_type="builder_assignment",
            decision_class="builder_assignment",
            decision_maker={"type": "system", "id": "scheduler-local"},
            decision_reason=decision_reason,
            artifact_refs=assignment_refs,
        ),
    ]

    if (selection or {}).get("selected") is None and selection is not None:
        return base[:3]  # policy blocked every candidate: the run stops at routing, no dispatch events

    if scenario != "hold":
        return base + [
            _event(
                "evt_005",
                "candidate.produced",
                "2026-07-02T15:02:00Z",
                workflow_id,
                run_id,
                builder_actor,
                "produced",
                {"branch": f"ccfarm/{run_id}/builder-1/lap1", "has_tests": True},
                lap_id="lap_001",
                candidate_id="cand_001",
                artifact_refs=[
                    {
                        "artifact_id": f"art_candidate_{run_id}",
                        "artifact_type": "candidate",
                        "path": f"runs/{run_id}/artifacts/candidates/cand_001.json",
                    }
                ],
            ),
            _event(
                "evt_006",
                "assay.started",
                "2026-07-02T15:02:15Z",
                workflow_id,
                run_id,
                {"type": "assay", "id": "assay-1"},
                "in_progress",
                {"candidate_count": 1},
                assay_id="assay_001",
            ),
            _event(
                "evt_007",
                "assay.passed",
                "2026-07-02T15:02:45Z",
                workflow_id,
                run_id,
                {"type": "assay", "id": "assay-1"},
                "completed",
                {"grade": "A", "winner": True},
                assay_id="assay_001",
                candidate_id="cand_001",
                outcome="passed",
            ),
            _event(
                "evt_008",
                "risk.scored",
                "2026-07-02T15:02:50Z",
                workflow_id,
                run_id,
                {"type": "system", "id": "risk-engine"},
                "completed",
                {"risk.level": "low", "risk_score": 0.12},
                risk_report_id="risk_001",
                candidate_id="cand_001",
            ),
            _event(
                "evt_009",
                "promotion.approved",
                "2026-07-02T15:03:00Z",
                workflow_id,
                run_id,
                {"type": "operator", "id": "derek"},
                "approved",
                {"target_ref": "mainline"},
                outcome="approved",
                promotion_id="promo_001",
                candidate_id="cand_001",
                decision_id="dec_001",
                decision_type="promotion_approval",
                decision_class="promotion_approval",
                decision_maker={"type": "operator", "id": "derek"},
                decision_reason="candidate accepted for promotion",
            ),
            _event(
                "evt_010",
                "retrospective.created",
                "2026-07-02T15:03:10Z",
                workflow_id,
                run_id,
                {"type": "system", "id": "commandcenter"},
                "created",
                {"scope": "run"},
                retrospective_id="retro_001",
                artifact_refs=[
                    {
                        "artifact_id": f"art_retro_{run_id}",
                        "artifact_type": "retrospective",
                        "path": f"runs/{run_id}/artifacts/retro.md",
                    },
                    {
                        "artifact_id": f"art_observation_{run_id}",
                        "artifact_type": "capacity_observation",
                        "path": f"runs/{run_id}/artifacts/observations/obs_001.json",
                    },
                ],
            ),
        ]

    if scenario == "hold":
        return base + [
            _event(
                "evt_005",
                "question.raised",
                "2026-07-02T15:01:00Z",
                workflow_id,
                run_id,
                builder_actor,
                "waiting_on_operator",
                {"question_kind": "permission", "blocking": True},
                lap_id="lap_001",
                question_id="q_001",
                operator_action_required=True,
                artifact_refs=[
                    {
                        "artifact_id": f"art_question_{run_id}",
                        "artifact_type": "question",
                        "path": f"runs/{run_id}/artifacts/questions/q_001.md",
                    }
                ],
            ),
            _event(
                "evt_006",
                "question.answered",
                "2026-07-02T15:01:30Z",
                workflow_id,
                run_id,
                {"type": "operator", "id": "derek"},
                "answered",
                {"decision": "continue"},
                question_id="q_001",
                outcome="approved",
                decision_id="dec_101",
                decision_type="question_answer",
                decision_class="question_answer",
                decision_maker={"type": "operator", "id": "derek"},
                decision_reason="permission granted",
            ),
            _event(
                "evt_007",
                "builder.resumed",
                "2026-07-02T15:01:35Z",
                workflow_id,
                run_id,
                builder_actor,
                "in_progress",
                {"resume_reason": "question_answered"},
                lap_id="lap_001",
            ),
            _event(
                "evt_008",
                "candidate.produced",
                "2026-07-02T15:02:30Z",
                workflow_id,
                run_id,
                builder_actor,
                "produced",
                {"branch": f"ccfarm/{run_id}/builder-1/lap1"},
                lap_id="lap_001",
                candidate_id="cand_001",
            ),
            _event(
                "evt_009",
                "assay.started",
                "2026-07-02T15:02:45Z",
                workflow_id,
                run_id,
                {"type": "assay", "id": "assay-1"},
                "in_progress",
                {},
                assay_id="assay_001",
            ),
            _event(
                "evt_010",
                "assay.passed",
                "2026-07-02T15:03:00Z",
                workflow_id,
                run_id,
                {"type": "assay", "id": "assay-1"},
                "completed",
                {"grade": "B", "winner": True},
                assay_id="assay_001",
                candidate_id="cand_001",
                outcome="passed",
            ),
            _event(
                "evt_011",
                "risk.scored",
                "2026-07-02T15:03:05Z",
                workflow_id,
                run_id,
                {"type": "system", "id": "risk-engine"},
                "completed",
                {"risk.level": "medium", "risk_score": 0.46},
                risk_report_id="risk_001",
                candidate_id="cand_001",
            ),
            _event(
                "evt_012",
                "promotion.held",
                "2026-07-02T15:03:10Z",
                workflow_id,
                run_id,
                {"type": "operator", "id": "derek"},
                "waiting_on_operator",
                {"hold_reason": "manual review required"},
                promotion_id="promo_001",
                candidate_id="cand_001",
                operator_action_required=True,
                decision_id="dec_102",
                decision_type="promotion_hold",
                decision_class="promotion_hold",
                decision_maker={"type": "operator", "id": "derek"},
                decision_reason="manual review required",
                artifact_refs=[
                    {
                        "artifact_id": f"art_observation_{run_id}",
                        "artifact_type": "capacity_observation",
                        "path": f"runs/{run_id}/artifacts/observations/obs_001.json",
                    }
                ],
            ),
        ]

    raise ValueError(f"unknown scenario: {scenario}")


def run_reference_workflow(work_item: Path, runs_root: Path, scenario: str,
                           policy_path: Path | None = None,
                           capabilities_path: Path | None = None,
                           disable_frontier: bool = False) -> dict:
    config = SCENARIOS.get(scenario)
    if config is None:
        raise ValueError(f"unknown scenario: {scenario}")
    if config["requires_policy"] and policy_path is None:
        raise ValueError(f"scenario {scenario} needs --policy (a materialized policy.json)")
    if config.get("requires_capabilities") and capabilities_path is None:
        raise ValueError(f"scenario {scenario} needs --capabilities (a materialized capabilities.json)")

    run_id = work_item.stem
    workflow_id = f"wf_{run_id}"
    task_kind = config.get("task_kind", DEFAULT_TASK_KIND)
    run_dir = runs_root / run_id
    artifacts_dir = run_dir / "artifacts"
    events_path = run_dir / "events.jsonl"

    if run_dir.exists():
        shutil.rmtree(run_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    rules = load_policy_rules(policy_path)
    selection = schedule(CANDIDATE_POOLS[config["pool"]], task_kind, rules,
                         experiment_flag=config["experiment_flag"],
                         capabilities=load_capabilities(capabilities_path),
                         disable_frontier=disable_frontier)

    _write_text(artifacts_dir / "inputs" / work_item.name, work_item.read_text(encoding="utf-8"))
    _write_text(artifacts_dir / "PLAN.md", "# Plan\n\nReference workflow plan.\n")

    experiment_plan = None
    if selection["selected"] is None:
        # No dispatch: the block still leaves an explaining artifact, just not a SchedulerDecision.
        _write_text(
            artifacts_dir / "policy-block.json",
            json.dumps({"decision_reason": selection["decision_reason"],
                        "candidates_considered": selection["candidates_considered"],
                        "candidates_blocked": selection["candidates_blocked"]}, indent=2) + "\n",
        )
    else:
        experiment_plan = build_experiment_plan(run_id, workflow_id, selection, rules)
        if experiment_plan is not None:
            _write_text(
                artifacts_dir / "experiments" / f"{experiment_plan['experiment_id']}.json",
                json.dumps(experiment_plan, indent=2) + "\n",
            )
        _write_text(artifacts_dir / "retro.md", "# Retro\n\nReference retrospective.\n")
        _write_text(artifacts_dir / "questions" / "q_001.md", "# Question\n\nPermission required.\n")
        _write_text(artifacts_dir / "candidates" / "cand_001.json", json.dumps({"candidate_id": "cand_001"}, indent=2))
        _write_text(
            artifacts_dir / "decisions" / "dec_assign_001.json",
            json.dumps(build_scheduler_decision(run_id, workflow_id, scenario, selection, task_kind),
                       indent=2) + "\n",
        )
        _write_text(
            artifacts_dir / "observations" / "obs_001.json",
            json.dumps(build_capacity_observation(run_id, workflow_id, scenario, selection["selected"],
                                                  task_kind), indent=2) + "\n",
        )

    state = {}
    for event in build_scenario_events(run_id, workflow_id, scenario, selection, experiment_plan):
        append_event(events_path, event)
        state = materialize_run(run_dir)
    if selection["selected"] is None:
        state = {**state, "dispatch": {"status": "blocked",
                                       "reason": selection["decision_reason"],
                                       "candidates_blocked": selection["candidates_blocked"]}}
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("work_item", help="Path to input work markdown")
    parser.add_argument("runs_root", help="Directory that will contain run directories")
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), default="happy")
    parser.add_argument("--policy", default=None, help="Materialized policy.json the scheduler must consult")
    parser.add_argument("--capabilities", default=None,
                        help="Materialized capabilities.json for capability-directed dispatch")
    parser.add_argument("--disable-frontier", action="store_true",
                        help="Exclude frontier (Claude) candidates; only local-model candidates "
                             "(ollama/vllm) are eligible for this dispatch")
    args = parser.parse_args(argv)

    state = run_reference_workflow(Path(args.work_item), Path(args.runs_root), args.scenario,
                                   policy_path=Path(args.policy) if args.policy else None,
                                   capabilities_path=Path(args.capabilities) if args.capabilities else None,
                                   disable_frontier=args.disable_frontier)
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
