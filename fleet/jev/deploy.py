"""Stage the bounded pilot, reusing the existing hash-guarded SSH installer."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from fleet.jev.client import atomic_json
from fleet.jev.local import ROOT, STATE

spec = importlib.util.spec_from_file_location('hermes_deploy', ROOT / 'fleet/hermes/deploy-review-builds.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)
INSTALL = transport.INSTALL.replace('.hermes-review-20260919.backup', '.jev-20260921.backup')


def stage():
    # Nothing here starts inference. No secrets occur in remote command arguments.
    base = '/home/derek/.local/share/fleet-scheduler/'
    files = [transport.file(base + 'fleet/jev/' + name, (ROOT / 'fleet/jev' / name).read_bytes(), mode=0o600)
             for name in ('__init__.py', 'cli.py', 'client.py', 'policy.py',
                          'review.py', 'review_evidence.py', 'budget_view.py')]
    files += [transport.file(base + 'install_key.py', (ROOT / 'fleet/jev/install_key.py').read_bytes(), mode=0o600),
              transport.file('/home/derek/.config/systemd/user/fleet-scheduler.service',
                             (ROOT / 'fleet/jev/fleet-scheduler.service').read_bytes(), mode=0o600),
              transport.file('/home/derek/.local/bin/fleet-scheduler',
                  b'#!/bin/sh\nexport PYTHONPATH=/home/derek/.local/share/fleet-scheduler\nexec /home/derek/.local/share/hermes-fleet/venv/bin/python -m fleet.jev.cli "$@"\n', mode=0o700)]
    for name in ('hearth.key', 'reviewer.key'):
        files.append(transport.file('/home/derek/.config/fleet-scheduler/' + name, (STATE / name).read_bytes(), mode=0o600))
    transport.run('fx99', 'import os,json\nfrom pathlib import Path\np=Path("/home/derek/.config/fleet-scheduler"); p.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(p,0o700); print(json.dumps({"private_directory":True}))')
    print(json.dumps(transport.run('fx99', INSTALL, {'files': files})))
    # AM4 uses the known direct Ethernet path, not its stale DNS alias.
    command = ['ssh', '-b', '10.44.0.1', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
               '-o', 'HostName=10.44.0.2', '-o', 'HostKeyAlias=am4', '-o', 'StrictHostKeyChecking=yes', 'am4']
    import base64, shlex
    loader = 'import base64;exec(base64.b64decode(' + repr(base64.b64encode(INSTALL.encode()).decode()) + '))'
    result = subprocess.run(command + ['python3 -c ' + shlex.quote(loader)], text=True, capture_output=True,
        input=json.dumps({'files': [transport.file(base + 'review_seat.py', (ROOT / 'fleet/jev/review_seat.py').read_bytes(), mode=0o600)]}), timeout=20)
    if result.returncode:
        raise RuntimeError('AM4 helper staging refused')
    print(result.stdout.strip())
    subprocess.run(['ssh', '-o', 'BatchMode=yes', 'fx99', 'systemctl --user daemon-reload'], check=True, timeout=15)


def conductor():
    host = 'claude@cc-conductor.mshome.net'
    names = ('hermes_run_policy.py', 'conductor_maf.py')
    source = '''import json,hashlib
from pathlib import Path
root=Path('/home/claude/work/commandcenter')
print(json.dumps({'idle':not list((root/'inbox').glob('*.md')),
 'hashes':{n:hashlib.sha256((root/'scripts'/n).read_bytes()).hexdigest() for n in %r}}))
''' % (names,)
    before = transport.run(host, source)
    if not before['idle']:
        raise RuntimeError('conductor_busy')
    files = []
    for name in names:
        old = subprocess.check_output(['git', 'show', 'd69d868:fleet/hermes/remote/conductor/' + name], cwd=ROOT)
        expected = hashlib.sha256(old).hexdigest()
        if before['hashes'][name] != expected:
            raise RuntimeError('conductor_source_changed_' + name)
        files.append(transport.file('/home/claude/work/commandcenter/scripts/' + name,
                                   (ROOT / 'fleet/hermes/remote/conductor' / name).read_bytes(), expected))
    subprocess.run(['ssh','-o','BatchMode=yes',host,'systemctl --user stop commandcenter-conductor.service'],check=True,timeout=20)
    try:
        print(json.dumps(transport.run(host, INSTALL, {'files': files})))
    finally:
        subprocess.run(['ssh','-o','BatchMode=yes',host,'systemctl --user start commandcenter-conductor.service'],check=True,timeout=20)
    atomic_json(STATE / 'conductor-before.json', before)


if __name__ == '__main__':
    {'stage': stage, 'conductor': conductor}[sys.argv[1]]()
