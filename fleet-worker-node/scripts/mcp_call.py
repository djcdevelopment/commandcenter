#!/usr/bin/env python3
"""Minimal MCP client for fleet workers — supports HTTP (streamable-http) and
stdio (local or over SSH). The conductor side of the S0 demo.

HTTP (preferred for the Windows OMEN conductor -> remote worker):
  python mcp_call.py --url http://172.19.133.70:8765/mcp --tool node_status

stdio over SSH:
  python mcp_call.py --ssh "ssh -i KEY USER@HOST" \
      --python /home/claude/fleet-worker-node/.venv/bin/python \
      --server /home/claude/fleet-worker-node/scripts/worker-mcp-server.py

stdio from a node manifest (no hand-carried ssh/venv/server paths):
  python mcp_call.py --node ../../am4-fleet-node/node.json --tool node_status
  The manifest's endpoints.mcp_stdio string IS the stdio argv; change the
  target once in node.json and every caller follows.
"""
import argparse
import asyncio
import json
import shlex

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(session, tool):
    await session.initialize()
    tools = await session.list_tools()
    print("TOOLS:", [t.name for t in tools.tools])
    result = await session.call_tool(tool, {})
    for c in result.content:
        if getattr(c, "type", None) == "text":
            try:
                print("RESULT:", json.dumps(json.loads(c.text), indent=2))
            except Exception:
                print("RESULT:", c.text)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="streamable-http MCP endpoint")
    ap.add_argument("--ssh", default="", help="ssh command prefix for stdio transport")
    ap.add_argument("--python", default="", help="remote/local python for the stdio server")
    ap.add_argument("--server", default="", help="path to worker-mcp-server.py for stdio")
    ap.add_argument("--node", default="", help="path to a node.json manifest; reads endpoints.mcp_stdio")
    ap.add_argument("--tool", default="node_status")
    args = ap.parse_args()

    if args.url:
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(args.url) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await call(session, args.tool)
        return

    if args.node:
        with open(args.node) as f:
            manifest = json.load(f)
        cmd = shlex.split(manifest["endpoints"]["mcp_stdio"])
        command, server_args = cmd[0], cmd[1:]
    elif args.ssh:
        parts = shlex.split(args.ssh)
        command, server_args = parts[0], parts[1:] + [args.python, args.server]
    else:
        command, server_args = args.python, [args.server]
    params = StdioServerParameters(command=command, args=server_args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await call(session, args.tool)


if __name__ == "__main__":
    asyncio.run(main())
