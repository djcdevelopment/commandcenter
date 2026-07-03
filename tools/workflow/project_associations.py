from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from tools.workflow.project_capacity import (
    _confidence,
    collect_event_files,
    extract_observations,
    extract_scheduler_decisions,
)
from tools.workflow.project_findings import _confidence_label, synthesize_findings
from tools.workflow.project_state import read_events

ASSOCIATIONS_FILE = "associations.json"
CAPABILITIES_FILE = "capabilities.json"

# Arbitrary-at-birth baselines, recorded per the arbitrary-but-traced decision rule (D18).
# Revision path: change here, append a superseding Dx with rationale.
ASSOCIATION_MIN_WORKFLOWS = 2   # the evidence-log discipline (docs/future-capability-ontology-notes.md):
                                # do not generalize from one workflow, however many samples it produced
QUALIFICATION_WINDOW_DAYS = 7.0  # evidence-days (vs corpus watermark, never wall clock) a capability
                                 # stays qualified without fresh validation

# Abstraction axes: each pattern asks "does the invariant hold while the varied fields change?"
# An association only forms when something actually varied — a repeated particular is a finding.
ASSOCIATION_PATTERNS = (
    {"pattern": "task_backend", "invariant": ("task_kind", "backend"), "varied": ("builder_id", "model_id")},
    {"pattern": "model_portability", "invariant": ("model_id",), "varied": ("builder_id", "backend")},
    {"pattern": "failure_signature", "invariant": ("failure_class", "backend"), "varied": ("builder_id", "model_id")},
)

ENVELOPE_TOKEN_METRIC = "tokens_per_s"


def _field(observation: dict, name: str) -> str | None:
    if name == "task_kind":
        return (observation.get("workload_shape") or {}).get("task_kind")
    return observation.get(name)


