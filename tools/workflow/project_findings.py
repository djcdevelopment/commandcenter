from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.workflow.project_capacity import (
    classify_known_bad,
    classify_known_good,
    collect_event_files,
    extract_observations,
    extract_scheduler_decisions,
    reduce_capacity,
    reduce_prediction_comparisons,
    _combo_key,
    _confidence,
)
from tools.workflow.project_state import read_events
from tools.workflow.corpus_guard import guard_write, make_extractor

FINDINGS_FILE = "findings.json"

# Arbitrary-at-birth baselines, recorded per the arbitrary-but-traced decision rule (D18).
# Revision path: change here, append a superseding Dx with rationale.
BIAS_MIN_SAMPLES = 2       # prediction/observation pairs a metric needs before a bias belief forms
BIAS_CONSISTENCY = 0.8     # |mean signed error| >= this fraction of MAE => errors are one-signed enough to call bias
HIGH_CONFIDENCE_MIN_SAMPLES = 3

KNOWN_BAD_RECOMMENDATION = "do not schedule without explicit experiment flag"
UNCERTAIN_RECOMMENDATION = "schedule only low-stakes work; gather more evidence"
REGRESSION_RECOMMENDATION = "re-assay before scheduling; a previously working combo is now failing"

BIAS_METRICS = (
    "expected_ttft_ms",
    "expected_generation_tokens_per_second",
    "expected_peak_ram_mb",
    "expected_peak_vram_mb",
)


def _confidence_label(samples: int, unanimous: bool) -> str:
    if samples >= HIGH_CONFIDENCE_MIN_SAMPLES and unanimous:
        return "high"
    if samples >= 2:
        return "medium"
    return "low"


def _combo_histories(observations: list[dict]) -> dict[str, dict]:
    """Per-combo evidence trail the capacity reducer discards: ids, task kinds, time-ordered outcomes."""
    histories: dict[str, dict] = {}
    for observation in observations:
        key = _combo_key(observation)
        history = histories.setdefault(
            key,
            {"observation_ids": [], "task_kinds": [], "outcomes": [], "first_observed": None, "last_observed": None},
        )
        if observation.get("observation_id"):
            history["observation_ids"].append(observation["observation_id"])
        task_kind = (observation.get("workload_shape") or {}).get("task_kind")
        if task_kind and task_kind not in history["task_kinds"]:
            history["task_kinds"].append(task_kind)
        history["outcomes"].append(
            {
                "timestamp": observation.get("timestamp"),
                "success": observation.get("outcome") == "success",
                "observation_id": observation.get("observation_id"),
                "failure_class": observation.get("failure_class"),
            }
        )
        timestamp = observation.get("timestamp")
        if timestamp:
            if history["first_observed"] is None or timestamp < history["first_observed"]:
                history["first_observed"] = timestamp
            if history["last_observed"] is None or timestamp > history["last_observed"]:
                history["last_observed"] = timestamp
    for history in histories.values():
        history["observation_ids"].sort()
        history["task_kinds"].sort()
        history["outcomes"].sort(key=lambda item: item["timestamp"] or "")
    return histories


def _finding(finding_type: str, finding_id: str, statement: str, subject: dict, samples: int,
             unanimous: bool, evidence: dict, recommendation: str | None,
             first_observed: str | None, last_observed: str | None, derived_from: list[str]) -> dict:
    return {
        "contract_version": "finding.v1",
        "finding_id": finding_id,
        "finding_type": finding_type,
        "statement": statement,
        "subject": {
            "builder_id": subject.get("builder_id"),
            "model_id": subject.get("model_id"),
            "backend": subject.get("backend"),
            "task_kind": subject.get("task_kind"),
            "metric": subject.get("metric"),
        },
        "confidence": _confidence_label(samples, unanimous),
        "confidence_score": _confidence(samples),
        "evidence": {
            "samples": samples,
            "successes": evidence.get("successes"),
            "failures": evidence.get("failures"),
            "summary": evidence["summary"],
            "observation_ids": evidence.get("observation_ids"),
            "decision_ids": evidence.get("decision_ids"),
            "mean_signed_error": evidence.get("mean_signed_error"),
            "mean_absolute_error": evidence.get("mean_absolute_error"),
        },
        "recommendation": recommendation,
        "first_observed": first_observed,
        "last_observed": last_observed,
        "derived_from": derived_from,
    }


