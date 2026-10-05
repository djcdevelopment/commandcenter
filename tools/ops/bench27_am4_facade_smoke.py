#!/usr/bin/env python3
"""Proposed 8090 facade smoke, parent-invoked only after review; never changes services."""
import argparse
import hashlib
import http.client
import json
import re
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hearth.toolsurface.backends import Backend, load_pool
from tools.ops.bench27_am4_probe import guard_check, event
from tools.ops.bench27_am4_report import counters, no_leases

HOST, PORT, ALIAS = '10.44.0.2', 8090, 'am4-dense-27b'
SCRIPT_SHA = 'd6d6887f5fe3c31ba574d6bdfbdc609e6474afbc7d2866c362355d0275c7d54e'
BUDGET, WINDOW = 24576, 49152
POOL_PATH = Path('/home/derek/hearth-production/backends-linux.toml')
AUDIT = """import hashlib,json,pathlib,urllib.request
h=pathlib.Path.home()
paths={'facade':h/'am4-fleet-node/scripts/oxen-facade.py','aliases':h/'.config/am4-fleet/alias-backends.json','recipe':h/'run-vllm-canary.sh'}
r={'sha256':{k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()}}
for key,path in [('models','/v1/models'),('metrics','/metrics')]:
 with urllib.request.urlopen('http://127.0.0.1:18094'+path,timeout=3) as f:
  raw=f.read(2000000).decode();r[key]=json.loads(raw) if key=='models' else raw
print(json.dumps(r))
"""


class SmokeError(RuntimeError):
    def __init__(self, code):
        self.code=code
        super().__init__(code)


def check_leases(path):
    try: no_leases(path)
    except Exception: raise SmokeError('door_lease_active_or_check_unavailable') from None


def validate_cancel_counters(before,after):
    delta={k:after[k]-before[k] for k in ['completed','abort','success']}
    if delta['completed']!=0: raise SmokeError('cancel_completed_counter_changed')
    if delta['abort'] not in (0,1) or delta['success']!=delta['abort']:
        raise SmokeError('cancel_unexpected_counter_delta')
    return delta


def make_overflow(count_tokens):
    low,high=1,WINDOW
    for _ in range(17):
        n=(low+high)//2
        body=request_body('x '*n);count=count_tokens(body)
        if WINDOW-BUDGET+128<=count<=WINDOW-128: return body,count
        if count<WINDOW-BUDGET+128: low=n+1
        else: high=n-1
    raise SmokeError('overflow_prompt_search_failed')


def validate_overflow(status,raw,count):
    if not WINDOW-BUDGET+128<=count<=WINDOW-128:
        raise SmokeError('overflow_input_not_inside_window')
    expected={'error':f'rendered prompt {count} + output {BUDGET} exceeds context {WINDOW}'}
    if status!=400 or json.loads(raw)!=expected:
        raise SmokeError('not_exact_facade_context_refusal')


def validate_short(rec):
    usage=rec.get('usage') or {}
    if (not rec['done'] or rec['finish_reason']!='stop' or rec['content'].strip()!='323'
            or not rec['reasoning'].strip() or any(t in rec['content'] for t in ['<think>','</think>'])
            or usage.get('prompt_tokens')!=rec['prompt_tokens_exact']
            or type(usage.get('completion_tokens')) is not int or not 0<usage['completion_tokens']<=BUDGET
            or type((usage.get('completion_tokens_details') or {}).get('reasoning_tokens')) is not int
            or not 0<usage['completion_tokens_details']['reasoning_tokens']<=usage['completion_tokens']):
        raise SmokeError('short reasoning/reservation smoke failed')
    return usage


def write(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def audit(timeout=8):
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', HOST,
                             'python3 -c '+shlex.quote(AUDIT)], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=timeout, check=True)
    return json.loads(result.stdout)


def validate_audit(record, facade_sha, alias_sha):
    if record['sha256'] != {'facade': facade_sha, 'aliases': alias_sha, 'recipe': SCRIPT_SHA}:
        raise SmokeError('source/alias/recipe hash mismatch')
    models = [m for m in record['models']['data'] if m['id']=='qwen3-27b']
    if len(models)!=1 or models[0].get('root')!='/home/derek/models/qwen3-27b-gptq-int4' or models[0].get('max_model_len')!=WINDOW:
        raise SmokeError('wrong actual model/root/window')
    try:
        result=counters(record['metrics'].encode())
        reasons={}
        for line in record['metrics'].splitlines():
            if line.startswith('vllm:request_success_total{'):
                reason=re.search(r'finished_reason="([^"]+)"',line)
                if not reason: raise ValueError('missing reason')
                key=reason.group(1);reasons[key]=reasons.get(key,0)+float(line.rsplit(' ',1)[1])
        if not {'stop','length','abort'}<=reasons.keys(): raise ValueError('missing labeled counters')
        result.update(completed=reasons['stop']+reasons['length'],abort=reasons['abort'],reasons=reasons)
        return result
    except Exception:
        raise SmokeError('engine_counters_invalid') from None


