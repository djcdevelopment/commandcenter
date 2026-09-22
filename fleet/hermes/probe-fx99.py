"""Readiness and MCP integration only; --task submits the real source task."""
import asyncio
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path('/home/derek/.config/hermes-fleet')


async def main():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    headers = {'X-Hearth-Key':(ROOT/'hearth.key').read_text().strip()}
    async with streamablehttp_client('http://127.0.0.1:8712/mcp',headers=headers) as (read,write,_):
        async with ClientSession(read,write) as client:
            await client.initialize()
            listing = await client.list_tools()
            names = [x.name for x in listing.tools]
            print(json.dumps({'mcp_tools':names}))
            result = await client.call_tool('query_knowledge', {'topic':'capacity','host':'am4','limit':2,'knowledge_dir':'C:/work/commandcenter/knowledge'})
            print(result.model_dump_json()[:6500])
            denied = await client.call_tool('read_file', {'path':'C:/work/commandcenter/hearth/var/gateway.cmd'})
            print(json.dumps({'credential_read_denied':bool(denied.isError)}))
    request = Request('http://192.168.12.233:8090/oxen/ready?alias=am4-dense-27b',
        headers={'Authorization':'Bearer '+(ROOT/'am4.key').read_text().strip()})
    with urlopen(request,timeout=5) as response:
        print(json.dumps({'am4':json.load(response)}))


asyncio.run(main())
