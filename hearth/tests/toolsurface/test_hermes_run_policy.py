"""CPU policy tests against the exact files that are deployed to fleet nodes."""
import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


policy = module('policy', 'fleet/hermes/remote/conductor/hermes_run_policy.py')
presets = module('presets', 'fleet/hermes/remote/worker/runner_presets.py')
META = dict(operator='hermes', promotion_policy='manual', runner_preset=policy.PRESET, builders=policy.PAIR)


@pytest.mark.parametrize('change', [dict(promotion_policy='auto'), dict(promotion_policy=None),
                                  dict(runner_preset='fx99'), dict(builders=['cc-builder-2'])])
def test_hermes_fails_closed(change):
    with pytest.raises(ValueError):
        policy.validate({**META, **change})


def test_saved_manual_policy_survives_restart_and_refuses_downgrade(tmp_path):
    snap = tmp_path / 'nodes.json'
    policy.write_snapshot(snap, policy.PAIR, None, META)
    for _ in range(3):
        saved = json.loads(snap.read_text())
        assert policy.persisted_target(saved, META) == META
        with pytest.raises(ValueError):
            policy.persisted_target(saved, {})
    with pytest.raises(ValueError):
        policy.persisted_target({'builders': policy.PAIR}, META)
    assert policy.persisted_target({'builders': []}, {}) == {}


def test_actual_promote_function_never_calls_git_write_for_manual(tmp_path):
    source = ast.parse((ROOT/'fleet/hermes/remote/conductor/conductor_maf.py').read_text(encoding='utf-8'))
    node = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_promote')
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('auto promotion reached')
    env = dict(asyncio=asyncio, validate_run_policy=policy.validate, review_result=policy.review_result,
               FARMER_REPO=tmp_path, _promote_winner=forbidden)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual _promote>', 'exec'), env)
    result = asyncio.run(env['_promote']('hermes-test', 'cc-builder-2', target_meta=META, builds={}))
    assert result['status'] == 'awaiting_review' and not calls
    with pytest.raises(ValueError):
        asyncio.run(env['_promote']('hermes-test', 'cc-builder-2'))
    with pytest.raises(ValueError):
        asyncio.run(env['_promote']('hearth-hermes-test', 'cc-builder-2'))
    assert policy.validate({}) == {}  # Legacy remains auto by default.


def test_preset_snapshot_does_not_touch_default_and_rejects_change(tmp_path):
    root = tmp_path
    (root/'runner-presets').mkdir()
    key = root/'key'; key.write_text('fixture')
    default = root/'runner.json'; default.write_text('{"model":"existing"}')
    cfg = dict(runner='openai', base_url=presets.BASE_URL, model=presets.MODEL,
               context_length=131072, max_steps=24, token_file=str(key))
    path = root/'runner-presets'/f'{presets.NAME}.json'; path.write_text(json.dumps(cfg))
    snap = root/'run.json'
    _, before = presets.resolve(root, presets.NAME, snap, probe=False)
    _, after = presets.resolve(root, presets.NAME, snap, probe=False)
    assert before == after and default.read_text() == '{"model":"existing"}'
    cfg['max_steps'] = 25; path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):
        presets.resolve(root, presets.NAME, snap, probe=False)
    with pytest.raises(ValueError):
        presets.resolve(root, 'unknown', probe=False)


def test_task_lane_metadata_and_no_padding(monkeypatch):
    from hearth.toolsurface import task_lane
    monkeypatch.setattr(task_lane, '_run_ssh', lambda *a: (_ for _ in ()).throw(AssertionError('SSH reached')))
    with pytest.raises(ValueError):
        task_lane.submit_task('task', builders=['cc-builder-2'], operator='hermes',
                              promotion_policy='manual', runner_preset=policy.PRESET)
    header = task_lane._ccmeta_header(policy.PAIR, promotion_policy='manual', runner_preset=policy.PRESET, operator='hermes')
    assert '"promotion_policy": "manual"' in header


def test_delegation_url_is_not_a_windows_drive():
    from hearth.toolsurface.build_requests import _WINDOWS_PATH_RE
    assert not _WINDOWS_PATH_RE.search('Use http://192.168.12.233:8090 and https://example.test')
    assert _WINDOWS_PATH_RE.search('Read C:/work/source.py')
    assert _WINDOWS_PATH_RE.search(r'Read C:\work\source.py')
    assert _WINDOWS_PATH_RE.search(r'Read \\server\share')


def test_external_receipt_duplicate_and_caller(tmp_path, monkeypatch):
    facade = module('receipt_facade', 'am4-fleet-node/scripts/oxen-facade.py')
    from hearth.execution.external_inference import import_outbox
    from hearth.execution.ledger import ExecutionLedger
    outbox = tmp_path/'outbox.sqlite'
    monkeypatch.setenv('AM4_HERMES_OUTBOX', str(outbox))
    facade.record_attempt('attempt-1', '2026-09-19T21:00:00+00:00', 'am4-dense-27b', {},
                          b'{"usage":{"prompt_tokens":12,"completion_tokens":3}}', True, 200, 'cc-builder-2')
    ledger = ExecutionLedger(tmp_path/'ledger')
    first = import_outbox(outbox, ledger)
    size = ledger.events_path.stat().st_size
    second = import_outbox(outbox, ExecutionLedger(tmp_path/'ledger'))
    assert first[0]['job_id'] == second[0]['job_id'] and second[0]['duplicate']
    assert ledger.events_path.stat().st_size == size
    events = [json.loads(line) for line in ledger.events_path.read_text().splitlines()]
    assert events[0]['principal']['id'] == 'cc-builder-2'
    receipt = events[0]['desired']['receipt']
    assert receipt['usage'] == {'tokens_in':12,'tokens_out':3}
    assert receipt['framework_run_id'] == 'not_supplied_by_client'
