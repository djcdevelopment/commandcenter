from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.workflow.project_capacity import (
    KNOWN_BAD_MIN_FAILURES,
    KNOWN_GOOD_MIN_SUCCESS_RATE,
    collect_event_files,
    extract_observations,
    extract_scheduler_decisions,
)
from tools.workflow.project_findings import (
    BIAS_CONSISTENCY,
    HIGH_CONFIDENCE_MIN_SAMPLES,
    _confidence_label,
    synthesize_findings,
)
from tools.workflow.project_associations import synthesize_associations, synthesize_capabilities
from tools.workflow.project_coverage import synthesize_coverage
from tools.workflow.project_policy import evaluate, synthesize_policy
from tools.workflow.project_state import read_events
from tools.workflow.corpus_guard import check_fixture_taint, guard_write, make_extractor

CANDIDATES_FILE = "experiment_candidates.json"
RESULTS_FILE = "experiment_results.json"

EXPERIMENT_TYPES = (
    "known_bad_retest",
    "prediction_bias_calibration",
    "uncertain_resolution",
    "regression_probe",
    "prefer_validation",
    "backend_comparison",
    "qualification_run",
    "coverage_probe",
)

GATING_EFFECTS = {"block": 2, "quarantine": 1, "exploratory_only": 0}


def _subject(finding_subject: dict) -> dict:
    return {field: finding_subject.get(field) for field in ("builder_id", "model_id", "backend", "task_kind", "metric")}


def _combo(subject: dict) -> str:
    return "|".join(subject.get(field) or "unknown" for field in ("builder_id", "model_id", "backend"))


def _label(subject: dict) -> str:
    return " + ".join(subject.get(field) or "any" for field in ("builder_id", "model_id", "backend"))


def _gate_for(finding_id: str, rules: list[dict]) -> dict | None:
    for rule in rules:
        if rule["derived_from_finding"] == finding_id and rule["effect"] in GATING_EFFECTS:
            override = rule.get("override") or {}
            return {
                "policy_id": rule["policy_id"],
                "effect": rule["effect"],
                "flag": override.get("flag"),
                "semantics": override.get("semantics"),
            }
    return None


def _candidate(experiment_type: str, candidate_id: str, subject: dict, question: str, worth: str,
               target_finding_id: str | None, gate: dict | None, evidence_sought: str,
               risk_accepted: str, confidence: str | None, last_observed: str | None,
               target_capability_id: str | None = None) -> dict:
    return {
        "candidate_id": candidate_id,
        "experiment_type": experiment_type,
        "subject": subject,
        "question": question,
        "worth": worth,
        "target_finding_id": target_finding_id,
        "target_capability_id": target_capability_id,
        "gate": gate,
        "evidence_sought": evidence_sought,
        "risk_accepted": risk_accepted,
        "confidence": confidence,
        "last_observed": last_observed,
    }


def _successes_to_known_good(successes: int, samples: int) -> int:
    needed = 0
    while (successes + needed) / (samples + needed) < KNOWN_GOOD_MIN_SUCCESS_RATE:
        needed += 1
    return needed


def _qualification_candidates(capabilities: list[dict]) -> list[dict]:
    """Qualification decay is the Experiment layer's standing demand source: a capability that has
    not been validated inside its window proposes its own re-qualification run — preventative
    maintenance, like instrument calibration, not emergency repair."""
    candidates = []
    for capability in capabilities:
        if capability["qualification_status"] != "requalification_due":
            continue
        invariant = capability["invariant"]
        subject = {"builder_id": None, "model_id": None, "backend": invariant.get("backend"),
                   "task_kind": invariant.get("task_kind"), "metric": None}
        resources = len(capability["qualified_resources"])
        candidates.append(_candidate(
            "qualification_run", f"qualification_run:{capability['capability_id']}", subject,
            f"does the capability '{capability['name']}' still hold "
            f"({capability['staleness_days']} evidence-day(s) since last validation)?",
            "dispatch decisions schedule against this capability; a stale qualification means they "
            "rest on old truth — the belief is 'capability still holds' and it decays",
            target_finding_id=None,
            gate=None,
            evidence_sought="one fresh success on any qualified resource renews last_validated and "
                            "restores qualified status; a failure demotes that resource and the "
                            "capability re-derives without it",
            risk_accepted=f"one dispatch slot on any of {resources} previously qualified resource(s); "
                          "the cost is scheduled, not emergency",
            confidence=capability["confidence"],
            last_observed=capability["last_validated"],
            target_capability_id=capability["capability_id"],
        ))
    return candidates


