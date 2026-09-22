"""Evaluate the nine authorities for one caller. Evaluation only — never enforcement.

The rule, in three lines (D-106's sibling decision, plan rev 3.1 "Authority and
approval"):

  granted        the profile holds EVERY required capability and no human gate applies
  human_required the profile holds every required capability and a human gate applies
  denied         a required capability is missing, or no identity was presented

Approval never adds a capability, so a `denied` authority cannot be approved
into a `granted` one — the human gate sits on top of a capability the caller
already holds, never in place of one. Machine or network changes and
merge/push/deploy are always human-gated.

This document is the only caller-specific thing the operator writes. It is never
merged into a capacity snapshot, never written to CURRENT.json, and lands only in
the noncanonical per-caller location.
"""

from __future__ import annotations

from typing import Optional

from hearth.operator import paths
from hearth.operator.canonical import canonical_json, file_sha256, sha256_hex
from hearth.operator.identity import Identity

CONTRACT_VERSION = "authority.v1"

# The nine authorities of the canonical product requirement, in requirement order.
AUTHORITIES: tuple[str, ...] = (
    "read_repo", "write_worktree", "run_tests", "call_door_generate",
    "submit_mechnet_task", "create_build_request", "lease_gpu",
    "change_machine_or_network", "merge_push_deploy",
)

# Never grantable without a human, whatever a policy file says.
ALWAYS_HUMAN_GATED = frozenset({"change_machine_or_network", "merge_push_deploy"})


class AuthorityMapError(ValueError):
    """Raised when the authority map is malformed. Fatal: a half-read policy is
    worse than none, because it silently narrows or widens a decision."""


def load_authority_map(path=None) -> dict[str, dict]:
    document = paths.load_toml(path or paths.AUTHORITY_MAP_PATH)
    table = document.get("authority")
    if not isinstance(table, dict):
        raise AuthorityMapError("authority map has no [authority.*] tables")
    missing = [name for name in AUTHORITIES if name not in table]
    if missing:
        raise AuthorityMapError(
            "authority map is incomplete; every one of the nine authorities must be "
            f"declared. Missing: {', '.join(missing)}")
    unknown = sorted(set(table) - set(AUTHORITIES))
    if unknown:
        raise AuthorityMapError(
            f"authority map declares unknown authorities: {', '.join(unknown)}")
    resolved: dict[str, dict] = {}
    for name in AUTHORITIES:
        entry = table[name]
        capabilities = entry.get("capabilities", [])
        if not isinstance(capabilities, list) or not all(isinstance(c, str) for c in capabilities):
            raise AuthorityMapError(f"authority {name!r}: capabilities must be a list of strings")
        gate = bool(entry.get("human_gate", False)) or name in ALWAYS_HUMAN_GATED
        resolved[name] = {
            "capabilities": sorted(capabilities),
            "human_gate": gate,
            "description": str(entry.get("description", "")),
        }
    return resolved


def policy_version(authority_map_path=None, profiles_path=None) -> str:
    """SHA-256 over the canonical JSON of both policy files' digests.

    Both files, because either one changes what a caller may do: the authority
    map says which capabilities an authority needs, profiles.toml says which
    capabilities a profile holds.
    """
    digests = {
        "authority-map.toml": file_sha256(authority_map_path or paths.AUTHORITY_MAP_PATH),
        "profiles.toml": file_sha256(profiles_path or paths.PROFILES_PATH),
    }
    return sha256_hex(canonical_json(digests))


def _decide(name: str, rule: dict, identity: Identity) -> dict:
    required = list(rule["capabilities"])
    gate = bool(rule["human_gate"])
    if not identity.present:
        return {
            "result": "denied",
            "required_capabilities": required,
            "missing_capabilities": required,
            "human_gate": gate,
            "reason": "no_identity: no caller key was presented, so nothing is authorized",
        }
    missing = [capability for capability in required
               if capability not in identity.capabilities]
    profile = (identity.caller or {}).get("profile")
    if missing:
        return {
            "result": "denied",
            "required_capabilities": required,
            "missing_capabilities": missing,
            "human_gate": gate,
            "reason": (f"profile {profile!r} does not hold {', '.join(missing)}; "
                       "approval never adds a capability"),
        }
    if gate:
        return {
            "result": "human_required",
            "required_capabilities": required,
            "missing_capabilities": [],
            "human_gate": True,
            "reason": (f"profile {profile!r} holds {', '.join(required) or 'the required capabilities'}, "
                       "and this authority is human-gated"),
        }
    return {
        "result": "granted",
        "required_capabilities": required,
        "missing_capabilities": [],
        "human_gate": False,
        "reason": f"profile {profile!r} holds {', '.join(required) or 'the required capabilities'}",
    }


def evaluate(identity: Identity, catalog_version: str, *,
             authority_map: Optional[dict] = None,
             policy: Optional[str] = None) -> dict:
    """Build this caller's authority.v1 document."""
    rules = authority_map if authority_map is not None else load_authority_map()
    return {
        "contract_version": CONTRACT_VERSION,
        "caller": dict(identity.caller) if identity.caller else None,
        "capabilities_granted": sorted(identity.capabilities),
        "evaluated_against": {
            "catalog_version": catalog_version,
            "policy_version": policy if policy is not None else policy_version(),
        },
        "authorities": {name: _decide(name, rules[name], identity) for name in AUTHORITIES},
    }
