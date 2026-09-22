"""Narrow JEV adapter over operator contracts and real, review-only VM builds.

This module is the sole queue writer. No cloud API calls, model rotation,
automatic promotion, raw command ingestion, or inventory-executor substitution.
"""
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from datetime import timedelta

from fleet.jev import policy
from fleet.jev.client import atomic_json
from hearth.operator import authority, canonical, core, envelope, inspection, paths, proposal, validate
from hearth.operator.identity import resolve_from_door
from hearth.kernel.hermes_worker import query_omen_worker
from hearth.kernel.governed_operator import qualify_builders
from hearth.toolsurface import build_requests as br

LOCK = threading.RLock()
PROFILE = 'omen-resident-hearth'
BUILDER = 'cc-builder-2'
TARGET = 'mechnet_build'


def home():
    return Path(os.environ['FLEET_SCHEDULER_STATE'])


def connection():
    home().mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home() / 'queue.sqlite', timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL, document TEXT NOT NULL, receipt_id TEXT, result TEXT)')
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active ON jobs ((1)) WHERE state IN ('dispatching','running','review_pending','reviewing')")
    return db


def caller():
    who = resolve_from_door()
    if not who.attested or (who.caller or {}).get('profile') != 'jev-scheduler':
        raise PermissionError('scheduler_identity_required')
    return who


def enqueue(document):
    """Local operator-only entry: queue is not writable through JEV or its API key."""
    task = document['envelope']
    canonical.validate_contract(task, 'task-envelope.v1')
    if canonical.identity_of(task, 'envelope_id') != task['envelope_id']:
        raise ValueError('envelope_identity_mismatch')
    if Path(task['inputs']['repo']).resolve() != Path(os.environ['HERMES_BUILD_REPO']).resolve():
        raise ValueError('unapproved_build_repository')
    policy.text(document['summary'], 1200)
    if not document.get('approved') or not document.get('deliverables'):
        raise ValueError('approved_task_and_deliverables_required')
    if not 60 <= document['build_seconds'] <= 600:
        raise ValueError('build_window_out_of_bounds')
    for item in document['deliverables']:
        for name in (item['output'], item['target']):
            if not re.fullmatch(r'[A-Za-z0-9_./-]+', name) or '..' in Path(name).parts or Path(name).is_absolute():
                raise ValueError('unsafe_deliverable')
    # Reuse the actual pure dispatch guard before storing or spending on this task.
    # Examples are part of the brief too; never silently rewrite a refused path.
    br._delegation_brief(
        {'repo': task['inputs']['repo'], 'title': 'JEV scheduled: ' + document['title'],
         'acceptance_criteria': task['acceptance_criteria']},
        document['brief'], [item['output'] for item in document['deliverables']])
    task_id = 'jev-' + task['envelope_id'][:24]
    with LOCK, connection() as db:
        envelope.store_envelope(task, task_id)
        db.execute('INSERT INTO jobs(id,state,document) VALUES(?,?,?)',
                   (task_id, 'ready', json.dumps(document)))
    return task_id


def capture(native, catalog):
    """Use the existing snapshot contract, explicitly observing only this route."""
    now = canonical.utc_now()
    def field(value, ttl=30, reason=None):
        return inspection.make_field(value, now, ttl, 'scheduler:native_omen_worker',
                                     reason or ('not_observed_by_scheduler' if value is None else None))
    snapshot = {
        'contract_version': 'capacity-snapshot.v1', 'kind': 'planning',
        'catalog_version': catalog['catalog_version'], 'observed_at': canonical.rfc3339(now),
        'planning_valid_until': canonical.rfc3339(now + timedelta(seconds=300)), 'planning_validity_s': 300,
        'door': {'reachable': field(True), 'kernel_version': field(None),
                 'providers_mounted': field(['hearth.toolsurface.jev_scheduler'])},
        'hosts': {}, 'gpus': {}, 'models': {},
        'rungs': {'omen-arc': {'ready': field(native.get('ready') is True),
            'rung_state': field(None), 'residency': field([native['model']] if native.get('ready') else None),
            'occupancy': field(native.get('busy_slots')), 'active_slots': field(native.get('parallel_slots')),
            'free_vram_gb': field(None), 'declared_live': field(True)}},
        'leases': field(None), 'holds': field(None), 'reachability': {'sweep': field(None)},
        'trial_runway': {key: field(None) for key in ('budget_tokens','reserve_tokens','tokens_spent','suppressed')},
    }
    snapshot = canonical.stamp_identity(snapshot, 'snapshot_id')
    canonical.validate_contract(snapshot, 'capacity-snapshot.v1')
    inspection.write_snapshot(snapshot)
    return snapshot


