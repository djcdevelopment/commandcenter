#!/usr/bin/env python3
"""Proposed 8090 facade smoke, parent-invoked only after review; never changes services."""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hearth.toolsurface.backends import Backend
from tools.ops.bench27_am4_probe import guard_check, event
from tools.ops.bench27_am4_report import counters, no_leases

HOST, PORT, ALIAS = '10.44.0.2', 8090, 'am4-dense-27b'
SCRIPT_SHA = 'd6d6887f5fe3c31ba574d6bdfbdc609e6474afbc7d2866c362355d0275c7d54e'
BUDGET, WINDOW = 24576, 49152
AUDIT = """import hashlib,json,pathlib,urllib.request
h=pathlib.Path.home()
paths={'facade':h/'am4-fleet-node/scripts/oxen-facade.py','aliases':h/'.config/am4-fleet/alias-backends.json','recipe':h/'run-vllm-canary.sh'}
r={'sha256':{k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()}}
for key,path in [('models','/v1/models'),('metrics','/metrics')]:
 with urllib.request.urlopen('http://127.0.0.1:18094'+path,timeout=3) as f:
  raw=f.read(2000000).decode();r[key]=json.loads(raw) if key=='models' else raw
print(json.dumps(r))
"""


def write(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def audit():
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', HOST,
                             'python3 -c '+shlex.quote(AUDIT)], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=8, check=True)
    return json.loads(result.stdout)


def validate_audit(record, facade_sha, alias_sha):
    if record['sha256'] != {'facade': facade_sha, 'aliases': alias_sha, 'recipe': SCRIPT_SHA}:
        raise RuntimeError('source/alias/recipe hash mismatch')
    models = [m for m in record['models']['data'] if m['id']=='qwen3-27b']
    if len(models)!=1 or models[0].get('root')!='/home/derek/models/qwen3-27b-gptq-int4' or models[0].get('max_model_len')!=WINDOW:
        raise RuntimeError('wrong actual model/root/window')
    return counters(record['metrics'].encode())


def request_body(text):
    return {'model': ALIAS, 'messages': [{'role':'user','content':text}],
            'max_tokens': BUDGET, 'temperature':0, 'seed':42,
            'chat_template_kwargs':{'enable_thinking':True}, 'stream':True,
            'stream_options':{'include_usage':True}}


