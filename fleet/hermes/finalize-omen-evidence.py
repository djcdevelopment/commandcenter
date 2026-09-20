"""Read-only final observations and receipt bookkeeping, without new inference."""
import asyncio
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('deploy',Path(__file__).with_name('deploy-review-builds.py'))
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
OUT=ROOT/'artifacts/hermes-fx99/omen-workers'
PRIVATE=Path('C:/Users/derek/.hermes-fleet')


async def close_children():
    from hearth.callers.client import HearthClient
    key=(PRIVATE/'hearth.key').read_text().strip()
    outcomes=[('br-20260920-025127-19eb2ef8','failed',
        'No model artifact: preflight environment/callback failures, then builder nested-event-loop error. '
        'Assisted dispatch recorded; original run produced no generation. Codex wrote OPERATING-MATRIX.md separately.',[]),
        ('br-20260920-030141-e036eff8','done',
        'Assisted result: Hermes authored/dispatched; Codex fixed singleton graph before OMEN generated. '
        'Original candidate had nested-type, bool, stale-capacity and label-sanitization defects. '
        'Reviewed corrected fleet/hermes/fleet_status_format.py integrated and deployed; '
        'targeted regression and actual FX99 CLI passed. Raw candidate NOT promoted.',
        ['fleet/hermes/fleet_status_format.py','fleet/hermes/fleet_status.py','fleet/hermes/fx99_cli.py']),
        ('br-20260920-031116-00f63902','done',
        'Hermes dispatched and OMEN wrote a real checklist without intervention, 12.642s worker runtime. '
        'Codex corrected command name, output reserve and stop rules before accepting '
        'fleet/hermes/OPERATIONS-CHECKLIST.md. Original model output and local candidate commit preserved; no auto merge.',
        ['fleet/hermes/OPERATIONS-CHECKLIST.md'])]
    async with HearthClient('http://127.0.0.1:8712/mcp',key) as client:
        async def call(tool,**args):
            result=await client.call(tool,**args)
            if not result['ok']: raise RuntimeError(tool+' refused')
            return result['structured'] or json.loads(result['text'])
        closed=[]
        for receipt,status,summary,files in outcomes:
            value=await call('update_build_request',receipt_id=receipt,sync_delegation=True)
            if value['execution']['delegation']['harvested']:
                raise RuntimeError('unexpected external harvest')
            validation=[{'criterion':c,'status':'passed' if status=='done' else 'failed',
                         'evidence':summary} for c in value['acceptance_criteria']]
            value=await call('close_build_request',receipt_id=receipt,status=status,summary=summary,
                             validation=validation,changed_files=files)
            closed.append({'id':receipt,'status':value['status'],'summary':summary})
        return closed


def main():
    report={'checked_at':datetime.now(timezone.utc).isoformat()}
    report['child_receipts']=asyncio.run(close_children())
    report['fx99']=d.run('fx99', '''import json,subprocess,time
start=time.monotonic()
p=subprocess.run(['/home/derek/.local/bin/hermes-fleet','fleet-status','--json'],capture_output=True,text=True,timeout=20)
print(json.dumps({'exit_code':p.returncode,'elapsed_s':round(time.monotonic()-start,3),'status':json.loads(p.stdout)}))
''',timeout=25)
    report['am4']=d.run('am4', '''import json
from pathlib import Path
p=Path('/proc/1738436'); cfg=Path('/home/derek/.config/am4-fleet/fleet-callers.json')
print(json.dumps({'native_pid':1738436,'native_pid_alive':p.exists(),
 'comm':(p/'comm').read_text().strip() if p.exists() else None,
 'dedicated_worker_callers':list(json.loads(cfg.read_text())),
 'revocation_backup_exists':cfg.with_name(cfg.name+'.pre-omen-20260920.backup').exists()}))
''')
    baseline=json.loads((PRIVATE/'omen-workers-20260920.json').read_text())
    report['workers']={}
    for worker in ('cc-builder-2','cc-builder-3'):
        value=d.run(worker,'''import json,hashlib
from pathlib import Path
root=Path('/home/claude/fleet-worker-node')
paths=['runner.json','scripts/worker-mcp-server.py','scripts/runner_presets.py','scripts/agent_hearth.py']
print(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}))
''')
        old=baseline['workers'][worker]['before']['/home/claude/fleet-worker-node/runner.json']
        value['default_unchanged']=old==value['runner.json']
        assert value['default_unchanged']
        for name in ('worker-mcp-server.py','runner_presets.py','agent_hearth.py'):
            assert value['scripts/'+name]==hashlib.sha256((ROOT/'fleet/hermes/remote/worker'/name).read_bytes()).hexdigest()
        report['workers'][worker]=value
    report['conductor']=d.run('claude@cc-conductor.mshome.net', '''import json,subprocess
from pathlib import Path
root=Path('/home/claude/work/commandcenter');out={}
for repo in ('/home/claude/work/commandcenter/farmer-repo','/home/claude/work/commandcenter-ontology/farmer-repo'):
 out[repo]={'main':subprocess.check_output(['git','-C',repo,'rev-parse','main'],text=True).strip(),
            'reflog':subprocess.run(['git','-C',repo,'reflog','show','main','-1','--format=%gD %gs','--date=iso-strict'],capture_output=True,text=True).stdout.strip()}
print(json.dumps({'repos':out,'inbox_empty':not list((root/'inbox').glob('*.md'))}))
''')
    jobs={}
    for line in (ROOT/'hearth/var/execution/events.ndjson').read_text().splitlines():
        row=json.loads(line); jid=row['job_id']
        if row['event_type']=='request.accepted' and (row.get('principal') or {}).get('id','').startswith('hermes-cc-builder-'):
            jobs[jid]={'job_id':jid,'caller':row['principal']['id'],
                       'task_id':row['desired']['arguments'].get('task_id'),'started_at':row['timestamp']}
        if jid in jobs and row['event_type']=='invocation.succeeded':
            jobs[jid].update(invocation_id=row['invocation_id'],finished_at=row['timestamp'],
                            **{k:row['observed'].get(k) for k in ('backend','model','tokens_in','tokens_out','duration_ms')})
    report['omen_attempts']=list(jobs.values())
    am4=json.loads((OUT/'accounting/physical-attempts.json').read_text())
    report['accounting']={'am4_attempts':len(am4),
       'am4_usage':{k:sum((r.get('usage') or {}).get(k) or 0 for r in am4) for k in ('tokens_in','tokens_out')},
       'omen_attempts':len(jobs),'omen_usage':{k:sum(r.get(k) or 0 for r in jobs.values()) for k in ('tokens_in','tokens_out')}}
    overlaps=[]
    for a in am4:
        for b in jobs.values():
            start=max(datetime.fromisoformat(a['started_at']),datetime.fromisoformat(b['started_at']))
            end=min(datetime.fromisoformat(a['finished_at']),datetime.fromisoformat(b['finished_at']))
            if end>start: overlaps.append({'controller_attempt':a['attempt_id'],'worker_job':b['job_id'],
                                          'overlap_s':round((end-start).total_seconds(),3)})
    report['controller_worker_inference_overlap']=overlaps
    (OUT/'final-state.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__': main()
