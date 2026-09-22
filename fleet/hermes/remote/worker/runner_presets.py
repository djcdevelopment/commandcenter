"""Trusted per-run routes; never modify the node's default runner.json."""
import hashlib
import json
from pathlib import Path

NAME = "omen-resident-hearth"
BASE_URL = "http://omen.mshome.net:8083/hermes-worker/mcp"
MODEL = "qwen3-30b-a3b"


def unwrap(result):
    value = result.get('structured')
    if not isinstance(value, dict):
        value = json.loads(result.get('text', '{}'))
    if not result.get('ok') or not isinstance(value, dict):
        raise ValueError('HEARTH call refused: '+str(result.get('text',''))[:300])
    return value


def resolve(root, name, snapshot=None, probe=True):
    if name != NAME:
        raise ValueError("unknown runner preset")
    raw = (Path(root) / "runner-presets" / (name + ".json")).read_bytes()
    cfg = json.loads(raw)
    if (cfg.get("runner"), cfg.get("base_url"), cfg.get("model")) != (
            "hearth", BASE_URL, MODEL):
        raise ValueError("preset is not the approved OMEN route")
    key = Path(cfg["token_file"]).read_text().strip()
    if not key:
        raise ValueError("preset credential missing")
    public = {k: cfg[k] for k in ("runner", "base_url", "model", "context_length", "max_steps")}
    public.update(preset=name, config_sha256=hashlib.sha256(raw).hexdigest())
    if snapshot is not None and Path(snapshot).exists():
        if json.loads(Path(snapshot).read_text()) != public:
            raise ValueError("resolved preset changed since run started")
    if probe:
        from hearth_client import HearthClient
        from concurrent.futures import ThreadPoolExecutor
        # MCP may invoke this synchronous tool inside its own event loop.
        # Keep the client's asyncio.run on a separate thread in either case.
        with ThreadPoolExecutor(max_workers=1) as pool:
            state = unwrap(pool.submit(HearthClient(BASE_URL,key).call_sync,
                                       'query_omen_worker').result(timeout=15))
        if not state.get('ready') or state.get('model') != MODEL or state.get('context_length') != cfg['context_length']:
            raise ValueError('OMEN native identity/capacity unavailable or changed: '+str(state.get('reason')))
    if snapshot is not None and not Path(snapshot).exists():
        with Path(snapshot).open("x") as out:
            json.dump(public, out, sort_keys=True)
    return cfg, public