def _coverage_candidates(coverage_gaps: list[dict]) -> list[dict]:
    """Blind spots are experiment demand: what the corpus reports it cannot see, the experiment
    layer proposes to look at. stale_capability gaps are excluded — qualification_run already owns
    that demand, and a gap must not propose the same experiment twice."""
    candidates = []
    for gap in coverage_gaps:
        if gap["proposed_experiment_type"] != "coverage_probe":
            continue
        subject = {field: gap["subject"].get(field)
                   for field in ("builder_id", "model_id", "backend", "task_kind")}
        subject["metric"] = None
        label = " + ".join(subject[field] or "any" for field in ("builder_id", "model_id", "backend"))
        if gap["gap_type"] == "unobserved_combo":
            question = f"what happens when {label} actually runs?"
            worth = "the scheduler is reasoning about a resource it has never observed; its first " \
                    "observation replaces assumption with evidence"
            evidence_sought = "a single observation of any outcome moves the combo from unknown " \
                              "into the corpus, where findings can form"
            risk = "one dispatch slot on unproven ground; exploratory by nature"
        elif gap["gap_type"] == "single_workflow_evidence":
            invariant = gap["subject"]["invariant"] or {}
            invariant_desc = ", ".join(f"{name}={value}" for name, value in invariant.items())
            question = f"does '{invariant_desc}' hold outside {(gap['evidence']['workflows'] or ['?'])[0]}?"
            worth = f"{gap['evidence']['samples']} consistent sample(s) agree but all come from one " \
                    "workflow; the association engine will not generalize from one workflow"
            evidence_sought = "one consistent observation from a second distinct workflow lets the " \
                              "invariant generalize into an association (and possibly a capability)"
            risk = "one dispatch slot; the invariant is already consistent where observed"
        else:  # unmeasured_metrics
            missing = ", ".join(gap["subject"]["missing_metrics"] or [])
            question = f"what are {missing} on {label}?"
            worth = "predictions of these metrics can never be calibrated while they go unmeasured"
            evidence_sought = "a single instrumented run gives the calibration layer its first " \
                              "prediction/observation comparison for each missing metric"
            risk = "none beyond a normal dispatch; only instrumentation is asked for"
        candidates.append(_candidate(
            "coverage_probe", f"coverage_probe:{gap['gap_id']}", subject,
            question, worth,
            target_finding_id=None,
            gate=None,
            evidence_sought=evidence_sought,
            risk_accepted=risk,
            confidence=None,
            last_observed=gap.get("last_observed"),
        ))
    return candidates