class Smoke:
    def __init__(self, args, token):
        self.args, self.token = args, token
        self.end = time.monotonic()+150
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
        if self.failure: raise RuntimeError(self.failure)
        if time.monotonic()>=self.end: raise RuntimeError('150-second smoke deadline')
        guard_check(self.args.guard_log,self.args.trip)

    def watch(self):
        while not self.closed.wait(.25):
            try: self.check()
            except Exception:
                self.cancel('guard/signal/deadline stopped smoke');return

    def request(self, path, body=None):
        self.check()
        if path=='/v1/chat/completions': no_leases(self.args.coordination_db)
        self.conn=http.client.HTTPConnection(HOST,PORT,timeout=5)
        self.conn.connect();self.sock=self.conn.sock
        self.sock.settimeout(min(60,max(.1,self.end-time.monotonic())))
        self.conn.request('GET' if body is None else 'POST',path,
                          body=None if body is None else json.dumps(body),
                          headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        response=self.conn.getresponse()
        self.sock=self.conn.sock or getattr(getattr(response.fp,'raw',None),'_sock',None)
        return response

    def count(self, body):
        request={k:body[k] for k in ['model','messages','chat_template_kwargs']}
        request['add_generation_prompt']=True
        response=self.request('/tokenize',request)
        raw=response.read();response.close();self.cancel()
        value=json.loads(raw).get('count')
        if response.status!=200 or type(value) is not int or value<=0:
            raise RuntimeError('exact facade tokenizer refused')
        return value

    def snapshot(self, name, idle=True):
        self.check();no_leases(self.args.coordination_db)
        record=audit();write(self.args.out/(name+'.audit.json'),record)
        result=validate_audit(record,self.args.facade_sha256,self.args.alias_sha256)
        if idle and (result['running'] or result['waiting']):raise RuntimeError('engine not idle')
        return result

    def stream(self, body, name, cancel_owned=False):
        count=self.count(body)
        if count+BUDGET+32>WINDOW:raise RuntimeError('smoke request would exceed exact headroom')
        write(self.args.out/(name+'.request.json'),body)
        rec={'content':'','reasoning':'','done':False,'finish_reason':None,'prompt_tokens_exact':count,'reserved_output':BUDGET}
        response=self.request('/v1/chat/completions',body)
        started=time.monotonic()
        try:
            if response.status!=200:raise RuntimeError('facade rejected reserved-output smoke')
            with (self.args.out/(name+'.sse')).open('wb') as wire:
                while True:
                    self.check();line=response.readline();wire.write(line);wire.flush()
                    if not line:break
                    if line.startswith(b'data: '):
                        event(line[6:].decode().strip(),rec,time.monotonic()-started)
                        if cancel_owned and (rec['reasoning'] or rec['content']):
                            running=self.snapshot('cancel-active',idle=False)
                            if running['running']!=1 or running['waiting']!=0:
                                raise RuntimeError('owned cancellation not demonstrated: no sole running request')
                            rec['cancelled_while_running']=True
                            self.cancel();break
                        if rec['done']:break
        finally:
            self.cancel();response.close();rec['elapsed_s']=time.monotonic()-started
            write(self.args.out/(name+'.result.json'),rec)
        return rec

    def drain(self):
        until=min(self.end,time.monotonic()+20)
        samples=0
        while time.monotonic()<until:
            record=self.snapshot('cancel-drain-'+str(samples),idle=False);samples+=1
            if record['running']==0 and record['waiting']==0:
                time.sleep(1)
                self.snapshot('cancel-drain-confirm')
                return {'idle_samples':2,'samples':samples+1}
            time.sleep(1)
        raise RuntimeError('cancelled owned request failed to drain in20s')


def run(args):
    if args.trip!=Path(str(args.guard_log)+'.tripped'):raise RuntimeError('trip path mismatch')
    # Existing backend interface: Linux caller unit supplies its existing EnvironmentFile.
    # No credential file read, shell export, token argument, header dump or token logging.
    token=Backend('am4-vllm',f'http://{HOST}:{PORT}','openai',auth_env='AM4_VLLM_TOKEN').token()
    if not token:raise RuntimeError('AM4_VLLM_TOKEN is absent from the caller environment')
    args.out.mkdir(parents=True,exist_ok=False)
    result={'status':'failed','caller':'codex','endpoint':f'http://{HOST}:{PORT}',
            'reserved_output':BUDGET,'generated_budget_claim':False,'qualification':False,
            'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    smoke=Smoke(args,token);handlers={s:signal.signal(s,lambda *_:smoke.cancel('operator signal')) for s in [signal.SIGTERM,signal.SIGINT]}
    try:
        result['stage']='preflight'
        before=smoke.snapshot('before')
        short=request_body('What is 17 times 19? Think briefly, then give only the integer.')
        result['stage']='short-reasoning-reservation'
        rec=smoke.stream(short,'short')
        usage=rec.get('usage') or {}
        if (not rec['done'] or rec['finish_reason']!='stop' or rec['content'].strip()!='323'
                or not rec['reasoning'].strip() or any(t in rec['content'] for t in ['<think>','</think>'])
                or usage.get('prompt_tokens')!=rec['prompt_tokens_exact']
                or type(usage.get('completion_tokens')) is not int or not 0<usage['completion_tokens']<=BUDGET
                or type((usage.get('completion_tokens_details') or {}).get('reasoning_tokens')) is not int
                or not 0<usage['completion_tokens_details']['reasoning_tokens']<=usage['completion_tokens']):
            raise RuntimeError('short reasoning/reservation smoke failed')
        result['short_usage']=usage
        after=smoke.snapshot('after-short')
        if after['success']-before['success']!=1:raise RuntimeError('short counter ownership mismatch')
        result['stage']='exact-overflow'
        overflow=request_body('x '*20000)
        for _ in range(5):
            count=smoke.count(overflow)
            if count+BUDGET+32>WINDOW:break
            overflow['messages'][0]['content']*=2
        else:raise RuntimeError('could not construct exact overflow')
        write(args.out/'overflow.request.json',overflow)
        response=smoke.request('/v1/chat/completions',overflow);raw=response.read(100000);response.close();smoke.cancel()
        write(args.out/'overflow.result.json',{'status':response.status,'prompt_tokens_exact':count,'budget':BUDGET,'headroom':32,'body':raw.decode()})
        if response.status!=400 or b'exceeds context' not in raw:raise RuntimeError('exact overflow not refused for context')
        after_overflow=smoke.snapshot('after-overflow')
        if after_overflow['success']!=after['success']:raise RuntimeError('overflow reached inference or foreign success')
        result['stage']='owned-cancel-drain'
        rec=smoke.stream(request_body('List the integers from1 through100000, one per line. Continue until the list is complete.'),'cancel',True)
        if not rec.get('cancelled_while_running'):raise RuntimeError('owned cancellation not demonstrated')
        result['drain']=smoke.drain()
        result['status']='smoke_passed_not_qualified'
    except Exception as exc:
        result['error_type']=type(exc).__name__  # Never serialize exception/header/token text.
    finally:
        smoke.cancel()
        if 'drain' not in result:
            try: result['cleanup_drain']=smoke.drain()
            except Exception: result['cleanup_drain']='unconfirmed; parent must retain hold and inspect engine counters'
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
