#!/usr/bin/env python3
"""Offline review interval accounting; reads JSON and prints JSON, no writes or services."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path

SCHEMA = 'bench27-review-effort.v1'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def instant(value):
    require(text(value), 'timestamp must be a UTC string')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0,
            'timestamp must have explicit UTC offset')
    return parsed.astimezone(timezone.utc).timestamp()


def minutes(intervals):
    """Length of the union, so overlap inside a worker session is counted once."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return sum(end-start for start, end in merged)/60


def measured(value):
    if value is None:
        return {'status': 'unknown', 'minutes': None}
    require(isinstance(value, dict), 'measurement must be an object')
    number = value.get('minutes')
    try:
        valid = type(number) in (int, float) and math.isfinite(number) and number >= 0
    except OverflowError:
        valid = False
    require(valid and text(value.get('evidence')), 'measurement needs finite minutes and evidence')
    return {'status': 'evidenced', 'minutes': number, 'evidence': value['evidence']}


def calculate(data):
    require(isinstance(data, dict) and data.get('schema') == SCHEMA, 'wrong schema')
    cohorts, deliveries, intervals = data.get('cohorts'), data.get('deliveries'), data.get('intervals')
    require(isinstance(cohorts, list) and cohorts, 'cohorts must be a nonempty list')
    require(isinstance(deliveries, list) and isinstance(intervals, list), 'deliveries/intervals must be lists')
    groups = {}
    for row in cohorts:
        require(isinstance(row, dict), 'cohort must be an object')
        name = row.get('cohort')
        require(text(name) and name not in groups, 'missing or duplicate cohort')
        require(row.get('kind') in ('qualification', 'capacity'), 'invalid cohort kind')
        require(type(row.get('complete')) is bool and text(row.get('evidence')),
                'cohort needs explicit completeness and evidence')
        groups[name] = {'declaration': row, 'deliveries': [], 'intervals': []}
    work = {}
    for row in deliveries:
        require(isinstance(row, dict), 'delivery must be an object')
        key = row.get('work_id')
        require(text(key) and key not in work, 'missing or duplicate delivery')
        require(row.get('cohort') in groups, 'unknown delivery cohort')
        require(row.get('status') in ('accepted', 'rejected', 'failed') and text(row.get('evidence')),
                'delivery needs terminal status and evidence')
        work[key] = row
        groups[row['cohort']]['deliveries'].append(row)
    ownership = {}
    for row in intervals:
        require(isinstance(row, dict), 'interval must be an object')
        require(row.get('actor') in ('codex', 'opus'), 'actor must be codex or opus')
        require(all(text(row.get(k)) for k in ('session', 'activity', 'evidence')), 'interval metadata/evidence missing')
        require(row.get('work_id') in work, 'unknown interval work')
        require(row.get('cohort') == work[row['work_id']]['cohort'], 'interval/delivery cohort mismatch')
        start, end = instant(row.get('start')), instant(row.get('end'))
        require(end > start, 'interval end must follow start')
        owner = (row['actor'], row['session'])
        # A session belongs to one cohort so the same effort cannot cross cohorts.
        require(owner not in ownership or ownership[owner] == row['cohort'], 'session spans cohorts')
        ownership[owner] = row['cohort']
        groups[row['cohort']]['intervals'].append((row, (start, end)))
    result = {'schema': SCHEMA, 'cohorts': []}
    for name, group in groups.items():
        declaration = group['declaration']
        covered = {row['work_id'] for row, span in group['intervals']}
        missing = sorted(row['work_id'] for row in group['deliveries'] if row['work_id'] not in covered)
        require(not declaration['complete'] or (group['deliveries'] and not missing),
                'complete cohort must have deliveries and interval coverage for each')
        sessions = {}
        for row, span in group['intervals']:
            sessions.setdefault((row['actor'], row['session']), []).append(span)
        rows = [{'actor': actor, 'session': session,
                 'label': 'agent effort' if actor == 'codex' else 'automated review duration',
                 'minutes': minutes(spans)} for (actor, session), spans in sorted(sessions.items())]
        worker = sum(row['minutes'] for row in rows)
        elapsed = minutes([span for row, span in group['intervals']])
        accepted = sum(row['status'] == 'accepted' for row in group['deliveries'])
        complete = declaration['complete']
        result['cohorts'].append({
            'cohort': name, 'kind': declaration['kind'], 'coverage': 'complete' if complete else 'partial',
            'coverage_evidence': declaration['evidence'], 'missing_interval_work_ids': missing,
            'delivery_counts': {state: sum(row['status'] == state for row in group['deliveries'])
                                for state in ('accepted', 'rejected', 'failed')},
            'sessions': rows, 'actor_minutes': {actor: sum(row['minutes'] for row in rows if row['actor'] == actor)
                                               for actor in ('codex', 'opus')},
            'actor_minutes_per_accepted': {actor: sum(row['minutes'] for row in rows if row['actor'] == actor)/accepted
                                           if accepted else None for actor in ('codex', 'opus')},
            'worker_sum_basis': 'heterogeneous sum: Codex active agent effort + Opus invocation wall duration',
            'worker_sum_minutes': worker, 'union_elapsed_minutes': elapsed,
            'rate_status': 'undefined_zero_accepted' if not accepted else ('complete' if complete else 'partial_observation'),
            'worker_minutes_per_accepted': worker/accepted if accepted else None,
            'union_minutes_per_accepted': elapsed/accepted if accepted else None,
            'human_review': measured(declaration.get('human_review')),
            'manual_baseline': measured(declaration.get('manual_baseline')),
            'economic_benefit': 'not_assessed'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    try:
        output = calculate(json.loads(args.input.read_text()))
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
        parser.exit(2, f'invalid review-effort input: {exc}\n')
    print(json.dumps(output, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
