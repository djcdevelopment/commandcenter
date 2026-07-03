from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.workflow.corpus_guard import check_fixture_taint, guard_write, make_extractor

POLICY_FILE = "policy.json"
POLICY_AUDIT_FILE = "policy_audit.ndjson"
POLICY_OVERRIDES_FILE = "policy_overrides.json"

# Arbitrary-at-birth baselines, recorded per the arbitrary-but-traced decision rule (D18).
# Revision path: change here, append a superseding Dx with rationale.
BLOCK_MIN_CONFIDENCE = "high"     # known_bad below this gates softer (exploratory_only), never silently passes
PREFER_MIN_CONFIDENCE = "high"    # a preference nudges every dispatch; it must be earned
ADJUST_MIN_CONFIDENCE = "medium"  # bias corrections need at least two one-signed comparisons

EXPERIMENT_FLAG = "experiment_flag"

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _meets(confidence: str, minimum: str) -> bool:
    return _CONFIDENCE_RANK.get(confidence, 0) >= _CONFIDENCE_RANK[minimum]


def _subject_key(subject: dict) -> str:
    parts = [subject.get("builder_id"), subject.get("model_id"), subject.get("backend"),
             subject.get("task_kind"), subject.get("metric")]
    return "|".join(part or "*" for part in parts)


def _rule(effect: str, finding: dict, statement: str, override: dict | None,
          adjustment: dict | None = None) -> dict:
    return {
        "contract_version": "policy.v1",
        "policy_id": f"{effect}:{_subject_key(finding['subject'])}",
        "effect": effect,
        "statement": statement,
        "subject": dict(finding["subject"]),
        "derived_from_finding": finding["finding_id"],
        "finding_type": finding["finding_type"],
        "confidence": finding["confidence"],
        "evidence_summary": finding["evidence"]["summary"],
        "override": override,
        "adjustment": adjustment,
        "status": "active",
        "suspended_reason": None,
        "last_observed": finding.get("last_observed"),
    }


def _combo_label(subject: dict) -> str:
    return " + ".join(subject.get(field) or "any" for field in ("builder_id", "model_id", "backend"))


def _experiment_override(semantics: str) -> dict:
    return {"flag": EXPERIMENT_FLAG, "semantics": semantics}


def synthesize_policy(findings: list[dict]) -> list[dict]:
    """finding_type x confidence -> operational effect. Findings this does not map (recommendation,
    low-confidence known_good) stay advisory: they inform the scheduler's ranking, not its gates."""
    rules: list[dict] = []
    for finding in findings:
        kind = finding["finding_type"]
        confidence = finding["confidence"]
        label = _combo_label(finding["subject"])

        if kind == "known_bad":
            if _meets(confidence, BLOCK_MIN_CONFIDENCE):
                rules.append(_rule(
                    "block", finding,
                    f"block {label}: {finding['evidence']['summary']}",
                    _experiment_override("dispatch allowed only as an explicit experiment"),
                ))
            else:
                rules.append(_rule(
                    "exploratory_only", finding,
                    f"{label} failing but under-evidenced ({finding['evidence']['summary']}); exploratory work only",
                    _experiment_override("dispatch normally as an explicit experiment"),
                ))
        elif kind == "known_good" and _meets(confidence, PREFER_MIN_CONFIDENCE):
            rules.append(_rule(
                "prefer", finding,
                f"prefer {label} when compatible: {finding['evidence']['summary']}",
                None,
            ))
        elif kind == "prediction_bias" and _meets(confidence, ADJUST_MIN_CONFIDENCE):
            metric = finding["subject"]["metric"]
            correction = finding["evidence"]["mean_signed_error"]
            rules.append(_rule(
                "adjust_prediction", finding,
                f"adjust {metric} predictions for {finding['subject']['model_id']} by {correction:+}",
                None,
                adjustment={"metric": metric, "additive_correction": correction},
            ))
        elif kind == "uncertain":
            rules.append(_rule(
                "exploratory_only", finding,
                f"{label} unproven ({finding['evidence']['summary']}); exploratory work only",
                _experiment_override("dispatch normally as an explicit experiment"),
            ))
        elif kind == "regression":
            rules.append(_rule(
                "quarantine", finding,
                f"quarantine {label}: {finding['evidence']['summary']}",
                _experiment_override("re-assay runs must dispatch as explicit experiments"),
            ))

    rules.sort(key=lambda rule: (rule["effect"], rule["policy_id"]))
    return rules


def apply_overrides(rules: list[dict], overrides: list[dict]) -> list[dict]:
    """Operator override mechanism: a suspended rule stops gating but stays visible with its reason —
    the operator sees what the system believes even while choosing not to act on it."""
    by_policy_id = {override["policy_id"]: override for override in overrides}
    for rule in rules:
        override = by_policy_id.get(rule["policy_id"])
        if override and override.get("action") == "suspend":
            rule["status"] = "suspended"
            rule["suspended_reason"] = f"{override.get('author') or 'operator'}: {override.get('reason') or 'no reason recorded'}"
    return rules


def _watermark(findings: list[dict]) -> str | None:
    timestamps = [f["last_observed"] for f in findings if f.get("last_observed")]
    return max(timestamps) if timestamps else None