def synthesize_candidates(findings: list[dict], rules: list[dict],
                          capabilities: list[dict] | None = None,
                          coverage_gaps: list[dict] | None = None) -> list[dict]:
    """Every finding proposes the experiment that would test it. The candidate answers, up front,
    the questions the eventual ExperimentPlan must answer: what to retest, why it is worth it,
    which gate opens, what evidence would move the belief, and what risk that purchase costs.
    Capabilities add qualification demand; coverage gaps add blind-spot demand — one funnel."""
    candidates: list[dict] = []
    combos_by_model: dict[str, dict[str, dict]] = {}

    for finding in findings:
        kind = finding["finding_type"]
        subject = _subject(finding["subject"])
        label = _label(subject)
        summary = finding["evidence"]["summary"]
        samples = finding["evidence"]["samples"]
        gate = _gate_for(finding["finding_id"], rules)
        common = {
            "target_finding_id": finding["finding_id"],
            "gate": gate,
            "confidence": finding["confidence"],
            "last_observed": finding.get("last_observed"),
        }

        if kind in {"known_good", "known_bad", "uncertain"}:
            combos_by_model.setdefault(subject["model_id"] or "unknown", {})[subject["backend"] or "unknown"] = finding

        if kind == "known_bad":
            candidates.append(_candidate(
                "known_bad_retest", f"known_bad_retest:{_combo(subject)}", subject,
                f"does {label} still fail ({summary})?",
                f"the block is a belief, not a fact: evidence ended {finding.get('last_observed')} and "
                "environment drift (driver, engine version, config) can invalidate it",
                evidence_sought=f"a single success contradicts '{summary}' and reclassifies known_bad -> uncertain "
                                f"(success rate 1/{samples + 1}); another failure raises confidence to "
                                f"{_confidence_label(samples + 1, unanimous=True)}",
                risk_accepted=f"one dispatch slot and a likely failure ({summary}); the opened gate admits only "
                              "the flagged experiment, never normal work",
                **common,
            ))
        elif kind == "uncertain":
            successes = finding["evidence"]["successes"] or 0
            failures = finding["evidence"]["failures"] or 0
            to_good = _successes_to_known_good(successes, samples)
            if successes == 0:
                to_bad = max(0, KNOWN_BAD_MIN_FAILURES - failures)
                resolution = (f"{to_good} consecutive success(es) reclassify uncertain -> known_good; "
                              f"{to_bad} more failure(s) with zero successes reclassify to known_bad")
            else:
                resolution = (f"{to_good} consecutive success(es) reclassify uncertain -> known_good; "
                              "known_bad is unreachable once a success exists, so only more evidence resolves it")
            candidates.append(_candidate(
                "uncertain_resolution", f"uncertain_resolution:{_combo(subject)}", subject,
                f"which way does {label} resolve ({summary})?",
                "the combo is stuck in exploratory_only until the evidence resolves it one way or the other",
                evidence_sought=resolution,
                risk_accepted=f"one dispatch slot; failure odds roughly {failures}/{samples} on current evidence",
                **common,
            ))
        elif kind == "regression":
            candidates.append(_candidate(
                "regression_probe", f"regression_probe:{_combo(subject)}", subject,
                f"is the regression on {label} real or transient ({summary})?",
                "quarantine costs capacity every day it is wrong; a fresh probe settles it",
                evidence_sought="one fresh success supersedes the post-success failures and clears the regression; "
                                "another failure confirms it and keeps the quarantine",
                risk_accepted=f"one dispatch slot; the regression may reproduce ({summary})",
                **common,
            ))
        elif kind == "known_good":
            runs_to_prefer = max(0, HIGH_CONFIDENCE_MIN_SAMPLES - samples)
            worth = ("the preference nudges every dispatch; it must keep earning that"
                     if runs_to_prefer == 0 else
                     f"{runs_to_prefer} more clean run(s) reach high confidence and earn a prefer rule ({summary} today)")
            candidates.append(_candidate(
                "prefer_validation", f"prefer_validation:{_combo(subject)}", subject,
                f"does {label} still hold up ({summary})?",
                worth,
                evidence_sought="a failure breaks unanimity, drops confidence, and revokes or defers the preference; "
                                "another success deepens it",
                risk_accepted="minimal: one run on a proven combo; a failure corrects an overtrusted belief",
                **common,
            ))
        elif kind == "prediction_bias":
            correction = finding["evidence"]["mean_signed_error"]
            candidates.append(_candidate(
                "prediction_bias_calibration", f"prediction_bias_calibration:{subject['model_id']}:{subject['metric']}",
                subject,
                f"is the {subject['metric']} correction for {subject['model_id']} ({correction:+}) still right?",
                f"every {subject['metric']} prediction for {subject['model_id']} is silently shifted by {correction:+}; "
                "fresh comparisons confirm the correction or dissolve it",
                evidence_sought=f"comparisons with opposite-signed error shrink the bias; the finding dissolves when "
                                f"|mean signed error| < {BIAS_CONSISTENCY} x MAE",
                risk_accepted="none beyond a normal dispatch; calibration needs only the prediction/observation pair",
                **common,
            ))

    for model_id in sorted(combos_by_model):
        backends = combos_by_model[model_id]
        if len(backends) < 2:
            continue
        backend_names = sorted(backends)
        statuses = ", ".join(f"{name}={backends[name]['finding_type']}" for name in backend_names)
        subject = {"builder_id": None, "model_id": model_id, "backend": None, "task_kind": None, "metric": None}
        candidates.append(_candidate(
            "backend_comparison", f"backend_comparison:{model_id}:{'+'.join(backend_names)}", subject,
            f"which backend serves {model_id} best ({statuses})?",
            "the model already runs on multiple backends but the beliefs were formed on different workloads; "
            "a controlled comparison replaces inference with measurement",
            target_finding_id=None,
            gate=None,
            evidence_sought="the same workload shape run on each backend yields a per-task recommendation "
                            "instead of a guess",
            risk_accepted="one dispatch slot per backend; a gated backend must be opened with its own experiment",
            confidence=None,
            last_observed=max((backends[name].get("last_observed") or "" for name in backend_names), default=None) or None,
        ))

    candidates.extend(_qualification_candidates(capabilities or []))
    candidates.extend(_coverage_candidates(coverage_gaps or []))
    candidates.sort(key=lambda candidate: (candidate["experiment_type"], candidate["candidate_id"]))
    return candidates


