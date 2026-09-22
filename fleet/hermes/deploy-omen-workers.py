"""Versioned, hash-guarded OMEN-worker deployment. Never touches a model process."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
STATE = Path('C:/Users/derek/.hermes-fleet')
STAMP = 'omen-workers-20260920'
spec = importlib.util.spec_from_file_location('deploy',Path(__file__).with_name('deploy-review-builds.py'))
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
INSTALL = d.INSTALL.replace('hermes-review-20260919',STAMP).replace('hermes-review-new',STAMP+'-new')
SCRIPT_ROOT = '/home/claude/fleet-worker-node/scripts/'


def inspect(host, paths):
    source = '''import json,hashlib,sys
from pathlib import Path
paths=json.load(sys.stdin)
print(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else None for p in paths}))'''
    return d.run(host,source,paths)


def main():
    manifest = {'base':'d046c90','workers':{}}
    registry = STATE/'callers.json'
    for worker in ('cc-builder-2','cc-builder-3'):
        key_file = STATE/(worker+'.omen.key')
        if not key_file.exists():
            subprocess.run([sys.executable,'-m','hearth.callers.callerctl','--registry',str(registry),
                'mint','--id','hermes-'+worker,'--runner-class','local','--node',worker,
                '--profile','hermes-worker','--secret-file',str(key_file)],cwd=ROOT,check=True,capture_output=True)
        token_path = '/home/claude/.config/hermes-fleet/omen-worker.key'
        preset_path = '/home/claude/fleet-worker-node/runner-presets/omen-resident-hearth.json'
        paths = [SCRIPT_ROOT+n for n in ('worker-mcp-server.py','runner_presets.py','agent_hearth.py','hearth_client.py')]
        paths += [token_path,preset_path,'/home/claude/fleet-worker-node/runner.json']
        before = inspect(worker,paths)
        expected = hashlib.sha256(subprocess.check_output(['git','show','d046c90:fleet/hermes/remote/worker/worker-mcp-server.py'],cwd=ROOT)).hexdigest()
        actual = before[SCRIPT_ROOT+'worker-mcp-server.py']
        # Deployed copy may use CRLF: compare source normalized as well.
        if actual != expected:
            expected = hashlib.sha256(subprocess.check_output(['git','show','d046c90:fleet/hermes/remote/worker/worker-mcp-server.py'],cwd=ROOT).replace(b'\n',b'\r\n')).hexdigest()
            if actual != expected:
                raise RuntimeError(worker+' source differs from recorded baseline')
        cfg = dict(runner='hearth',base_url='http://omen.mshome.net:8083/hermes-worker/mcp',
                   model='qwen3-30b-a3b',context_length=16384,token_file=token_path,max_steps=12)
        files = [d.file(SCRIPT_ROOT+n,(ROOT/'fleet/hermes/remote/worker'/n).read_bytes(),before[SCRIPT_ROOT+n])
                 for n in ('worker-mcp-server.py','runner_presets.py','agent_hearth.py')]
        files += [d.file(SCRIPT_ROOT+'hearth_client.py',(ROOT/'hearth/callers/client.py').read_bytes(),before[SCRIPT_ROOT+'hearth_client.py']),
                  d.file(token_path,key_file.read_bytes(),before[token_path],0o600),
                  d.file(preset_path,json.dumps(cfg).encode(),before[preset_path])]
        print(json.dumps(d.run(worker,INSTALL,{'files':files})))
        after = inspect(worker,paths)
        if before[paths[-1]] != after[paths[-1]]: raise RuntimeError('default runner changed')
        manifest['workers'][worker] = {'before':before,'after':after}
        (STATE/(STAMP+'.json')).write_text(json.dumps(manifest,indent=2))
    host='claude@cc-conductor.mshome.net'
    idle='''import json
from pathlib import Path
print(json.dumps({'idle':not list(Path('/home/claude/work/commandcenter/inbox').glob('*.md'))}))'''
    if not d.run(host,idle)['idle']: raise RuntimeError('conductor busy')
    names=('hermes_run_policy.py','conductor_maf.py')
    paths=['/home/claude/work/commandcenter/scripts/'+name for name in names]
    before=inspect(host,paths)
    for name,path in zip(names,paths):
        expected=hashlib.sha256(subprocess.check_output(['git','show','d046c90:fleet/hermes/remote/conductor/'+name],cwd=ROOT)).hexdigest()
        if before[path] != expected: raise RuntimeError('conductor baseline differs: '+name)
    subprocess.run(['ssh','-o','BatchMode=yes',host,'systemctl --user stop commandcenter-conductor.service'],check=True,timeout=20)
    try:
        print(json.dumps(d.run(host,INSTALL,{'files':[d.file(path,
            (ROOT/'fleet/hermes/remote/conductor'/name).read_bytes(),before[path])
            for name,path in zip(names,paths)]})))
    finally:
        subprocess.run(['ssh','-o','BatchMode=yes',host,'systemctl --user start commandcenter-conductor.service'],check=True,timeout=20)
    manifest['conductor']=before
    (STATE/(STAMP+'.json')).write_text(json.dumps(manifest,indent=2))


if __name__ == '__main__':
    main()
