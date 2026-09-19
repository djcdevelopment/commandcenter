import copy
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

import pytest

from hearth.scheduler.native_capacity import normalize_am4_native, ALIAS, RESOURCE, MODEL

NOW = datetime(2026,9,19,22,0,tzinfo=timezone.utc)
PAYLOAD = {'all_ready':True,'aliases':[{'alias':ALIAS,'ready':True,'status':200,
    'context_length':131072,'parallel_slots':1,'physical_resource':RESOURCE,'model':'models/'+MODEL}]}


def check(value=PAYLOAD, stamp=NOW):
    return normalize_am4_native(value,stamp.isoformat(),NOW)


def test_ready_is_one_slot_not_placement():
    result = check()
    assert result['ready'] and result['parallel_slots'] == 1 and result['context_length'] == 131072
    assert result['gpu_placed'] is None and result['gpu_qualified_models'] == []


@pytest.mark.parametrize('field,value',[('ready',False),('status',503),('model','other.gguf'),
    ('context_length',4096),('context_length',True),('parallel_slots',2),('parallel_slots',True),
    ('physical_resource','another host')])
def test_invalid_native_identity_is_unavailable(field,value):
    payload=copy.deepcopy(PAYLOAD); payload['aliases'][0][field]=value
    result=check(payload)
    assert result['ready'] is False and result['parallel_slots'] == 0


@pytest.mark.parametrize('payload',[None,[],{}, {'aliases':None}, {'aliases':[None,False]}, {'aliases':[]}])
def test_malformed_payload_fails_closed(payload):
    assert not check(payload)['ready']


@pytest.mark.parametrize('seconds',[-6,31])
def test_stale_and_future(seconds):
    assert not check(stamp=NOW-timedelta(seconds=seconds))['ready']


def test_aliases_cannot_multiply_physical_slots():
    payload=copy.deepcopy(PAYLOAD)
    payload['aliases'].append({**payload['aliases'][0], 'alias':'other-name'})
    assert check(payload)['parallel_slots'] == 1
    payload['aliases'].append({**payload['aliases'][0], 'ready':False})
    assert not check(payload)['ready']


def test_capture_native_is_authenticated_passive_and_not_gpu_qualified(monkeypatch):
    from hearth.toolsurface import scheduler, backends
    node=SimpleNamespace(endpoint='http://fixture',auth_env='FIXTURE_KEY')
    monkeypatch.setattr(backends,'load_pool',lambda:SimpleNamespace(by_name=lambda name:node if name=='am4-dense' else None))
    monkeypatch.delenv('HERMES_AM4_KEY_FILE',raising=False)
    monkeypatch.setenv('FIXTURE_KEY','fixture')
    calls=[]
    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,size): return json.dumps(PAYLOAD).encode()
    def request(req,timeout):
        calls.append((req.full_url,req.get_header('Authorization')))
        return Response()
    monkeypatch.setattr(scheduler.urllib.request,'urlopen',request)
    result=scheduler.capture_resource_snapshot()
    assert result['am4-dense']['ready'] and result['am4-dense']['gpu_placed'] is None
    assert calls == [('http://fixture/oxen/ready?alias=am4-dense-27b','Bearer fixture')]
    assert not result['am4-ollama']['ready']
