#!/usr/bin/env python3
"""Offline single-pass arm package. Run again into a NEW directory after completion.
Never grades substance, dispatches work, or modifies input evidence. Pass the control
and treatment seats explicitly; a swapped pass needs the opposite assignment.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import statistics
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.ops import bench27_summarize as summary
from hearth.delivery import contract, render, sourcemap


def speculative_intervals(path: Path) -> dict:
    """Retain monotonic segments separately across resets; missing != zero."""
    captured = path.read_bytes()
    records = [json.loads(line) for line in captured.decode().splitlines() if line.strip()]
    seats = {}
    for seat in sorted({str(k) for r in records for k in r.get('seats', {})}):
        segments, current, errors = [], [], 0
        for record in records:
            raw = record.get('seats', {}).get(seat)
            if not isinstance(raw, str):
                errors += 1
                if current:
                    segments.append(current)
                    current = []
                continue
            values = {}
            for line in raw.splitlines():
                if line.startswith('vllm:spec_decode_') and '_total{' in line:
                    key, value = line.rsplit(' ', 1)
                    values[key] = float(value)
            if not values:
                if current:
                    segments.append(current)
                    current = []
                continue
            if current and (values.keys() != current[-1][1].keys() or any(values[k] < current[-1][1][k] for k in values)):
                segments.append(current)
                current = []
            current.append((record['utc'], values))
        if current:
            segments.append(current)
        seats[seat] = {'errors_or_missing_samples': errors, 'segments': [
            {'first_utc': segment[0][0], 'last_utc': segment[-1][0], 'samples': len(segment),
             'first_values': segment[0][1], 'last_values': segment[-1][1],
             'deltas': {k: segment[-1][1][k] - v for k, v in segment[0][1].items()}}
            for segment in segments]}
    return {'path': str(path.resolve()), 'sha256': summary.digest(captured), 'seats': seats,
            'scope': 'Observed endpoints only; verify timestamps bracket intended load. Never assume whole-pass, warm-only, or per-stage coverage. Missing counters are unmeasured, not zero; resets split segments. Positions lack proposal denominators.'}


def pairs(values: list[str]) -> dict:
    result = {}
    for value in values:
        key, sep, reason = value.partition('=')
        if not sep or not key.strip() or not reason.strip() or key in result:
            raise ValueError('entries require unique KEY=nonempty value')
        result[key] = reason
    return result


def validate_binding(rows, control, treatment, workload_hash, state=None):
    observed = {r['seat'] for r in rows}
    if control == treatment or control not in observed or treatment not in observed:
        raise ValueError('control/treatment must be distinct observed seats')
    if any((r.get('workload') or {}).get('sha256') != workload_hash for r in rows):
        raise ValueError('workload hash does not match every run')
    assignment = {'control': control, 'treatment': treatment, 'verified': False, 'basis': 'caller-asserted'}
    if state is not None:
        identity = state.get('spec', {}).get('id')
        if not isinstance(identity, str) or not identity or {r.get('arm') for r in rows} != {identity}:
            raise ValueError('harness spec.id does not match every run arm')
        assignment['experiment_id'] = identity
        dropins = state.get('spec', {}).get('dropin', {})
        treatments = ['seat-'+str(k) for k,v in dropins.items() if v is not None]
        controls = ['seat-'+str(k) for k,v in dropins.items() if v is None]
        if treatments != [treatment] or control not in controls:
            raise ValueError('harness dropin disagrees with control/treatment assignment')
        assignment.update(verified=True, basis='harness spec.dropin')
    return assignment


def regime(row):
    stages = []
    for conversation in row['conversations']:
        item = {}
        for stage in ('work', 'final'):
            controls = conversation[stage]['controls']
            item[stage] = {k: controls.get(k) for k in ('temperature','seed','top_p','max_tokens')}
            item[stage]['enable_thinking'] = (controls.get('chat_template_kwargs') or {}).get('enable_thinking')
        stages.append(item)
    return {'workload': (row.get('workload') or {}).get('sha256'), 'stages': stages}


def compare(rows, parity, control, treatment):
    arm = [r for r in rows if not r['timing_exclusions']]
    baseline = [r for r in parity['runs'] if not r['timing_exclusions']]
    reasons = []
    if not arm or not baseline or len({summary.canonical(regime(r)) for r in arm+baseline}) != 1:
        reasons.append('arm/parity workload or generation settings differ or lack admissible rows')
    comparisons = {}
    for metric in summary.METRICS:
        seats = {seat: summary.spread([r[metric] for r in arm if r['seat'] == seat and summary.number(r.get(metric))]) for seat in (control,treatment)}
        c,t = seats[control]['median'], seats[treatment]['median']
        frozen = parity['calibration']['metrics'][metric]
        threshold = frozen.get('threshold')
        unavailable = reasons + list(parity['calibration'].get('reasons', []))
        ready = all(v['n'] >= 3 for v in seats.values())
        if not ready:
            unavailable.append('fewer than three finite admissible observations per seat')
        if frozen.get('ready') is not True or not summary.number(threshold):
            unavailable.append('frozen parity metric not ready or threshold unavailable')
        relative = abs(t-c)/statistics.mean([abs(t),abs(c)]) if c is not None and t is not None and (c or t) else None
        comparisons[metric] = {'seats': seats, 'ready_n3_each': ready, 'comparable': not reasons,
            'parity_ready': frozen.get('ready'), 'parity_reasons': parity['calibration'].get('reasons', []),
            'frozen_threshold': threshold, 'unavailable_reasons': unavailable,
            'treatment_over_control_minus_one': t/c-1 if c and t is not None else None,
            'symmetric_relative_difference': relative,
            'exceeds_frozen_spread': relative > threshold if not unavailable and relative is not None else None}
    return comparisons


def prepare(args) -> dict:
    root, out = args.root.resolve(), args.out.resolve()
    if out.exists() or out == root or root in out.parents:
        raise ValueError('output must be a new directory outside input root')
    if args.control == args.treatment:
        raise ValueError('control and treatment must be different seats')
    workload_bytes = args.workload.read_bytes()
    workload_hash = summary.digest(workload_bytes)
    workload = json.loads(workload_bytes)
    state = summary.read_json(args.harness) if args.harness else None
    result = summary.summarize(root, args.cards, exclude_repeats=pairs(args.exclude_repeat),
                               card_map=pairs(getattr(args, 'card_map', [])) or None)
    result.pop('calibration')  # Never recalibrate A/A from treatment measurements.
    result['schema'] = 'bench27-arm-package.v1'
    result['assignment'] = validate_binding(result['runs'], args.control, args.treatment, workload_hash, state)
    result['workload_binding'] = {'path': str(args.workload.resolve()), 'sha256': workload_hash}
    parity = summary.read_json(args.parity)
    if parity.get('schema') != summary.SCHEMA:
        raise ValueError('unsupported frozen parity schema')
    result['frozen_parity'] = {'path': str(args.parity.resolve()), 'sha256': summary.digest(args.parity.read_bytes()),
                             'reasons': parity['calibration'].get('reasons', [])}
    result['comparison'] = compare(result['runs'], parity, args.control, args.treatment)
    if args.harness:
        result['harness'] = {'path': str(args.harness.resolve()), 'sha256': summary.digest(args.harness.read_bytes()),
            **{k: state.get(k) for k in ('phase', 'outcome', 'started', 'finished', 'calls_reported', 'foreign_requests', 'requests_during_restore')},
            'declared_recipe': {k: state.get('spec', {}).get(k) for k in ('dropin', 'expect_argv', 'expect_model')},
            'restoration': {k: {f: v.get(f) for f in ('phase','hashes_verified','served_after_restore','counters_restored')} for k,v in state.get('restoration_by_seat', {}).items()}}
    result['raw_speculative_intervals'] = speculative_intervals(args.raw_metrics) if args.raw_metrics else {'state': 'not supplied'}
    result['needle_grades'] = {'state': 'not supplied; report runs do not establish retrieval'}
    if args.needle_grades:
        result['needle_grades'] = {'path': str(args.needle_grades.resolve()), 'sha256': summary.digest(args.needle_grades.read_bytes()), 'records': json.loads(args.needle_grades.read_text())}
    pin = workload['source_commit']
    if not re.fullmatch(r'[0-9a-fA-F]{40}', pin) or any(x['commit'] != pin for x in workload['brief']['sources']):
        raise ValueError('source commit must be a full SHA matching every brief source')
    sm = sourcemap.build(str(args.source_repo.resolve()), workload['source_commit'], [x['path'] for x in workload['brief']['sources']])
    result['source_binding'] = {'commit': sm.commit, 'files': {f.path: f.sha256 for f in sm.files}}
    out.mkdir(parents=False)
    blind, sidecars = out/'blind', out/'sidecars'
    blind.mkdir(); sidecars.mkdir()
    (blind/'brief.json').write_text(json.dumps(workload['brief'], indent=2, allow_nan=False)+'\n')
    (blind/'sources.txt').write_text(sourcemap.render_for_model(sm, numbered=True, symbols=False))
    (blind/'README.md').write_text('Review only this directory. Grade each answer against brief.json and pinned sources.txt. Assess each substance criterion and false claims separately from form. Do not inspect parent files. Duplicated answers are preserved.\n')
    mapping = []
    for index, row in enumerate(sorted(result['runs'], key=lambda r: summary.digest((root.name+r['path']).encode())), 1):
        ident = f'answer-{index:02d}'
        source = root/Path(row['path']).parent/'conversation-1/artifacts/final.output.txt'
        if not source.exists():
            mapping.append({'answer': ident, 'run': row['path'], 'state': 'missing final artifact'})
            continue
        raw = source.read_bytes()
        try:
            md, manifest = render.render(json.loads(raw), workload['brief'], sm, {'aids_used': ['constrained_output']})
            contract.check_manifest(manifest)
        except (ValueError, contract.ContractError) as exc:
            mapping.append({'answer': ident, 'run': row['path'], 'state': 'render failed', 'reason': str(exc)})
            (sidecars/f'{ident}.raw.txt').write_bytes(raw)
            continue
        (blind/f'{ident}.md').write_text(md)
        (sidecars/f'{ident}.raw.json').write_bytes(raw)
        (sidecars/f'{ident}.manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False)+'\n')
        mapping.append({'answer': ident, 'run': row['path'], 'state': 'rendered', 'raw_sha256': summary.digest(raw), 'rendered_sha256': summary.digest(md.encode()), 'excluded': row['timing_exclusions']})
    (out/'blind-map.json').write_text(json.dumps(mapping, indent=2, allow_nan=False)+'\n')
    (out/'metrics.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    lines = ['# Offline arm preliminary package', '', f'Input: `{root}`. Control: {args.control}; treatment: {args.treatment}.', '',
        f"Run records found: {len(result['runs'])}. Harness phase: {result.get('harness', {}).get('phase', 'unknown')}. No completion or restoration claim is inferred.", '',
        'Cold exclusions remain visible. At least three admissible warm rows per seat are required to compare against frozen spread. No substance grades or adoption decision. Raw metrics may cover only part of the workload.', '',
        '| Metric | Control median | Treatment median | n control / treatment | Treatment change | Frozen spread | Ready |', '|---|---:|---:|---:|---:|---:|---|']
    for metric, v in result['comparison'].items():
        c,t=[v['seats'][seat] for seat in (args.control,args.treatment)]
        lines.append(f"| {metric} | {c['median']} | {t['median']} | {c['n']} / {t['n']} | {v['treatment_over_control_minus_one']} | {v['frozen_threshold']} | {v['ready_n3_each']} |")
    lines += ['', f"Assignment verified: {result['assignment']['verified']} ({result['assignment']['basis']}). Card map: {result['card_map']}. Divergence reference: {result['reference_run']} (may be the treatment seat in a swapped pass).",
        'Card and treatment are confounded within one pass; an arm verdict needs both swapped passes. Lower seconds is better; higher decode tokens/s is better. Null spread comparisons remain unavailable; see per-metric reasons in metrics.json.', '', 'All rates above are fractions. Exact source fields, run hashes, thermal windows, first-token/prefill metadata, counters and exclusions are in metrics.json. Decode uses generation_tokens_total/decode_seconds_sum; durations use run.seconds_total and conversation work/final.seconds. Output length may differ. No A/B-derived recalibration.', '', 'Only blind/ goes to the grader. Raw copies, deterministic renderer repair manifests and mapping remain outside it. Needles are unmeasured unless a grade file was explicitly supplied. Re-run into a new output directory after remaining work completes; preserve this snapshot.']
    (out/'SUMMARY.md').write_text('\n'.join(lines)+'\n')
    (out/'COMPLETE.json').write_text(json.dumps({'schema': 'bench27-package-complete.v1', 'meaning': 'Offline package construction complete, not experiment success', 'metrics_sha256': summary.digest((out/'metrics.json').read_bytes())}, allow_nan=False)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root','out','parity','workload','source-repo'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('harness','raw-metrics','needle-grades'):
        parser.add_argument('--'+name, type=Path)
    parser.add_argument('--cards', type=Path, action='append', default=[])
    parser.add_argument('--card-map', action='append', default=[])
    parser.add_argument('--exclude-repeat', action='append', default=[])
    parser.add_argument('--control', required=True)
    parser.add_argument('--treatment', required=True)
    prepare(parser.parse_args())

if __name__ == '__main__':
    main()
