"""Passive authentication/admission checks; never sends a valid generation."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hearth.callers.client import HearthClient


async def main():
    private=Path('C:/Users/derek/.hermes-fleet')
    endpoint='http://127.0.0.1:8083/hermes-worker/mcp'
    report={'checked_at':datetime.now(timezone.utc).isoformat()}
    async with HearthClient(endpoint,'invalid-fixture-key') as client:
        assert await client.list_tools()==[]
        report['unknown_key_has_no_tools']=True
    key=(private/'cc-builder-2.omen.key').read_text().strip()
    async with HearthClient(endpoint,key) as client:
        names={t['name'] for t in await client.list_tools()}
        assert names=={'local_generate','query_omen_worker'}
        report['worker_tool_surface']=sorted(names)
        denied=await client.call('read_file',path='C:/work/commandcenter-hermes-fx99/AGENTS.md')
        assert not denied['ok'];report['worker_file_read_denied']=True
        denied=await client.call('local_generate',prompt='Do not execute',backend='am4-dense',
                                 max_tokens=1,timeout_s=10,task_id='hermes-refusal-check')
        assert not denied['ok'];report['am4_override_denied']=True
        denied=await client.call('local_generate',prompt='a '*20000,backend='omen-arc',
                                 model='qwen3-30b-a3b',max_tokens=100,timeout_s=15,
                                 task_id='hermes-overflow-check')
        assert not denied['ok'] and 'context exceeded' in denied['text']
        report['real_native_token_overflow_refused_before_generation']=True
    key=(private/'hearth.key').read_text().strip()
    async with HearthClient('http://127.0.0.1:8712/mcp',key) as client:
        names={t['name'] for t in await client.list_tools()}
        assert not names & {'local_generate','run_tests','git_commit_push','rotation_load'}
        report['controller_has_no_generation_shell_or_rotation']=True
        for receipt in ('br-20260920-025127-19eb2ef8','br-20260920-030141-e036eff8'):
            synced=await client.call('update_build_request',receipt_id=receipt,sync_delegation=True)
            assert synced['ok']
            value=synced['structured'] or json.loads(synced['text'])
            assert not value['execution']['delegation']['harvested']
        report['candidate_sync_did_not_harvest_or_promote']=True
    out=ROOT/'artifacts/hermes-fx99/omen-workers/live-qualification.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    asyncio.run(main())
