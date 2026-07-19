"""HEARTH tool provider: AM4 model-catalog ingest (JS7a + O5 llama-server).

Pulls the hard-won AM4 B70 model-lifecycle data — vllama's ``models.json``
catalog and b70tools eval ``manifest.json`` warmup samples — over a one-shot
SSH call (imitating the ``_run_ssh`` base64-payload mechanism used elsewhere on
this surface, e.g. ``hearth/toolsurface/dream.py`` / ``occupancy.py`` — no new
ssh wrapper here) and materializes the frozen ``am4-catalog.v1`` knowledge
document the CP-SAT scheduler consumes.

vllama is blind to the llama-server lane (the resident MoE, b70-moe.service
:8082 — ADR-0018), so the gather also collects three optional signals for it:
the b70-moe unit + serve-script text and ``is-active`` state (same SSH trip),
plus a serve-truth ``/v1/models`` probe against :8082 run from THIS side of the
LAN with the ``AM4_OXEN_TOKEN`` bearer the gateway already holds (no token in
env -> probe is skipped, never attempted). Measured perf rides in from the O5
capacity-facts artifact (``am4-fleet-node/results/capacity-facts-*.json``).

The remote script is read-only: it globs manifests, loads both files (tolerant
of the UTF-8 BOM vllama/b70tools write), and prints ONE JSON payload
containing ``models_json`` + ``manifests`` + ``moe`` — all the shaping happens
locally in ``hearth.projection.am4_catalog`` (pure, testable, no SSH).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from hearth.projection.am4_catalog import build_catalog
from hearth.toolsurface._scope import resolve_in_scope, scope_root

AM4_SSH = "derek@192.168.12.233"  # LAN, not tailnet (ADR-0014)
SSH_TIMEOUT_S = 30

MODELS_JSON_PATH = "/mnt/win/work/vllama/config/models.json"
MANIFESTS_GLOB = "/mnt/win/work/b70tools/eval/runs/*/manifest.json"

# The resident-MoE lane (ADR-0018): llama-server bound 0.0.0.0:8082 with
# --api-key bearer auth; ufw allows :8082 LAN-side, so the probe runs from
# OMEN directly (the remote SSH user cannot expand $AM4_OXEN_TOKEN).
MOE_MODELS_URL = "http://192.168.12.233:8082/v1/models"
MOE_TOKEN_ENV = "AM4_OXEN_TOKEN"
MOE_PROBE_TIMEOUT_S = 5

# O5 capacity-facts artifact (repo-relative, dated snapshots; latest wins).
CAPACITY_FACTS_GLOB = "am4-fleet-node/results/capacity-facts-*.json"

DEFAULT_OUT = "knowledge/am4_catalog.json"

# Remote python3 script: reads models.json + every manifest.json it can find,
# tolerates the UTF-8 BOM these Windows-side tools write (utf-8-sig), skips
# unreadable files rather than failing the whole gather, and prints exactly
# one line: "RESULT <json>" with {"models_json":..., "manifests":[...],
# "moe": {...}}. The moe block is read-only too (unit/script text + is-active);
# any missing piece degrades to null rather than failing the gather.
_GATHER_SCRIPT = r'''
import json, glob, os, subprocess, sys

def load(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None

def read_text(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return None

def run(cmd, any_exit=False):
    # any_exit: is-active exits non-zero for "inactive" but still prints the
    # state word — that answer is signal, not failure.
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return done.stdout if (any_exit or done.returncode == 0) else None
    except Exception:
        return None

models_json = load("''' + MODELS_JSON_PATH + r'''") or {}
manifests = []
for path in sorted(glob.glob("''' + MANIFESTS_GLOB + r'''")):
    doc = load(path)
    if doc is not None:
        manifests.append(doc)

active = run(["systemctl", "--user", "is-active", "b70-moe.service"], any_exit=True)
moe = {
    "unit_text": run(["systemctl", "--user", "cat", "b70-moe.service"])
                 or read_text("~/.config/systemd/user/b70-moe.service"),
    "serve_script": read_text("~/baseline/serve-moe.sh"),
    "unit_active": active.strip() if active else None,
}

print("RESULT " + json.dumps({"models_json": models_json, "manifests": manifests, "moe": moe}))
'''


def _run_ssh(remote_command: str, timeout_s: float,
             runner: Optional[Callable[..., subprocess.CompletedProcess]] = None
             ) -> "tuple[Optional[str], Optional[str]]":
    active = runner or subprocess.run
    try:
        completed = active(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", AM4_SSH, remote_command],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        return None, f"ssh exit {completed.returncode}: {(completed.stderr or '').strip()[:300]}"
    return completed.stdout, None


def _parse_result(stdout: Optional[str]) -> dict:
    for line in (stdout or "").splitlines():
        if line.startswith("RESULT "):
            try:
                return json.loads(line[len("RESULT "):])
            except json.JSONDecodeError:
                break
    raise ValueError("no parseable RESULT from am4 gather script")


def _gather_remote(runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
                    timeout_s: float = SSH_TIMEOUT_S) -> dict:
    """One-shot SSH fetch of models.json + all eval manifests from AM4.

    Returns {"models_json": dict, "manifests": [dict, ...]}. Raises ValueError
    / RuntimeError on unreachable host or unparseable payload — the caller
    decides how to surface that as an ``ok: false`` tool result.
    """
    script_b64 = base64.b64encode(_GATHER_SCRIPT.encode("utf-8")).decode("ascii")
    remote = f"echo {script_b64} | base64 -d | python3 -"
    stdout, error = _run_ssh(remote, timeout_s, runner=runner)
    if error is not None:
        raise RuntimeError(f"am4 unreachable: {error}")
    return _parse_result(stdout)


def _probe_moe_models(opener: Optional[Callable] = None,
                       timeout_s: float = MOE_PROBE_TIMEOUT_S) -> Optional[dict]:
    """Serve-truth probe: GET :8082 /v1/models with the AM4_OXEN_TOKEN bearer.

    Returns the parsed response dict, or None when the token is absent (probe
    deliberately skipped — the endpoint requires auth, and skipping keeps
    offline/test runs from ever touching the network) or the lane is down /
    unparseable. Never raises: the probe is an optional enrichment, not a gate.
    """
    token = os.environ.get(MOE_TOKEN_ENV)
    if not token:
        return None
    request = urllib.request.Request(
        MOE_MODELS_URL, headers={"Authorization": f"Bearer {token}"})
    active = opener or urllib.request.urlopen
    try:
        with active(request, timeout=timeout_s) as response:
            document = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _load_capacity_facts() -> Optional[dict]:
    """Latest O5 capacity-facts snapshot from the primary scope root (dated
    filenames sort lexically, so max name == newest). None when absent — the
    catalog then carries the llama-server entry without measured perf."""
    try:
        candidates = sorted(scope_root().glob(CAPACITY_FACTS_GLOB))
        if not candidates:
            return None
        document = json.loads(candidates[-1].read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def gather_am4_catalog(write: bool = True, out: str = DEFAULT_OUT) -> dict:
    """Fetch AM4's model catalog + measured warmups over SSH and materialize
    ``knowledge/am4_catalog.json`` (the am4-catalog.v1 contract).

    Pulls vllama's models.json (placement, VRAM notes, safety gates) and every
    b70tools eval manifest.json (measured warmup.wall_ms per run), aggregates
    per model_id (warmup_ms_p50/max, sample_count), and writes the frozen
    contract shape the CP-SAT scheduler consumes. Also catalogs the
    llama-server lane vllama can't see (b70-moe unit + serve script gathered on
    the same SSH trip, :8082 /v1/models serve-truth probe, O5 capacity facts) —
    see hearth.projection.am4_catalog. Set write=False to build the catalog
    without touching the sandboxed knowledge store (dry run).
    """
    payload = _gather_remote()
    llama_server = payload.get("moe") if isinstance(payload.get("moe"), dict) else {}
    llama_server = dict(llama_server)
    llama_server["models_api"] = _probe_moe_models()
    catalog = build_catalog(
        payload.get("models_json") or {},
        payload.get("manifests") or [],
        host="am4",
        gathered_at=datetime.now(timezone.utc).isoformat(),
        llama_server=llama_server,
        capacity_facts=_load_capacity_facts(),
    )
    if write:
        target = resolve_in_scope(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    return catalog


def query_am4_catalog(out: str = DEFAULT_OUT) -> dict:
    """Return the last-materialized am4_catalog.json (with file mtime) from
    the sandbox, or {"available": False} if it hasn't been gathered yet."""
    path = resolve_in_scope(out)
    if not path.is_file():
        return {"available": False, "path": str(path)}
    return {
        "available": True,
        "path": str(path),
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "content": json.loads(path.read_text(encoding="utf-8-sig")),
    }


def get_tools() -> "list[Callable]":
    return [gather_am4_catalog, query_am4_catalog]
