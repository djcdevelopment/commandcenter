"""OMEN operator entry points. Queue approval never comes from the cloud."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
STATE = Path('C:/Users/derek/.fleet-scheduler')


def environment():
    os.environ.update(HEARTH_SCOPE=str(ROOT), HEARTH_ROOT='C:/work/commandcenter/hearth',
        HEARTH_EXECUTION_DIR=str(STATE / 'execution'), HEARTH_ARTIFACT_DIR=str(STATE / 'execution-artifacts'),
        HEARTH_BUILD_REQUEST_DIR=str(STATE / 'receipts'), HERMES_BUILD_REPO=str(ROOT),
        HERMES_CALLERS_PATH=str(STATE / 'callers.json'), HERMES_LEDGER_DIR=str(STATE / 'ledger'),
        HERMES_LISTEN_PORT='8713', HERMES_PROVIDERS='hearth.toolsurface.jev_scheduler',
        HEARTH_OPERATOR_HOME=str(STATE / 'operator'), FLEET_SCHEDULER_STATE=str(STATE / 'queue'))


def initialize():
    STATE.mkdir(exist_ok=True)
    user = subprocess.check_output(['whoami'], text=True).strip()
    subprocess.run(['icacls', str(STATE), '/inheritance:r', '/grant:r', user + ':(OI)(CI)F'],
                   check=True, capture_output=True)
    registry = STATE / 'callers.json'
    if not registry.exists():
        registry.write_text('{}\n')
    for name, profile, filename in [('jev-fx99', 'jev-scheduler', 'hearth.key'),
                                     ('hermes-review-fx99', 'hermes-reviewer', 'reviewer.key')]:
        if not (STATE / filename).exists():
            subprocess.run([sys.executable, '-m', 'hearth.callers.callerctl', '--registry', str(registry),
                'mint', '--id', name, '--runner-class', 'local', '--node', 'fx99', '--profile', profile,
                '--file-scope', str(ROOT), '--repo-access', str(ROOT),
                '--secret-file', str(STATE / filename)], cwd=ROOT, check=True, capture_output=True)
    from hearth.operator import core
    value = core.compile_and_write()
    print(json.dumps({'initialized': True, 'catalog_version': value['document']['catalog_version']}))


def enqueue_smoke():
    from hearth.operator.envelope import build_envelope
    from hearth.toolsurface.jev_scheduler import enqueue
    target = 'fleet/hermes/fleet_status_format.py'
    original = (ROOT / target).read_text(encoding='utf-8')
    criteria = [
        'Write fleet_status_format.py containing the complete modified format_status function; no test files.',
        'Absent scheduler data preserves the existing four-line output exactly, including the trailing newline.',
        'When state.scheduler is a dict with ready:true, show Scheduler: FX99 / JEV: ready; CPU only, independently of controller.ready.',
        'When state.reviewer is a dict with state:unloaded, show Reviewer: Hermes / AM4: intentionally unloaded; that is not a scheduler failure.',
        'Show Scheduler work: active N; pending review N; wait LABEL using nonnegative integer active_work and pending_reviews, otherwise unknown. Reject booleans. Only allow wait labels idle, work_in_progress, worker_unavailable, review_recovery_required, needs_clarification; all other labels become unknown.',
        'Preserve worker capacity checks; accept malformed nested values safely; render no user-controlled labels or control characters.',
    ]
    task = build_envelope('Extend the existing fleet status formatter for CPU-only JEV scheduling and an intentionally unloaded on-demand Hermes reviewer.',
        criteria, inputs={'repo': str(ROOT), 'base_commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                          'paths': [target], 'files': [target]},
        classification={'task_type': 'engineering', 'language': 'python'},
        constraints={'deadline_s': 1800, 'max_attempts': 1, 'max_context_tokens': 8192, 'budget': 'USD 0.10'},
        submitted_by='derek', submission_source='approved JEV qualification')
    brief = ('Implement one small Python edit now. Write the COMPLETE candidate to fleet_status_format.py in your CURRENT writable workspace root. '
             'Do not search for the original: all its source is below. Do not copy the source repository, create tests, or read other files. '
             'First action: write the candidate. Then syntax-check that file. Preserve the requested old behavior exactly.\n\n'
             + '\n'.join(criteria) + '\n\nEXISTING SOURCE:\n```python\n' + original + '\n```\n'
             'New example: state.scheduler={ready:true,active_work:0,pending_reviews:0,wait_reason:"idle"}; '
             'state.reviewer={state:"unloaded"}. These are nested dictionaries, not the old controller dictionary. '
             'Keep the old four lines when scheduler is absent/non-dict. In scheduler mode use separate scheduler/reviewer/work lines plus the existing worker and policy lines. '
             'Scheduler readiness is exact boolean true, and an absent/malformed reviewer is unknown, not unloaded.')
    doc = {'approved': True, 'title': 'CPU scheduler and sleeping reviewer status', 'envelope': task,
           'summary': 'Extend a small existing pure Python status formatter to distinguish a healthy CPU-only scheduler from an intentionally unloaded model reviewer. Show active work, pending reviews and a safe wait reason; preserve legacy output. Complete source and exact acceptance criteria are supplied locally. One file, one attempt, syntax check, no new tests.',
           'brief': brief, 'build_seconds': 300, 'priority': 10,
           'deliverables': [{'output': 'fleet_status_format.py', 'target': target}]}
    print(json.dumps({'task_id': enqueue(doc)}))


def enqueue_counts():
    from hearth.operator.envelope import build_envelope
    from hearth.toolsurface.jev_scheduler import enqueue
    prior = json.loads((ROOT / 'fleet/jev/evidence/20260921-worker-candidate.json').read_text())
    target = 'fleet/hermes/fleet_status_format.py'
    criteria = [
        'Write the complete fleet_status_format.py from the supplied candidate; change only the two scheduler count conditions. No test files or refactoring.',
        'Replace isinstance(active_work_val, int) with type(active_work_val) is int, and likewise for pending_reviews_val. Preserve the >= 0 checks.',
        'With scheduler ready=True, boolean counts True/False must both render unknown; integer counts 0/2 must render 0/2.',
        'Preserve the exact legacy four-line output including its trailing newline; retain all scheduler, reviewer, worker and safe-label behavior otherwise.',
    ]
    task = build_envelope('Correct two boolean-accepting integer checks in the previous local formatter candidate; preserve everything else.',
        criteria, inputs={'repo': str(ROOT), 'base_commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                          'paths': [target], 'files': [target]},
        classification={'task_type': 'engineering', 'language': 'python'},
        constraints={'deadline_s': 1200, 'max_attempts': 1, 'max_context_tokens': 8192, 'budget': 'USD 0.10'},
        supersedes='8bc92b76ef510e1e3e75c555912c44dd332b9114aa921617ccce0718d0d91377',
        submitted_by='derek', submission_source='approved onward count-correction lap')
    brief = ('First action: write the COMPLETE fleet_status_format.py in your current writable workspace root. '
             'The full source is below. Do not search or read other files, create tests, or refactor. '
             'Only replace the two isinstance count expressions specified below, leaving all other source unchanged. '
             'Syntax-check your saved file, then finish.\n\n' + '\n'.join(criteria)
             + '\n\nCANDIDATE SOURCE:\n```python\n' + prior['files']['fleet_status_format.py'] + '\n```\n')
    doc = {'approved': True, 'title': 'Reject boolean scheduler counts', 'envelope': task,
           'summary': 'A mechanical correction of two integer type checks in one pure Python formatter. Complete source, exact replacements, expected outputs and write access are supplied locally. One attempt, one file, syntax check, no tests or infrastructure changes. The qualified resident local builder has all required tools and context.',
           'brief': brief, 'build_seconds': 180, 'priority': 10, 'verification': 'formatter-counts-v1',
           'deliverables': [{'output': 'fleet_status_format.py', 'target': target}]}
    print(json.dumps({'task_id': enqueue(doc)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init', 'serve', 'enqueue-smoke', 'enqueue-counts', 'inspect'])
    args = parser.parse_args()
    environment()
    if args.action == 'init':
        initialize()
    elif args.action == 'serve':
        sys.argv = [sys.argv[0]]
        spec = importlib.util.spec_from_file_location('jev_gateway', ROOT / 'fleet/hermes/serve-hearth.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
    elif args.action == 'enqueue-smoke':
        enqueue_smoke()
    elif args.action == 'enqueue-counts':
        enqueue_counts()
    else:
        from hearth.toolsurface.jev_scheduler import connection
        with connection() as db:
            print(json.dumps([dict(r) for r in db.execute('SELECT id,state,receipt_id FROM jobs')]))


if __name__ == '__main__':
    main()
