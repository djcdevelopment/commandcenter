"""Export one completed assistant answer, unchanged, with reported session usage."""
import json
from pathlib import Path
import sqlite3

root=Path('/home/derek/.config/hermes-fleet/sessions')
out=Path('/home/derek/.local/share/hermes-fleet/artifacts/first-task')
with sqlite3.connect(f'file:{root}/state.db?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    session=db.execute('SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1').fetchone()
    row=db.execute("SELECT id,content,tool_calls FROM messages WHERE session_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",(session['id'],)).fetchone()
    if not row or row['tool_calls'] not in (None,'','[]'):
        raise SystemExit('No completed assistant answer')
    (out/'source-note.md').write_text(row['content'])
    metadata={key:session[key] for key in session.keys() if key in ('id','started_at','ended_at','model','input_tokens','output_tokens','cache_read_tokens','message_count','tool_call_count','api_call_count')}
    metadata.update(provenance='Unedited Hermes final answer from state.db',message_id=row['id'],accounting='Session-reported aggregates; not per-attempt receipts')
    (out/'provenance.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata))
