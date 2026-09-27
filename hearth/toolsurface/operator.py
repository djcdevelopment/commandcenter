"""HEARTH tools: the operator control plane, mounted through the ordinary loader.

Three tools, all thin. Every one of them calls the same core function the CLI
calls (D-102), so the door and the terminal cannot answer differently:

    operator_inspect   query          the current inspection bundle, read-only
    operator_whoami    query          this caller's identity and authority
    operator_catalog   knowledge_write compile the capability catalog

Identity comes from the gateway: it has already authenticated the X-Hearth-Key
header and pushed a dispatch identity for this call, so these tools never look
at a registry, a key, or an environment variable.

`operator_inspect` here is READ-ONLY on purpose. Capturing capacity writes an
immutable snapshot, moves CURRENT.json and appends to the system history, which
is not what `query` means; `--refresh` stays with the CLI in G1.

This module is a provider, not the implementation: it holds no policy, no
canonicalization and no probes, and it imports nothing from the kernel.
"""

from __future__ import annotations

from typing import Callable

from hearth.operator import core, inspection
from hearth.operator.identity import resolve_from_door


def operator_inspect() -> dict:
    """Return the current inspection bundle: catalog reference, the immutable
    capacity snapshot, and this caller's authority.

    Read-only. It never captures: if CURRENT.json is missing, corrupt, or past
    its planning window, the result is ok:false with the reason and the remedy,
    because two agents citing a silently re-observed environment is the failure
    this control plane exists to prevent.
    """
    try:
        bundle = core.inspect_bundle(resolve_from_door(), write=False)
    except inspection.InspectError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "bundle": bundle}


def operator_whoami() -> dict:
    """Return this caller's identity, capabilities, and the nine authorities.

    Evaluation only: an authority reported `human_required` still needs a human,
    and a `denied` one is never approvable — approval does not add a capability.
    """
    try:
        return {"ok": True, "whoami": core.whoami_document(resolve_from_door())}
    except inspection.InspectError as exc:
        return {"ok": False, "error": str(exc)}


def operator_catalog(check: bool = False) -> dict:
    """Compile the capability catalog, or with check=True report whether the
    written catalog still matches the tree (exit-1 semantics as a boolean).

    Byte-stable: two compilations of an unchanged tree produce identical bytes
    and the same catalog_version.
    """
    from hearth.operator import catalog as catalog_mod

    if check:
        ok, message, document = catalog_mod.check_catalog()
        return {"ok": ok, "message": message,
                "catalog_version": document["catalog_version"]}
    result = core.compile_and_write()
    return {"ok": True, "catalog_version": result["document"]["catalog_version"],
            "changed": result["changed"],
            "previous_catalog_version": result["previous_catalog_version"],
            "path": str(result["path"])}


def operator_approve(run_id: str, approval_id: str, decision: str, scope: str = "one_use") -> dict:
    """Record a human operator decision ('approve' or 'reject') under the D-112 approval boundary.

    Requires the 'approve' capability, held exclusively by profile 'human-operator'.
    Agents (including unrestricted) are refused with a permission error.
    """
    from hearth.operator import approve
    try:
        record = approve.decide_approval(run_id, approval_id, decision, resolve_from_door(), scope=scope)
        return {"ok": True, "approval": record}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_tools() -> "list[Callable]":
    return [operator_inspect, operator_whoami, operator_catalog, operator_approve]
