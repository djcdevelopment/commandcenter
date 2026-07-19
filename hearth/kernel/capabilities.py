"""HEARTH capability profiles: caller -> profile -> capability set -> tool routing.

ADR-0019. HEARTH authenticated callers long before it authorized them: the
gateway resolved an X-Hearth-Key to a Caller and used it only for ledger
attribution, so every valid key reached the entire mounted tool surface. This
module is the authorization layer that makes a network-reachable door safe.

Two halves, deliberately split:

  * TAXONOMY (here, in code): ``TOOL_CAPABILITY`` maps every mounted tool to
    exactly one capability. Same shape and precedent as the gateway's
    ``TOOL_CLASS``. ``assert_surface_complete`` refuses to start a gateway that
    mounts a tool with no entry, so adding tool #48 forces an explicit policy
    decision instead of silently landing inside somebody's profile.

  * POLICY (hearth/etc/profiles.toml, in git): which profiles exist and what
    each grants. Version-controlled because authorization policy should be
    reviewable in a diff.

Every load-time defect is a startup error, never a warning: an unknown parent,
an inheritance cycle, an unknown capability name, or a mounted tool with no
capability. A door that half-loads its policy is worse than one that refuses to
open.

The two-state profile distinction is load-bearing (ADR-0019 §5):

  * caller has NO ``profile`` field  -> legacy, full pre-ADR-0019 surface.
    Required by the no-compatibility-break constraint. Attributed in the ledger
    as ``LEGACY_PROFILE`` so the permissive path is visible in every event.
  * caller names a profile granting nothing -> a real profile with no
    capabilities. Denies everything. NOT the same as the above.

Pure stdlib (tomllib, 3.11+); no gateway imports, so tests and the caller CLI
can use it without standing up a server.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

DEFAULT_PROFILES_PATH = Path(__file__).resolve().parents[1] / "etc" / "profiles.toml"

# Ledger attribution for a caller carrying no profile field. Not a real profile:
# it can never be named in profiles.toml (load rejects it) and cannot be
# assigned by the caller CLI. It exists so "this call ran unrestricted" is an
# explicit, greppable fact in the ledger rather than an absence.
LEGACY_PROFILE = "legacy-unrestricted"


class ProfileError(ValueError):
    """Raised when the profile policy is malformed. Always fatal at startup."""


# --- Taxonomy: every mounted tool -> exactly one capability -------------------
#
# Grouped by capability for review. Keep alphabetical within a group. A tool
# appears exactly once; assert_surface_complete() proves this covers whatever is
# actually mounted at runtime.
TOOL_CAPABILITY: dict[str, str] = {
    # read: filesystem reads, scope-limited by the caller's `scope` (ADR-0019 §4)
    "glob_files": "read",
    "list_dir": "read",
    "read_file": "read",
    # write: filesystem mutation
    "write_file": "write",
    # query: read-only belief / capacity / catalog queries
    "query_am4_catalog": "query",
    "query_beliefs_summary": "query",
    "query_capabilities": "query",
    "query_capacity": "query",
    "query_findings": "query",
    "query_offload": "query",
    # knowledge_write: writes or derived-state mutation, even where the name
    # sits adjacent to the query family. project/record_event/rebuild_knowledge
    # are the corpus writers (the 2026-07-02 overwrite lives in this family's
    # history) and are excluded from research on purpose.
    "project": "knowledge_write",
    "project_capacity_knowledge": "knowledge_write",
    "project_offload_knowledge": "knowledge_write",
    "rebuild_knowledge": "knowledge_write",
    "record_event": "knowledge_write",
    # repo_metadata: repository FACTS with no file contents -- branch, changed
    # paths, commit sha/author/date/subject. git_log returns exactly that and no
    # blobs, which is why it is safe to grant alongside a narrowed file_scope.
    "git_log": "repo_metadata",
    "git_status": "repo_metadata",
    # repo_content: materializes tracked file CONTENTS. HEARTH's git_diff is
    # working-tree/index only (no revision ranges), but it still renders the
    # contents of any changed file in the repo -- including files a narrowed
    # file_scope denies. That laundering path is why this is its own capability
    # and why assert_authority_coherence refuses the incoherent combination.
    "git_diff": "repo_content",
    # repo_write: mutation + push
    "git_commit_push": "repo_write",
    # generate: inference through the router
    "local_generate": "generate",
    # status: cheap gateway self-report
    "kernel_status": "status",
    # kernel_admin: the kernel change ceremony
    "kernel_change": "kernel_admin",
    # dispatch: fleet work submission (SSH lane, host-side credential)
    "submit_batch": "dispatch",
    "submit_task": "dispatch",
    "task_status": "dispatch",
    # queue: fleet queue inspection
    "queue_status": "queue",
    # test: subprocess test/lint runs
    "lint_digest": "test",
    "run_tests": "test",
    # summon: remote machine control (SSH / Hyper-V / service start)
    "checkpoint_vm": "summon",
    "start_ollama": "summon",
    "wake_am4": "summon",
    # health: watchdog-style sweeps
    "masters_pet": "health",
    "patrol": "health",
    # harvest: fleet run mirroring
    "harvest_fleet_run": "harvest",
    "list_fleet_runs": "harvest",
    # schedule: scheduler advice + hindsight
    "propose_schedule": "schedule",
    "schedule_hindsight": "schedule",
    # catalog_write: the am4 catalog owner (writes its own non-corpus file)
    "gather_am4_catalog": "catalog_write",
    # commander: intent refinement lane
    "refine_idea": "commander",
    "refine_result": "commander",
    # build_request: the auditable build-request lane
    "close_build_request": "build_request",
    "create_build_request": "build_request",
    "execute_build_request": "build_request",
    "get_build_request": "build_request",
    "list_build_requests": "build_request",
    "update_build_request": "build_request",
    # dream: speculative idle lane
    "dream": "dream",
}

KNOWN_CAPABILITIES: frozenset[str] = frozenset(TOOL_CAPABILITY.values())

# --- Authority domains --------------------------------------------------------
#
# An authority domain says WHICH RESOURCE MODEL governs a capability, and
# therefore HOW it is enforced. This is not cosmetic grouping: path containment
# is the right enforcement for a filesystem and the wrong one for a repository,
# a container runtime, or a database. Modelling everything as a path was the
# category error that made "give research git access" collide with "narrow the
# filesystem scope" -- a repository is a NAMED RESOURCE, not an ancestor path.
#
#   AUTH_FILESYSTEM  enforced by path containment against the caller's file_scope
#   AUTH_REPOSITORY  enforced by named-repo membership against repo_access
#   AUTH_GATEWAY     capability gate only; no resource dimension exists yet
#
# New surfaces (container runtime, k8s, database, cloud API) get their own
# domain with their own resource model rather than being crowbarred into paths.
AUTH_FILESYSTEM = "filesystem"
AUTH_REPOSITORY = "repository"
AUTH_GATEWAY = "gateway"

CAPABILITY_AUTHORITY: dict[str, str] = {
    "read": AUTH_FILESYSTEM,
    "write": AUTH_FILESYSTEM,
    "repo_metadata": AUTH_REPOSITORY,
    "repo_content": AUTH_REPOSITORY,
    "repo_write": AUTH_REPOSITORY,
}

# Capabilities that materialize file CONTENTS through a non-filesystem
# authority. These can launder around a narrowed file_scope, so granting one
# alongside a narrowing is a coherence error rather than merely a wide grant.
CONTENT_BEARING_CAPABILITIES: frozenset[str] = frozenset({"repo_content"})


def authority_for(capability: Optional[str]) -> str:
    """The authority domain governing a capability. Unknown/None -> gateway,
    which is capability-gate-only and carries no resource dimension."""
    if capability is None:
        return AUTH_GATEWAY
    return CAPABILITY_AUTHORITY.get(capability, AUTH_GATEWAY)


@dataclass(frozen=True)
class Profile:
    """One resolved profile: a name and its fully-inherited capability set."""

    name: str
    capabilities: frozenset[str]

    def grants(self, capability: str) -> bool:
        return capability in self.capabilities


def capability_for(tool: str) -> Optional[str]:
    """The capability a tool belongs to, or None if unmapped (=> denied)."""
    return TOOL_CAPABILITY.get(tool)


def assert_surface_complete(tool_names: Iterable[str]) -> None:
    """Refuse a tool surface containing anything with no capability mapping.

    Called by the gateway against the ACTUALLY-mounted tool names, so a new
    provider cannot introduce an unclassified tool. Fail-closed at startup
    beats fail-open at request time.
    """
    unmapped = sorted(set(tool_names) - set(TOOL_CAPABILITY))
    if unmapped:
        raise ProfileError(
            "tools mounted with no TOOL_CAPABILITY entry: "
            + ", ".join(unmapped)
            + " - add each to hearth/kernel/capabilities.py and decide which "
              "profiles may reach it (ADR-0019 section 3)"
        )


def _raw_profiles(path: Path) -> Mapping[str, Mapping]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ProfileError(f"profile policy not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        # tomllib rejects duplicate keys itself, which is exactly the
        # "duplicate or conflicting profile declaration" rule.
        raise ProfileError(f"profile policy is not valid TOML ({path}): {exc}") from exc
    table = data.get("profile")
    if not isinstance(table, dict):
        raise ProfileError(f"profile policy has no [profile.*] tables: {path}")
    return table


def _resolve(name: str, raw: Mapping[str, Mapping], seen: tuple[str, ...]) -> frozenset[str]:
    """Depth-first inheritance resolution with cycle and unknown-name checks."""
    if name in seen:
        cycle = " -> ".join([*seen, name])
        raise ProfileError(f"profile inheritance cycle: {cycle}")
    entry = raw.get(name)
    if entry is None:
        raise ProfileError(
            f"profile {name!r} is not defined (referenced via inherits by "
            f"{seen[-1]!r})" if seen else f"profile {name!r} is not defined"
        )
    if not isinstance(entry, dict):
        raise ProfileError(f"profile {name!r} must be a table")

    declared = entry.get("capabilities", [])
    if not isinstance(declared, list) or not all(isinstance(c, str) for c in declared):
        raise ProfileError(f"profile {name!r}: capabilities must be a list of strings")
    unknown = sorted(set(declared) - KNOWN_CAPABILITIES)
    if unknown:
        raise ProfileError(
            f"profile {name!r} declares unknown capabilities: {', '.join(unknown)}; "
            f"known: {', '.join(sorted(KNOWN_CAPABILITIES))}"
        )

    caps = set(declared)
    parent = entry.get("inherits")
    if parent is not None:
        if not isinstance(parent, str) or not parent.strip():
            raise ProfileError(f"profile {name!r}: inherits must be a non-empty string")
        caps |= _resolve(parent, raw, (*seen, name))
    return frozenset(caps)


def load_profiles(path: Optional[Path | str] = None) -> dict[str, Profile]:
    """Load and fully resolve the profile policy. Every defect raises."""
    resolved_path = Path(path) if path else DEFAULT_PROFILES_PATH
    raw = _raw_profiles(resolved_path)
    if LEGACY_PROFILE in raw:
        raise ProfileError(
            f"{LEGACY_PROFILE!r} is reserved for callers with no profile field "
            f"and must not be declared in {resolved_path}"
        )
    profiles: dict[str, Profile] = {}
    for name in raw:
        if not name.strip():
            raise ProfileError("profile names must be non-empty")
        # An empty capability list is legal and meaningful: a real profile that
        # grants nothing (ADR-0019 §5). Only a MISSING profile field on the
        # caller means legacy-unrestricted.
        profiles[name] = Profile(name=name, capabilities=_resolve(name, raw, ()))
    return profiles


def check_tool_access(profile: Optional[Profile], tool: str) -> tuple[bool, Optional[str]]:
    """Decide whether `profile` may invoke `tool`.

    Returns (allowed, capability). `profile is None` means a legacy caller with
    no profile field and is allowed everything — the documented compatibility
    path. A profiled caller is denied any tool whose capability is unmapped
    (fail-closed) or ungranted.
    """
    capability = capability_for(tool)
    if profile is None:
        return True, capability
    if capability is None:
        return False, None
    return profile.grants(capability), capability
