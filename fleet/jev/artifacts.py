"""Bounded read-only artifact capture through established conductor host trust."""
import difflib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

from fleet.jev.client import atomic_json


def helper():
    path = Path(__file__).resolve().parents[1] / 'hermes' / 'builder-access.py'
    spec = importlib.util.spec_from_file_location('jev_builder_access', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review_packet(document, receipt, out):
    delegation = receipt['execution']['delegation']
    plan = delegation['plan_id']
    if not re.fullmatch('[a-z0-9-]{1,150}', plan):
        raise ValueError('invalid_plan_id')
    names = [item['output'] for item in document['deliverables']]
    source = f'''import json,subprocess
from pathlib import Path
root=Path('/home/claude/farmer-workspace')/{plan!r}
files={{}}
for name in {names!r}:
 p=root/name
 if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()) or not p.is_file() or p.stat().st_size>30000:
  raise RuntimeError('missing_or_oversized_artifact')
 files[name]=p.read_text()
print(json.dumps({{'files':files,'commit':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()}}))
'''
    captured = subprocess.run(helper().command('cc-builder-2', source), capture_output=True, text=True, timeout=30)
    if captured.returncode or len(captured.stdout) > 140000:
        raise RuntimeError('candidate_capture_failed')
    value = json.loads(captured.stdout)
    atomic_json(out / 'candidate.json', value)
    sections = ['# Independent local build review', document['envelope']['intent'],
                '\nAcceptance criteria:\n' + '\n'.join(document['envelope']['acceptance_criteria']),
                '\nReview the candidate below against the original. Cite concrete file/line findings. '
                'Return PASS, NEEDS_WORK, or INCONCLUSIVE and a short complete report. '
                'Do not claim checks were run if no output proves them. Do not write code or dispatch work.']
    root = Path(document['envelope']['inputs']['repo'])
    for item in document['deliverables']:
        target = root / item['target']
        before = target.read_text(encoding='utf-8') if target.is_file() else ''
        after = value['files'][item['output']]
        sections.extend(['\n## ' + item['target'], '\nOriginal:\n' + before,
                         '\nCandidate:\n' + after, '\nDiff:\n' + ''.join(difflib.unified_diff(
                             before.splitlines(True), after.splitlines(True), fromfile='before', tofile='candidate'))])
    packet = '\n'.join(sections)
    if len(packet.encode()) > 60000:
        raise RuntimeError('review_packet_too_large')
    return packet


def review_seat(action, owner):
    if action not in ('start', 'stop', 'status') or not re.fullmatch(r'jev-[0-9a-f]{24}', owner):
        raise ValueError('invalid_review_action')
    command = ['ssh', '-b', '10.44.0.1', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
               '-o', 'HostName=10.44.0.2', '-o', 'HostKeyAlias=am4', '-o', 'StrictHostKeyChecking=yes',
               'am4', 'python3', '/home/derek/.local/share/fleet-scheduler/review_seat.py', action, '--owner', owner]
    result = subprocess.run(command, capture_output=True, text=True, timeout=105)
    value = json.loads(result.stdout)
    if result.returncode or value.get('ok') is not True:
        raise RuntimeError(value.get('reason_code', 'review_seat_failed'))
    return value