def extract_experiment_plans(events: list[dict], events_path: Path) -> tuple[list[dict], int]:
    run_dir = events_path.parent
    plans: list[dict] = []
    unresolved = 0
    for event in events:
        for ref in event.get("artifact_refs") or []:
            if ref.get("artifact_type") != "experiment_plan":
                continue
            normalized = (ref.get("path") or "").replace("\\", "/")
            marker = "artifacts/"
            index = normalized.find(marker)
            resolved = run_dir / "artifacts" / Path(normalized[index + len(marker):]) if index != -1 else None
            if resolved is not None and resolved.is_file():
                plans.append(json.loads(resolved.read_text(encoding="utf-8")))
            else:
                unresolved += 1
    return plans, unresolved


def _belief_snapshot(findings: list[dict], subject: dict) -> dict | None:
    if subject.get("metric"):
        target_ids = {f"prediction_bias:{subject.get('model_id')}:{subject['metric']}"}
    else:
        combo = _combo(subject)
        target_ids = {f"{kind}:{combo}" for kind in ("known_good", "known_bad", "uncertain")}
    finding = next((f for f in findings if f["finding_id"] in target_ids), None)
    if finding is None:
        return None
    return {
        "finding_id": finding["finding_id"],
        "finding_type": finding["finding_type"],
        "confidence": finding["confidence"],
        "statement": finding["statement"],
        "evidence_summary": finding["evidence"]["summary"],
    }


def _gate_snapshot(rules: list[dict], subject: dict) -> dict | None:
    verdict = evaluate(rules, builder_id=subject.get("builder_id"), model_id=subject.get("model_id"),
                       backend=subject.get("backend"), task_kind=subject.get("task_kind"))
    gating = [match for match in verdict["matched_rules"]
              if match["status"] == "active" and match["effect"] in GATING_EFFECTS]
    if not gating:
        return None
    top = max(gating, key=lambda match: GATING_EFFECTS[match["effect"]])
    return {"policy_id": top["policy_id"], "effect": top["effect"]}


def _describe_belief(snapshot: dict | None) -> str:
    return f"{snapshot['finding_type']} ({snapshot['confidence']})" if snapshot else "none"


def _describe_gate(gate: dict | None) -> str:
    return gate["effect"] if gate else "open"


def synthesize_results(plans: list[dict], decisions: list[dict], observations: list[dict]) -> list[dict]:
    """Judge each experiment by re-synthesizing beliefs with and without its evidence. The result is
    derived, never authored: belief_before is the counterfactual corpus, belief_after is the real one,
    and the gate change shows how policy re-gated the subject."""
    findings_after = synthesize_findings(observations, decisions)
    rules_after = synthesize_policy(findings_after)

    results: list[dict] = []
    for plan in sorted(plans, key=lambda plan: (plan.get("run_id") or "", plan.get("experiment_id") or "")):
        experiment_obs = [obs for obs in observations
                          if obs.get("run_id") == plan["run_id"] and obs.get("decision_id") == plan["decision_id"]]
        experiment_keys = {(obs.get("run_id"), obs.get("observation_id")) for obs in experiment_obs}
        baseline = [obs for obs in observations
                    if (obs.get("run_id"), obs.get("observation_id")) not in experiment_keys]
        findings_before = synthesize_findings(baseline, decisions)
        rules_before = synthesize_policy(findings_before)

        subject = _subject(plan.get("subject") or {})
        belief_before = _belief_snapshot(findings_before, subject)
        belief_after = _belief_snapshot(findings_after, subject)
        gate_before = _gate_snapshot(rules_before, subject)
        gate_after = _gate_snapshot(rules_after, subject)

        outcomes = [obs.get("outcome") for obs in experiment_obs]
        if not experiment_obs:
            outcome = "no_observation"
        elif all(value == "success" for value in outcomes):
            outcome = "success"
        else:
            outcome = next(value for value in outcomes if value != "success")
        failure_classes = sorted({obs["failure_class"] for obs in experiment_obs if obs.get("failure_class")}) or None

        before_key = (belief_before or {}).get("finding_type"), (belief_before or {}).get("confidence")
        after_key = (belief_after or {}).get("finding_type"), (belief_after or {}).get("confidence")
        belief_changed = before_key != after_key

        if outcome == "no_observation":
            verdict = "experiment dispatched but no observation recorded; belief unchanged"
        else:
            verdict = (f"belief {'moved' if belief_changed else 'held'}: "
                       f"{_describe_belief(belief_before)} -> {_describe_belief(belief_after)}; "
                       f"gate: {_describe_gate(gate_before)} -> {_describe_gate(gate_after)}")

        timestamps = sorted(obs["timestamp"] for obs in experiment_obs if obs.get("timestamp"))
        results.append({
            "contract_version": "experiment-result.v1",
            "experiment_id": plan["experiment_id"],
            "experiment_type": plan["experiment_type"],
            "workflow_id": plan["workflow_id"],
            "run_id": plan["run_id"],
            "decision_id": plan["decision_id"],
            "subject": subject,
            "target_finding_id": plan.get("target_finding_id"),
            "observation_ids": sorted(obs["observation_id"] for obs in experiment_obs if obs.get("observation_id")),
            "outcome": outcome,
            "failure_classes": failure_classes,
            "timestamp": timestamps[-1] if timestamps else plan.get("timestamp"),
            "belief_before": belief_before,
            "belief_after": belief_after,
            "belief_changed": belief_changed,
            "gate_before": gate_before,
            "gate_after": gate_after,
            "verdict": verdict,
        })
    return results


