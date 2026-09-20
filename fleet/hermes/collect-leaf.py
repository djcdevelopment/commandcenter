"""Copy this single retry's bounded model artifacts, without executing them."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('deploy_review', Path(__file__).with_name('deploy-review-builds.py'))
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
PLAN = 'hearth-hermes-br-20260920-014532-19828821-2115d229'
OUT = Path(__file__).resolve().parents[2]/'artifacts/hermes-fx99/qualification/narrow-retry'
for worker in ('cc-builder-2', 'cc-builder-3'):
    source = f'''
import hashlib,json,subprocess
from pathlib import Path
base=Path('/home/claude'); task={PLAN!r}+'-'+{worker!r}
root=base/'farmer-workspace'/{PLAN!r}
files={{}}; mtimes={{}}
for name in ('native_capacity.py','test_native_capacity.py','CAPACITY-REVIEW.md'):
 p=root/name
 if p.exists() and p.stat().st_size <= 20000:
  files[name]=p.read_text(); mtimes[name]=p.stat().st_mtime
log=base/'.comms'/('run-'+task+'.log')
done=base/'.comms/done'/(task+'.json')
print(json.dumps({{'files':files,'mtimes':mtimes,'workspace':str(root),'done':json.loads(done.read_text()) if done.exists() else None,
 'runner_sha256':hashlib.sha256((base/'fleet-worker-node/runner.json').read_bytes()).hexdigest(),
 'log_tail':log.read_text(errors='replace')[-10000:] if log.exists() else None}}))
'''
    record = deploy.run(worker, source)
    target = OUT/worker; target.mkdir(parents=True, exist_ok=True)
    (target/'capture.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    for name, content in record['files'].items():
        (target/name).write_text(content,encoding='utf-8')
    print(json.dumps({'worker':worker,'files':list(record['files']),'done':record['done'], 'log_tail':record['log_tail']}))
record = deploy.run('claude@cc-conductor.mshome.net', f'''
import json,subprocess
from pathlib import Path
root=Path('/home/claude/work/commandcenter')
path=root/'runs'/{PLAN!r}/'result.json'
print(json.dumps({{'result':json.loads(path.read_text()) if path.exists() else None,
 'main':subprocess.check_output(['git','-C',str(root/'farmer-repo'),'rev-parse','main'],text=True).strip()}}))
''')
(OUT/'conductor.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'conductor_done':record['result'] is not None, 'main':record['main']}))
