"""Use the conductor's established host trust, never accept an unverified key."""
import base64
import json
from pathlib import Path
import shlex
import subprocess
import sys

SOURCES = {
 'ready': '''
import json,urllib.request
from pathlib import Path
cfg=json.loads(Path('/home/claude/fleet-worker-node/runner.json').read_text())
key=Path(cfg['token_file']).read_text().strip() if cfg.get('token_file') else ''
url=cfg['base_url'].removesuffix('/v1')+'/oxen/ready?alias='+cfg['model']
if not key: url=cfg['base_url'].removesuffix('/v1')+'/api/ps'
req=urllib.request.Request(url,headers={'Authorization':'Bearer '+key})
with urllib.request.urlopen(req,timeout=5) as r: print(r.read().decode())
''',
 'inspect': '''
import json
from pathlib import Path
root=Path('/home/claude/fleet-worker-node')
config=json.loads((root/'runner.json').read_text())
print(json.dumps({k:v for k,v in config.items() if k not in ('api_key','token','secret')}))
for name in ('worker-mcp-server.py','agent_openai.py'):
 lines=(root/'scripts'/name).read_text().splitlines()
 for i,line in enumerate(lines):
  if any(word in line for word in ('token_file','--reference','runner.json','COMMANDCENTER')):
   print(name, i+1, '\\n'.join(lines[max(0,i-2):i+5]))
print('reference_exists',Path('/home/claude/commandcenter-src').exists())
''',
 'configure': '''
import hashlib,json,os,shutil,sys
from pathlib import Path
root=Path('/home/claude/fleet-worker-node')
target=root/'runner.json'
backup=root/'runner.json.hermes-20260919.backup'
if backup.exists(): raise SystemExit('backup exists; inspect before retry')
shutil.copy2(target,backup)
data=json.load(sys.stdin)
key=root/'hermes-am4.key'
os.umask(0o077)
key.write_text(data.pop('key')+'\\n')
data['token_file']=str(key)
target.write_text(json.dumps(data)+'\\n')
print(json.dumps({'configured':True,'backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest()}))
''',
 'restore': '''
import hashlib,json,shutil
from pathlib import Path
root=Path('/home/claude/fleet-worker-node')
backup=root/'runner.json.hermes-20260919.backup'
current=json.loads((root/'runner.json').read_text())
if current.get('model') != 'am4-dense-27b' or current.get('token_file') != str(root/'hermes-am4.key'):
 raise SystemExit('runner changed since our deployment; refusing to overwrite')
shutil.copy2(backup,root/'runner.json')
(root/'hermes-am4.key').unlink()
print(json.dumps({'restored_sha256':hashlib.sha256((root/'runner.json').read_bytes()).hexdigest()}))
''',
}


def command(builder, source):
    loader = 'import base64;exec(base64.b64decode('+repr(base64.b64encode(source.encode()).decode())+'))'
    remote = 'python3 -c ' + shlex.quote(loader)
    return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','claude@cc-conductor.mshome.net',
            'ssh -o BatchMode=yes -o ConnectTimeout=5 '+shlex.quote(builder)+' '+shlex.quote(remote)]


if __name__ == '__main__':
    builder, operation = sys.argv[1:3]
    if builder not in ('cc-builder-2','cc-builder-3'):
        raise SystemExit('unknown builder')
    body = None
    if operation == 'stage-source':
        archive = Path(sys.argv[3])
        remote = 'mkdir /home/claude/hermes-capacity-source-20260919 && tar -xf - -C /home/claude/hermes-capacity-source-20260919'
        cmd = ['ssh','-o','BatchMode=yes','claude@cc-conductor.mshome.net',
               'ssh -o BatchMode=yes '+shlex.quote(builder)+' '+shlex.quote(remote)]
        with archive.open('rb') as incoming:
            subprocess.run(cmd,stdin=incoming,check=True,timeout=40)
        raise SystemExit(0)
    if operation == 'configure':
        body = json.dumps({'runner':'openai','base_url':'http://192.168.12.233:8090/v1',
            'model':'am4-dense-27b','max_steps':24,
            'key':Path(r'C:\Users\derek\.hermes-fleet\am4.key').read_text().strip()})
    result = subprocess.run(command(builder,SOURCES[operation]),input=body,text=True,capture_output=True,timeout=25)
    print(result.stdout)
    print(result.stderr,file=sys.stderr)
    raise SystemExit(result.returncode)
