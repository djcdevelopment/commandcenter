from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.workflow.project_state import read_events
from tools.workflow.corpus_guard import check_fixture_taint, guard_write, make_extractor

KNOWN_GOOD_MIN_SUCCESS_RATE = 0.7
KNOWN_BAD_MIN_FAILURES = 2

CAPACITY_ESTIMATES_FILE = "capacity_estimates.json"
KNOWN_GOOD_FILE = "known_good_models.json"
KNOWN_BAD_FILE = "known_bad_models.json"
PREDICTION_ACCURACY_FILE = "prediction_accuracy.json"


def _combo_key(observation: dict) -> str:
    return "|".join(
        [
            observation.get("builder_id") or "unknown",
            observation.get("model_id") or "unknown",
            observation.get("backend") or "unknown",
        ]
    )


def _resolve_artifact_path(run_dir: Path, artifact_path: str) -> Path | None:
    # Artifact refs record paths like "runs/<run_id>/artifacts/..."; resolve the
    # portion after "artifacts/" against the run directory the events file lives in.
    normalized = artifact_path.replace("\\", "/")
    marker = "artifacts/"
    index = normalized.find(marker)
    if index == -1:
        return None
    return run_dir / "artifacts" / Path(normalized[index + len(marker):])


def extract_observations(events: list[dict], events_path: Path) -> tuple[list[dict], int]:
    run_dir = events_path.parent
    observations: list[dict] = []
    unresolved = 0
    for event in events:
        for ref in event.get("artifact_refs") or []:
            if ref.get("artifact_type") != "capacity_observation":
                continue
            resolved = _resolve_artifact_path(run_dir, ref.get("path") or "")
            if resolved is not None and resolved.is_file():
                observations.append(json.loads(resolved.read_text(encoding="utf-8")))
            else:
                unresolved += 1
    return observations, unresolved


def extract_scheduler_decisions(events: list[dict], events_path: Path) -> tuple[list[dict], int]:
    run_dir = events_path.parent
    decisions: list[dict] = []
    unresolved = 0
    for event in events:
        for ref in event.get("artifact_refs") or []:
            if ref.get("artifact_type") != "scheduler_decision":
                continue
            resolved = _resolve_artifact_path(run_dir, ref.get("path") or "")
            if resolved is not None and resolved.is_file():
                decisions.append(json.loads(resolved.read_text(encoding="utf-8")))
            else:
                unresolved += 1
    return decisions, unresolved


def _confidence(samples: int) -> float:
    # Evidence-count based: 1 sample -> 0.33, 3 -> 0.6, 8 -> 0.8. Explainable, monotonic, never 1.0.
    return round(samples / (samples + 2), 2)


def reduce_capacity(observations: list[dict]) -> dict:
    combos: dict[str, dict] = {}
    for observation in observations:
        key = _combo_key(observation)
        combo = combos.setdefault(
            key,
            {
                "builder_id": observation.get("builder_id") or "unknown",
                "model_id": observation.get("model_id") or "unknown",
                "backend": observation.get("backend") or "unknown",
                "samples": 0,
                "successes": 0,
                "failures": 0,
                "failure_classes": {},
                "promotion_holds": 0,
                "tokens_per_s_samples": [],
                "max_context_tokens": None,
                "max_vram_gb_peak": None,
                "last_observed": None,
            },
        )

        combo["samples"] += 1
        if observation.get("outcome") == "success":
            combo["successes"] += 1
        else:
            combo["failures"] += 1
            failure_class = observation.get("failure_class") or observation.get("outcome") or "unknown"
            combo["failure_classes"][failure_class] = combo["failure_classes"].get(failure_class, 0) + 1

        if observation.get("promotion_status") == "held":
            combo["promotion_holds"] += 1

        observed = observation.get("observed") or {}
        if observed.get("tokens_per_s") is not None:
            combo["tokens_per_s_samples"].append(observed["tokens_per_s"])
        if observed.get("context_tokens") is not None:
            current = combo["max_context_tokens"]
            combo["max_context_tokens"] = max(current, observed["context_tokens"]) if current is not None else observed["context_tokens"]
        if observed.get("vram_gb_peak") is not None:
            current = combo["max_vram_gb_peak"]
            combo["max_vram_gb_peak"] = max(current, observed["vram_gb_peak"]) if current is not None else observed["vram_gb_peak"]

        timestamp = observation.get("timestamp")
        if timestamp and (combo["last_observed"] is None or timestamp > combo["last_observed"]):
            combo["last_observed"] = timestamp

    estimates: dict[str, dict] = {}
    for key in sorted(combos):
        combo = combos[key]
        tokens_samples = combo.pop("tokens_per_s_samples")
        combo["mean_tokens_per_s"] = round(sum(tokens_samples) / len(tokens_samples), 2) if tokens_samples else None
        combo["success_rate"] = round(combo["successes"] / combo["samples"], 2)
        combo["confidence"] = _confidence(combo["samples"])
        estimates[key] = combo
    return estimates


