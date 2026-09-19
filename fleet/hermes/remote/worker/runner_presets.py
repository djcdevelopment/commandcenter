"""Trusted per-run routes; never modify the node's default runner.json."""
import hashlib
import json
from pathlib import Path
import urllib.request

NAME = "am4-shared-27b"
BASE_URL = "http://192.168.12.233:8090/v1"
MODEL = "am4-dense-27b"


def resolve(root, name, snapshot=None, probe=True):
    if name != NAME:
        raise ValueError("unknown runner preset")
    raw = (Path(root) / "runner-presets" / (name + ".json")).read_bytes()
    cfg = json.loads(raw)
    if (cfg.get("runner"), cfg.get("base_url"), cfg.get("model"), cfg.get("context_length")) != (
            "openai", BASE_URL, MODEL, 131072):
        raise ValueError("preset is not the approved AM4 128k route")
    key = Path(cfg["token_file"]).read_text().strip()
    if not key:
        raise ValueError("preset credential missing")
    public = {k: cfg[k] for k in ("runner", "base_url", "model", "context_length", "max_steps")}
    public.update(preset=name, config_sha256=hashlib.sha256(raw).hexdigest())
    if snapshot is not None and Path(snapshot).exists():
        if json.loads(Path(snapshot).read_text()) != public:
            raise ValueError("resolved preset changed since run started")
    if probe:
        request = urllib.request.Request(BASE_URL.removesuffix("/v1") + "/oxen/ready?alias=" + MODEL,
                                         headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(request, timeout=5) as response:
            state = json.load(response)
        rows = state.get("aliases", [])
        if len(rows) != 1 or not rows[0].get("ready") or rows[0].get("context_length") != 131072 or rows[0].get("parallel_slots") != 1:
            raise ValueError("AM4 readiness/model/context changed")
        if not str(rows[0].get("model", "")).endswith("Qwen3.8-27B-Q4_K_M.gguf"):
            raise ValueError("AM4 native model identity changed")
    if snapshot is not None and not Path(snapshot).exists():
        with Path(snapshot).open("x") as out:
            json.dump(public, out, sort_keys=True)
    return cfg, public