def _parse_ts(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _staleness_days(last_validated: str | None, watermark: str | None) -> float | None:
    if last_validated is None or watermark is None:
        return None
    return round((_parse_ts(watermark) - _parse_ts(last_validated)).total_seconds() / 86400, 2)


def evidence_watermark(observations: list[dict]) -> str | None:
    """'Now' as the organization knows it: the newest fact in the corpus, never the wall clock."""
    timestamps = [o["timestamp"] for o in observations if o.get("timestamp")]
    return max(timestamps) if timestamps else None


def analyze_buckets(observations: list[dict]) -> list[dict]:
    """Every invariant bucket the patterns produce, each carrying its own gate verdict. Buckets that
    fail a gate are kept (with the reason) — a gated bucket is a coverage gap, not a discard."""
    buckets: dict[tuple, dict] = {}
    for spec in ASSOCIATION_PATTERNS:
        for observation in observations:
            invariant = {name: _field(observation, name) for name in spec["invariant"]}
            if any(value is None for value in invariant.values()):
                continue
            key = (spec["pattern"], tuple(invariant[name] for name in spec["invariant"]))
            bucket = buckets.setdefault(key, {
                "pattern": spec["pattern"],
                "invariant": invariant,
                "varied_fields": spec["varied"],
                "observations": [],
            })
            bucket["observations"].append(observation)

    analyzed = []
    for key in sorted(buckets):
        bucket = buckets[key]
        members = bucket["observations"]
        workflows = sorted({o.get("workflow_id") or "unknown" for o in members})
        varied = {field: sorted({_field(o, field) or "unknown" for o in members})
                  for field in bucket["varied_fields"]}
        outcomes = {"success" if o.get("outcome") == "success" else "failure" for o in members}
        failure_classes = sorted({o.get("failure_class") for o in members if o.get("failure_class")})
        timestamps = sorted(o["timestamp"] for o in members if o.get("timestamp"))

        reasons = []
        if len(workflows) < ASSOCIATION_MIN_WORKFLOWS:
            reasons.append(f"all {len(members)} sample(s) come from one workflow ({workflows[0]}); "
                           "no generalization from one workflow")
        if not any(len(values) >= 2 for values in varied.values()):
            reasons.append("nothing varied under the invariant; a repeated particular is a finding, "
                           "not an abstraction")
        if len(outcomes) > 1:
            reasons.append("outcomes are mixed; contested evidence stays at the findings layer")
        if outcomes == {"failure"} and len(failure_classes) > 1:
            reasons.append("failures do not share a failure class; no single invariant to state")

        analyzed.append({
            "pattern": bucket["pattern"],
            "invariant": bucket["invariant"],
            "varied": varied,
            "workflows": workflows,
            "outcome": outcomes.pop() if len(outcomes) == 1 else "mixed",
            "failure_classes": failure_classes,
            "observations": members,
            "observation_ids": sorted(o["observation_id"] for o in members if o.get("observation_id")),
            "first_observed": timestamps[0] if timestamps else None,
            "last_observed": timestamps[-1] if timestamps else None,
            "generalizes": not reasons,
            "gated_reasons": reasons,
        })
    return analyzed


def _invariant_key(invariant: dict) -> str:
    return "|".join(f"{name}={invariant[name]}" for name in invariant)


def _member_combos(observations: list[dict]) -> list[tuple[str, str, str]]:
    return sorted({(o.get("builder_id") or "unknown", o.get("model_id") or "unknown",
                    o.get("backend") or "unknown") for o in observations})


def _supporting_findings(findings: list[dict], combos: list[tuple[str, str, str]],
                         finding_types: set[str]) -> list[str]:
    combo_ids = {"|".join(combo) for combo in combos}
    return sorted(
        finding["finding_id"] for finding in findings
        if finding["finding_type"] in finding_types
        and "|".join([finding["subject"].get("builder_id") or "unknown",
                      finding["subject"].get("model_id") or "unknown",
                      finding["subject"].get("backend") or "unknown"]) in combo_ids
    )


def synthesize_associations(observations: list[dict], findings: list[dict]) -> list[dict]:
    """The abstraction step: what remains true when the particulars change? Confidence is keyed to
    distinct workflows — an abstraction earns belief from independent contexts, not repetition."""
    associations = []
    for bucket in analyze_buckets(observations):
        if not bucket["generalizes"]:
            continue
        association_type = "success_invariant" if bucket["outcome"] == "success" else "failure_invariant"
        invariant_desc = ", ".join(f"{name}={value}" for name, value in bucket["invariant"].items())
        varied_desc = ", ".join(f"{len(values)} {field} value(s)"
                                for field, values in bucket["varied"].items() if len(values) >= 2)
        verb = "succeeds" if association_type == "success_invariant" else \
            f"fails with {bucket['failure_classes'][0]}"
        combos = _member_combos(bucket["observations"])
        finding_types = {"known_good"} if association_type == "success_invariant" else \
            {"known_bad", "regression", "uncertain"}

        associations.append({
            "contract_version": "association.v1",
            "association_id": f"{association_type}:{bucket['pattern']}:{_invariant_key(bucket['invariant'])}",
            "association_type": association_type,
            "pattern": bucket["pattern"],
            "statement": f"{invariant_desc} {verb} independent of the particulars: held across "
                         f"{len(bucket['workflows'])} workflow(s) ({varied_desc})",
            "invariant": bucket["invariant"],
            "varied": bucket["varied"],
            "workflows": bucket["workflows"],
            "confidence": _confidence_label(len(bucket["workflows"]), unanimous=True),
            "confidence_score": _confidence(len(bucket["workflows"])),
            "evidence": {
                "samples": len(bucket["observations"]),
                "workflow_count": len(bucket["workflows"]),
                "observation_ids": bucket["observation_ids"],
                "failure_class": bucket["failure_classes"][0] if association_type == "failure_invariant" else None,
                "summary": f"{len(bucket['observations'])} observation(s) across "
                           f"{len(bucket['workflows'])} workflow(s), all {bucket['outcome']}",
            },
            "supporting_findings": _supporting_findings(findings, combos, finding_types),
            "first_observed": bucket["first_observed"],
            "last_observed": bucket["last_observed"],
            "derived_from": ["capacity-observation.v1", "findings.v1"],
        })
    associations.sort(key=lambda association: association["association_id"])
    return associations


def _matches_invariant(observation: dict, invariant: dict) -> bool:
    return all(_field(observation, name) == value for name, value in invariant.items())


def _envelope(members: list[dict]) -> dict:
    contexts = [o["observed"]["context_tokens"] for o in members
                if (o.get("observed") or {}).get("context_tokens") is not None]
    speeds = [o["observed"][ENVELOPE_TOKEN_METRIC] for o in members
              if (o.get("observed") or {}).get(ENVELOPE_TOKEN_METRIC) is not None]
    rams = [o["observed"]["ram_gb_peak"] for o in members
            if (o.get("observed") or {}).get("ram_gb_peak") is not None]
    vrams = [o["observed"]["vram_gb_peak"] for o in members
             if (o.get("observed") or {}).get("vram_gb_peak") is not None]
    gpu_flags = {(o.get("workload_shape") or {}).get("requires_gpu") for o in members}
    return {
        "max_context_tokens": max(contexts) if contexts else None,
        "tokens_per_s_range": [round(min(speeds), 2), round(max(speeds), 2)] if speeds else None,
        "max_ram_gb_peak": max(rams) if rams else None,
        "max_vram_gb_peak": max(vrams) if vrams else None,
        "requires_gpu": gpu_flags.pop() if len(gpu_flags) == 1 and None not in gpu_flags else None,
    }


def synthesize_capabilities(associations: list[dict], findings: list[dict],
                            observations: list[dict]) -> list[dict]:
    """Capability.v1 is the association engine's output schema, nothing more: a success invariant
    over a class of work (task_kind) restated as an organizational qualification. Confidence and
    qualification_status are computed on every projection — there is no write path for either."""
    watermark = evidence_watermark(observations)
    capabilities = []
    for association in associations:
        if association["association_type"] != "success_invariant":
            continue
        if "task_kind" not in association["invariant"]:
            continue  # a capability is about a class of work; other invariants stay associations
        invariant = association["invariant"]
        members = [o for o in observations
                   if _matches_invariant(o, invariant) and o.get("outcome") == "success"]
        combos = _member_combos(members)
        combo_set = {combo for combo in combos}
        failure_modes = sorted({o["failure_class"] for o in observations
                                if o.get("failure_class")
                                and (o.get("builder_id") or "unknown", o.get("model_id") or "unknown",
                                     o.get("backend") or "unknown") in combo_set})
        last_validated = association["last_observed"]
        staleness = _staleness_days(last_validated, watermark)
        task_kind = invariant["task_kind"]
        scope = ", ".join(f"{name}={value}" for name, value in invariant.items() if name != "task_kind")

        capabilities.append({
            "contract_version": "capability.v1",
            "capability_id": f"capability:{_invariant_key(invariant)}",
            "name": f"{task_kind} on {scope}" if scope else task_kind,
            "statement": f"the organization can perform task_kind={task_kind} work"
                         f"{' under ' + scope if scope else ''} using any of {len(combos)} "
                         f"qualified resource combination(s)",
            "invariant": invariant,
            "derived_from_association": association["association_id"],
            "confidence": association["confidence"],
            "confidence_score": association["confidence_score"],
            "qualified_resources": [{"builder_id": builder, "model_id": model, "backend": backend}
                                    for builder, model, backend in combos],
            "operating_envelope": _envelope(members),
            "failure_modes": failure_modes,
            "evidence": {
                "samples": len(members),
                "workflow_count": len(association["workflows"]),
                "observation_ids": sorted(o["observation_id"] for o in members if o.get("observation_id")),
                "workflows": association["workflows"],
                "summary": association["evidence"]["summary"],
            },
            "supporting_findings": association["supporting_findings"],
            "first_observed": association["first_observed"],
            "last_validated": last_validated,
            "evidence_watermark": watermark,
            "staleness_days": staleness,
            "qualification_status": "qualified" if staleness is not None
                                    and staleness <= QUALIFICATION_WINDOW_DAYS else "requalification_due",
            "derived_from": ["association.v1"],
        })
    capabilities.sort(key=lambda capability: capability["capability_id"])
    return capabilities


def _gated_summary(buckets: list[dict]) -> list[dict]:
    """No silent caps: every bucket the gates held back is reported with its reason."""
    return [
        {
            "pattern": bucket["pattern"],
            "invariant": bucket["invariant"],
            "samples": len(bucket["observations"]),
            "workflows": bucket["workflows"],
            "reasons": bucket["gated_reasons"],
        }
        for bucket in buckets if not bucket["generalizes"]
    ]


def materialize_associations(event_files: list[Path], knowledge_dir: Path) -> dict:
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
    buckets = analyze_buckets(observations)
    associations = synthesize_associations(observations, findings)
    capabilities = synthesize_capabilities(associations, findings, observations)

    outputs = {
        ASSOCIATIONS_FILE: {
            "contract_version": "associations.v1",
            "observation_count": len(observations),
            "unresolved_refs": unresolved_refs,
            "evidence_watermark": evidence_watermark(observations),
            "association_count": len(associations),
            "gated_bucket_count": len(_gated_summary(buckets)),
            "associations": associations,
            "gated_buckets": _gated_summary(buckets),
        },
        CAPABILITIES_FILE: {
            "contract_version": "capabilities.v1",
            "evidence_watermark": evidence_watermark(observations),
            "capability_count": len(capabilities),
            "requalification_due": sum(1 for capability in capabilities
                                       if capability["qualification_status"] == "requalification_due"),
            "capabilities": capabilities,
        },
    }
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    for file_name, content in outputs.items():
        (knowledge_dir / file_name).write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", help="events.jsonl files, or directories to scan recursively for them")
    parser.add_argument("--out", default="knowledge", help="Directory for materialized knowledge files")
    args = parser.parse_args(argv)

    event_files = collect_event_files([Path(raw) for raw in args.sources])
    outputs = materialize_associations(event_files, Path(args.out))
    print(json.dumps({
        "event_files": len(event_files),
        "associations": outputs[ASSOCIATIONS_FILE]["association_count"],
        "gated_buckets": outputs[ASSOCIATIONS_FILE]["gated_bucket_count"],
        "capabilities": outputs[CAPABILITIES_FILE]["capability_count"],
        "requalification_due": outputs[CAPABILITIES_FILE]["requalification_due"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
