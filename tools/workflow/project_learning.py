"""project_learning.py — Stream Ga: experiment worth realized per hour.

Formulation E from LEARNING-RATE-CRITIQUE.html (the unanimously approved one):
  numerator   = Σ worth_points over experiments where belief_changed OR gate_before != gate_after,
                using the authored overlay in knowledge/candidate_worth.json.
  denominator = Σ dispatch durations (builder.assigned → first close event) from events.jsonl.

Dispatch-use tripwire:  "dispatch_use_forbidden": true is a machine-readable field in every report.
This is a REPORTING projection. It must NOT feed candidate ranking or the scheduler.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tools.workflow.project_capacity import collect_event_files
from tools.workflow.project_experiments import (
    extract_experiment_plans,
    synthesize_results,
)
from tools.workflow.project_capacity import (
    extract_observations,
    extract_scheduler_decisions,
)
from tools.workflow.project_findings import synthesize_findings
from tools.workflow.project_policy import synthesize_policy
from tools.workflow.project_state import read_events

REPORT_FILE = "worth_realized.json"

# D18 traced threshold constants
DISPATCH_OPEN_EVENT = "builder.assigned"
DISPATCH_CLOSE_EVENTS = frozenset({
    "promotion.approved",
    "promotion.rejected",
    "promotion.held",
    "retrospective.created",
})

WORTH_OVERLAY_PATH = "knowledge/candidate_worth.json"


# ---------------------------------------------------------------------------
# Authored worth overlay loader
# ---------------------------------------------------------------------------

def load_worth_overlay(overlay_path: str | Path) -> dict[str, dict]:
    """Return {candidate_id: {worth_points, reason, author}} from the authored overlay.
    Empty entries list → empty dict (all candidates unpriced)."""
    path = Path(overlay_path)
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        entry["candidate_id"]: entry
        for entry in (raw.get("entries") or [])
    }


# ---------------------------------------------------------------------------
# Dispatch duration extractor
# ---------------------------------------------------------------------------

def _extract_durations_from_events(events: list[dict]) -> list[dict]:
    """Return one record per builder-assigned dispatch found in an events list.

    A dispatch = builder.assigned event; duration closes on the FIRST of
    promotion.approved / promotion.rejected / promotion.held / retrospective.created,
    else on the run's last event timestamp.

    Returns list of:
        {run_id, node_id, open_ts, close_ts, duration_s, close_reason}

    Runs lacking builder.assigned are not yielded; they are reported separately.
    """
    opens: dict[str, dict] = {}  # run_id → {ts, node_id}
    all_ts: dict[str, list[str]] = {}  # run_id → [ts]
    closes: dict[str, tuple[str, str]] = {}  # run_id → (ts, reason)

    for ev in events:
        run_id = ev.get("run_id") or ""
        ts = ev.get("timestamp") or ""
        event_type = ev.get("event_type") or ""

        if ts:
            all_ts.setdefault(run_id, []).append(ts)

        if event_type == DISPATCH_OPEN_EVENT:
            actor = ev.get("actor") or {}
            node_id = actor.get("id") or "unknown"
            if run_id not in opens:
                opens[run_id] = {"ts": ts, "node_id": node_id}

        elif event_type in DISPATCH_CLOSE_EVENTS:
            if run_id not in closes:
                closes[run_id] = (ts, event_type)

    records = []
    for run_id, open_info in opens.items():
        open_ts = open_info["ts"]
        node_id = open_info["node_id"]
        if run_id in closes:
            close_ts, close_reason = closes[run_id]
        else:
            # Fall back to last event in this run
            run_timestamps = [t for t in (all_ts.get(run_id) or []) if t >= open_ts]
            if run_timestamps:
                close_ts = max(run_timestamps)
                close_reason = "last_event_fallback"
            else:
                close_ts = open_ts
                close_reason = "no_close_event_or_later_ts"

        # Parse ISO timestamps — stdlib fromisoformat handles Z suffix in 3.11+;
        # for 3.10 compatibility replace Z→+00:00.
        try:
            from datetime import datetime, timezone
            open_dt = datetime.fromisoformat(open_ts.replace("Z", "+00:00"))
            close_dt = datetime.fromisoformat(close_ts.replace("Z", "+00:00"))
            duration_s = max(0.0, (close_dt - open_dt).total_seconds())
        except (ValueError, TypeError):
            duration_s = 0.0

        records.append({
            "run_id": run_id,
            "node_id": node_id,
            "open_ts": open_ts,
            "close_ts": close_ts,
            "duration_s": duration_s,
            "close_reason": close_reason,
        })
    return records


def extract_all_durations(event_files: list[Path]) -> tuple[list[dict], list[dict]]:
    """Scan all event files; return (dispatches_with_duration, excluded_runs).

    excluded_runs: list of {run_id, reason} for runs that lacked builder.assigned.
    """
    all_dispatches: list[dict] = []
    run_ids_with_assigned: set[str] = set()
    all_run_ids: set[str] = set()

    for ef in event_files:
        events = read_events(ef)
        for ev in events:
            run_id = ev.get("run_id") or ""
            if run_id:
                all_run_ids.add(run_id)
            if ev.get("event_type") == DISPATCH_OPEN_EVENT:
                run_ids_with_assigned.add(run_id)
        dispatches = _extract_durations_from_events(events)
        all_dispatches.extend(dispatches)

    excluded = [
        {"run_id": rid, "reason": "no builder.assigned event — excluded from denominator"}
        for rid in sorted(all_run_ids - run_ids_with_assigned)
    ]
    return all_dispatches, excluded


# ---------------------------------------------------------------------------
# Window filtering helpers
# ---------------------------------------------------------------------------

def _in_window(ts: str | None, t1: str | None, t2: str | None) -> bool:
    if not ts:
        return False
    if t1 and ts < t1:
        return False
    if t2 and ts > t2:
        return False
    return True


# ---------------------------------------------------------------------------
# Main projector
# ---------------------------------------------------------------------------

def worth_realized(
    sources: list[str | Path],
    t1: str | None = None,
    t2: str | None = None,
    group_by: str = "node",
    worth_overlay_path: str | Path = WORTH_OVERLAY_PATH,
) -> dict:
    """Compute worth_realized per hour for the given source directories / event files.

    Parameters
    ----------
    sources: list of paths to directories or events.jsonl files
    t1, t2: ISO 8601 window boundaries (inclusive). None = open bound.
    group_by: "node" (builder id from builder.assigned actor.id). "economy" is unavailable in v1.
    worth_overlay_path: path to the authored candidate_worth.json overlay.

    Returns a report dict. "dispatch_use_forbidden": true is a machine-readable tripwire.
    """
    if group_by != "node":
        return {
            "error": f"group_by={group_by!r} is not implemented",
            "supported": ["node"],
            "dispatch_use_forbidden": True,
        }

    event_files = collect_event_files([Path(s) for s in sources])
    worth_overlay = load_worth_overlay(worth_overlay_path)

    # Collect all observations + decisions + plans across the source tree
    all_observations: list[dict] = []
    all_decisions: list[dict] = []
    all_plans: list[dict] = []

    for ef in event_files:
        events = read_events(ef)
        obs, _ = extract_observations(events, ef)
        decs, _ = extract_scheduler_decisions(events, ef)
        plans, _ = extract_experiment_plans(events, ef)
        all_observations.extend(obs)
        all_decisions.extend(decs)
        all_plans.extend(plans)

    # Derive experiment results (synthesize_results handles belief before/after)
    findings = synthesize_findings(all_observations, all_decisions)
    watermark = _evidence_watermark(all_observations)
    results = synthesize_results(all_plans, all_decisions, all_observations)

    # Filter results to window
    windowed_results = [
        r for r in results
        if _in_window(r.get("timestamp"), t1, t2)
    ]

    # Load worth overlay — build lookup by candidate_id
    priced_ids = set(worth_overlay.keys())

    # Numerator: belief_changed OR gate transition, candidate must be priced
    unpriced_candidates: list[str] = []
    realized_entries: list[dict] = []

    seen_experiment_ids: set[str] = set()
    for r in windowed_results:
        exp_id = r.get("experiment_id") or ""
        if exp_id in seen_experiment_ids:
            continue
        seen_experiment_ids.add(exp_id)

        # Map experiment → candidate via experiment_id convention
        # The candidate_id is the canonical key in the overlay
        candidate_id = _candidate_id_from_result(r)
        belief_changed = r.get("belief_changed", False)
        gate_transition = (r.get("gate_before") != r.get("gate_after"))
        worth_event = belief_changed or gate_transition

        if worth_event:
            if candidate_id not in priced_ids:
                if candidate_id not in unpriced_candidates:
                    unpriced_candidates.append(candidate_id)
            else:
                entry = worth_overlay[candidate_id]
                realized_entries.append({
                    "experiment_id": exp_id,
                    "candidate_id": candidate_id,
                    "worth_points": entry["worth_points"],
                    "belief_changed": belief_changed,
                    "gate_transition": gate_transition,
                    "node_id": r.get("subject", {}).get("builder_id"),
                })
        else:
            # No worth event — candidate does not contribute to numerator
            pass

    # Denominator: dispatch durations from events.jsonl
    all_dispatches, excluded_runs = extract_all_durations(event_files)

    # Filter dispatches to window (by open_ts)
    windowed_dispatches = [
        d for d in all_dispatches
        if _in_window(d.get("open_ts"), t1, t2)
    ]

    # Group by node
    nodes: dict[str, dict] = {}
    for disp in windowed_dispatches:
        nid = disp["node_id"]
        if nid not in nodes:
            nodes[nid] = {"total_duration_s": 0.0, "dispatch_count": 0, "dispatches": []}
        nodes[nid]["total_duration_s"] += disp["duration_s"]
        nodes[nid]["dispatch_count"] += 1
        nodes[nid]["dispatches"].append(disp)

    # Assign realized entries to nodes
    node_worth: dict[str, float] = {}
    for entry in realized_entries:
        nid = entry.get("node_id") or "unknown"
        node_worth[nid] = node_worth.get(nid, 0.0) + entry["worth_points"]

    # Build per-node report
    per_node: list[dict] = []
    all_node_ids = sorted(set(list(nodes.keys()) + list(node_worth.keys())))
    for nid in all_node_ids:
        dispatch_info = nodes.get(nid, {"total_duration_s": 0.0, "dispatch_count": 0})
        total_hours = dispatch_info["total_duration_s"] / 3600.0
        worth_pts = node_worth.get(nid, 0.0)
        per_node.append({
            "node_id": nid,
            "worth_points_realized": worth_pts,
            "dispatch_hours": round(total_hours, 4),
            "worth_per_hour": round(worth_pts / total_hours, 4) if total_hours > 0 else None,
            "dispatch_count": dispatch_info.get("dispatch_count", 0),
        })

    # Fleet totals
    total_worth = sum(e["worth_points"] for e in realized_entries)
    total_hours = sum(d["duration_s"] for d in windowed_dispatches) / 3600.0
    fleet_per_hour = round(total_worth / total_hours, 4) if total_hours > 0 else None

    # Companion metrics
    belief_movement = _belief_movement(windowed_results)

    return {
        "contract_version": "worth-realized.v1",
        "window": {"t1": t1, "t2": t2},
        "group_by": group_by,

        "fleet": {
            "worth_points_realized": total_worth,
            "dispatch_hours": round(total_hours, 4),
            "worth_per_hour": fleet_per_hour,
            "experiment_count": len(seen_experiment_ids),
            "worth_events": len(realized_entries),
        },
        "per_node": per_node,

        "unpriced_candidates": sorted(unpriced_candidates),
        "excluded_runs": excluded_runs,

        "companion_metrics": {
            "belief_movement": belief_movement,
            "qualifications_renewed": "unavailable: requires snapshot-diff (F1)",
            "gaps_closed": "unavailable: requires snapshot-diff (F1)",
            "economy_grouping": "unavailable: requires economy_influence on decisions (economics stream)",
        },

        "footer": {
            "constants_traced": {
                "DISPATCH_OPEN_EVENT": DISPATCH_OPEN_EVENT,
                "DISPATCH_CLOSE_EVENTS": sorted(DISPATCH_CLOSE_EVENTS),
                "WORTH_OVERLAY_PATH": str(worth_overlay_path),
            },
            "evidence_watermark": watermark,
            "event_files_scanned": len(event_files),
            "dispatch_use_forbidden": True,
        },

        "dispatch_use_forbidden": True,
    }


def _candidate_id_from_result(result: dict) -> str:
    """Reconstruct the canonical candidate_id from an experiment result.

    The experiment_id is the canonical experiment identity; by convention
    (see synthesize_candidates) each experiment type produces candidate_ids of
    the form "<experiment_type>:<combo>" which matches the plan's experiment_id.
    """
    return result.get("experiment_id") or ""


def _evidence_watermark(observations: list[dict]) -> str | None:
    timestamps = [obs.get("timestamp") for obs in observations if obs.get("timestamp")]
    return max(timestamps) if timestamps else None


def _belief_movement(results: list[dict]) -> dict:
    """Σ |confidence_score_after − confidence_score_before| per experiment (nulls skipped)."""
    total = 0.0
    counted = 0
    null_skipped = 0
    for r in results:
        before = (r.get("belief_before") or {}).get("confidence_score")
        after = (r.get("belief_after") or {}).get("confidence_score")
        if before is None or after is None:
            null_skipped += 1
        else:
            total += abs(after - before)
            counted += 1
    return {
        "total_abs_delta": round(total, 4),
        "experiment_count": counted,
        "null_skipped": null_skipped,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project experiment worth realized per hour (Stream Ga, Formulation E)."
    )
    parser.add_argument("sources", nargs="+",
                        help="events.jsonl files or directories to scan recursively")
    parser.add_argument("--t1", default=None, help="Window start (ISO 8601, inclusive)")
    parser.add_argument("--t2", default=None, help="Window end (ISO 8601, inclusive)")
    parser.add_argument("--group-by", default="node", choices=["node"],
                        help="Grouping dimension (default: node)")
    parser.add_argument("--worth-overlay", default=WORTH_OVERLAY_PATH,
                        help="Path to candidate_worth.json authored overlay")
    parser.add_argument("--out", default=None,
                        help="Write report JSON to this file (default: stdout only)")
    args = parser.parse_args(argv)

    report = worth_realized(
        sources=args.sources,
        t1=args.t1,
        t2=args.t2,
        group_by=args.group_by,
        worth_overlay_path=args.worth_overlay,
    )

    output = json.dumps(report, indent=2) + "\n"
    print(output, end="")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
