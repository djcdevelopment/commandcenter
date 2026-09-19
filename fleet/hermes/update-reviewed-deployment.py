"""Deploy the reviewed post-smoke deltas only when the conductor/model are idle."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

spec=importlib.util.spec_from_file_location('deploy',Path(__file__).with_name('deploy-review-builds.py'))
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
install=d.INSTALL.replace('.hermes-review-20260919.backup','.hermes-review-20260919-v2.backup')
host='claude@cc-conductor.mshome.net'
idle='''
import json
from pathlib import Path
print(json.dumps({'idle':not list(Path('/home/claude/work/commandcenter/inbox').glob('*.md'))}))
'''
if not d.run(host,idle)['idle']:
    raise SystemExit('Conductor busy; no update performed')
for worker in ('cc-builder-2','cc-builder-3'):
    source=(d.ROOT/'fleet/hermes/remote/worker/worker-mcp-server.py').read_bytes()
    old=source.replace(b'plan_id.startswith(("hermes-", "hearth-hermes-"))',b'plan_id.startswith("hermes-")')
    print(json.dumps(d.run(worker,install,{'files':[d.file('/home/claude/fleet-worker-node/scripts/worker-mcp-server.py',source,hashlib.sha256(old).hexdigest())]})))
subprocess.run(['ssh','-o','BatchMode=yes',host,'systemctl --user stop commandcenter-conductor.service'],check=True,timeout=25)
try:
    files=[d.file('/home/claude/work/commandcenter/scripts/conductor_maf.py',
        (d.ROOT/'fleet/hermes/remote/conductor/conductor_maf.py').read_bytes(),
        '222bf364831626fc6d3d91ba94b37fb609c8c1795f149230715adffdc3f91d58'),
        d.file('/home/claude/work/commandcenter/scripts/hermes_run_policy.py',
        (d.ROOT/'fleet/hermes/remote/conductor/hermes_run_policy.py').read_bytes(),
        'f526299d052dd7649a47459d62c7e6f5c7905642b727f452056dc0d8af71a7c3')]
    print(json.dumps(d.run(host,install,{'files':files})))
finally:
    subprocess.run(['ssh','-o','BatchMode=yes',host,'systemctl --user start commandcenter-conductor.service'],check=True,timeout=25)
model_idle=d.run('am4','''
import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:18090/slots',timeout=5) as r: slots=json.load(r)
print(json.dumps({'idle':not any(s.get('is_processing') for s in slots)}))
''')
if not model_idle['idle']: raise SystemExit('AM4 busy; facade delta remains undeployed')
print(json.dumps(d.run('am4',install,{'files':[d.file('/home/derek/am4-fleet-node/scripts/oxen-facade.py',
    (d.ROOT/'am4-fleet-node/scripts/oxen-facade.py').read_bytes(),
    '1c7a8f6d477eec6685d2e807793bfdf87cbf800110f0af908fb06850fc344501')]})))
subprocess.run(['ssh','-o','BatchMode=yes','am4','sudo -n systemctl restart am4-oxen-facade.service'],check=True,timeout=25)
print('Reviewed deltas deployed; native model was not restarted.')
