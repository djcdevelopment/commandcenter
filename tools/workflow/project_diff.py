import hashlib
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from tools.workflow.project_findings import materialize_findings
from tools.workflow.project_associations import materialize_associations
from tools.workflow.project_coverage import materialize_coverage


def diff_projections(sources: List[str], t1: str, t2: str, out_path: str) -> None:
    """
    Compute a deterministic diff between two time-bound snapshots of the knowledge state.

    Args:
        sources: List of paths to event files or directories containing events.jsonl.
        t1: ISO-8601 timestamp (inclusive) marking the start of the interval.
        t2: ISO-8601 timestamp (inclusive) marking the end of the interval.
        out_path: Path to write the output transitions JSON.

    Semantics: A transition is defined as a change in existence or state between t1 and t2.
    - A gap present at t1 and absent at t2 is gap_closed.
    - A gap absent at t1 and present at t2 is gap_opened.
    - A capability present at t1 and absent at t2 is capability_appeared.
    - An association present at t1 and absent at t2 is association_retired.
    - An association absent at t1 and present at t2 is association_formed.
    - A qualification status change from requalification_due to qualified is qualification_transition.
    - A finding's confidence_score change is finding_confidence_moved.

    All transitions are deterministic and stable-ID based.
    """
    # Collect and resolve event files
    event_files = []
    for src in sources:
        p = Path(src)
        if p.is_dir():
            event_files.extend(p.rglob("events.jsonl"))
        else:
            event_files.append(p)

    # Extract and filter observations and decisions
    def extract_filtered_events(event_files, as_of):
        observations = []
        decisions = []
        for f in event_files:
            with open(f, 'r') as fp:
                for line in fp:
                    event = json.loads(line)
                    if 'timestamp' not in event:
                        continue
                    if as_of is None or event['timestamp'] <= as_of:
                        if 'observation' in event:
                            observations.append(event)
                        elif 'decision' in event:
                            decisions.append(event)
        return observations, decisions

    # Compute snapshots at t1 and t2
    obs_t1, decs_t1 = extract_filtered_events(event_files, t1)
    obs_t2, decs_t2 = extract_filtered_events(event_files, t2)

    # Materialize state at t1 and t2
    state_t1 = {
        'findings': materialize_findings(obs_t1, decs_t1),
        'associations': materialize_associations(obs_t1, decs_t1),
        'coverage': materialize_coverage(obs_t1, decs_t1)
    }
    state_t2 = {
        'findings': materialize_findings(obs_t2, decs_t2),
        'associations': materialize_associations(obs_t2, decs_t2),
        'coverage': materialize_coverage(obs_t2, decs_t2)
    }

    # Compute transitions
    transitions = []

    # Findings: confidence_score change
    for fid in set(state_t1['findings'].keys()) | set(state_t2['findings'].keys()):
        f1 = state_t1['findings'].get(fid)
        f2 = state_t2['findings'].get(fid)
        if f1 is not None and f2 is not None:
            if f1.get('confidence_score') != f2.get('confidence_score'):
                tid = _stable_id('finding_confidence_moved', fid, t1, t2)
                transitions.append({
                    'type': 'finding_confidence_moved',
                    'id': tid,
                    'subject': fid,
                    'before': f1['confidence_score'],
                    'after': f2['confidence_score']
                })
        elif f1 is None and f2 is not None:
            # New finding
            pass  # No transition for new finding
        elif f1 is not None and f2 is None:
            # Removed finding
            pass  # No transition for removed finding

    # Associations: formed/retired
    for aid in set(state_t1['associations'].keys()) | set(state_t2['associations'].keys()):
        a1 = state_t1['associations'].get(aid)
        a2 = state_t2['associations'].get(aid)
        if a1 is not None and a2 is None:
            tid = _stable_id('association_retired', aid, t1, t2)
            transitions.append({
                'type': 'association_retired',
                'id': tid,
                'subject': aid
            })
        elif a1 is None and a2 is not None:
            tid = _stable_id('association_formed', aid, t1, t2)
            transitions.append({
                'type': 'association_formed',
                'id': tid,
                'subject': aid
            })

    # Capabilities: appeared (via association or direct)
    # We track capability_id from association_id or capability_id
    cap_ids_t1 = set(state_t1['associations'].keys())
    cap_ids_t2 = set(state_t2['associations'].keys())
    # But capability_id is in the form "capability:..."
    # So we extract all capability_ids from the associations
    def _get_cap_ids(assoc_dict):
        caps = set()
        for aid, a in assoc_dict.items():
            if 'capability_id' in a:
                caps.add(a['capability_id'])
        return caps
    
    caps_t1 = _get_cap_ids(state_t1['associations'])
    caps_t2 = _get_cap_ids(state_t2['associations'])

    for cid in caps_t1 - caps_t2:
        tid = _stable_id('capability_appeared', cid, t1, t2)
        transitions.append({
            'type': 'capability_appeared',
            'id': tid,
            'subject': cid
        })

    # Qualification transitions: requalification_due → qualified
    for cid in caps_t2:
        c1 = state_t1['associations'].get(cid)
        c2 = state_t2['associations'].get(cid)
        if c1 is not None and c2 is not None:
            if c1.get('qualification_status') == 'requalification_due' and \
               c2.get('qualification_status') == 'qualified':
                tid = _stable_id('qualification_transition', cid, t1, t2)
                transitions.append({
                    'type': 'qualification_transition',
                    'id': tid,
                    'subject': cid,
                    'from_status': 'requalification_due',
                    'to_status': 'qualified'
                })

    # Gaps: opened/closed
    for gid in set(state_t1['coverage'].keys()) | set(state_t2['coverage'].keys()):
        g1 = state_t1['coverage'].get(gid)
        g2 = state_t2['coverage'].get(gid)
        if g1 is not None and g2 is None:
            tid = _stable_id('gap_closed', gid, t1, t2)
            transitions.append({
                'type': 'gap_closed',
                'id': tid,
                'subject': gid
            })
        elif g1 is None and g2 is not None:
            tid = _stable_id('gap_opened', gid, t1, t2)
            transitions.append({
                'type': 'gap_opened',
                'id': tid,
                'subject': gid
            })

    # Write output
    with open(out_path, 'w') as fp:
        json.dump(transitions, fp, indent=2)


def _stable_id(type_name: str, subject_id: str, t1: str, t2: str) -> str:
    """Generate a deterministic stable ID using SHA256 hash of (type + subject + t1 + t2)"""
    input_str = f"{type_name}:{subject_id}:{t1}:{t2}"
    return hashlib.sha256(input_str.encode()).hexdigest()[:16]