def route(task, snapshot, catalog, provider='typesafe', model=policy.MODEL):
    return proposal.build_proposal(
        envelope_id=task['envelope_id'], catalog_version=catalog['catalog_version'],
        snapshot_id=snapshot['snapshot_id'], snapshot_expires_at=snapshot['planning_valid_until'],
        orchestrator={'provider': provider, 'model': model, 'endpoint_or_version': policy.ENDPOINT,
                      'client': 'jev-scheduler', 'harness': 'fleet-scheduler', 'session': task['envelope_id']},
        policy_version=authority.policy_version(),
        eligible_routes=[{'route_id': TARGET, 'route_kind': 'build', 'target': TARGET, 'estimated_cost': 'USD 0.00'}],
        rejected_routes=[], selected_graph={'nodes': [{'id': 'build', 'route_kind': 'build', 'target': TARGET,
            'inputs': {'envelope_id': task['envelope_id']}, 'expected': {'attempts': 1}}], 'edges': []},
        assumptions=['Only the separately qualified resident VM builder is admitted.'],
        uncertainty={'confidence': 'JEV suitability is advisory, not verification', 'unknowns': ['Future task runtime']},
        expected={'time_s': 600, 'attempts': 1, 'context_tokens': min(8192, task['constraints']['max_context_tokens']),
                  'resources': ['omen-arc']},
        required_authority=['submit_mechnet_task', 'create_build_request'], required_approvals=[],
        rationale='JEV ranked an approved task; local operator validation and native admission control dispatch.')


def knowledge_digest():
    """Read bounded authoritative knowledge files, never regenerate projections."""
    root = Path(os.environ.get('FLEET_KNOWLEDGE_ROOT', 'C:/work/commandcenter/knowledge'))
    observed = {}
    for name in ('findings.json', 'known_bad_models.json', 'known_good_models.json', 'offload.json'):
        target = root / name
        if target.is_file() and target.stat().st_size <= 2_000_000:
            doc = json.loads(target.read_text(encoding='utf-8-sig'))
            observed[name] = doc.get('corpus_digest') or policy.digest(doc)
    return policy.digest(observed)


def recent_outcomes(db):
    """Numeric projection fields only; never forward arbitrary historical prose."""
    root = Path(os.environ.get('FLEET_KNOWLEDGE_ROOT', 'C:/work/commandcenter/knowledge'))
    result = []
    target = root / 'known_good_models.json'
    if target.is_file() and target.stat().st_size < 2_000_000:
        document = json.loads(target.read_text(encoding='utf-8-sig'))
        for row in document.get('entries', []):
            if row.get('backend') != 'omen-arc':
                continue
            samples, rate = row.get('samples'), row.get('success_rate')
            if type(samples) is int and samples > 0 and type(rate) in (int, float) and 0 <= rate <= 1:
                result.append(f'Historical OMEN inference: {samples} samples, success fraction {rate:.3f}. This is not patch acceptance or current capacity.')
                break
    counts = dict(db.execute("SELECT state,COUNT(*) FROM jobs WHERE state IN ('awaiting_manual_acceptance','review_failed','failed') GROUP BY state"))
    for state, count in sorted(counts.items()):
        result.append(f'This scheduler profile: {count} task outcomes in state {state}; no automatic promotion.')
    return result[:4]


