import hashlib
import json
from typing import List, Dict, Any, Tuple, Optional

from tools.workflow.project_findings import synthesize_findings, materialize_findings
from tools.workflow.project_associations import synthesize_associations, materialize_associations
from tools.workflow.project_coverage import synthesize_coverage, materialize_coverage
from tools.workflow.project_capacity import collect_event_files


# Transition types and their stable ID format
TRANSITION_TYPES = {
    'association_formed': 'association_formed',
    'association_retired': 'association_retired',
    'capability_appeared': 'capability_appeared',
    'qualification_transition': 'qualification_transition',
    'gap_opened': 'gap_opened',
    'gap_closed': 'gap_closed',
    'finding_confidence_moved': 'finding_confidence_moved'
}


def _stable_id(type_name: str, subject_id: str, t1: str, t2: str) -> str:
    """Generate a deterministic stable ID for a transition using SHA256 hash."""
    key = f"{type_name}:{subject_id}:{t1}:{t2}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _get_snapshot(event_files: List[str], as_of: Optional[str]) -> Dict[str, Any]:
    """Compute a snapshot of the current state at a given as_of timestamp."""
    # Collect and filter events
    all_events = collect_event_files(event_files)
    filtered_events = [
        e for e in all_events
        if as_of is None or e.get('timestamp', '') <= as_of
    ]

    # Synthesize state
    findings = synthesize_findings(filtered_events)
    associations = synthesize_associations(filtered_events)
    coverage = synthesize_coverage(filtered_events)

    return {
        'findings': findings,
        'associations': associations,
        'coverage': coverage
    }


def diff_projections(sources: List[str], t1: str, t2: str, out_path: str) -> None:
    """Compute the difference between two bounded snapshots and emit transitions to out_path."
    
    # Collect event files from sources
    event_files = collect_event_files(sources)

    # Compute snapshots at t1 and t2
    snapshot_t1 = _get_snapshot(event_files, t1)
    snapshot_t2 = _get_snapshot(event_files, t2)

    transitions = []

    # Compare findings
    findings_t1 = {f['finding_id']: f for f in snapshot_t1['findings']}
    findings_t2 = {f['finding_id']: f for f in snapshot_t2['findings']}

    for fid in set(findings_t1.keys()) | set(findings_t2.keys()):
        f1 = findings_t1.get(fid)
        f2 = findings_t2.get(fid)
        if f1 is None and f2 is not None:
            transitions.append({
                'type': 'finding_confidence_moved',
                'id': _stable_id('finding_confidence_moved', fid, t1, t2),
                'subject': fid,
                'before': None,
                'after': f2['confidence_score']
            })
        elif f1 is not None and f2 is None:
            transitions.append({
                'type': 'finding_confidence_moved',
                'id': _stable_id('finding_confidence_moved', fid, t1, t2),
                'subject': fid,
                'before': f1['confidence_score'],
                'after': None
            })
        elif f1 is not None and f2 is not None and f1['confidence_score'] != f2['confidence_score']:
            transitions.append({
                'type': 'finding_confidence_moved',
                'id': _stable_id('finding_confidence_moved', fid, t1, t2),
                'subject': fid,
                'before': f1['confidence_score'],
                'after': f2['confidence_score']
            })

    # Compare associations
    assoc_t1 = {a['association_id']: a for a in snapshot_t1['associations']}
    assoc_t2 = {a['association_id']: a for a in snapshot_t2['associations']}

    for aid in set(assoc_t1.keys()) | set(assoc_t2.keys()):
        a1 = assoc_t1.get(aid)
        a2 = assoc_t2.get(aid)
        if a1 is None and a2 is not None:
            transitions.append({
                'type': 'association_formed',
                'id': _stable_id('association_formed', aid, t1, t2),
                'subject': aid
            })
        elif a1 is not None and a2 is None:
            transitions.append({
                'type': 'association_retired',
                'id': _stable_id('association_retired', aid, t1, t2),
                'subject': aid
            })

    # Compare capabilities
    cap_t1 = {c['capability_id']: c for c in snapshot_t1['associations']}
    cap_t2 = {c['capability_id']: c for c in snapshot_t2['associations']}

    for cid in set(cap_t1.keys()) | set(cap_t2.keys()):
        c1 = cap_t1.get(cid)
        c2 = cap_t2.get(cid)
        if c1 is None and c2 is not None:
            transitions.append({
                'type': 'capability_appeared',
                'id': _stable_id('capability_appeared', cid, t1, t2),
                'subject': cid
            })
        elif c1 is not None and c2 is None:
            # No retired capability transition needed; capability is retired via status
            pass

    # Compare qualification status transitions
    for cid in set(cap_t1.keys()) & set(cap_t2.keys()):
        c1 = cap_t1[cid]
        c2 = cap_t2[cid]
        if c1['qualification_status'] == 'requalification_due' and c2['qualification_status'] == 'qualified':
            transitions.append({
                'type': 'qualification_transition',
                'id': _stable_id('qualification_transition', cid, t1, t2),
                'subject': cid,
                'from_status': 'requalification_due',
                'to_status': 'qualified'
            })

    # Compare gaps
    gap_t1 = {g['gap_id']: g for g in snapshot_t1['coverage']}
    gap_t2 = {g['gap_id']: g for g in snapshot_t2['coverage']}

    for gid in set(gap_t1.keys()) | set(gap_t2.keys()):
        g1 = gap_t1.get(gid)
        g2 = gap_t2.get(gid)
        if g1 is None and g2 is not None:
            transitions.append({
                'type': 'gap_opened',
                'id': _stable_id('gap_opened', gid, t1, t2),
                'subject': gid
            })
        elif g1 is not None and g2 is None:
            transitions.append({
                'type': 'gap_closed',
                'id': _stable_id('gap_closed', gid, t1, t2),
                'subject': gid
            })

    # Write transitions to output
    with open(out_path, 'w') as f:
        json.dump(transitions, f, indent=2)

    return


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Compute diff between two bounded projections.')
    parser.add_argument('sources', nargs='+', help='Event files or directories')
    parser.add_argument('--t1', required=True, help='Start time (ISO-8601)')
    parser.add_argument('--t2', required=True, help='End time (ISO-8601)')
    parser.add_argument('--out', required=True, help='Output file path')
    args = parser.parse_args()
    diff_projections(args.sources, args.t1, args.t2, args.out)


if __name__ == '__main__':
    main()