def classify_known_good(estimates: dict) -> list[dict]:
    entries = []
    for key in sorted(estimates):
        combo = estimates[key]
        if combo["successes"] >= 1 and combo["success_rate"] >= KNOWN_GOOD_MIN_SUCCESS_RATE:
            entries.append(
                {
                    "builder_id": combo["builder_id"],
                    "model_id": combo["model_id"],
                    "backend": combo["backend"],
                    "success_rate": combo["success_rate"],
                    "samples": combo["samples"],
                    "mean_tokens_per_s": combo["mean_tokens_per_s"],
                    "promotion_holds": combo["promotion_holds"],
                    "confidence": combo["confidence"],
                    "last_observed": combo["last_observed"],
                    "reason": f"{combo['successes']}/{combo['samples']} successful runs (success_rate {combo['success_rate']})",
                }
            )
    return entries


def classify_known_bad(estimates: dict) -> list[dict]:
    entries = []
    for key in sorted(estimates):
        combo = estimates[key]
        if combo["successes"] == 0 and combo["failures"] >= KNOWN_BAD_MIN_FAILURES:
            dominant_class = max(combo["failure_classes"], key=lambda name: combo["failure_classes"][name]) if combo["failure_classes"] else "unknown"
            entries.append(
                {
                    "builder_id": combo["builder_id"],
                    "model_id": combo["model_id"],
                    "backend": combo["backend"],
                    "failures": combo["failures"],
                    "samples": combo["samples"],
                    "dominant_failure_class": dominant_class,
                    "failure_classes": combo["failure_classes"],
                    "confidence": combo["confidence"],
                    "last_observed": combo["last_observed"],
                    "reason": f"{combo['failures']}/{combo['samples']} failed runs, zero successes (dominant: {dominant_class})",
                }
            )
    return entries


def _workload_shape_key(workload_shape: dict | None) -> str:
    shape = workload_shape or {}
    return "|".join(
        [
            shape.get("task_kind") or "unknown",
            str(shape.get("estimated_context_tokens")) if shape.get("estimated_context_tokens") is not None else "unknown",
            str(shape.get("requires_gpu")) if shape.get("requires_gpu") is not None else "unknown",
        ]
    )


def _observed_assay_outcome(observation: dict) -> str | None:
    outcome = observation.get("outcome")
    if outcome is None:
        return None
    return "passed" if outcome == "success" else "failed"


def _compare_numeric(predicted: float | None, observed: float | None) -> dict | None:
    if predicted is None or observed is None:
        return None
    error = observed - predicted
    absolute_error = abs(error)
    percent_error = None if predicted == 0 else round((error / predicted) * 100, 2)
    return {
        "predicted": predicted,
        "observed": observed,
        "error": round(error, 2),
        "absolute_error": round(absolute_error, 2),
        "percent_error": percent_error,
    }


