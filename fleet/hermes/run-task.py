"""Save the real Hermes CLI output and enforce the delivery ceiling externally."""
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path('/home/derek/.local/share/hermes-fleet')
name = sys.argv[1] if len(sys.argv) > 1 else 'first-task'
seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 300
out = root/'artifacts'/name
out.mkdir(parents=True,exist_ok=True)
started = time.monotonic()
metadata = {'started_at':datetime.now(timezone.utc).isoformat(),'ceiling_s':seconds,'task':name}
with (out/'transcript.log').open('wb') as log:
    command = ['/home/derek/.local/bin/hermes-fleet','chat','--cli','--oneshot',
        '--ignore-rules','--reasoning','none','--max-turns','8',
        '--run-budget',str(max(30,seconds-15)),'--query-file',str(root/(name+'.md'))]
    if len(sys.argv) > 3:
        command.extend(['--resume',sys.argv[3]])
        metadata['resumed_session'] = sys.argv[3]
    p = subprocess.Popen(command,
        stdout=log,stderr=subprocess.STDOUT,start_new_session=True,cwd=root)
    try:
        metadata['exit_code'] = p.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        metadata['ceiling_hit'] = True
        p.send_signal(signal.SIGINT)
        try:
            metadata['exit_code'] = p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            import os
            os.killpg(p.pid,signal.SIGKILL)
            metadata['exit_code'] = p.wait()
metadata['elapsed_s'] = round(time.monotonic()-started,3)
metadata['finished_at'] = datetime.now(timezone.utc).isoformat()
(out/'run.json').write_text(json.dumps(metadata,indent=2)+'\n')
print(json.dumps(metadata))