def _combo_id(entry: dict) -> str:
    return "|".join([entry.get("builder_id") or "unknown", entry.get("model_id") or "unknown", entry.get("backend") or "unknown"])


def synthesize_known_good(known_good: list[dict], histories: dict[str, dict]) -> list[dict]:
    findings = []
    for entry in known_good:
        history = histories.get(_combo_id(entry), {})
        samples = entry["samples"]
        successes = round(entry["success_rate"] * samples)
        speed = f", mean {entry['mean_tokens_per_s']} tok/s" if entry.get("mean_tokens_per_s") is not None else ""
        findings.append(
            _finding(
                "known_good",
                f"known_good:{_combo_id(entry)}",
                f"{_combo_id(entry).replace('|', ' + ')} works: {entry['reason']}{speed}",
                entry,
                samples,
                unanimous=entry["success_rate"] == 1.0,
                evidence={
                    "successes": successes,
                    "failures": samples - successes,
                    "summary": f"{successes}/{samples} success",
                    "observation_ids": history.get("observation_ids"),
                },
                recommendation=None,
                first_observed=history.get("first_observed"),
                last_observed=entry.get("last_observed"),
                derived_from=["capacity-estimates.v1"],
            )
        )
    return findings


def synthesize_known_bad(known_bad: list[dict], histories: dict[str, dict]) -> list[dict]:
    findings = []
    for entry in known_bad:
        history = histories.get(_combo_id(entry), {})
        findings.append(
            _finding(
                "known_bad",
                f"known_bad:{_combo_id(entry)}",
                f"{_combo_id(entry).replace('|', ' + ')} fails: {entry['reason']}",
                entry,
                entry["samples"],
                unanimous=len(entry.get("failure_classes") or {}) <= 1,
                evidence={
                    "successes": 0,
                    "failures": entry["failures"],
                    "summary": f"{entry['failures']}/{entry['samples']} {entry['dominant_failure_class']}",
                    "observation_ids": history.get("observation_ids"),
                },
                recommendation=KNOWN_BAD_RECOMMENDATION,
                first_observed=history.get("first_observed"),
                last_observed=entry.get("last_observed"),
                derived_from=["capacity-estimates.v1"],
            )
        )
    return findings


def synthesize_uncertain(estimates: dict, known_good: list[dict], known_bad: list[dict], histories: dict[str, dict]) -> list[dict]:
    classified = {_combo_id(entry) for entry in known_good} | {_combo_id(entry) for entry in known_bad}
    findings = []
    for key in sorted(estimates):
        if key in classified:
            continue
        combo = estimates[key]
        history = histories.get(key, {})
        findings.append(
            _finding(
                "uncertain",
                f"uncertain:{key}",
                f"{key.replace('|', ' + ')} has mixed or insufficient evidence: "
                f"{combo['successes']}/{combo['samples']} success (rate {combo['success_rate']})",
                combo,
                combo["samples"],
                unanimous=False,
                evidence={
                    "successes": combo["successes"],
                    "failures": combo["failures"],
                    "summary": f"{combo['successes']}/{combo['samples']} success, {combo['failures']} failures",
                    "observation_ids": history.get("observation_ids"),
                },
                recommendation=UNCERTAIN_RECOMMENDATION,
                first_observed=history.get("first_observed"),
                last_observed=combo.get("last_observed"),
                derived_from=["capacity-estimates.v1"],
            )
        )
    return findings


