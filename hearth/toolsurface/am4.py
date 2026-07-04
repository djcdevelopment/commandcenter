"""HEARTH tool provider: AM4 model-catalog ingest (JS7a).

Pulls the hard-won AM4 B70 model-lifecycle data — vllama's ``models.json``
catalog and b70tools eval ``manifest.json`` warmup samples — over a one-shot
SSH call (imitating the ``_run_ssh`` base64-payload mechanism used elsewhere on
this surface, e.g. ``hearth/toolsurface/dream.py`` / ``occupancy.py`` — no new
ssh wrapper here) and materializes the frozen ``am4-catalog.v1`` knowledge
document the CP-SAT scheduler consumes.

The remote script is read-only: it globs manifests, loads both files (tolerant
of the UTF-8 BOM vllama/b70tools write), and prints ONE JSON payload
containing ``models_json`` + ``manifests`` — all the shaping happens locally
in ``hearth.projection.am4_catalog`` (pure, testable, no SSH).
"""

from __future__ import annotations

import base64
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from hearth.projection.am4_catalog import build_catalog
from hearth.toolsurface._scope import resolve_in_scope

AM4_SSH = "derek@100.116.82.60"
SSH_TIMEOUT_S = 30

MODELS_JSON_PATH = "/mnt/win/work/vllama/config/models.json"
MANIFESTS_GLOB = "/mnt/win/work/b70tools/eval/runs/*/manifest.json"

DEFAULT_OUT = "knowledge/am4_catalog.json"

# Remote python3 script: reads models.json + every manifest.json it can find,
# tolerates the UTF-8 BOM these Windows-side tools write (utf-8-sig), skips
# unreadable files rather than failing the whole gather, and prints exactly
# one line: "RESULT <json>" with {"models_json":..., "manifests":[...]}.
_GATHER_SCRIPT = r'''
import json, glob, sys

def load(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None

models_json = load("''' + MODELS_JSON_PATH + r'''") or {}
manifests = []
for path in sorted(glob.glob("''' + MANIFESTS_GLOB + r'''")):
    doc = load(path)
    if doc is not None:
        manifests.append(doc)

print("RESULT " + json.dumps({"models_json": models_json, "manifests": manifests}))
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


def gather_am4_catalog(write: bool = True, out: str = DEFAULT_OUT) -> dict:
    """Fetch AM4's model catalog + measured warmups over SSH and materialize
    ``knowledge/am4_catalog.json`` (the am4-catalog.v1 contract).

    Pulls vllama's models.json (placement, VRAM notes, safety gates) and every
    b70tools eval manifest.json (measured warmup.wall_ms per run), aggregates
    per model_id (warmup_ms_p50/max, sample_count), and writes the frozen
    contract shape the CP-SAT scheduler consumes. Set write=False to build the
    catalog without touching the sandboxed knowledge store (dry run).
    """
    payload = _gather_remote()
    catalog = build_catalog(
        payload.get("models_json") or {},
        payload.get("manifests") or [],
        host="am4",
        gathered_at=datetime.now(timezone.utc).isoformat(),
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
