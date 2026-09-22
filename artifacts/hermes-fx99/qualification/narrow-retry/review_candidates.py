"""Codex independent contract probes; model source is preserved unchanged."""
import copy
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 9, 20, 1, 47, tzinfo=timezone.utc)
BASE = {'all_ready': True, 'aliases': [{'alias': 'am4-dense-27b', 'ready': True,
    'status': 200, 'context_length': 131072, 'parallel_slots': 1,
    'physical_resource': 'am4:127.0.0.1:18090', 'model': 'models/Qwen3.8-27B-Q4_K_M.gguf'}]}


def probe(fn):
    cases = [('valid', BASE, NOW.isoformat(), True)]
    for field, value in [('ready', False), ('status', 503), ('model', 'other.gguf'),
                         ('context_length', True), ('parallel_slots', True),
                         ('physical_resource', 'other')]:
        row = copy.deepcopy(BASE); row['aliases'][0][field] = value
        cases.append((field, row, NOW.isoformat(), False))
    for name, payload in [('malformed', None), ('absent', {}), ('bad-list', {'aliases': None})]:
        cases.append((name, payload, NOW.isoformat(), False))
    for age in [-6, -5, 30, 31]:
        cases.append((f'age-{age}', BASE, (NOW-timedelta(seconds=age)).isoformat(), -5 <= age <= 30))
    for stamp in ['', None, NOW.replace(tzinfo=None).isoformat()]:
        cases.append((f'timestamp-{stamp}', BASE, stamp, False))
    for name, duplicate, overall, expected in [
            ('identical-alias', {}, True, True),
            ('conflicting-alias', {'ready': False}, True, False),
            ('another-alias', {'alias': 'another'}, True, True),
            ('unrelated-unready', {'alias': 'another', 'ready': False}, False, True)]:
        row = copy.deepcopy(BASE); row['all_ready'] = overall
        row['aliases'].append({**row['aliases'][0], **duplicate})
        cases.append((name, row, NOW.isoformat(), expected))
    result = []
    for name, payload, stamp, expected in cases:
        try:
            output = fn(copy.deepcopy(payload), stamp, NOW)
            ok = output['ready'] is expected and output['parallel_slots'] == int(expected) and output['gpu_placed'] is None
            result.append({'case': name, 'passed': ok, 'actual': output})
        except Exception as exc:
            result.append({'case': name, 'passed': False, 'exception': repr(exc)})
    return result


report = {}
for worker in ('cc-builder-2', 'cc-builder-3'):
    spec = importlib.util.spec_from_file_location(worker, ROOT/worker/'native_capacity.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    report[worker] = probe(module.normalize_am4_native)
(ROOT/'independent-review.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({worker: {'passed': sum(r['passed'] for r in rows), 'total': len(rows),
    'failed': [r['case'] for r in rows if not r['passed']]} for worker, rows in report.items()}))
