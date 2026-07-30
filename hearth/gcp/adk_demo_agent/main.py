"""Entrypoint matching the captured engine spec (entrypointModule=main,
entrypointObject=app). Used only when deploying via source packaging."""

import os

from agent import build_agent

app = build_agent(os.environ["FUNNEL_MCP_URL"])
