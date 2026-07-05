"""HEARTH tool provider: the commander intent lane (REFINE, slice 1).

Exposes the refine<->critique loop as door tools so the commander can issue
intent with no frontier session in the loop:

    refine_idea(idea, rounds, fan)  -> run the loop, persist the trail, return a digest
    refine_result(intent_id)        -> retrieve a stored refinement (idea + final + trail)

Results persist under HEARTH_SCOPE at hearth/var/commander/refine/<intent_id>.json
(hearth/var/ is gitignored — artifacts, not source). The gateway wraps these with
auth + provenance + ledger like every other tool, so each intent is captured.
Provider stays kernel-free (import contract): only _scope + fsio + the pure loop.
"""
from __future__ import annotations

import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from hearth.commander.refine import run_refine
from hearth.toolsurface._scope import resolve_in_scope

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from tools.workflow.fsio import atomic_write_json  # noqa: E402

_STORE_DIR = "hearth/var/commander/refine"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(idea: str, n: int = 40) -> str:
    s = _SLUG_RE.sub("-", idea.strip().lower()).strip("-")
    return (s[:n].rstrip("-") or "idea")


def _intent_id(idea: str) -> str:
    return f"refine-{_slug(idea)}-{uuid.uuid4().hex[:8]}"


def _store_path(intent_id: str) -> Path:
    # Reject a caller-supplied id that would escape the store (path-traversal).
    if "/" in intent_id or "\\" in intent_id or intent_id in ("", ".", ".."):
        raise ValueError(f"invalid intent_id: {intent_id!r}")
    return resolve_in_scope(f"{_STORE_DIR}/{intent_id}.json")


def persist_refine(result: dict, idea: str) -> dict:
    """Write a completed refine result to the scoped store; return {intent_id, path}."""
    intent_id = _intent_id(idea)
    path = _store_path(intent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "contract_version": "commander-refine.v1",
        "intent_id": intent_id,
        "mode": "refine",
        "created": _now_iso(),
        "idea": idea,
        "final": result.get("final"),
        "rounds_run": result.get("rounds_run"),
        "converged": result.get("converged"),
        "cost": result.get("cost"),
        "trail": result.get("trail"),
        "ok": result.get("ok"),
        "error": result.get("error"),
    }
    atomic_write_json(str(path), document)
    return {"intent_id": intent_id, "path": str(path)}


def refine_idea(idea: str, rounds: int = 3, fan: bool = False) -> dict:
    """Refine an idea by a local author<->critic loop; persist and return a digest.

    The commander's "refine & review this a bunch of times". Runs entirely on
    OMEN's local models (no frontier). ``rounds`` caps the iterations; ``fan``
    spreads each review across several local models (qwen + mixtral) for diverse
    perspectives at the cost of wall-clock. Returns {ok, intent_id, path, final,
    rounds_run, converged, cost}. The full round-by-round trail is in the stored
    file; fetch it with refine_result(intent_id).
    """
    if not isinstance(idea, str) or not idea.strip():
        raise ValueError("idea must be a non-empty string")
    rounds = int(rounds)
    if rounds < 1:
        raise ValueError("rounds must be >= 1")

    result = run_refine(idea, rounds=rounds, fan=bool(fan))
    stored = persist_refine(result, idea)
    return {
        "ok": result.get("ok"),
        "intent_id": stored["intent_id"],
        "path": stored["path"],
        "final": result.get("final"),
        "rounds_run": result.get("rounds_run"),
        "converged": result.get("converged"),
        "cost": result.get("cost"),
        "error": result.get("error"),
    }


def refine_result(intent_id: str) -> dict:
    """Retrieve a stored refinement (idea + final + full trail) by intent_id."""
    if not isinstance(intent_id, str) or not intent_id.strip():
        raise ValueError("intent_id must be a non-empty string")
    path = _store_path(intent_id)
    if not path.is_file():
        return {"ok": False, "error": f"no refinement found for {intent_id}",
                "intent_id": intent_id}
    import json
    document = json.loads(path.read_text(encoding="utf-8"))
    document.setdefault("ok", True)
    return document


def get_tools() -> list[Callable]:
    return [refine_idea, refine_result]
