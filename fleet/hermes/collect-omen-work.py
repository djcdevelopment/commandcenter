"""Preserve bounded original model artifacts and physical-attempt evidence."""
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('deploy',Path(__file__).with_name('deploy-review-builds.py'))
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
plan = sys.argv[1]
if not plan.startswith('hearth-hermes-') or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in plan):
    raise SystemExit('invalid plan id')
out = d.ROOT/'artifacts/hermes-fx99/omen-workers'/plan
out.mkdir(parents=True,exist_ok=True)
for worker in ('cc-builder-2','cc-builder-3'):
    source = f'''import json
from pathlib import Path
base=Path('/home/claude'); plan={plan!r}; worker={worker!r}; task=plan+'-'+worker
root=base/'farmer-workspace'/plan
files={{}}; mtimes={{}}
for name in ('fleet_status_format.py','OPERATING-MATRIX.md','OPERATIONS-CHECKLIST.md','retro.md'):
 p=root/name
 if p.exists() and p.stat().st_size <= 25000:
  files[name]=p.read_text();mtimes[name]=p.stat().st_mtime
log=base/'.comms'/('run-'+task+'.log'); done=base/'.comms/done'/(task+'.json')
print(json.dumps({{'files':files,'mtimes':mtimes,'done':json.loads(done.read_text()) if done.exists() else None,
 'log_tail':log.read_text(errors='replace')[-25000:] if log.exists() else None}}))
'''
    record = d.run(worker,source)
    target = out/worker; target.mkdir(exist_ok=True)
    (target/'capture.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    for name,content in record['files'].items():
        (target/name).write_text(content,encoding='utf-8')
    print(json.dumps({'worker':worker, **record}))
record = d.run('claude@cc-conductor.mshome.net',f'''import json,subprocess
from pathlib import Path
root=Path('/home/claude/work/commandcenter'); p=root/'runs'/{plan!r}/'result.json'
print(json.dumps({{'result':json.loads(p.read_text()) if p.exists() else None,
 'main':subprocess.check_output(['git','-C','/home/claude/work/commandcenter-ontology/farmer-repo','rev-parse','main'],text=True).strip()}}))
''')
(out/'conductor.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
print(json.dumps(record))