def pending_review(row):
    document = json.loads(row['document'])
    waiting = (document.get('review_requires_execution_evidence') is True
               and not (home() / row['id'] / 'execution-evidence.json').is_file())
    return {'ok': True, 'candidates': [], 'pending_reviews': [] if waiting else [row['id']],
            'active_build': None, 'reviewer': 'unloaded',
            'wait_reason': 'review_evidence_pending' if waiting else None}


def _prepare():
    who = caller()
    with LOCK, connection() as db:
        active = db.execute("SELECT * FROM jobs WHERE state IN ('dispatching','running','review_pending','reviewing')").fetchone()
        if active:
            if active['state'] == 'running':
                outcome = br.update_build_request(active['receipt_id'], sync_delegation=True)
                delegation = outcome.get('execution', {}).get('delegation') or {}
                if delegation.get('result') == 'awaiting_review':
                    db.execute('UPDATE jobs SET state=?,result=? WHERE id=?',
                               ('review_pending', json.dumps(outcome), active['id']))
                    return pending_review(active)
                if outcome.get('status') in ('failed', 'blocked'):
                    db.execute('UPDATE jobs SET state=?,result=? WHERE id=?', ('failed', json.dumps(outcome), active['id']))
                    return {'ok': True, 'candidates': [], 'pending_reviews': [], 'wait_reason': 'build_failed'}
            if active['state'] == 'review_pending':
                return pending_review(active)
            return {'ok': True, 'candidates': [], 'active_build': active['receipt_id'],
                    'pending_reviews': [active['id']] if active['state'] == 'review_pending' else [],
                    'reviewer': 'recovery_required' if active['state'] == 'reviewing' else 'unloaded',
                    'wait_reason': 'review_recovery_required' if active['state'] == 'reviewing' else 'work_in_progress'}
        if db.execute("SELECT id FROM jobs WHERE state IN ('failed','review_failed')").fetchone():
            return {'ok': True, 'candidates': [], 'pending_reviews': [], 'wait_reason': 'failed_task_requires_operator'}
        jobs = db.execute("SELECT * FROM jobs WHERE state='ready' ORDER BY id").fetchall()
        native = query_omen_worker()
        if not native.get('ready') or native.get('free_slots', 0) < 1:
            return {'ok': True, 'candidates': [], 'pending_reviews': [], 'wait_reason': 'worker_unavailable'}
        catalog = core.catalog_document()
        snapshot = capture(native, catalog)
        candidates, private = [], {}
        for row in jobs:
            doc = json.loads(row['document'])
            task = doc['envelope']
            draft = route(task, snapshot, catalog)
            verdict = validate.validate_proposal(draft, who, catalog=catalog, current_snapshot=snapshot,
                                                envelope=task, run_id=row['id'])
            atomic_json(home() / row['id'] / 'admission.json', verdict)
            if verdict['verdict'] != 'validated' or task['constraints']['max_context_tokens'] > native['context_length']:
                continue
            cid = policy.digest({'task': row['id'], 'profile': PROFILE, 'policy': authority.policy_version()})[:32]
            deadline = canonical.parse_rfc3339(task['submitted_at']) + timedelta(seconds=task['constraints']['deadline_s'])
            candidates.append({'candidate_id': cid, 'task_id': row['id'], 'summary': doc['summary'],
                'task_type': task['classification']['task_type'], 'language': task['classification']['language'],
                'profile': PROFILE, 'profile_summary': 'Linux MechNet VM with scoped file read/write and Python syntax-check tools. Uses already resident OMEN inference, 16384 context per physical slot. Receives complete original function and exact acceptance criteria in its local prompt. Can write a single Python file in its isolated workspace. One attempt; candidate retained for manual review. No hardware, service or network change is required for this task.',
                'context_tokens': native['context_length'], 'expected_seconds': None, 'recent_outcomes': recent_outcomes(db),
                'priority': doc.get('priority', 0), 'deadline_at': canonical.rfc3339(deadline),
                'submitted_at': task['submitted_at']})
            private[cid] = {'task_id': row['id'], 'proposal': draft}
        candidates.sort(key=lambda r: (-r['priority'], r['deadline_at'], r['submitted_at']))
        candidates = candidates[:policy.MAX_CANDIDATES]
        atomic_json(home() / 'prepared.json', {'snapshot': snapshot, 'catalog_version': catalog['catalog_version'],
                    'candidates': candidates, 'private': {r['candidate_id']: private[r['candidate_id']] for r in candidates}})
        return {'ok': True, 'snapshot_id': snapshot['snapshot_id'], 'policy_version': authority.policy_version(),
                'knowledge_digest': knowledge_digest(), 'candidates': candidates, 'pending_reviews': [],
                'wait_reason': None if candidates else 'no_admissible_ready_task'}


