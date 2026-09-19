"""Create only this listener's credential registry using HEARTH's mint machinery."""
from pathlib import Path
import os
import subprocess
import sys

root = Path(r'C:\work\commandcenter-hermes-fx99')
state = Path(r'C:\Users\derek\.hermes-fleet')
state.mkdir(exist_ok=True)
os.environ['HEARTH_SCOPE'] = str(root) + os.pathsep + r'C:\work\commandcenter\knowledge'
registry = state/'callers.json'
if not registry.exists():
    registry.write_text('{}\n')
if not (state/'hearth.key').exists():
    subprocess.run([sys.executable,'-m','hearth.callers.callerctl','--registry',str(registry),
        'mint','--id','hermes-fx99','--runner-class','local','--node','fx99',
        '--profile','governed-operator','--file-scope',str(root),
        '--file-scope',r'C:\work\commandcenter\knowledge','--repo-access',str(root),
        '--secret-file',str(state/'hearth.key')],cwd=root,check=True)
print('Dedicated operator registry ready; no production keys copied.')
