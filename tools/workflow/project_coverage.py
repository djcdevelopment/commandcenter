from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.workflow.project_associations import (
    ASSOCIATION_MIN_WORKFLOWS,
    analyze_buckets,
    evidence_watermark,
    synthesize_associations,
    synthesize_capabilities,
)
from tools.workflow.project_capacity import (
    collect_event_files,
    extract_observations,
    extract_scheduler_decisions,
)
from tools.workflow.project_findings import synthesize_findings
from tools.workflow.project_state import read_events
from tools.workflow.corpus_guard import guard_write, make_extractor

COVERAGE_FILE = "coverage.json"

# The metrics a combo is expected to report eventually; a combo that has never reported one is a
# calibration blind spot (predictions of it can never be checked).
COVERAGE_METRICS = ("ttft_s", "tokens_per_s", "ram_gb_peak", "vram_gb_peak")


def _combo_of(record: dict) -> tuple[str, str, str]:
    return (record.get("builder_id") or "unknown", record.get("model_id") or "unknown",
            record.get("backend") or "unknown")


def _combo_label(combo: tuple[str, str, str]) -> str:
    return " + ".join(combo)


def _combo_subject(combo: tuple[str, str, str]) -> dict:
    return {"builder_id": combo[0], "model_id": combo[1], "backend": combo[2], "task_kind": None,
            "invariant": None, "missing_metrics": None, "capability_id": None}


def _gap(gap_type: str, gap_id: str, statement: str, subject: dict, samples: int,
         summary: str, proposed_experiment_type: str | None, workflows: list[str] | None = None,
         last_observed: str | None = None) -> dict:
    return {
        "contract_version": "coverage-gap.v1",
        "gap_id": gap_id,
        "gap_type": gap_type,
        "statement": statement,
        "subject": subject,
        "evidence": {"samples": samples, "workflows": workflows, "summary": summary},
        "proposed_experiment_type": proposed_experiment_type,
        "last_observed": last_observed,
    }


def _unobserved_combos(observations: list[dict], decisions: list[dict]) -> list[dict]:
    """Combos the scheduler has reasoned about but never once observed: their qualification is
    unknown, not bad — and unknown is a gap, not a verdict."""
    observed = {_combo_of(observation) for observation in observations}
    considered: dict[tuple[str, str, str], int] = {}
    for decision in decisions:
        for candidate in decision.get("candidates_considered") or []:
            combo = _combo_of(candidate)
            if combo not in observed:
                considered[combo] = considered.get(combo, 0) + 1

    gaps = []
    for combo in sorted(considered):
        label = _combo_label(combo)
        gaps.append(_gap(
            "unobserved_combo", f"unobserved_combo:{'|'.join(combo)}",
            f"the scheduler has considered {label} in {considered[combo]} decision(s) but no observation "
            "of it exists; its qualification is unknown, not bad",
            _combo_subject(combo),
            samples=0,
            summary=f"considered {considered[combo]} time(s), observed 0 times",
            proposed_experiment_type="coverage_probe",
        ))
    return gaps


def _single_workflow_buckets(buckets: list[dict]) -> list[dict]:
    """Evidence that exists but cannot generalize: the association engine's own gate, surfaced as
    demand for one more distinct workflow."""
    gaps = []
    for bucket in buckets:
        if bucket["generalizes"] or len(bucket["workflows"]) >= ASSOCIATION_MIN_WORKFLOWS:
            continue
        invariant_desc = ", ".join(f"{name}={value}" for name, value in bucket["invariant"].items())
        gaps.append(_gap(
            "single_workflow_evidence",
            f"single_workflow_evidence:{bucket['pattern']}:"
            + "|".join(f"{name}={value}" for name, value in bucket["invariant"].items()),
            f"'{invariant_desc}' has {len(bucket['observations'])} consistent sample(s) but all from "
            f"{bucket['workflows'][0]}; a second distinct workflow would let the association engine "
            "generalize it",
            {"builder_id": None, "model_id": None, "backend": bucket["invariant"].get("backend"),
             "task_kind": bucket["invariant"].get("task_kind"), "invariant": bucket["invariant"],
             "missing_metrics": None, "capability_id": None},
            samples=len(bucket["observations"]),
            summary=f"{len(bucket['observations'])} sample(s), 1 workflow "
                    f"(need {ASSOCIATION_MIN_WORKFLOWS})",
            proposed_experiment_type="coverage_probe",
            workflows=bucket["workflows"],
            last_observed=bucket["last_observed"],
        ))
    return gaps