def reduce_prediction_comparisons(decisions: list[dict], observations: list[dict]) -> list[dict]:
    decisions_by_id = {decision["decision_id"]: decision for decision in decisions}
    comparisons: list[dict] = []

    for observation in observations:
        decision_id = observation.get("decision_id")
        decision = decisions_by_id.get(decision_id)
        predictions = (decision or {}).get("predictions") or {}
        observed = observation.get("observed") or {}

        comparison = {
            "decision_id": decision_id,
            "observation_id": observation.get("observation_id"),
            "workflow_id": observation.get("workflow_id"),
            "run_id": observation.get("run_id"),
            "builder_id": observation.get("builder_id"),
            "model_id": observation.get("model_id"),
            "backend": observation.get("backend"),
            "workload_shape_key": _workload_shape_key(observation.get("workload_shape") or (decision or {}).get("workload_shape")),
            "timestamp": observation.get("timestamp"),
            "confidence": predictions.get("confidence"),
            "prediction_source": predictions.get("prediction_source"),
            "prediction_present": bool(predictions),
            "metrics": {
                "expected_ttft_ms": _compare_numeric(predictions.get("expected_ttft_ms"), None if observed.get("ttft_s") is None else round(observed["ttft_s"] * 1000, 2)),
                "expected_generation_tokens_per_second": _compare_numeric(predictions.get("expected_generation_tokens_per_second"), observed.get("tokens_per_s")),
                "expected_peak_ram_mb": _compare_numeric(predictions.get("expected_peak_ram_mb"), None if observed.get("ram_gb_peak") is None else round(observed["ram_gb_peak"] * 1024, 2)),
                "expected_peak_vram_mb": _compare_numeric(predictions.get("expected_peak_vram_mb"), None if observed.get("vram_gb_peak") is None else round(observed["vram_gb_peak"] * 1024, 2)),
            },
            "outcomes": {
                "assay": {
                    "predicted": predictions.get("expected_assay_outcome"),
                    "observed": _observed_assay_outcome(observation),
                },
                "promotion": {
                    "predicted": predictions.get("expected_promotion_status"),
                    "observed": observation.get("promotion_status"),
                },
            },
        }
        for outcome_name, values in comparison["outcomes"].items():
            predicted = values["predicted"]
            observed_value = values["observed"]
            values["matched"] = None if predicted is None or observed_value is None else predicted == observed_value

        comparisons.append(comparison)

    comparisons.sort(key=lambda item: (item["timestamp"] or "", item["decision_id"] or "", item["observation_id"] or ""))
    return comparisons


def _new_group_summary() -> dict:
    return {
        "sample_count": 0,
        "last_updated": None,
        "confidence_sum": 0.0,
        "confidence_count": 0,
        "metrics": {},
        "outcomes": {
            "assay": {"matched": 0, "mismatched": 0, "unknown": 0},
            "promotion": {"matched": 0, "mismatched": 0, "unknown": 0},
        },
    }


def _accumulate_metric(summary: dict, metric_name: str, metric: dict | None) -> None:
    if metric is None:
        return
    target = summary["metrics"].setdefault(metric_name, {"sample_count": 0, "absolute_error_sum": 0.0, "error_sum": 0.0})
    target["sample_count"] += 1
    target["absolute_error_sum"] += metric["absolute_error"]
    target["error_sum"] += metric["error"]


def _accumulate_outcome(summary: dict, outcome_name: str, matched: bool | None) -> None:
    bucket = summary["outcomes"][outcome_name]
    if matched is None:
        bucket["unknown"] += 1
    elif matched:
        bucket["matched"] += 1
    else:
        bucket["mismatched"] += 1