def diff_policy(previous_rules: list[dict], rules: list[dict]) -> list[dict]:
    previous = {rule["policy_id"]: rule for rule in previous_rules}
    current = {rule["policy_id"]: rule for rule in rules}
    changes = []
    for policy_id in sorted(set(previous) | set(current)):
        before, after = previous.get(policy_id), current.get(policy_id)
        if before is None:
            changes.append({"action": "added", "policy_id": policy_id, "effect": after["effect"],
                            "statement": after["statement"], "derived_from_finding": after["derived_from_finding"]})
        elif after is None:
            changes.append({"action": "removed", "policy_id": policy_id, "effect": before["effect"],
                            "was": before["statement"]})
        elif before != after:
            changed_fields = sorted(field for field in after if before.get(field) != after.get(field))
            changes.append({"action": "changed", "policy_id": policy_id, "fields": changed_fields,
                            "statement": after["statement"], "status": after["status"]})
    return changes


def evaluate(rules: list[dict], builder_id: str | None = None, model_id: str | None = None,
             backend: str | None = None, task_kind: str | None = None,
             experiment_flag: bool = False) -> dict:
    """The scheduler's entry point: what does policy say about dispatching this combo?
    Suspended rules never gate; they are still reported so the influence is explainable."""
    candidate = {"builder_id": builder_id, "model_id": model_id, "backend": backend, "task_kind": task_kind}

    def matches(rule: dict) -> bool:
        subject = rule["subject"]
        return all(
            subject.get(field) is None or candidate.get(field) is None or subject[field] == candidate[field]
            for field in ("builder_id", "model_id", "backend", "task_kind")
        )

    matched = [rule for rule in rules if matches(rule)]
    active = [rule for rule in matched if rule["status"] == "active"]
    gating = [rule for rule in active if rule["effect"] in {"block", "quarantine"}]
    exploratory = [rule for rule in active if rule["effect"] == "exploratory_only"]

    return {
        "allowed": experiment_flag or not gating,
        "requires_experiment_flag": bool(gating),
        "exploratory_only": bool(exploratory) and not experiment_flag,
        "preferred": any(rule["effect"] == "prefer" for rule in active),
        "prediction_adjustments": [{"policy_id": rule["policy_id"], **rule["adjustment"]} for rule in active
                                   if rule["effect"] == "adjust_prediction" and rule["adjustment"]],
        "matched_rules": [{"policy_id": rule["policy_id"], "effect": rule["effect"], "status": rule["status"],
                           "statement": rule["statement"]} for rule in matched],
    }


def load_findings(findings_path: Path) -> list[dict]:
    content = json.loads(findings_path.read_text(encoding="utf-8"))
    return content["findings"]


def load_overrides(overrides_path: Path) -> list[dict]:
    if not overrides_path.is_file():
        return []
    # utf-8-sig: this file is operator-authored, and Windows editors love a BOM.
    content = json.loads(overrides_path.read_text(encoding="utf-8-sig"))
    return content.get("overrides") or []


def materialize_policy(findings_path: Path, knowledge_dir: Path) -> dict:
    findings = load_findings(findings_path)
    rules = apply_overrides(synthesize_policy(findings),
                            load_overrides(knowledge_dir / POLICY_OVERRIDES_FILE))

    policy_path = knowledge_dir / POLICY_FILE
    previous_rules = json.loads(policy_path.read_text(encoding="utf-8"))["rules"] if policy_path.is_file() else []

    counts: dict[str, int] = {}
    for rule in rules:
        counts[rule["effect"]] = counts.get(rule["effect"], 0) + 1

    content = {
        "contract_version": "policies.v1",
        "source_findings": len(findings),
        "evidence_watermark": _watermark(findings),
        "rule_counts": counts,
        "rules": rules,
    }
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    guard_write(policy_path, content, make_extractor("source_findings"))

    # Audit trail: append-only record of every change to the rule set, stamped with the evidence
    # watermark (not wall clock) so a re-projection of the same corpus appends nothing.
    changes = diff_policy(previous_rules, rules)
    if changes:
        with (knowledge_dir / POLICY_AUDIT_FILE).open("a", encoding="utf-8") as audit:
            for change in changes:
                audit.write(json.dumps({"evidence_watermark": content["evidence_watermark"], **change}) + "\n")
    return {"content": content, "changes": changes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("findings", help="Path to a materialized findings.json")
    parser.add_argument("--out", default="knowledge", help="Directory for policy.json + audit trail")
    parser.add_argument("--allow-fixture-sources", action="store_true",
                        help="Authored escape hatch (audited): permit a fixture-derived findings file to project into the repo knowledge/ store")
    args = parser.parse_args(argv)

    # Taint for the policy projector = the input findings file itself living under a
    # fixtures/ component while --out is the repo's own knowledge dir (same rule shape).
    check_fixture_taint([Path(args.findings)], Path(args.out), allow=args.allow_fixture_sources)
    result = materialize_policy(Path(args.findings), Path(args.out))
    print(json.dumps({"rules": len(result["content"]["rules"]),
                      "by_effect": result["content"]["rule_counts"],
                      "audit_changes": len(result["changes"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