def synthesize_regressions(estimates: dict, histories: dict[str, dict]) -> list[dict]:
    findings = []
    for key in sorted(histories):
        outcomes = histories[key]["outcomes"]
        last_success = None
        for outcome in outcomes:
            if outcome["success"]:
                last_success = outcome["timestamp"]
        if last_success is None:
            continue
        # A failure only reads as regression if it happened AFTER the combo had proven itself.
        post_failures = [o for o in outcomes if not o["success"] and o["timestamp"] and o["timestamp"] > last_success]
        if not post_failures:
            continue
        combo = estimates.get(key, {})
        failure_classes = sorted({o["failure_class"] or "unknown" for o in post_failures})
        findings.append(
            _finding(
                "regression",
                f"regression:{key}",
                f"{key.replace('|', ' + ')} regressed: {len(post_failures)} failure(s) "
                f"({', '.join(failure_classes)}) after last success at {last_success}",
                combo,
                len(post_failures),
                unanimous=len(failure_classes) <= 1,
                evidence={
                    "successes": combo.get("successes"),
                    "failures": len(post_failures),
                    "summary": f"{len(post_failures)} failure(s) after success at {last_success}",
                    "observation_ids": sorted(o["observation_id"] for o in post_failures if o["observation_id"]),
                },
                recommendation=REGRESSION_RECOMMENDATION,
                first_observed=histories[key].get("first_observed"),
                last_observed=histories[key].get("last_observed"),
                derived_from=["capacity-estimates.v1"],
            )
        )
    return findings


def synthesize_prediction_bias(comparisons: list[dict]) -> list[dict]:
    by_model: dict[str, list[dict]] = {}
    for comparison in comparisons:
        by_model.setdefault(comparison.get("model_id") or "unknown", []).append(comparison)

    findings = []
    for model_id in sorted(by_model):
        model_comparisons = by_model[model_id]
        for metric_name in BIAS_METRICS:
            samples = [c for c in model_comparisons if c["metrics"].get(metric_name)]
            if len(samples) < BIAS_MIN_SAMPLES:
                continue
            errors = [c["metrics"][metric_name]["error"] for c in samples]
            mean_error = sum(errors) / len(errors)
            mae = sum(abs(e) for e in errors) / len(errors)
            if mae == 0 or abs(mean_error) < BIAS_CONSISTENCY * mae:
                continue
            direction = "low" if mean_error > 0 else "high"
            timestamps = sorted(c["timestamp"] for c in samples if c["timestamp"])
            findings.append(
                _finding(
                    "prediction_bias",
                    f"prediction_bias:{model_id}:{metric_name}",
                    f"predictions of {metric_name} for {model_id} run {direction}: observed "
                    f"{'exceeds' if direction == 'low' else 'falls short of'} predicted by "
                    f"~{round(abs(mean_error), 2)} on average over {len(samples)} runs",
                    {"model_id": model_id, "metric": metric_name},
                    len(samples),
                    unanimous=True,  # the consistency gate already required one-signed errors
                    evidence={
                        "summary": f"{len(samples)} comparisons, mean signed error {round(mean_error, 2)} (MAE {round(mae, 2)})",
                        "observation_ids": sorted(c["observation_id"] for c in samples if c.get("observation_id")),
                        "decision_ids": sorted(c["decision_id"] for c in samples if c.get("decision_id")),
                        "mean_signed_error": round(mean_error, 2),
                        "mean_absolute_error": round(mae, 2),
                    },
                    recommendation=f"correct {metric_name} predictions for {model_id} {'upward' if direction == 'low' else 'downward'}",
                    first_observed=timestamps[0] if timestamps else None,
                    last_observed=timestamps[-1] if timestamps else None,
                    derived_from=["prediction-accuracy.v1"],
                )
            )
    return findings


