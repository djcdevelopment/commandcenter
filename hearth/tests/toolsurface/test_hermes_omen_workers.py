import asyncio
from contextvars import ContextVar
import importlib.util
from pathlib import Path
import threading
import pytest

from hearth.kernel import hermes_worker as lane
from hearth.kernel.governed_operator import check_governed_call
from hearth.kernel.gateway import _threaded_tool

ROOT = Path(__file__).resolve().parents[3]


def load(name):
    spec = importlib.util.spec_from_file_location(name,ROOT/'fleet/hermes'/(name+'.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def args(**kw):
    return dict(prompt='bounded work',task_id='hearth-hermes-unit',max_tokens=100,
                timeout_s=30,**kw)


@pytest.mark.parametrize('change',[{'backend':'am4-dense'},{'backend':'gcp-gemini'},
    {'model':'other'}, {'endpoint':'http://example.test'}, {'files':[]},
    {'task_family':'drafting'}, {'task_id':'unattributed'}, {'max_tokens':True},
    {'max_tokens':4097}, {'timeout_s':181}, {'prompt':''}])
def test_worker_rejects_before_native(monkeypatch,change):
    monkeypatch.setattr(lane,'native',lambda *a: pytest.fail('native reached'))
    value=args();value.update(change)
    with pytest.raises(PermissionError): lane.admit(value)


def test_worker_context_and_identity(monkeypatch):
    monkeypatch.setattr(lane,'query_omen_worker',lambda:dict(ready=True,free_slots=1,context_length=256))
    calls=[]
    def native(path,payload):
        calls.append((path,payload))
        return {'prompt':'template'} if path=='/apply-template' else {'tokens':[1]*124}
    monkeypatch.setattr(lane,'native',native)
    value=args();lane.admit(value)
    assert value['backend']=='omen-arc' and value['model']==lane.MODEL
    assert value['timeout_s']<30
    assert calls[0][1]['messages']==[{'role':'user','content':'bounded work'}]
    value=args();value['max_tokens']=101
    with pytest.raises(PermissionError,match='context exceeded'): lane.admit(value)


def test_unavailable_never_falls_back(monkeypatch):
    monkeypatch.setattr(lane,'query_omen_worker',lambda:dict(ready=False,reason='maintenance'))
    with pytest.raises(PermissionError,match='maintenance'): lane.admit(args())


def test_native_status_requires_readable_tenancy_and_known_slot_state(monkeypatch):
    from hearth.toolsurface import rotation
    rotation_value={'tenancy':{'readable':True,'image_session':None},'open_windows':[]}
    monkeypatch.setattr(rotation,'rotation_status',lambda:rotation_value)
    responses={'/health':{},'/v1/models':{'data':[{'id':lane.MODEL}]},
               '/props':{'default_generation_settings':{'n_ctx':16384},'total_slots':2},
               '/slots':[{'is_processing':False},{'is_processing':True}]}
    monkeypatch.setattr(lane,'native',lambda path:responses[path])
    state=lane.query_omen_worker()
    assert state['ready'] and state['free_slots']==1 and state['busy_slots']==1
    responses['/slots']=[{},{}]
    assert not lane.query_omen_worker()['ready']
    responses['/slots']=[{'is_processing':False}]*2
    rotation_value['tenancy']['readable']=False
    assert not lane.query_omen_worker()['ready']
    rotation_value['tenancy']['readable']=True
    rotation_value['open_windows']=['maintenance']
    assert not lane.query_omen_worker()['ready']


def test_worker_deadline_and_supervisor_are_in_actual_launch_source():
    source=(ROOT/'fleet/hermes/remote/worker/worker-mcp-server.py').read_text(encoding='utf-8')
    assert 'run_limit = min(BUILD_MAX_WAIT, max_age_s)' in source
    assert 'str(run_limit), str(run_cwd)' in source
    assert "raise ValueError('preset jobs require the hard-timeout supervisor')" in source


@pytest.mark.parametrize('name',['read_file','execute_build_request','rotation_status','kernel_status'])
def test_worker_cannot_control(name):
    with pytest.raises(PermissionError): check_governed_call('hermes-worker',name,{})


def test_threaded_gateway_keeps_identity_and_event_loop_responsive():
    identity=ContextVar('identity'); entered=threading.Event(); release=threading.Event()
    def worker():
        entered.set(); release.wait(2)
        return identity.get()
    async def run():
        identity.set('worker-3')
        task=asyncio.create_task(_threaded_tool(worker)())
        for _ in range(100):
            if entered.is_set(): break
            await asyncio.sleep(.001)
        assert entered.is_set() and not task.done()
        release.set()
        assert await task=='worker-3'
    asyncio.run(run())


def test_builtin_caller_is_isolated_across_concurrent_tools():
    from concurrent.futures import ThreadPoolExecutor
    from hearth.kernel.context import HearthContext
    context=HearthContext(repo_root=ROOT,ledger=None)
    barrier=threading.Barrier(2)
    def call(name):
        context.caller=name
        barrier.wait(timeout=2)
        return context.caller
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(call,'controller');second=pool.submit(call,'worker')
        assert first.result()=='controller' and second.result()=='worker'
    assert context.caller is None


def test_formatter_fails_closed_and_never_dumps_input():
    formatter=load('fleet_status_format').format_status
    for state in (None,[],{}, {'controller':None,'worker':[]},
                  {'controller':{'ready':1,'context_length':999},
                   'worker':{'ready':False,'context_length':999,'model':'SECRET\nESC\x1b'}}):
        text=formatter(state)
        assert len(text.splitlines())==4 and text.endswith('\n')
        assert '999' not in text and 'SECRET' not in text and '\x1b' not in text
    text=formatter({'controller':{'ready':True,'context_length':131072,'parallel_slots':1},
                    'worker':{'ready':True,'context_length':True,'parallel_slots':-1}})
    assert '131072 tokens per slot; 1 physical slots; unknown busy' in text
    assert 'True tokens' not in text and '-1' not in text


def test_status_projection_rejects_stale_values_and_secrets():
    from datetime import datetime,timezone
    clean=load('fleet_status').public_capacity
    assert not clean({'ready':True,'observed_at':'2020-01-01T00:00:00Z','context_length':128})['ready']
    state=clean({'ready':True,'observed_at':datetime.now(timezone.utc).isoformat(),
                 'context_length':16384,'free_slots':False,'key':'SECRET','reason':'SECRET'})
    assert state['context_length']==16384 and state['free_slots'] is None
    assert 'key' not in state and 'reason' not in state
