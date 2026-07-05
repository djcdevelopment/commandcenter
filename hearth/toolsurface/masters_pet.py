"""HEARTH tool: masters_pet — the guard dog's first healing spell (Watchfire auto-heal).

Policy (Derek, 2026-07-04 — "this is a research lab: let it make the obvious
decisions, document them, we can undo"): auto-heal only OBVIOUS + REVERSIBLE
gaps; flag everything ambiguous. v0 auto-heals ``phantom_in_flight`` — a run that
reads in-flight but has produced no result for a long time — by writing an
abandoned-stub ``result.json``, which releases its phantom occupancy.

Kept honest three ways:
  * ``patrol`` stays a pure observer; this is the only tool that mutates.
  * the stub is self-documenting (``_healed_by`` / ``_healed_at`` / reversible
    note) and reversible — delete the file to restore the pre-heal state.
  * every ``masters_pet`` call is a HEARTH tool call, so the gateway wrapper
    records it (args + result) to the MCP tool-chain ledger automatically — the
    post-mortem trail.

Ambiguous gaps (``false_success``, ``stale_checkout``) are returned as
``flagged`` and never auto-fixed — the auto-heal-vs-flag-only line from
WATCHFIRE-FLARE-DESIGN-2026-07-04.html. ``apply`` defaults to False (dry-run) so
calling it to *see* the plan is always safe; the scheduled watchdog (Watchfire)
is what will call it with ``apply=True``.
"""
from __future__ import annotations

import base64
import json
from typing import Callable, Optional

from hearth.health.gaps import gaps_as_dicts, scan_runs
from hearth.toolsurface.patrol import _gather_runs
from hearth.toolsurface.task_lane import CONDUCTOR_REPO, _run_ssh

# Obvious + reversible only. Everything else stays flag-only.
AUTO_HEAL_KINDS = {"phantom_in_flight"}

# Runs on the conductor: stubs each named phantom run (only if the dir exists and
# has no result yet), stamping reversible provenance. Emits JSON of what it did.
_HEAL_TEMPLATE = '''
import json, os, datetime
PLAN_IDS = {plan_ids_json}
now = datetime.datetime.utcnow().isoformat() + "Z"
done = []
for pid in PLAN_IDS:
    d = os.path.join("runs", pid)
    p = os.path.join(d, "result.json")
    if not os.path.isdir(d):
        done.append({{"plan_id": pid, "action": "skip", "reason": "no run dir"}}); continue
    if os.path.exists(p):
        done.append({{"plan_id": pid, "action": "skip", "reason": "already has result"}}); continue
    stub = {{
        "plan_id": pid, "status": "abandoned", "ok": False,
        "error": "auto-healed by watchfire: phantom_in_flight (no result, aged) - occupancy released",
        "winner": None, "builds": {{}}, "assay": {{"scoreboard": []}},
        "promotion": {{"promoted": False}}, "target": {{"promote": False}},
        "_stub": True, "_stub_reason": "watchfire-phantom-heal",
        "_healed_by": "watchfire.masters_pet", "_healed_at": now,
        "_reversible": "delete this file to restore the pre-heal state",
    }}
    with open(p, "w") as f:
        json.dump(stub, f, indent=2)
    done.append({{"plan_id": pid, "action": "stubbed", "result_path": p, "at": now}})
print(json.dumps({{"healed": done}}))
'''


def _apply_heal(plan_ids, runner: Optional[Callable] = None):
    """Stub each phantom run on the conductor. Returns (payload, error)."""
    src = _HEAL_TEMPLATE.format(plan_ids_json=json.dumps(plan_ids))
    b64 = base64.b64encode(src.encode("utf-8")).decode("ascii")
    remote = f"cd {CONDUCTOR_REPO} && echo {b64} | base64 -d | python3 -"
    stdout, error = _run_ssh(remote, runner=runner)
    if error is not None:
        return None, error
    try:
        return json.loads(stdout), None
    except (json.JSONDecodeError, TypeError) as exc:
        return None, f"non-JSON heal output: {exc}"


def masters_pet(apply: bool = False) -> dict:
    """Find gaps and, for the obvious+reversible ones, heal them (Watchfire).

    With ``apply=False`` (default) this is a dry run: it returns what it *would*
    heal (``healable``) and what it will only ever flag (``flagged``). With
    ``apply=True`` it stubs each ``phantom_in_flight`` run — releasing its phantom
    occupancy — and returns the per-run ``healed`` actions. Reversible: each heal
    is a single stub file; delete it to undo.
    """
    payload, error = _gather_runs()
    if error is not None:
        return {"ok": False, "error": error}
    gaps = scan_runs(payload.get("records", []))
    healable = [g for g in gaps if g.kind in AUTO_HEAL_KINDS]
    flagged = [g for g in gaps if g.kind not in AUTO_HEAL_KINDS]
    out = {
        "ok": True,
        "dry_run": not apply,
        "healable": gaps_as_dicts(healable),
        "flagged": gaps_as_dicts(flagged),
    }
    if apply and healable:
        heal_payload, heal_error = _apply_heal([g.plan_id for g in healable])
        if heal_error is not None:
            out["heal_error"] = heal_error
        else:
            out["healed"] = heal_payload.get("healed", [])
    return out


def get_tools() -> "list[Callable]":
    return [masters_pet]
