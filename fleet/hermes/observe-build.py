"""Bounded read-only view of this qualification's run, using established SSH trust."""
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('deploy_review', Path(__file__).with_name('deploy-review-builds.py'))
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
PLAN = sys.argv[1] if len(sys.argv) > 1 else 'hearth-hermes-br-20260919-215719-70852cc4-5f05200a'

for worker in ('cc-builder-2','cc-builder-3'):
    source = f'''
import json
from pathlib import Path
task={PLAN!r}+'-'+{worker!r}
base=Path('/home/claude')
log=base/'.comms'/('run-'+task+'.log')
done=base/'.comms/done'/(task+'.json')
route=base/'projects/plans'/(task+'.runner.json')
print(json.dumps({{'worker':{worker!r},'route':json.loads(route.read_text()) if route.exists() else None,
 'done':json.loads(done.read_text()) if done.exists() else None,
 'log_tail':log.read_text(errors='replace')[-4000:] if log.exists() else 'not launched'}}))
'''
    print(json.dumps(deploy.run(worker,source),indent=2))
