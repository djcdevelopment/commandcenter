"""Reversible facade-only deployment. Never starts/stops a model process."""
from pathlib import Path
import hashlib
import json
import os
import secrets
import shutil
import subprocess

root = Path('/home/derek/.config/am4-fleet')
source = Path('/tmp/hermes-oxen-facade-20260919.py')
target = Path('/home/derek/am4-fleet-node/scripts/oxen-facade.py')
backup = root/'hermes-backup-20260919'
if backup.exists():
    raise SystemExit('Deployment backup already exists; inspect before retrying.')
backup.mkdir(mode=0o700)
for path in (target, root/'alias-backends.json', root/'oxen.env'):
    shutil.copy2(path, backup/path.name)
os.umask(0o077)
key = root/'hermes.key'
if not key.exists():
    key.write_text(secrets.token_urlsafe(48)+'\n')
env = root/'oxen.env'
body = env.read_text()
if 'AM4_HERMES_TOKEN_FILE=' in body:
    raise SystemExit('Unexpected existing Hermes credential setting')
try:
    aliases = json.loads((root/'alias-backends.json').read_text())
    aliases['am4-dense-27b'] = {'host':'127.0.0.1','port':18090,
                              'model_id':'models/Qwen3.8-27B-Q4_K_M.gguf'}
    (root/'alias-backends.json').write_text(json.dumps(aliases,indent=2)+'\n')
    env.write_text(body.rstrip()+'\nAM4_HERMES_TOKEN_FILE='+str(key)+'\n')
    shutil.copy2(source,target)
    subprocess.run(['sudo','-n','systemctl','restart','am4-oxen-facade.service'],check=True)
except BaseException:
    for path in (target, root/'alias-backends.json', root/'oxen.env'):
        shutil.copy2(backup/path.name,path)
    subprocess.run(['sudo','-n','systemctl','restart','am4-oxen-facade.service'])
    raise
print(json.dumps({'deployed':'facade-only','backup':str(backup),
                  'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                  'old_dense_service':'unchanged inactive; recovery not yet enabled'}))
