"""Minimal Track 2.0 demo agent: an ADK agent whose one distinctive feature is
an MCP toolset aimed at the HEARTH Funnel (ADR-0025 path, unchanged).

Skeleton to verify on first campaign — ADK's MCP toolset import path has moved
between releases; the two candidates below cover the known spellings. The old
Studio-built engine exposed no source (see engine-specs-2026-07-23.json), so
this file is the repo-owned successor, not a recovery.
"""

from __future__ import annotations


def build_agent(funnel_mcp_url: str):
    from google.adk.agents import Agent

    try:
        from google.adk.tools.mcp_tool.mcp_toolset import (
            MCPToolset,
            StreamableHTTPConnectionParams,
        )
        toolset = MCPToolset(
            connection_params=StreamableHTTPConnectionParams(url=funnel_mcp_url)
        )
    except ImportError:  # older/newer ADK spelling
        from google.adk.tools.mcp_tool import MCPToolset, StreamableHTTPServerParams
        toolset = MCPToolset(
            connection_params=StreamableHTTPServerParams(url=funnel_mcp_url)
        )

    return Agent(
        name="track20_demo",
        model="gemini-3.5-flash",
        instruction=(
            "You are the Track 2.0 demo: a cloud-hosted agent that reaches the "
            "operator's HEARTH gateway through its funnel. Use the MCP tools to "
            "answer questions about the commandcenter repo; you are read-only "
            "(research profile) and should say so if asked to modify anything."
        ),
        tools=[toolset],
    )
