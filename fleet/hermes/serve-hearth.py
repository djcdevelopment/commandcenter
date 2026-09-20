"""A restricted listener using HEARTH's existing kernel, guards and ledger.

Two inference executors; no scheduler/agent loop, ops timers, media workers or
integration HTTP routes.
The production gateway and all of its lifecycle owners remain running unchanged.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hearth.kernel.gateway import build_server


def main():
    import uvicorn
    # Share production's leases/tenancy fence, while keeping this listener's
    # append-only execution stream distinct and independently attributable.
    os.environ['HEARTH_COORDINATION_DB'] = 'C:/work/commandcenter/hearth/var/execution/coordination.sqlite'
    from hearth.toolsurface import rotation
    rotation.VAR_DIR = Path('C:/work/commandcenter/hearth/var')
    rotation.CATALOG_PATH = Path('C:/work/commandcenter/knowledge/omen_catalog.json')
    # Read only the existing rung secret; never inherit the production launcher
    # wholesale (it may override this listener's scope and ledger isolation).
    if not os.environ.get('OMEN_ARC_TOKEN'):
        import re
        env_file = Path('C:/work/commandcenter/hearth/var/gateway.cmd')
        for line in env_file.read_text(encoding='utf-8-sig').splitlines():
            match = re.fullmatch(r'\s*set\s+"?OMEN_ARC_TOKEN=([A-Za-z0-9_\-]+)"?\s*', line, re.I)
            if match:
                os.environ['OMEN_ARC_TOKEN'] = match.group(1)
                break
    if sys.argv[1:] == ['--probe-worker']:
        import json
        from hearth.kernel.hermes_worker import query_omen_worker
        print(json.dumps(query_omen_worker()))
        return
    from hearth.execution.defaults import replace_execution_service
    from hearth.execution.service import ExecutionService
    # Only this process's inference; do not recover another gateway's jobs or
    # start optional render/image/media daemons. Coordination is shared; the
    # canonical execution stream is explicitly this restricted listener's.
    replace_execution_service(ExecutionService(workers=2, recover_pending=False))
    mcp = build_server(
        providers_spec='hearth.toolsurface.hermes_operator', port=8712,
        callers_path=os.environ['HERMES_CALLERS_PATH'],
        ledger_dir=os.environ['HERMES_LEDGER_DIR'], threaded_tools=True,
    )
    upstream = mcp.streamable_http_app()

    async def only_mcp(scope, receive, send):
        if scope['type'] == 'http' and scope['path'] not in ('/mcp','/mcp/','/healthz'):
            await send({'type':'http.response.start','status':404,'headers':[]})
            await send({'type':'http.response.body','body':b'Not found'})
            return
        await upstream(scope, receive, send)

    uvicorn.run(only_mcp, host='127.0.0.1', port=8712, log_level='warning')


if __name__ == '__main__':
    main()
