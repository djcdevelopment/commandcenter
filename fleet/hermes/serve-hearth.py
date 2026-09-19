"""A restricted listener using HEARTH's existing kernel, guards and ledger.

No scheduler/agent loop, ops timers, execution workers or integration HTTP routes.
The production gateway and all of its lifecycle owners remain running unchanged.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hearth.kernel.gateway import build_server


def main():
    import uvicorn
    mcp = build_server(
        providers_spec='hearth.toolsurface.hermes_operator', port=8712,
        callers_path=os.environ['HERMES_CALLERS_PATH'],
        ledger_dir=os.environ['HERMES_LEDGER_DIR'],
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
