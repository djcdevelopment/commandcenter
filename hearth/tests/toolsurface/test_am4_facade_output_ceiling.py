"""Offline boundary tests for the scoped dense capability; no live endpoints."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import pytest

spec = importlib.util.spec_from_file_location('facade_ceiling', Path(__file__).parents[3] / 'am4-fleet-node/scripts/oxen-facade.py')
facade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facade)


def backend(alias='am4-dense-27b', **changes):
    return {'_alias':alias,'model_id':'qwen3-27b','api':'vllm','host':'127.0.0.1','port':18094,**changes}


def guard(budget, *, b=None, context=49152, tokens=100, model='qwen3-27b', **payload_options):
    b = b if b is not None else backend(output_ceiling=24576)
    payload={'model':'qwen3-27b','messages':[{'role':'user','content':'source'}], 'max_tokens':budget,**payload_options}
    state={'alias':'am4-dense-27b','ready':True,'context_length':context,'model':model}
    with patch.object(facade,'backend_request',return_value=(200,{},json.dumps({'count':tokens}).encode())) as request:
        result=facade.guard_context(payload,b,state)
    return result,payload,request


def test_default_remains_8192():
    assert guard(8192,b=backend())[0]['output_budget']==8192
    with pytest.raises(ValueError,match='1..8192'):guard(8193,b=backend())


def test_scoped_ceiling_and_exact_context_boundary():
    result,payload,request=guard(24576,tokens=24544,tools=[{'type':'function'}],chat_template_kwargs={'enable_thinking':True})
    assert result=={'prompt_tokens':24544,'output_budget':24576}
    wire=json.loads(request.call_args.args[2]);assert wire['tools']==[{'type':'function'}]
    assert wire['chat_template_kwargs']=={'enable_thinking':True} and wire['model']=='qwen3-27b'
    with pytest.raises(ValueError,match='exceeds context'):guard(24576,tokens=24545)
    with pytest.raises(ValueError,match='1..24576'):guard(24577)


@pytest.mark.parametrize('context',[32768,49151,None,True,'65536'])
def test_undersized_or_invalid_live_context_refuses(context):
    with pytest.raises(facade.CapabilityUnavailable,match='requires live'):guard(24576,context=context)


def test_wrong_live_model_refuses_and_alias_cannot_impersonate():
    with pytest.raises(facade.CapabilityUnavailable,match='requires live'):guard(24576,model='other')
    with patch.object(facade,'alias_backends',return_value={'other':backend(output_ceiling=24576)}):
        with pytest.raises(ValueError,match='restricted'):facade.backend_for('other')


@pytest.mark.parametrize('setting',[None,True,'24576',9000,32768,-1])
def test_invalid_config_refuses(setting):
    with pytest.raises(ValueError,match='output_ceiling'):guard(1,b=backend(output_ceiling=setting))


@pytest.mark.parametrize('change',[{'_alias':'am4-tool-5070'},{'api':'llama'},{'model_id':'other'}])
def test_extended_config_restricted_to_dense_vllm(change):
    with pytest.raises(ValueError,match='restricted'):guard(24576,b=backend(output_ceiling=24576,**change))


def test_other_alias_and_caller_override_stay_default():
    b=backend('other')
    assert guard(8192,b=b,output_ceiling=24576)[0]['output_budget']==8192
    with pytest.raises(ValueError,match='1..8192'):guard(24576,b=b,output_ceiling=24576)
    with pytest.raises(ValueError,match='one completion'):guard(24576,n=2)


def test_completion_token_synonym_normalized():
    result,payload,_=guard(1,max_completion_tokens=24576)
    assert result['output_budget']==payload['max_tokens']==24576
    assert 'max_completion_tokens' not in payload


def test_scoped_dense_keeps_ordinary_32k_requests():
    assert guard(8192,context=32768)[0]['output_budget']==8192
    with pytest.raises(facade.CapabilityUnavailable):guard(8193,context=32768)


def test_tracked_alias_map_ceilings():
    path=Path(__file__).parents[3]/'am4-fleet-node/config/alias-backends.json'
    aliases=json.loads(path.read_text())
    with patch.object(facade,'alias_backends',return_value=aliases):
        for alias in aliases:
            assert facade.output_ceiling(facade.backend_for(alias))==(24576 if alias=='am4-dense-27b' else 8192)
