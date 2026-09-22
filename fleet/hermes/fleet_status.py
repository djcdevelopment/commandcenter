"""Read-only, bounded fleet observations. No model calls or lifecycle changes."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

PROFILE = Path('/home/derek/.config/hermes-fleet')
MCP = 'http://127.0.0.1:8712/mcp'


def controller_state():
    from native_capacity import normalize_am4_native
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        key = (PROFILE/'am4.key').read_text().strip()
        request = Request('http://192.168.12.233:8090/oxen/ready?alias=am4-dense-27b',
                          headers={'Authorization':'Bearer '+key})
        with urlopen(request, timeout=5) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('response too large')
            state = normalize_am4_native(json.loads(raw), stamp)
    except Exception:
        state = {'ready':False, 'observed_at':stamp}
    return public_capacity(state, host='fx99', backend='am4', model='am4-dense-27b')


def public_capacity(value, **labels):
    """Allowlist fields; never include arbitrary upstream errors, paths or secrets."""
    value = value if isinstance(value, dict) else {}
    ready = value.get('ready') is True
    result = {**labels, 'ready':ready}
    for key in ('context_length','parallel_slots','busy_slots','free_slots'):
        n = value.get(key)
        result[key] = n if ready and type(n) is int and n >= 0 else None
    stamp = value.get('observed_at')
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timezone required')
        age = (datetime.now(timezone.utc)-parsed).total_seconds()
        if not -5 <= age <= 30:
            raise ValueError('stale observation')
        result['observed_at'] = parsed.isoformat()
    except (AttributeError, TypeError, ValueError):
        result.update(ready=False, context_length=None, parallel_slots=None,
                      busy_slots=None, free_slots=None, observed_at=None)
    return result


async def hearth_state():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    key = (PROFILE/'hearth.key').read_text().strip()
    async with streamablehttp_client(MCP,headers={'X-Hearth-Key':key}) as (read,write,_):
        async with ClientSession(read,write) as client:
            await client.initialize()
            async def call(tool, args):
                answer = await client.call_tool(tool,args)
                if answer.isError:
                    raise RuntimeError('HEARTH observation refused')
                if isinstance(answer.structuredContent,dict):
                    return answer.structuredContent
                return json.loads(next(c.text for c in answer.content if c.type=='text'))
            worker, receipts = await asyncio.gather(call('query_omen_worker',{}),
                call('list_build_requests',{'limit':10}))
            return worker, receipts


async def collect():
    worker, receipts = {}, {}
    async def bounded_hearth():
        try:
            return await asyncio.wait_for(hearth_state(),timeout=12)
        except Exception:
            return {}, {}
    controller, pair = await asyncio.gather(asyncio.to_thread(controller_state), bounded_hearth())
    worker, receipts = pair
    jobs = []
    for row in receipts.get('requests',[]):
        if not isinstance(row,dict) or row.get('status') not in ('open','running','blocked'):
            continue
        receipt_id = row.get('id','')
        if isinstance(receipt_id,str) and re.fullmatch(r'br-[a-z0-9-]{1,100}',receipt_id):
            jobs.append({'receipt_id':receipt_id,'status':row['status']})
    return {'controller':controller,
            'worker':public_capacity(worker,backend='omen-arc',model='qwen3-30b-a3b'),
            'policy':{'max_builders':2,'promotion':'manual','fallback':'none'},
            'recent_open_receipts':jobs, 'receipts_available':receipts.get('ok') is True}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if args not in ([],['--json']):
        raise SystemExit('Usage: hermes-fleet fleet-status [--json]')
    state = asyncio.run(collect())
    if args == ['--json']:
        print(json.dumps(state,indent=2))
    else:
        from fleet_status_format import format_status
        print(format_status(state),end='')
        if state['recent_open_receipts']:
            print('Recent open receipts: '+', '.join(r['receipt_id']+' ('+r['status']+')'
                  for r in state['recent_open_receipts']))
        if not state['receipts_available']:
            print('Receipt observation unavailable.')
    return 0 if state['controller']['ready'] and state['worker']['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
