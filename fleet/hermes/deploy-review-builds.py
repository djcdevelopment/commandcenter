"""Hash-guarded deployment of versioned sources; no model lifecycle operations.

Secrets travel on SSH stdin, never argv or logs. Existing runner.json is read only.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
STATE = Path(r'C:\Users\derek\.hermes-fleet')
spec = importlib.util.spec_from_file_location('builder_access', Path(__file__).with_name('builder-access.py'))
access = importlib.util.module_from_spec(spec); spec.loader.exec_module(access)
STAMP = 'hermes-review-20260919'


def run(host, source, data=None, timeout=40):
    if host.startswith('cc-builder-'):
        cmd = access.command(host, source)
    else:
        loader = 'import base64;exec(base64.b64decode('+repr(base64.b64encode(source.encode()).decode())+'))'
        cmd = ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',host,'python3 -c '+shlex.quote(loader)]
    result = subprocess.run(cmd, input=json.dumps(data) if data is not None else None,
                            text=True,capture_output=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{host}: remote operation failed: {result.stderr[-1200:]}')
    return json.loads(result.stdout)


INSPECT = '''
import hashlib,json
from pathlib import Path
root=Path('/home/claude/fleet-worker-node')
print(json.dumps({'worker_sha256':hashlib.sha256((root/'scripts/worker-mcp-server.py').read_bytes()).hexdigest(),
 'runner_sha256':hashlib.sha256((root/'runner.json').read_bytes()).hexdigest(),
 'preset_exists':(root/'runner-presets/am4-shared-27b.json').exists()}))
'''

INSTALL = '''
import base64,hashlib,json,os,shutil,sys
from pathlib import Path
data=json.load(sys.stdin)
# Validate EVERY target before copying any source.
for file in data['files']:
 path=Path(file['path'])
 expected=file.get('expected')
 actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
 if actual != expected: raise RuntimeError('concurrent change or unexpected target: '+str(path))
for file in data['files']:
 path=Path(file['path']); path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists():
  backup=path.with_name(path.name+'.hermes-review-20260919.backup')
  if backup.exists(): raise RuntimeError('backup exists: '+str(backup))
  shutil.copy2(path,backup)
 temp=path.with_name(path.name+'.hermes-review-new')
 with temp.open('xb') as out: out.write(base64.b64decode(file['content']))
 os.chmod(temp,file.get('mode',0o644)); os.replace(temp,path)
print(json.dumps({'installed':[f['path'] for f in data['files']]}))
'''


def file(path, content, expected=None, mode=0o644):
    return dict(path=path, content=base64.b64encode(content).decode(), expected=expected, mode=mode)


def deploy():
    manifest = {'base_commit':'d350de9', 'workers':{}}
    for builder in ('cc-builder-2','cc-builder-3'):
        record = run(builder, INSPECT)
        if record['worker_sha256'] != '3496735cc866e85c7c211a3352aac875c1e773b235d04fa86b9c85d14e113e69':
            raise RuntimeError(builder+' worker source differs from reviewed source')
        if record['preset_exists']:
            raise RuntimeError(builder+' preset already exists; inspect before retry')
        manifest['workers'][builder] = record
    print(json.dumps({'preflight':manifest}))
    keys = {}
    for builder in manifest['workers']:
        key_path = STATE/(builder+'.am4.key')
        with key_path.open('x') as out:
            out.write(secrets.token_urlsafe(36)+'\n')
        keys[builder] = key_path.read_bytes()
    # AM4 deployment is facade-only; native :18090 PID and all GPU seats stay.
    am4root = '/home/derek/.config/am4-fleet/'
    caller_paths = {b:am4root+b+'.hermes.key' for b in keys}
    am4files = [file('/home/derek/am4-fleet-node/scripts/oxen-facade.py',
        (ROOT/'am4-fleet-node/scripts/oxen-facade.py').read_bytes(),
        '2368eae8345aa0dfdd0d084517f0a9bb263648324f82c6517bd5da88c217812e')]
    am4files += [file(caller_paths[b],key,mode=0o600) for b,key in keys.items()]
    am4files += [file(am4root+'fleet-callers.json',json.dumps(caller_paths).encode(),mode=0o600)]
    print(json.dumps(run('am4',INSTALL,{'files':am4files})))
    subprocess.run(['ssh','-o','BatchMode=yes','am4','sudo -n systemctl restart am4-oxen-facade.service'],check=True,timeout=25)
    for builder, record in manifest['workers'].items():
        remote = '/home/claude/fleet-worker-node/'
        token_path = '/home/claude/.config/hermes-fleet/am4-builder.key'
        cfg = dict(runner='openai',base_url='http://192.168.12.233:8090/v1',model='am4-dense-27b',
                   context_length=131072,token_file=token_path,max_steps=24)
        files = [file(remote+'scripts/worker-mcp-server.py',
                      (ROOT/'fleet/hermes/remote/worker/worker-mcp-server.py').read_bytes(),record['worker_sha256']),
                 file(remote+'scripts/runner_presets.py',(ROOT/'fleet/hermes/remote/worker/runner_presets.py').read_bytes()),
                 file(remote+'runner-presets/am4-shared-27b.json',json.dumps(cfg).encode()),
                 file(token_path,keys[builder],mode=0o600)]
        print(json.dumps(run(builder,INSTALL,{'files':files})))
        after = run(builder,INSPECT)
        if after['runner_sha256'] != record['runner_sha256']:
            raise RuntimeError('default runner changed during deployment')
        record['after'] = after
    (STATE/'review-deployment.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({'worker_deployment':'complete','default_runners_unchanged':True}))


def conductor():
    host = 'claude@cc-conductor.mshome.net'
    source = '''
import json,subprocess
from pathlib import Path
root=Path('/home/claude/work/commandcenter')
if list((root/'inbox').glob('*.md')): raise RuntimeError('conductor has work; will not stop it')
subprocess.run(['systemctl','--user','stop','commandcenter-conductor.service'],check=True,timeout=20)
print(json.dumps({'stopped_idle_conductor':True}))
'''
    print(json.dumps(run(host,source)))
    try:
        files = [file('/home/claude/work/commandcenter/scripts/conductor_maf.py',
                      (ROOT/'fleet/hermes/remote/conductor/conductor_maf.py').read_bytes(),
                      'e320f289cb20ea7dea90fd9db2822fc897cb75301df57e5a08a2cd25425eb0a5'),
                 file('/home/claude/work/commandcenter/scripts/hermes_run_policy.py',
                      (ROOT/'fleet/hermes/remote/conductor/hermes_run_policy.py').read_bytes())]
        print(json.dumps(run(host,INSTALL,{'files':files})))
    finally:
        subprocess.run(['ssh','-o','BatchMode=yes',host,'systemctl --user start commandcenter-conductor.service'],check=True,timeout=25)
    print(json.dumps({'conductor':'restarted'}))


if __name__ == '__main__':
    if sys.argv[1:] == ['workers']:
        deploy()
    elif sys.argv[1:] == ['conductor']:
        conductor()
    else:
        print(json.dumps({b:run(b,INSPECT) for b in ('cc-builder-2','cc-builder-3')}))