def summarize_prediction_accuracy(comparisons: list[dict]) -> dict:
    groups = {
        "overall": {"overall": _new_group_summary()},
        "per_builder": {},
        "per_model": {},
        "per_workload_shape": {},
    }

    def touch(group_name: str, key: str) -> dict:
        return groups[group_name].setdefault(key, _new_group_summary())

    for comparison in comparisons:
        targets = [
            groups["overall"]["overall"],
            touch("per_builder", comparison.get("builder_id") or "unknown"),
            touch("per_model", comparison.get("model_id") or "unknown"),
            touch("per_workload_shape", comparison.get("workload_shape_key") or "unknown"),
        ]
        for summary in targets:
            summary["sample_count"] += 1
            timestamp = comparison.get("timestamp")
            if timestamp and (summary["last_updated"] is None or timestamp > summary["last_updated"]):
                summary["last_updated"] = timestamp
            if comparison.get("confidence") is not None:
                summary["confidence_sum"] += comparison["confidence"]
                summary["confidence_count"] += 1
            for metric_name, metric in comparison["metrics"].items():
                _accumulate_metric(summary, metric_name, metric)
            for outcome_name, values in comparison["outcomes"].items():
                _accumulate_outcome(summary, outcome_name, values["matched"])

    for scope in groups.values():
        for key, summary in scope.items():
            summary["prediction_confidence"] = round(summary["confidence_sum"] / summary["confidence_count"], 2) if summary["confidence_count"] else None
            del summary["confidence_sum"]
            del summary["confidence_count"]
            for metric_name, metric_summary in summary["metrics"].items():
                samples = metric_summary["sample_count"]
                metric_summary["mean_absolute_error"] = round(metric_summary["absolute_error_sum"] / samples, 2) if samples else None
                metric_summary["prediction_bias"] = round(metric_summary["error_sum"] / samples, 2) if samples else None
                del metric_summary["absolute_error_sum"]
                del metric_summary["error_sum"]

    return groups


def collect_event_files(paths: list[Path]) -> list[Path]:
    event_files: list[Path] = []
    for path in paths:
        if path.is_dir():
            event_files.extend(sorted(path.rglob("events.jsonl")))
        else:
            event_files.append(path)
    return event_files


def materialize_knowledge(event_files: list[Path], knowledge_dir: Path) -> dict:
    observations: list[dict] = []
    unresolved_refs = 0
    decisions: list[dict] = []
    unresolved_decision_refs = 0
    for event_file in event_files:
        extracted, unresolved = extract_observations(read_events(event_file), event_file)
        observations.extend(extracted)
        unresolved_refs += unresolved
        extracted_decisions, unresolved_decisions = extract_scheduler_decisions(read_events(event_file), event_file)
        decisions.extend(extracted_decisions)
        unresolved_decision_refs += unresolved_decisions

    estimates = reduce_capacity(observations)
    known_good = classify_known_good(estimates)
    known_bad = classify_known_bad(estimates)
    comparisons = reduce_prediction_comparisons(decisions, observations)
    prediction_accuracy = summarize_prediction_accuracy(comparisons)

    knowledge_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        CAPACITY_ESTIMATES_FILE: {
            "contract_version": "capacity-estimates.v1",
            "observation_count": len(observations),
            "unresolved_observation_refs": unresolved_refs,
            "combos": estimates,
        },
        KNOWN_GOOD_FILE: {
            "contract_version": "known-good-models.v1",
            "entries": known_good,
        },
        KNOWN_BAD_FILE: {
            "contract_version": "known-bad-models.v1",
            "entries": known_bad,
        },
        PREDICTION_ACCURACY_FILE: {
            "contract_version": "prediction-accuracy.v1",
            "decision_count": len(decisions),
            "observation_count": len(observations),
            "comparison_count": len(comparisons),
            "unresolved_decision_refs": unresolved_decision_refs,
            "comparisons": comparisons,
            "summary": prediction_accuracy,
        },
    }
    guarded = {
        CAPACITY_ESTIMATES_FILE: make_extractor("observation_count"),
        PREDICTION_ACCURACY_FILE: make_extractor("observation_count"),
    }
    for file_name, content in outputs.items():
        target = knowledge_dir / file_name
        extractor = guarded.get(file_name)
        if extractor is None:
            # known_good/known_bad_models.json carry neither a watermark nor a count, so there
            # is no monotonic quantity to guard on — left unguarded; see DECISION-NEEDED-A2.md.
            target.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
        else:
            guard_write(target, content, extractor)
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
    outputs = materialize_knowledge(event_files, Path(args.out))
    summary = {
        "event_files": len(event_files),
        "observations": outputs[CAPACITY_ESTIMATES_FILE]["observation_count"],
        "combos": len(outputs[CAPACITY_ESTIMATES_FILE]["combos"]),
        "known_good": len(outputs[KNOWN_GOOD_FILE]["entries"]),
        "known_bad": len(outputs[KNOWN_BAD_FILE]["entries"]),
        "prediction_comparisons": outputs[PREDICTION_ACCURACY_FILE]["comparison_count"],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
