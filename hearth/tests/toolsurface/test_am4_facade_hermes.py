"""CPU-only protocol tests; no production endpoint or model is touched."""
import importlib.util
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

spec = importlib.util.spec_from_file_location('facade', Path(__file__).parents[3] / 'am4-fleet-node/scripts/oxen-facade.py')
facade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facade)


@pytest.fixture
def server(monkeypatch):
    class Engine(BaseHTTPRequestHandler):
        calls = []
        def log_message(self, *args):
            pass
        def reply(self, value, status=200, content='application/json'):
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', content)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self.reply({'default_generation_settings': {'n_ctx': 131072}, 'total_slots': 1} if self.path == '/props' else {'status':'ok'})
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.calls.append((self.path, data))
            if self.path == '/apply-template':
                self.reply({'prompt': json.dumps(data)})
            elif self.path == '/tokenize':
                self.reply({'tokens': [1] * (131070 if 'oversized' in data['content'] else 100)})
            elif data.get('stream'):
                self.reply(b'data: {"choices":[{"delta":{"tool_calls":[{"id":"c1","function":{"name":"query_knowledge","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n', content='text/event-stream')
            else:
                self.reply({'choices':[{'message':{'role':'assistant','content':'done'}}]})
    engine = ThreadingHTTPServer(('127.0.0.1', 0), Engine)
    monkeypatch.setenv('AM4_ALIAS_BACKENDS', json.dumps({name: {'host':'127.0.0.1','port':engine.server_port,'model_id':'native'} for name in ('am4-dense-27b','alias-two')}))
    monkeypatch.setenv('AM4_OXEN_TOKEN', 'fixture-token')
    monkeypatch.setenv('AM4_ADMISSION_WAIT_S', '0')
    monkeypatch.delenv('AM4_HERMES_TOKEN_FILE', raising=False)
    gateway = ThreadingHTTPServer(('127.0.0.1',0), facade.Handler)
    for srv in (engine, gateway):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield gateway.server_port, Engine
    for srv in (gateway, engine):
        srv.shutdown()
        srv.server_close()


def call(server, path='/v1/chat/completions', body=None, token='fixture-token'):
    request = Request(f'http://127.0.0.1:{server[0]}{path}', headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'}, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, response.read()
    except HTTPError as response:
        return response.code, response.read()


def prompt(**kw):
    return {'model':'am4-dense-27b','messages':[{'role':'user','content':'real task'}], 'max_tokens':256, **kw}


def test_auth_fails_closed(server, monkeypatch):
    monkeypatch.delenv('AM4_OXEN_TOKEN')
    assert call(server, body=prompt())[0] == 401


def test_readiness_is_passive_and_per_alias(server):
    status, data = call(server, '/oxen/ready?alias=am4-dense-27b')
    assert status == 200
    assert json.loads(data)['aliases'][0]['context_length'] == 131072
    assert not server[1].calls


def test_streamed_tool_arguments_are_unchanged(server):
    status, data = call(server, body=prompt(stream=True, tools=[{'type':'function','function':{'name':'query_knowledge','parameters':{'type':'object'}}}]))
    assert status == 200 and b'query_knowledge' in data and b'data: [DONE]' in data
    assert server[1].calls[0][1]['tools'][0]['function']['name'] == 'query_knowledge'


def test_prompt_plus_output_rejected_before_generation(server):
    assert call(server, body=prompt(messages=[{'role':'user','content':'oversized'}]))[0] == 400
    assert not any(path == '/v1/chat/completions' for path, _ in server[1].calls)


def test_aliases_share_single_lease_and_release(server):
    lock = facade.admission(facade.backend_for('alias-two'))
    with lock:
        assert call(server, body=prompt())[0] == 429
    assert call(server, body=prompt())[0] == 200


def test_native_admin_controls_are_not_exposed(server):
    assert call(server, '/slots/0?action=restore', {})[0] == 404
    assert call(server, body=prompt(n_predict=-1))[0] == 400
    assert call(server, body=prompt(max_tokens=-1))[0] == 400
