"""One AM4 controller-model lap; preserve and restore the observed resident argv."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/home/derek/work/am4-dual-nvidia-poc')
STATE = Path('/home/derek/.local/state/hermes-rnd-20260920')
STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
SNAPSHOT = STATE / 'resident.json'


def read_native(path):
    headers = {}
    if (STATE/'native.key').exists():
        headers['Authorization'] = 'Bearer ' + (STATE/'native.key').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:18090' + path,headers=headers)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split()[2] != 'Z'
    except FileNotFoundError:
        return False


def stop(pid, expected):
    proc = Path(f'/proc/{pid}')
    if not alive(pid):
        return
    actual = [s.decode() for s in (proc/'cmdline').read_bytes().split(b'\0') if s]
    if actual != expected:
        raise RuntimeError('PID identity changed; not stopping it')
    os.kill(pid, signal.SIGTERM)
    until = time.monotonic() + 20
    while alive(pid) and time.monotonic() < until:
        time.sleep(.2)
    if alive(pid):
        raise RuntimeError('server did not stop; no second model will be loaded')


def launch(argv, environment, cwd, label):
    with (STATE/(label+'.log')).open('ab') as log:
        p = subprocess.Popen(argv, cwd=cwd, env=environment, stdout=log,
                             stderr=subprocess.STDOUT, start_new_session=True)
    return p.pid


action = sys.argv[1]
if action == 'start':
    if SNAPSHOT.exists():
        raise RuntimeError('existing lap snapshot; inspect or restore it')
    slots = read_native('/slots')
    if not isinstance(slots, list) or not slots or any(s.get('is_processing') is not False for s in slots):
        raise RuntimeError('resident slot is busy or unknown')
    pid = 1738436
    proc = Path(f'/proc/{pid}')
    argv = [s.decode() for s in (proc/'cmdline').read_bytes().split(b'\0') if s]
    if 'models/Qwen3.8-27B-Q4_K_M.gguf' not in argv or '18090' not in argv:
        raise RuntimeError('resident differs from observed baseline')
    environment = dict(s.decode().split('=', 1) for s in (proc/'environ').read_bytes().split(b'\0') if s)
    record = {'original_pid':pid, 'argv':argv, 'cwd':str((proc/'cwd').resolve()), 'env':environment}
    with os.fdopen(os.open(SNAPSHOT, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'w') as f:
        json.dump(record, f)
    stop(pid, argv)
    candidate = [argv[0], '-m', 'models/qwen2.5-14b-instruct-q4_K_M.gguf',
        '--alias','rnd-qwen25-14b-q4', '--host','127.0.0.1','--port','18090',
        '-ngl','99','-fa','on','-c','16384','-np','1','-ub','256','-b','512',
        '-ctk','q8_0','-ctv','q8_0','--cache-ram','0','--slots','--jinja','-lv','3']
    env = dict(environment)
    devices = subprocess.check_output(['nvidia-smi','--query-gpu=name,uuid','--format=csv,noheader'],text=True)
    selected = [r.split(',')[1].strip() for r in devices.splitlines() if r.split(',')[0].strip() == 'NVIDIA GeForce RTX 5070']
    if len(selected) != 1:
        raise RuntimeError('5070 UUID not unique; restore original model')
    env['CUDA_VISIBLE_DEVICES'] = selected[0]
    env['LLAMA_API_KEY'] = (STATE/'native.key').read_text().strip()
    record['candidate_argv'] = candidate
    record['candidate_pid'] = launch(candidate, env, record['cwd'], '14b-5070')
    SNAPSHOT.write_text(json.dumps(record))
    print(json.dumps({'candidate_pid':record['candidate_pid'], 'model':'qwen2.5-14b-q4',
                      'device':selected[0], 'context':16384}))
elif action in ('repin', '64k', '8b', '14b-cpu'):
    record = json.loads(SNAPSHOT.read_text())
    if record.get('restored_pid'):
        slots=read_native('/slots')
        if any(s.get('is_processing') is not False for s in slots):
            raise RuntimeError('restored resident is busy; leave it running')
        stop(record['restored_pid'],record['argv'])
        del record['restored_pid']
        SNAPSHOT.write_text(json.dumps(record))
    stop(record['candidate_pid'], record['candidate_argv'])
    devices = subprocess.check_output(['nvidia-smi','--query-gpu=name,uuid','--format=csv,noheader'],text=True)
    selected = [r.split(',')[1].strip() for r in devices.splitlines() if r.split(',')[0].strip() == 'NVIDIA GeForce RTX 5070']
    if len(selected) != 1:
        raise RuntimeError('5070 UUID not unique; restore original model')
    env = dict(record['env'])
    env['CUDA_VISIBLE_DEVICES'] = selected[0]
    env['LLAMA_API_KEY'] = (STATE/'native.key').read_text().strip()
    if action in ('64k', '8b', '14b-cpu'):
        for flag,value in (('-c','65536'),('-ctk','q4_0'),('-ctv','q4_0')):
            record['candidate_argv'][record['candidate_argv'].index(flag)+1] = value
    if action == '8b':
        record['candidate_argv'][record['candidate_argv'].index('-m')+1] = 'models/llama3.1-8b-ollama-Q4_0.gguf'
        record['candidate_argv'][record['candidate_argv'].index('--alias')+1] = 'rnd-llama31-8b'
    if action == '14b-cpu':
        for flag,value in (('-m','models/qwen2.5-14b-instruct-q4_K_M.gguf'),
                           ('--alias','rnd-qwen25-14b-q4'),('-ngl','40')):
            record['candidate_argv'][record['candidate_argv'].index(flag)+1] = value
    record['candidate_pid'] = launch(record['candidate_argv'],env,record['cwd'],
                                     '14b-5070-cpu-64k' if action == '14b-cpu' else
                                     '8b-5070-64k' if action == '8b' else
                                     '14b-5070-64k' if action == '64k' else '14b-5070-uuid')
    record['candidate_gpu_uuid'] = selected[0]
    SNAPSHOT.write_text(json.dumps(record))
    print(json.dumps({'candidate_pid':record['candidate_pid'],'gpu_uuid':selected[0]}))
elif action == 'restore':
    record = json.loads(SNAPSHOT.read_text())
    if record.get('restored_pid'):
        print(json.dumps({'restored_pid':record['restored_pid'], 'alive':alive(record['restored_pid'])}))
    else:
        if record.get('candidate_pid'):
            stop(record['candidate_pid'], record['candidate_argv'])
        record['restored_pid'] = launch(record['argv'], record['env'], record['cwd'], 'restored-27b')
        SNAPSHOT.write_text(json.dumps(record))
        print(json.dumps({'restored_pid':record['restored_pid'], 'original_argv_restored':True}))
elif action == 'status':
    result = {}
    for name in ('health','props','slots'):
        try:
            value = read_native('/'+name)
            if name == 'props':
                value = {'model_path':value.get('model_path'),'total_slots':value.get('total_slots'),
                         'context':value.get('default_generation_settings',{}).get('n_ctx')}
            result[name] = value
        except Exception as exc:
            result[name] = type(exc).__name__
    result['gpu'] = subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.used,memory.total',
                                            '--format=csv,noheader'],text=True)
    print(json.dumps(result))
else:
    raise SystemExit('start | status | restore')
