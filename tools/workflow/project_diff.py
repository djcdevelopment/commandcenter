"""
project_diff.py — deterministic diff between two bounded projection snapshots.

SEMANTICS: diff what EXISTS at each bound. An entity present at t1 and absent at t2 is
retired/closed regardless of intermediate flapping between the bounds. Conversely, an entity
absent at t1 and present at t2 is formed/appeared/opened regardless of when between t1 and t2
it actually arose. The diff is computed from two snapshots, not from event history.

This is a demand-computed projection — never stored under knowledge/. Calling diff_projections()
or the CLI never writes to knowledge/; the output goes only to the caller-chosen path.

Transition vocabulary v1:
  association_formed          — association_id absent at t1, present at t2
  association_retired         — association_id present at t1, absent at t2
  capability_appeared         — capability_id absent at t1, present at t2
  qualification_transition    — capability present at both bounds, qualification_status changed
                                (from_status/to_status are values from the real enum:
                                 qualified | requalification_due)
  gap_opened                  — gap_id absent at t1, present at t2
  gap_closed                  — gap_id present at t1, absent at t2
  finding_confidence_moved    — finding_id present at both bounds, confidence_score (float) changed

Transition ID: sha256(type + subject_id + t1 + t2), first 16 hex chars. Stable and reproducible:
same corpus + same t1/t2 + same transition type/subject = same ID across any number of runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.workflow.project_associations import (
    synthesize_associations,
    synthesize_capabilities,
)
from tools.workflow.project_capacity import (
    collect_event_files,
    extract_observations,
    extract_scheduler_decisions,
)
from tools.workflow.project_coverage import synthesize_coverage
from tools.workflow.project_findings import synthesize_findings
from tools.workflow.project_state import read_events


def _transition_id(transition_type: str, subject_id: str, t1: str, t2: str) -> str:
    """Stable deterministic ID: sha256(type + subject_id + t1 + t2), first 16 hex chars."""
    payload = f"{transition_type}{subject_id}{t1}{t2}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _snapshot_at(observations: list[dict], decisions: list[dict], as_of: str) -> dict:
    """Bounded projection snapshot: all entities that existed as of the given ISO-8601 bound."""
    obs = [o for o in observations if (o.get("timestamp") or "") <= as_of]
    dec = [d for d in decisions if (d.get("timestamp") or "") <= as_of]
    findings = synthesize_findings(obs, dec)
    associations = synthesize_associations(obs, findings)
    capabilities = synthesize_capabilities(associations, findings, obs)
    gaps = synthesize_coverage(obs, dec, capabilities)
    return {
        "findings": {f["finding_id"]: f for f in findings},
        "associations": {a["association_id"]: a for a in associations},
        "capabilities": {c["capability_id"]: c for c in capabilities},
        "gaps": {g["gap_id"]: g for g in gaps},
    }


def _count_by_type(transitions: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in transitions:
        counts[t["transition_type"]] = counts.get(t["transition_type"], 0) + 1
    return counts


def diff_projections(sources: list[Path], t1: str, t2: str) -> dict:
    """
    Compute transitions between the bounded projection at t1 and at t2.

    sources: events.jsonl files or directories (same convention as collect_event_files).
    t1, t2:  ISO-8601 strings. Typically t1 <= t2; equal bounds produce zero transitions.

    Returns a transitions document dict. Never writes to knowledge/.
    """
    event_files = collect_event_files(sources)

    observations: list[dict] = []
    decisions: list[dict] = []
    for event_file in event_files:
        events = read_events(event_file)
        obs, _ = extract_observations(events, event_file)
        dec, _ = extract_scheduler_decisions(events, event_file)
        observations.extend(obs)
        decisions.extend(dec)

    snap1 = _snapshot_at(observations, decisions, t1)
    snap2 = _snapshot_at(observations, decisions, t2)

    transitions: list[dict] = []

    # Associations: formed (new at t2) or retired (gone from t2)
    a_ids1 = set(snap1["associations"])
    a_ids2 = set(snap2["associations"])
    for aid in sorted(a_ids2 - a_ids1):
        transitions.append({
            "transition_id": _transition_id("association_formed", aid, t1, t2),
            "transition_type": "association_formed",
            "subject_id": aid,
        })
    for aid in sorted(a_ids1 - a_ids2):
        transitions.append({
            "transition_id": _transition_id("association_retired", aid, t1, t2),
            "transition_type": "association_retired",
            "subject_id": aid,
        })

    # Capabilities: appeared (new at t2) or qualification status changed
    c_ids1 = set(snap1["capabilities"])
    c_ids2 = set(snap2["capabilities"])
    for cid in sorted(c_ids2 - c_ids1):
        transitions.append({
            "transition_id": _transition_id("capability_appeared", cid, t1, t2),
            "transition_type": "capability_appeared",
            "subject_id": cid,
            "qualification_status": snap2["capabilities"][cid]["qualification_status"],
        })
    for cid in sorted(c_ids1 & c_ids2):
        from_status = snap1["capabilities"][cid]["qualification_status"]
        to_status = snap2["capabilities"][cid]["qualification_status"]
        if from_status != to_status:
            transitions.append({
                "transition_id": _transition_id("qualification_transition", cid, t1, t2),
                "transition_type": "qualification_transition",
                "subject_id": cid,
                "from_status": from_status,
                "to_status": to_status,
            })

    # Gaps: opened (new at t2) or closed (gone from t2)
    g_ids1 = set(snap1["gaps"])
    g_ids2 = set(snap2["gaps"])
    for gid in sorted(g_ids2 - g_ids1):
        transitions.append({
            "transition_id": _transition_id("gap_opened", gid, t1, t2),
            "transition_type": "gap_opened",
            "subject_id": gid,
        })
    for gid in sorted(g_ids1 - g_ids2):
        transitions.append({
            "transition_id": _transition_id("gap_closed", gid, t1, t2),
            "transition_type": "gap_closed",
            "subject_id": gid,
        })

    # Findings: confidence_score changed for findings present at both bounds
    f_ids1 = set(snap1["findings"])
    f_ids2 = set(snap2["findings"])
    for fid in sorted(f_ids1 & f_ids2):
        before = snap1["findings"][fid]["confidence_score"]
        after = snap2["findings"][fid]["confidence_score"]
        if before != after:
            transitions.append({
                "transition_id": _transition_id("finding_confidence_moved", fid, t1, t2),
                "transition_type": "finding_confidence_moved",
                "subject_id": fid,
                "before": before,
                "after": after,
            })

    transitions.sort(key=lambda t: (t["transition_type"], t["subject_id"]))

    obs1_count = len([o for o in observations if (o.get("timestamp") or "") <= t1])
    obs2_count = len([o for o in observations if (o.get("timestamp") or "") <= t2])

    return {
        "contract_version": "snapshot-diff.v1",
        "t1": t1,
        "t2": t2,
        "event_files": len(event_files),
        "observation_count_at_t1": obs1_count,
        "observation_count_at_t2": obs2_count,
        "transition_count": len(transitions),
        "by_type": _count_by_type(transitions),
        "transitions": transitions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diff two bounded projection snapshots and emit a transitions document."
    )
    parser.add_argument("sources", nargs="+",
                        help="events.jsonl files or directories to scan for them")
    parser.add_argument("--t1", required=True,
                        help="ISO-8601 start bound (inclusive): entities at this bound = 'before'")
    parser.add_argument("--t2", required=True,
                        help="ISO-8601 end bound (inclusive): entities at this bound = 'after'")
    parser.add_argument("--out", required=True,
                        help="Output file path for the transitions document (never under knowledge/)")
    args = parser.parse_args(argv)

    result = diff_projections([Path(s) for s in args.sources], args.t1, args.t2)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "t1": args.t1,
        "t2": args.t2,
        "transitions": result["transition_count"],
        "by_type": result["by_type"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
