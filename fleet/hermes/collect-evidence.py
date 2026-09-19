"""Copy bounded factual output, then optionally import through ExecutionLedger.

No aggregate-to-attempt reconstruction. No raw ledger writes or model calls.
"""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
spec = importlib.util.spec_from_file_location('deploy_review', Path(__file__).with_name('deploy-review-builds.py'))
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
OUT = ROOT/'artifacts/hermes-fx99/qualification'
SESSION = '20260919_215628_f4a9d7'
PLAN = 'hearth-hermes-br-20260919-215719-70852cc4-5f05200a'


def collect():
    OUT.mkdir(parents=True,exist_ok=True)
    source = f'''
import json,sqlite3
from pathlib import Path
base=Path('/home/derek/.config/hermes-fleet/sessions')
with sqlite3.connect('file:'+str(base/'state.db')+'?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row
 session=db.execute('SELECT * FROM sessions WHERE id=?',({SESSION!r},)).fetchone()
 rows=db.execute("SELECT id,role,content,tool_calls FROM messages WHERE session_id=? ORDER BY id",({SESSION!r},)).fetchall()
 summaries=[dict(row) for row in rows if row['role']=='assistant' and row['tool_calls'] in (None,'','[]')]
 stats={{k:session[k] for k in session.keys() if k in ('id','started_at','ended_at','model','input_tokens','output_tokens','cache_read_tokens','message_count','tool_call_count','api_call_count')}}
print(json.dumps({{'session':stats,'final_messages':summaries}}))
'''
    session = deploy.run('fx99',source)
    (OUT/'hermes-session.json').write_text(json.dumps(session,indent=2)+'\n',encoding='utf-8')
    for message in session['final_messages']:
        (OUT/f"hermes-message-{message['id']}.md").write_text(message['content'] or '',encoding='utf-8')
    attempts = deploy.run('am4', '''
import json,sqlite3
from pathlib import Path
path=Path('/home/derek/.config/am4-fleet/hermes-attempts.sqlite')
if not path.exists(): print('[]')
else:
 with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
  rows=db.execute('SELECT terminal FROM attempts ORDER BY rowid DESC LIMIT 2000').fetchall()
  print(json.dumps([r for row in reversed(rows) if (r:=json.loads(row[0]))['started_at']>='2026-09-19T21:56:00']))
''')
    (OUT/'physical-attempts.json').write_text(json.dumps(attempts,indent=2)+'\n',encoding='utf-8')
    counts = {}
    for attempt in attempts:
        caller=attempt.get('caller_id','unknown'); counts[caller]=counts.get(caller,0)+1
    print(json.dumps({'session':session['session'],'physical_attempts':len(attempts),'by_caller':counts}))
    if '--import' in sys.argv:
        from hearth.execution.external_inference import import_receipt
        from hearth.execution.ledger import ExecutionLedger
        ledger = ExecutionLedger(r'C:\work\commandcenter\hearth\var\execution')
        first = [import_receipt(ledger,row) for row in attempts]
        second = [import_receipt(ledger,row) for row in attempts]
        result={'first':first,'replay':second,'all_replays_duplicate':all(r['duplicate'] for r in second)}
        (OUT/'import-results.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'imported':len(first),'all_replays_duplicate':result['all_replays_duplicate']}))


if __name__ == '__main__':
    collect()