def scheduler_prepare() -> dict:
    """Observe, reconcile, and prepare metadata-only candidates; writes snapshots."""
    try:
        return _prepare()
    except Exception as error:
        return {'ok': False, 'reason_code': 'prepare_' + type(error).__name__}


def _select(candidate_id, snapshot_id, evidence):
    who = caller()
    with LOCK, connection() as db:
        db.execute('BEGIN IMMEDIATE')
        saved = json.loads((home() / 'prepared.json').read_text())
        selected = saved['private'].get(candidate_id)
        if selected is None or saved['snapshot']['snapshot_id'] != snapshot_id:
            raise ValueError('unknown_or_stale_candidate')
        task_id = selected['task_id']
        job = db.execute('SELECT * FROM jobs WHERE id=?', (task_id,)).fetchone()
        if job['receipt_id']:
            return {'ok': True, 'task_id': task_id, 'receipt_id': job['receipt_id'], 'duplicate': True}
        if job['state'] != 'ready' or db.execute("SELECT id FROM jobs WHERE state IN ('dispatching','running','review_pending','reviewing')").fetchone():
            raise ValueError('queue_not_available')
        if evidence.get('candidate_id') != candidate_id or evidence.get('snapshot_id') != snapshot_id:
            raise ValueError('decision_binding_mismatch')
        # Reevaluate the bounded answers locally; a caller cannot submit an arbitrary graph.
        answers = {}
        for judgment in evidence['judgments']:
            cid = judgment['candidate_id']
            answers['fit_' + cid] = {'type': 'score', 'score': judgment['score'], 'confidence': judgment['confidence'], 'probabilities': judgment['probabilities']}
            answers['ambiguity_' + cid] = {'type': 'noul', 'noul': judgment['ambiguity']}
        winner, _ = policy.evaluate({'model': evidence['usage']['model'], 'answers': answers}, saved['candidates'])
        if winner != candidate_id:
            raise ValueError('selection_does_not_match_policy')
        doc = json.loads(job['document'])
        task = doc['envelope']
        native = query_omen_worker()
        if not native.get('ready') or native.get('free_slots', 0) < 1 or native['context_length'] < task['constraints']['max_context_tokens']:
            raise ValueError('worker_changed')
        catalog = core.catalog_document()
        verdict = validate.validate_proposal(selected['proposal'], who, catalog=catalog,
            current_snapshot=saved['snapshot'], envelope=task, run_id=task_id)
        validate.store_validation(verdict, task_id, requester=who)
        if verdict['verdict'] != 'validated':
            raise ValueError('operator_validation_refused')
        qualify_builders([BUILDER])
        proposal.store_proposal(selected['proposal'], task_id)
        receipt = br.create_build_request(title='JEV scheduled: ' + doc['title'], repo=task['inputs']['repo'],
            request=doc['brief'], acceptance_criteria=task['acceptance_criteria'],
            deliverables=[r['output'] for r in doc['deliverables']], backend='omen-arc', lane='jev')
        receipt_id = receipt['receipt_id']
        atomic_json(home() / task_id / 'decision.json', evidence)
        db.execute('UPDATE jobs SET state=?,receipt_id=? WHERE id=?', ('dispatching', receipt_id, task_id))
        db.commit()  # Persist before network: interrupted dispatch is held, never guessed/repeated.
        result = br.execute_build_request(receipt_id, mode='delegate', builders=[BUILDER],
            max_age_s=doc['build_seconds'], promotion_policy='manual', runner_preset=PROFILE,
            operator='jev', evidence='JEV decision; operator validation; real OMEN-backed MechNet VM dispatch.')
        db.execute('UPDATE jobs SET state=?,result=? WHERE id=?',
                   ('running' if result.get('execution', {}).get('delegation', {}).get('plan_id') else 'failed', json.dumps(result), task_id))
        return {'ok': True, 'task_id': task_id, 'receipt_id': receipt_id}