def materialize_experiments(event_files: list[Path], knowledge_dir: Path) -> dict:
    observations: list[dict] = []
    decisions: list[dict] = []
    plans: list[dict] = []
    unresolved_refs = 0
    for event_file in event_files:
        events = read_events(event_file)
        extracted_observations, unresolved_observations = extract_observations(events, event_file)
        extracted_decisions, unresolved_decisions = extract_scheduler_decisions(events, event_file)
        extracted_plans, unresolved_plans = extract_experiment_plans(events, event_file)
        observations.extend(extracted_observations)
        decisions.extend(extracted_decisions)
        plans.extend(extracted_plans)
        unresolved_refs += unresolved_observations + unresolved_decisions + unresolved_plans

    findings = synthesize_findings(observations, decisions)
    rules = synthesize_policy(findings)
    associations = synthesize_associations(observations, findings)
    capabilities = synthesize_capabilities(associations, findings, observations)
    coverage_gaps = synthesize_coverage(observations, decisions, capabilities)
    candidates = synthesize_candidates(findings, rules, capabilities, coverage_gaps)
    results = synthesize_results(plans, decisions, observations)

    candidate_counts: dict[str, int] = {}
    for candidate in candidates:
        candidate_counts[candidate["experiment_type"]] = candidate_counts.get(candidate["experiment_type"], 0) + 1

    outputs = {
        CANDIDATES_FILE: {
            "contract_version": "experiment-candidates.v1",
            "source_findings": len(findings),
            "candidate_counts": candidate_counts,
            "candidates": candidates,
        },
        RESULTS_FILE: {
            "contract_version": "experiment-results.v1",
            "plan_count": len(plans),
            "unresolved_refs": unresolved_refs,
            "beliefs_changed": sum(1 for result in results if result["belief_changed"]),
            "results": results,
        },
    }
    extractors = {
        CANDIDATES_FILE: make_extractor("source_findings"),
        RESULTS_FILE: make_extractor("plan_count"),
    }
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    for file_name, content in outputs.items():
        guard_write(knowledge_dir / file_name, content, extractors[file_name])
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", help="events.jsonl files, or directories to scan recursively for them")
    parser.add_argument("--out", default="knowledge", help="Directory for materialized knowledge files")
    parser.add_argument("--allow-fixture-sources", action="store_true",
                        help="Authored escape hatch (audited): permit fixture sources to project into the repo knowledge/ store")
    args = parser.parse_args(argv)

    event_files = collect_event_files([Path(raw) for raw in args.sources])
    check_fixture_taint([Path(raw) for raw in args.sources] + event_files, Path(args.out),
                        allow=args.allow_fixture_sources)
    outputs = materialize_experiments(event_files, Path(args.out))
    print(json.dumps({
        "event_files": len(event_files),
        "candidates": len(outputs[CANDIDATES_FILE]["candidates"]),
        "by_type": outputs[CANDIDATES_FILE]["candidate_counts"],
        "experiments": outputs[RESULTS_FILE]["plan_count"],
        "beliefs_changed": outputs[RESULTS_FILE]["beliefs_changed"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