def request_body(text):
    return {'model': ALIAS, 'messages': [{'role':'user','content':text}],
            'max_tokens': BUDGET, 'temperature':0, 'seed':42,
            'chat_template_kwargs':{'enable_thinking':True}, 'stream':True,
            'stream_options':{'include_usage':True}}


class Smoke:
    def __init__(self, args, token):
        self.args, self.token = args, token
        self.started = time.monotonic()
        self.end = self.started+150
        self.cleanup_end = self.started+170
        self.conn = self.sock = None
        self.failure = None
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.watcher = threading.Thread(target=self.watch, daemon=True)
        self.watcher.start()

    def cancel(self, reason=None):
        with self.lock:
            if reason: self.failure = self.failure or reason
            if self.sock:
                try: self.sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            if self.conn: self.conn.close()

    def check(self):
        if self.failure: raise SmokeError(self.failure)
        if time.monotonic()>=self.end:
            self.cancel('deadline'); raise SmokeError('deadline')
        try: guard_check(self.args.guard_log,self.args.trip)
        except Exception:
            self.cancel('guard'); raise SmokeError('guard')

    def watch(self):
        while not self.closed.wait(.25):
            try: self.check()
            except Exception:
                self.cancel(self.failure or 'guard')

    def request(self, path, body=None):
        self.check()
        if path=='/v1/chat/completions': check_leases(self.args.coordination_db)
        with self.lock:
            self.check()
            conn=http.client.HTTPConnection(HOST,PORT,timeout=5)
            self.conn=conn
        conn.connect()
        with self.lock:
            self.sock=conn.sock
            self.check()  # Trips/signals during connect cannot send a request.
            self.sock.settimeout(min(60,max(.1,self.end-time.monotonic())))
            self.check()  # Serialize the final gate/send against cancellation.
            conn.request('GET' if body is None else 'POST',path,
                         body=None if body is None else json.dumps(body),
                         headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        response=conn.getresponse()
        with self.lock:
            self.sock=conn.sock or getattr(getattr(response.fp,'raw',None),'_sock',None)
            self.check()
        return response

    def count(self, body):
        request={k:body[k] for k in ['model','messages','chat_template_kwargs']}
        request['add_generation_prompt']=True
        response=self.request('/tokenize',request)
        raw=response.read();response.close();self.cancel()
        if response.status!=200: raise SmokeError('tokenizer_http_status')
        value=json.loads(raw).get('count')
        if type(value) is not int or value<=0:
            raise SmokeError('exact facade tokenizer refused')
        return value

    def snapshot(self, name, idle=True, cleanup=False):
        if not cleanup: self.check()
        check_leases(self.args.coordination_db)
        remaining=(self.cleanup_end if cleanup else self.end)-time.monotonic()
        if remaining<=0: raise SmokeError('observer_deadline')
        record=audit(timeout=min(8,remaining));write(self.args.out/(name+'.audit.json'),record)
        result=validate_audit(record,self.args.facade_sha256,self.args.alias_sha256)
        if idle and (result['running'] or result['waiting']):raise SmokeError('engine not idle')
        return result

    def stream(self, body, name, cancel_owned=False):
        count=self.count(body)
        if count+BUDGET+32>WINDOW:raise SmokeError('smoke request would exceed exact headroom')
        write(self.args.out/(name+'.request.json'),body)
        rec={'content':'','reasoning':'','done':False,'finish_reason':None,'prompt_tokens_exact':count,'reserved_output':BUDGET}
        response=self.request('/v1/chat/completions',body)
        started=time.monotonic()
        try:
            if response.status!=200:raise SmokeError('facade rejected reserved-output smoke')
            with (self.args.out/(name+'.sse')).open('wb') as wire:
                while True:
                    self.check();line=response.readline();wire.write(line);wire.flush()
                    if not line:break
                    if line.startswith(b'data: '):
                        event(line[6:].decode().strip(),rec,time.monotonic()-started)
                        if cancel_owned and (rec['reasoning'] or rec['content']):
                            running=self.snapshot('cancel-active',idle=False)
                            if running['running']!=1 or running['waiting']!=0:
                                raise SmokeError('owned cancellation not demonstrated: no sole running request')
                            rec['cancelled_while_running']=True
                            self.cancel();break
                        if rec['done']:break
        finally:
            self.cancel();response.close();rec['elapsed_s']=time.monotonic()-started
            write(self.args.out/(name+'.result.json'),rec)
        return rec

    def drain(self, cleanup=False):
        until=min(self.cleanup_end if cleanup else self.end,time.monotonic()+20)
        prefix='cleanup-drain-' if cleanup else 'cancel-drain-'
        samples=0
        while time.monotonic()<until:
            record=self.snapshot(prefix+str(samples),idle=False,cleanup=cleanup);samples+=1
            if record['running']==0 and record['waiting']==0:
                time.sleep(1)
                confirmed=self.snapshot(prefix+'confirm',cleanup=cleanup)
                return {'idle_samples':2,'samples':samples+1,**confirmed}
            time.sleep(1)
        raise SmokeError('cancelled owned request failed to drain in 20s')


def run(args):
    if args.trip!=Path(str(args.guard_log)+'.tripped'):raise SmokeError('trip path mismatch')
    # Existing backend interface: Linux caller unit supplies its existing EnvironmentFile.
    # No credential file read, shell export, token argument, header dump or token logging.
    backend=load_pool(POOL_PATH).by_name('am4-vllm')
    pool_sha=hashlib.sha256(POOL_PATH.read_bytes()).hexdigest()
    if not backend or backend.endpoint.rstrip('/')!=f'http://{HOST}:{PORT}' or backend.auth_env!='AM4_VLLM_TOKEN':
        raise SmokeError('backend_endpoint_or_auth_drift')
    token=backend.token()
    if not token:raise SmokeError('AM4_VLLM_TOKEN is absent from the caller environment')
    if any(not re.fullmatch(r'[0-9a-f]{64}',getattr(args,k,'')) for k in ['facade_sha256','alias_sha256']):
        raise SmokeError('invalid_sha256_argument')
    args.out.mkdir(parents=True,exist_ok=False)
    result={'status':'failed','caller':'codex','endpoint':f'http://{HOST}:{PORT}',
            'pool_path':str(POOL_PATH),'pool_sha256':pool_sha,
            'reserved_output':BUDGET,'generated_budget_claim':False,'qualification':False,
            'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'helper_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [
                Path(__file__).with_name('bench27_am4_probe.py'), Path(__file__).with_name('bench27_am4_report.py'),
                Path(__file__).resolve().parents[2]/'hearth/toolsurface/backends.py']}}
    smoke=Smoke(args,token);handlers={s:signal.signal(s,lambda *_:smoke.cancel('operator signal')) for s in [signal.SIGTERM,signal.SIGINT]}
    try:
        result['stage']='preflight'
        before=smoke.snapshot('before')
        short=request_body('What is 17 times 19? Think briefly, then give only the integer.')
        result['stage']='short-reasoning-reservation'
        rec=smoke.stream(short,'short')
        usage=validate_short(rec)
        result['short_usage']=usage
        after=smoke.snapshot('after-short')
        if after['success']-before['success']!=1:raise SmokeError('short counter ownership mismatch')
        result['stage']='exact-overflow'
        overflow,count=make_overflow(smoke.count)
        write(args.out/'overflow.request.json',overflow)
        response=smoke.request('/v1/chat/completions',overflow);raw=response.read(100000);response.close();smoke.cancel()
        write(args.out/'overflow.result.json',{'status':response.status,'prompt_tokens_exact':count,'budget':BUDGET,'headroom':32,'body':raw.decode()})
        validate_overflow(response.status,raw,count)
        after_overflow=smoke.snapshot('after-overflow')
        if after_overflow['success']!=after['success']:raise SmokeError('overflow reached inference or foreign success')
        result['stage']='owned-cancel-drain'
        rec=smoke.stream(request_body('List the integers from 1 through 100000, one per line. Continue until the list is complete.'),'cancel',True)
        if not rec.get('cancelled_while_running'):raise SmokeError('owned cancellation not demonstrated')
        result['drain']=smoke.drain()
        result['cancel_counter_delta']=validate_cancel_counters(after_overflow,result['drain'])
        result['status']='smoke_passed_not_qualified'
    except Exception as exc:
        result['error_type']=type(exc).__name__
        result['error_code']=exc.code if isinstance(exc,SmokeError) else 'external_'+type(exc).__name__
    finally:
        smoke.cancel()
        if 'drain' not in result:
            try: result['cleanup_drain']=smoke.drain(cleanup=True)
            except Exception: result['cleanup_drain']='unconfirmed; parent must retain hold and inspect engine counters'
        result['stop_reason']=smoke.failure
        smoke.closed.set();smoke.watcher.join(1)
        for sig,handler in handlers.items():signal.signal(sig,handler)
        write(args.out/'summary.json',result)
        print(json.dumps({'status':result['status'],'out':str(args.out)}))
    return 0 if result['status']=='smoke_passed_not_qualified' else 3


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for field in ['out','guard-log','trip','coordination-db']:p.add_argument('--'+field,type=Path,required=True)
    for field in ['facade-sha256','alias-sha256']:p.add_argument('--'+field,required=True)
    raise SystemExit(run(p.parse_args()))