def synthesize_recommendations(known_good: list[dict], histories: dict[str, dict]) -> list[dict]:
    by_task_kind: dict[str, list[dict]] = {}
    for entry in known_good:
        for task_kind in histories.get(_combo_id(entry), {}).get("task_kinds", []):
            by_task_kind.setdefault(task_kind, []).append(entry)

    findings = []
    for task_kind in sorted(by_task_kind):
        candidates = by_task_kind[task_kind]
        best = max(
            candidates,
            key=lambda entry: (
                entry["success_rate"],
                entry["samples"],
                entry["mean_tokens_per_s"] if entry["mean_tokens_per_s"] is not None else -1,
            ),
        )
        speed = f", {best['mean_tokens_per_s']} tok/s" if best.get("mean_tokens_per_s") is not None else ""
        findings.append(
            _finding(
                "recommendation",
                f"recommendation:task_kind={task_kind}",
                f"for task_kind {task_kind}, prefer {_combo_id(best).replace('|', ' + ')} "
                f"(success rate {best['success_rate']} over {best['samples']} run(s){speed})",
                {"builder_id": best["builder_id"], "model_id": best["model_id"],
                 "backend": best["backend"], "task_kind": task_kind},
                best["samples"],
                unanimous=best["success_rate"] == 1.0,
                evidence={
                    "summary": f"best of {len(candidates)} known_good combo(s) observed on task_kind {task_kind}",
                    "observation_ids": histories.get(_combo_id(best), {}).get("observation_ids"),
                },
                recommendation=f"route task_kind {task_kind} to {_combo_id(best).replace('|', ' + ')}",
                first_observed=histories.get(_combo_id(best), {}).get("first_observed"),
                last_observed=best.get("last_observed"),
                derived_from=["capacity-estimates.v1"],
            )
        )
    return findings


def synthesize_findings(observations: list[dict], decisions: list[dict]) -> list[dict]:
    estimates = reduce_capacity(observations)
    known_good = classify_known_good(estimates)
    known_bad = classify_known_bad(estimates)
    comparisons = reduce_prediction_comparisons(decisions, observations)
    histories = _combo_histories(observations)

    findings = (
        synthesize_known_good(known_good, histories)
        + synthesize_known_bad(known_bad, histories)
        + synthesize_uncertain(estimates, known_good, known_bad, histories)
        + synthesize_regressions(estimates, histories)
        + synthesize_prediction_bias(comparisons)
        + synthesize_recommendations(known_good, histories)
    )
    findings.sort(key=lambda finding: (finding["finding_type"], finding["finding_id"]))
    return findings


def materialize_findings(event_files: list[Path], knowledge_dir: Path) -> dict:
    observations: list[dict] = []
    decisions: list[dict] = []
    unresolved_refs = 0
    for event_file in event_files:
        events = read_events(event_file)
        extracted_observations, unresolved_observations = extract_observations(events, event_file)
        extracted_decisions, unresolved_decisions = extract_scheduler_decisions(events, event_file)
        observations.extend(extracted_observations)
        decisions.extend(extracted_decisions)
        unresolved_refs += unresolved_observations + unresolved_decisions

    findings = synthesize_findings(observations, decisions)
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["finding_type"]] = counts.get(finding["finding_type"], 0) + 1

    content = {
        "contract_version": "findings.v1",
        "observation_count": len(observations),
        "decision_count": len(decisions),
        "unresolved_refs": unresolved_refs,
        "finding_counts": counts,
        "findings": findings,
    }
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    guard_write(knowledge_dir / FINDINGS_FILE, content, make_extractor("observation_count"))
    return content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", help="events.jsonl files, or directories to scan recursively for them")
    parser.add_argument("--out", default="knowledge", help="Directory for materialized knowledge files")
    args = parser.parse_args(argv)

    event_files = collect_event_files([Path(raw) for raw in args.sources])
    content = materialize_findings(event_files, Path(args.out))
    print(json.dumps({"event_files": len(event_files), "findings": len(content["findings"]),
                      "by_type": content["finding_counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
