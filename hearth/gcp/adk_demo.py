"""adk_demo — deploy/teardown for the Track 2.0 ADK demo engine (ADR-0026).

Ephemeral by default: `deploy` creates the demo ReasoningEngine with
content-capturing telemetry OFF; `teardown` deletes every reasoning engine in
the project/region. An empty `list` is the resting state.

    python hearth/gcp/adk_demo.py list
    python hearth/gcp/adk_demo.py deploy   [--staging-bucket gs://...]
    python hearth/gcp/adk_demo.py teardown [--name NAME] [--force]

list/teardown ride the same REST surface verified live on 2026-07-23 (the
gcloud CLI has no reasoning-engines command group). `deploy` needs
google-cloud-aiplatform[adk,agent_engines] installed and FUNNEL_MCP_URL set
(the Funnel URL is the de facto shared secret — ADR-0025 — so it is never
committed; see hearth/var/ conventions). Deploy is unverified until its first
campaign run: expect to adjust SDK arg names once, then pin them here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

PROJECT = "lumberjacks-exp-20260711-djc"
REGION = "us-west1"
BASE = f"https://{REGION}-aiplatform.googleapis.com/v1"
PARENT = f"projects/{PROJECT}/locations/{REGION}"

# ADR-0026 decision 4: capture stays off unless a campaign turns it on, bounded.
NO_CAPTURE_ENV = {
    "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "false",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
}


def _token() -> str:
    out = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True, shell=(os.name == "nt"),
    )
    return out.stdout.strip()


def _rest(method: str, url: str) -> dict:
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {_token()}")
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def list_engines() -> list[dict]:
    return _rest("GET", f"{BASE}/{PARENT}/reasoningEngines").get("reasoningEngines", [])


def cmd_list(_args: argparse.Namespace) -> int:
    engines = list_engines()
    if not engines:
        print("resting state: no reasoning engines deployed")
        return 0
    for e in engines:
        print(f"{e['displayName']:<40} {e['name'].rsplit('/', 1)[-1]}  created {e.get('createTime', '?')}")
    return 0


def cmd_teardown(args: argparse.Namespace) -> int:
    engines = list_engines()
    targets = [e for e in engines if args.name is None or e["displayName"] == args.name]
    if not targets:
        print("nothing to tear down")
        return 0
    for e in targets:
        url = f"{BASE}/{e['name']}"
        if args.force:
            url += "?force=true"  # required when memory-bank children exist
        print(f"deleting {e['displayName']} ...")
        op = _rest("DELETE", url)
        print(f"  operation: {op.get('name', '<none>')}")
    print("teardown issued; verify with `list` (deletion is an LRO, allow ~1 min)")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    funnel = os.environ.get("FUNNEL_MCP_URL")
    if not funnel:
        print("FUNNEL_MCP_URL is not set (ADR-0025: the URL is the secret; not committed)", file=sys.stderr)
        return 2
    import vertexai
    from vertexai import agent_engines
    from adk_demo_agent.agent import build_agent

    vertexai.init(project=PROJECT, location=REGION, staging_bucket=args.staging_bucket)
    engine = agent_engines.create(
        agent_engine=build_agent(funnel),
        display_name="track20-demo",
        requirements=["google-cloud-aiplatform[adk,agent_engines]"],
        env_vars=dict(NO_CAPTURE_ENV),
    )
    print(f"deployed: {engine.resource_name}")
    print("ADR-0026: tear this down when the campaign ends.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="adk_demo", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    t = sub.add_parser("teardown")
    t.add_argument("--name", help="displayName to delete (default: all)")
    t.add_argument("--force", action="store_true", help="delete child resources (memory bank) too")
    t.set_defaults(fn=cmd_teardown)
    d = sub.add_parser("deploy")
    d.add_argument("--staging-bucket", default="gs://lumberjacks-adk-staging-djc",
                   help="GCS staging bucket (must exist; create once with gcloud storage buckets create)")
    d.set_defaults(fn=cmd_deploy)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
