import json

import pytest

from hearth.kernel.governed_operator import check_governed_call
from hearth.kernel.capabilities import load_profiles
from hearth.toolsurface.knowledge import query_knowledge
from hearth.execution.external_inference import validate_receipt, ADAPTERS


def test_governed_profile_never_inherits_write_or_admin():
    caps = load_profiles()['governed-operator'].capabilities
    assert not caps & {'write','repo_write','kernel_admin','execution','knowledge_write','generate','rotation_admin'}


@pytest.mark.parametrize('tool,args', [
    ('local_generate',{'backend':'gcp-gemini'}), ('rebuild_knowledge',{}),
    ('git_commit_push',{}), ('run_tests',{}),
    ('submit_task',{'builders':['cc-builder-2']}),
    ('create_build_request',{'repo':'C:/work/commandcenter','backend':'gcp-gemini'}),
    ('execute_build_request',{'mode':'agent'}),
    ('query_findings',{}),
])
def test_denies_unbounded_or_nonlocal_paths(tool, args):
    with pytest.raises(PermissionError):
        check_governed_call('governed-operator',tool,args)


def test_builder_qualification_is_required(monkeypatch):
    args = {'builders':['cc-builder-2','cc-builder-3'], 'max_age_s':900, 'mode':'delegate'}
    def refused(builders):
        raise PermissionError('not ready')
    monkeypatch.setattr('hearth.kernel.governed_operator.qualify_builders',refused)
    with pytest.raises(PermissionError):
        check_governed_call('governed-operator','execute_build_request',args)
    monkeypatch.setattr('hearth.kernel.governed_operator.qualify_builders',lambda builders: None)
    check_governed_call('governed-operator','execute_build_request',args)
    assert args['promotion_policy'] == 'manual' and args['operator'] == 'hermes'
    assert args['runner_preset'] == 'omen-resident-hearth'


def test_knowledge_filters_bounds_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv('HEARTH_SCOPE',str(tmp_path))
    (tmp_path/'findings.json').write_text(json.dumps({'evidence_watermark':'2026-09-19', 'entries':[
        {'id':'1','host':'am4','model':'27b','topic':'capacity','hardware_profile_id':'two-cards'},
        {'id':'2','host':'fx99','model':'7b','topic':'capacity'},
    ]}))
    result = query_knowledge('capacity','am4','27b',limit=1,knowledge_dir=str(tmp_path))
    assert len(result['results']) == 1
    row = result['results'][0]
    assert row['source_id'] == 'findings.json#/entries/0'
    assert row['watermark'] == '2026-09-19' and row['hardware_profile'] == 'two-cards'
    assert result['live_state_precedence']
    with pytest.raises(ValueError):
        query_knowledge(limit=100)


def test_receipt_schema_has_honest_hermes_adapter():
    assert ADAPTERS['hermes.physical-attempt.v1'] == 'hermes-direct'
    assert ADAPTERS['deepagents.physical-attempt.v1'] == 'deepagents-direct'
    receipt = {key:'test' for key in ('run_id','attempt_id','logical_call_id','framework_run_id','supervisor_id','model_alias','started_at','finished_at')}
    receipt.update(schema='hermes.physical-attempt.v1',accounting_owner='direct',execution_mode='external',phase='task',outcome='unknown',provider={'execution_class':'local','identity_sha256':'test'},usage={'tokens_in':None,'tokens_out':None})
    validate_receipt(receipt)
    receipt['usage']['tokens_out'] = -1
    with pytest.raises(ValueError):
        validate_receipt(receipt)