def _unmeasured_metrics(observations: list[dict]) -> list[dict]:
    """Combos that run but never report a metric: predictions of that metric can never be
    calibrated, so the calibration layer is blind there by construction."""
    seen: dict[tuple[str, str, str], dict] = {}
    for observation in observations:
        combo = seen.setdefault(_combo_of(observation), {"metrics": set(), "samples": 0, "last": None})
        combo["samples"] += 1
        observed = observation.get("observed") or {}
        combo["metrics"].update(metric for metric in COVERAGE_METRICS if observed.get(metric) is not None)
        timestamp = observation.get("timestamp")
        if timestamp and (combo["last"] is None or timestamp > combo["last"]):
            combo["last"] = timestamp

    gaps = []
    for combo in sorted(seen):
        missing = [metric for metric in COVERAGE_METRICS if metric not in seen[combo]["metrics"]]
        if not missing:
            continue
        subject = _combo_subject(combo)
        subject["missing_metrics"] = missing
        gaps.append(_gap(
            "unmeasured_metrics", f"unmeasured_metrics:{'|'.join(combo)}",
            f"{_combo_label(combo)} has run {seen[combo]['samples']} time(s) but never reported: "
            f"{', '.join(missing)}; predictions of these metrics can never be calibrated",
            subject,
            samples=seen[combo]["samples"],
            summary=f"{len(missing)}/{len(COVERAGE_METRICS)} metrics never measured",
            proposed_experiment_type="coverage_probe",
            last_observed=seen[combo]["last"],
        ))
    return gaps


def _stale_capabilities(capabilities: list[dict]) -> list[dict]:
    """The coverage view of qualification decay. The experiment demand itself comes from the
    qualification_run candidate, not from here — this row keeps the blind spot visible."""
    gaps = []
    for capability in capabilities:
        if capability["qualification_status"] != "requalification_due":
            continue
        subject = {"builder_id": None, "model_id": None,
                   "backend": capability["invariant"].get("backend"),
                   "task_kind": capability["invariant"].get("task_kind"),
                   "invariant": None, "missing_metrics": None,
                   "capability_id": capability["capability_id"]}
        gaps.append(_gap(
            "stale_capability", f"stale_capability:{capability['capability_id']}",
            f"capability '{capability['name']}' was last validated {capability['staleness_days']} "
            "evidence-day(s) ago; dispatch decisions that rely on it rest on old truth",
            subject,
            samples=capability["evidence"]["samples"],
            summary=f"{capability['staleness_days']} evidence-days stale",
            proposed_experiment_type="qualification_run",
            workflows=capability["evidence"]["workflows"],
            last_observed=capability["last_validated"],
        ))
    return gaps


def synthesize_coverage(observations: list[dict], decisions: list[dict],
                        capabilities: list[dict]) -> list[dict]:
    gaps = (
        _unobserved_combos(observations, decisions)
        + _single_workflow_buckets(analyze_buckets(observations))
        + _unmeasured_metrics(observations)
        + _stale_capabilities(capabilities)
    )
    gaps.sort(key=lambda gap: (gap["gap_type"], gap["gap_id"]))
    return gaps


def materialize_coverage(event_files: list[Path], knowledge_dir: Path) -> dict:
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
    associations = synthesize_associations(observations, findings)
    capabilities = synthesize_capabilities(associations, findings, observations)
    gaps = synthesize_coverage(observations, decisions, capabilities)

    counts: dict[str, int] = {}
    for gap in gaps:
        counts[gap["gap_type"]] = counts.get(gap["gap_type"], 0) + 1

    content = {
        "contract_version": "coverage.v1",
        "observation_count": len(observations),
        "decision_count": len(decisions),
        "unresolved_refs": unresolved_refs,
        "evidence_watermark": evidence_watermark(observations),
        "gap_counts": counts,
        "gaps": gaps,
    }
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    guard_write(knowledge_dir / COVERAGE_FILE, content, make_extractor("observation_count"))
    return content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", help="events.jsonl files, or directories to scan recursively for them")
    parser.add_argument("--out", default="knowledge", help="Directory for materialized knowledge files")
    args = parser.parse_args(argv)

    event_files = collect_event_files([Path(raw) for raw in args.sources])
    content = materialize_coverage(event_files, Path(args.out))
    print(json.dumps({"event_files": len(event_files), "gaps": len(content["gaps"]),
                      "by_type": content["gap_counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