def scheduler_select(candidate_id: str, snapshot_id: str, evidence: dict) -> dict:
    """Validate and dispatch one prepared candidate; never accepts commands/paths."""
    try:
        if not re.fullmatch('[0-9a-f]{32}', candidate_id) or len(json.dumps(evidence)) > 20000:
            raise ValueError('invalid_decision_evidence')
        return _select(candidate_id, snapshot_id, evidence)
    except Exception as error:
        return {'ok': False, 'reason_code': 'select_' + type(error).__name__}


def scheduler_review(task_id: str, report: str | None = None, metadata: dict | None = None) -> dict:
    """Local-only review packet/outcome exchange; no cloud forwarding or promotion."""
    caller()
    if not re.fullmatch(r'jev-[0-9a-f]{24}', task_id):
        raise ValueError('invalid_task_id')
    with LOCK, connection() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (task_id,)).fetchone()
        if row is None or row['state'] not in ('review_pending', 'reviewing'):
            return {'ok': False, 'reason_code': 'review_not_pending'}
        from fleet.jev.artifacts import review_packet, review_seat
        if report is None and not (metadata or {}).get('failed'):
            packet = review_packet(json.loads(row['document']), json.loads(row['result']), home() / task_id)
            db.execute('UPDATE jobs SET state=? WHERE id=?', ('reviewing', task_id))
            db.commit()  # Recovery is visible even if the transport/model load fails.
            try:
                review_seat('start', task_id)
            except Exception:
                review_seat('stop', task_id)
                db.execute('UPDATE jobs SET state=? WHERE id=?', ('review_failed', task_id))
                raise
            return {'ok': True, 'packet': packet, 'task_id': task_id}
        released = review_seat('stop', task_id)
        if released.get('model_resident'):
            raise RuntimeError('reviewer_not_released')
        if (metadata or {}).get('failed'):
            db.execute('UPDATE jobs SET state=? WHERE id=?', ('review_failed', task_id))
            br.update_build_request(row['receipt_id'], summary='Hermes review failed; candidate retained, review model released.')
            return {'ok': True, 'task_id': task_id, 'reviewer_unloaded': True}
        if not isinstance(report, str) or not 80 <= len(report) <= 16000:
            raise ValueError('review_artifact_required')
        metadata = {**(metadata or {}), 'reviewer_unloaded': True}
        atomic_json(home() / task_id / 'review.json', {'report': report, 'metadata': metadata,
            'automatic_promotion': False, 'semantic_review_is_not_proof': True})
        br.update_build_request(row['receipt_id'], summary='Hermes review delivered; manual acceptance/promotion remains.',
            evidence='Review SHA256=' + policy.digest(report) + '; model=' + str(metadata.get('model')))
        db.execute('UPDATE jobs SET state=? WHERE id=?', ('awaiting_manual_acceptance', task_id))
        return {'ok': True, 'task_id': task_id, 'promoted': False}


def get_tools():
    from .fs import read_file, list_dir, glob_files
    from .git import git_status, git_log
    return [scheduler_prepare, scheduler_select, scheduler_review, read_file, list_dir, glob_files, git_status, git_log]
