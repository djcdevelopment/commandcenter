"""One isolated real-Hermes prompt; the installed production profile is untouched."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path('/home/derek/.local/share/hermes-fleet')
label = sys.argv[1] if len(sys.argv)>1 else 'rnd-14b-5070'
context = int(sys.argv[2]) if len(sys.argv)>2 else 16384
model = sys.argv[3] if len(sys.argv)>3 else 'rnd-qwen25-14b-q4'
ceiling = int(sys.argv[4]) if len(sys.argv)>4 else 180
LAP = ROOT/'artifacts'/label
LAP.mkdir(parents=True,exist_ok=True)
os.chmod(LAP,0o700)
os.environ['HERMES_HOME'] = str(LAP/'profile')
os.environ['OPENAI_BASE_URL'] = 'http://127.0.0.1:18091/v1'
native_key = Path('/home/derek/.config/hermes-fleet/am4.key').read_text().strip()
os.environ['OPENAI_API_KEY'] = native_key
os.environ['HERMES_HEARTH_KEY'] = Path('/home/derek/.config/hermes-fleet/hearth.key').read_text().strip()
from hermes_cli.config import save_config
provider = {'provider':'custom','model':model,
            'base_url':'http://127.0.0.1:18091/v1','api_key':native_key,'timeout':120}
save_config({
    'model':{'provider':'custom','default':model,
             'base_url':provider['base_url'],'api_key':native_key,'context_length':context},
    'providers':{'custom':{**provider,'request_timeout_seconds':120}},
    'compression':{'enabled':False},
    'auxiliary':{'title_generation':{'enabled':False},'background_review':{'enabled':False}},
    'mcp_servers':{'hearth':{'url':'http://127.0.0.1:8712/mcp',
                           'headers':{'X-Hearth-Key':'${HERMES_HEARTH_KEY}'}}},
    'platform_toolsets':{'cli':['hearth']},'display':{'interface':'cli'},
}, merge_existing=False)
command = [str(ROOT/'venv/bin/hermes'),'chat','--cli','--oneshot','--ignore-rules',
           '--reasoning','none','--max-turns','5','--run-budget',str(ceiling-10),
           '--toolsets','hearth','--query-file',str(ROOT/'rnd-14b-task.md')]
started=time.monotonic()
with (LAP/'transcript.log').open('wb') as log:
    p=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,cwd=LAP)
    try:
        result={'exit_code':p.wait(timeout=ceiling)}
    except subprocess.TimeoutExpired:
        os.killpg(p.pid,signal.SIGINT)
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);p.wait()
        result={'ceiling_hit':True,'exit_code':p.returncode}
result['elapsed_s']=round(time.monotonic()-started,3)
(LAP/'run.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
