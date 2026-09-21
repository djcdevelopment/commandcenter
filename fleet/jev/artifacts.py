"""Bounded read-only artifact capture through established conductor host trust."""
import ast
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

from fleet.jev.client import atomic_json
from fleet.jev.baseline import read_baseline


def formatter_evidence(candidate):
    """Execute only the reviewed, exact two-condition correction for this lap.

    This is deliberately not a general candidate-code executor or sandbox.
    Unexpected syntax is evidence of a changed task, not permission to run it.
    """
    root = Path(__file__).resolve().parents[2]
    prior = json.loads((root / 'fleet/jev/evidence/20260921-worker-candidate.json').read_text())
    expected = prior['files']['fleet_status_format.py']
    for name in ('active_work_val', 'pending_reviews_val'):
        expected = expected.replace(f'isinstance({name}, int)', f'type({name}) is int')
    evidence = {'executor': 'Codex-supplied independent execution, not Hermes',
                'baseline_commit': 'd69d8685f153962ae1ef1ad987d34efdec8470ba',
                'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(), 'cases': []}
    try:
        tree = ast.parse(candidate)
        matches = ast.dump(tree) == ast.dump(ast.parse(expected))
    except SyntaxError:
        matches = False
    if not matches:
        return dict(evidence, verdict='INCONCLUSIVE', reason='candidate_outside_exact_reviewed_change')
    safe = {'isinstance': isinstance, 'dict': dict, 'str': str, 'type': type, 'int': int}
    namespace = {'__builtins__': safe}
    exec(compile(tree, '<captured-local-candidate>', 'exec'), namespace)
    baseline = {'__builtins__': safe}
    original = subprocess.check_output(['git', 'show', evidence['baseline_commit']
                                       + ':fleet/hermes/fleet_status_format.py'], cwd=root, text=True)
    exec(compile(original, '<baseline>', 'exec'), baseline)
    cases = evidence['cases']
    actual = namespace['format_status']({})
    wanted = baseline['format_status']({})
    cases.append({'name': 'legacy_empty_state_byte_equal', 'input': {},
                  'expected': wanted, 'actual': actual, 'passed': actual == wanted})
    for name, active, pending, wait, wanted in (
        ('boolean_counts', True, False, 'idle', 'Scheduler work: active unknown; pending review unknown; wait idle'),
        ('integer_counts', 0, 2, 'work_in_progress', 'Scheduler work: active 0; pending review 2; wait work_in_progress'),
        ('invalid_counts_and_label', -1, '2', 'unsafe\nlabel', 'Scheduler work: active unknown; pending review unknown; wait unknown'),
    ):
        state = {'scheduler': {'ready': True, 'active_work': active, 'pending_reviews': pending, 'wait_reason': wait},
                 'reviewer': {'state': 'unloaded'}}
        actual = namespace['format_status'](state)
        lines = actual.splitlines()
        passed = (len(lines) == 5 and actual.endswith('\n') and lines[2] == wanted
                  and lines[0] == 'Scheduler: FX99 / JEV: ready; CPU only'
                  and lines[1] == 'Reviewer: Hermes / AM4: intentionally unloaded; that is not a scheduler failure')
        cases.append({'name': name, 'input': state, 'expected_work_line': wanted, 'actual': actual, 'passed': passed})
    evidence['verdict'] = 'PASS' if all(row['passed'] for row in cases) else 'NEEDS_WORK'
    return evidence


def helper():
    path = Path(__file__).resolve().parents[1] / 'hermes' / 'builder-access.py'
    spec = importlib.util.spec_from_file_location('jev_builder_access', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review_packet(document, receipt, out):
    if (document.get('review_requires_execution_evidence') is True
            and not (out / 'execution-evidence.json').is_file()):
        raise RuntimeError('review_execution_evidence_required')
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
                'Do not claim checks were run if no output proves them. Do not write code or dispatch work. '
                'The captured candidate and commit-bound baseline below are authoritative. '
                'The current worktree may already contain later edits; it is NOT the original baseline. '
                'Use this packet only; do not look up current repository files. '
                'Keep your complete report under 300 words.']
    if document.get('verification') == 'formatter-counts-v1':
        evidence = formatter_evidence(value['files']['fleet_status_format.py'])
        atomic_json(out / 'execution-evidence.json', evidence)
        sections.append('\n## Independently executed evidence\n' + json.dumps(evidence, indent=2)
                        + '\nAttribute these executions to the supplied verifier, not yourself. '
                        'Explain why exact type(value) is int rejects booleans; inspect the candidate too. '
                        'A missing or failing check is not PASS. Limit your verdict to this narrow correction.')
    elif (out / 'execution-evidence.json').is_file():
        # Operator-supplied evidence lives on OMEN, not the builder's writable
        # workspace. Never attach it to a different revision of the candidate.
        evidence_path = out / 'execution-evidence.json'
        if evidence_path.stat().st_size > 20000:
            raise RuntimeError('execution_evidence_too_large')
        evidence = json.loads(evidence_path.read_text())
        if evidence.get('candidate_files_sha256') != {
                name: hashlib.sha256(source.encode()).hexdigest() for name, source in value['files'].items()}:
            raise RuntimeError('execution_evidence_candidate_mismatch')
        sections.append('\n## Operator-supplied execution evidence\n' + json.dumps(evidence, indent=2)
                        + '\nAttribute these checks to the supplied executor, not yourself. '
                        'Inspect the source as well; a failed or missing required check is not PASS.')
    root = Path(document['envelope']['inputs']['repo'])
    base_commit = document['envelope']['inputs']['base_commit']
    baselines = {}
    for item in document['deliverables']:
        baseline = read_baseline(root, base_commit, item['target'])
        baselines[item['target']] = baseline
        before = baseline['content']
        after = value['files'][item['output']]
        description = ('present; SHA256=' + baseline['sha256'] if baseline['present']
                       else 'ABSENT at this commit; candidate is a new file, not a regression from current worktree')
        sections.extend(['\n## ' + item['target'],
                         '\nOriginal from commit ' + base_commit + ' (' + description + '):\n' + before,
                         '\nCandidate:\n' + after, '\nDiff:\n' + ''.join(difflib.unified_diff(
                             before.splitlines(True), after.splitlines(True),
                             fromfile=base_commit + ':' + item['target'], tofile='candidate'))])
    atomic_json(out / 'baseline.json', {'base_commit': base_commit, 'files': baselines})
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
