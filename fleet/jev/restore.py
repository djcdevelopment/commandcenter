"""Restore only the pilot-owned conductor delta, after a failed qualification."""
import hashlib
import json
import subprocess
from fleet.jev.deploy import transport
from fleet.jev.local import ROOT, STATE


def main():
    before = json.loads((STATE / 'conductor-before.json').read_text())
    expected = {name: hashlib.sha256((ROOT / 'fleet/hermes/remote/conductor' / name).read_bytes()).hexdigest()
                for name in before['hashes']}
    source = '''import hashlib,json,shutil,subprocess,sys
from pathlib import Path
data=json.load(sys.stdin)
root=Path('/home/claude/work/commandcenter')
if list((root/'inbox').glob('*.md')): raise RuntimeError('conductor_busy')
for name,expected in data['current'].items():
 p=root/'scripts'/name; b=p.with_name(p.name+'.jev-20260921.backup')
 if hashlib.sha256(p.read_bytes()).hexdigest()!=expected: raise RuntimeError('conductor_changed')
 if hashlib.sha256(b.read_bytes()).hexdigest()!=data['baseline'][name]: raise RuntimeError('backup_changed')
subprocess.run(['systemctl','--user','stop','commandcenter-conductor.service'],check=True,timeout=20)
try:
 for name in data['current']:
  p=root/'scripts'/name; shutil.copy2(p.with_name(p.name+'.jev-20260921.backup'),p)
finally:
 subprocess.run(['systemctl','--user','start','commandcenter-conductor.service'],check=True,timeout=20)
print(json.dumps({'conductor_restored':True}))
'''
    print(json.dumps(transport.run('claude@cc-conductor.mshome.net', source,
                                  {'current': expected, 'baseline': before['hashes']}, timeout=50)))


if __name__ == '__main__':
    main()
