"""Passive live MCP checks and duplicate receipt replay; never new inference."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

ROOT=Path('/home/derek/.config/hermes-fleet')
RECEIPT='br-20260919-215719-70852cc4'


async def main():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    report={'checked_at':datetime.now(timezone.utc).isoformat()}
    headers={'X-Hearth-Key':(ROOT/'hearth.key').read_text().strip()}
    async with streamablehttp_client('http://127.0.0.1:8712/mcp',headers=headers) as (read,write,_):
        async with ClientSession(read,write) as client:
            await client.initialize()
            async def call(name,args):
                result=await client.call_tool(name,args)
                if result.isError:
                    raise RuntimeError(name+' refused: '+str(result.content)[:600])
                return json.loads(next(block.text for block in result.content if block.type=='text'))
            tools={row.name for row in (await client.list_tools()).tools}
            assert not tools & {'local_generate','git_commit_push','run_tests','execute','rotate_model','rebuild_knowledge'}
            report['mcp_tool_count']=len(tools)
            denied=await client.call_tool('read_file',{'path':'C:/Users/derek/.hermes-fleet/am4.key'})
            assert denied.isError, 'credential read gate failed'
            report['credential_read_denied']=True
            knowledge=await call('query_knowledge',{'host':'am4','limit':3,
                'knowledge_dir':'C:/work/commandcenter-hermes-fx99/knowledge'})
            report['authored_catalog_sources']=[row['source_id'] for row in knowledge['results']]
            snapshot=await call('capture_resource_snapshot',{})
            report['native_snapshot']=snapshot.get('am4-dense')
            report['old_am4_route_ready']=snapshot.get('am4-ollama',{}).get('ready')
            assert report['native_snapshot']['ready'] and report['native_snapshot']['parallel_slots']==1
            assert report['native_snapshot']['gpu_placed'] is None
            assert report['old_am4_route_ready'] is False
            args={'receipt_id':RECEIPT,'mode':'delegate','backend':'am4-dense',
                  'builders':['cc-builder-2','cc-builder-3'],'max_age_s':900,
                  'promotion_policy':'manual','runner_preset':'am4-shared-27b'}
            replay=await call('execute_build_request',args)
            assert replay.get('duplicate_delegation') or replay.get('duplicate_execution')
            again=await call('execute_build_request',args)
            assert again.get('duplicate_delegation') or again.get('duplicate_execution')
            report['duplicate_dispatches_suppressed']=2
            synced=await call('update_build_request',{'receipt_id':RECEIPT,'sync_delegation':True})
            delegation=synced['execution']['delegation']
            assert not delegation['harvested']
            assert delegation['promotion']['promoted'] is False
            report['manual_review_no_harvest']=True
            report['candidate_commits']=[row['commit'] for row in delegation['candidates']]
    target=Path('/home/derek/.local/share/hermes-fleet/artifacts/controller-check.json')
    target.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


asyncio.run(main())
