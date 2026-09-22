"""Who is asking — resolved the same way on both sides of the door (D-102).

Identity and authorization are the caller's; the catalog and the capacity
snapshot are everyone's. That separation is the whole point of this module: it
answers "who" and nothing else, and it is the only place in the operator package
that touches caller data.

Two entry points, one answer shape:

  * **CLI.** The caller presents its own door key in ``HEARTH_API_KEY``. The CLI
    does not parse the registry itself — it hands the key to
    ``hearth.kernel.auth.AuthRegistry``, the same code the gateway runs, reading
    the same ``hearth/var/callers.json`` the gateway was started with. With no
    key, ``caller`` is ``None``: ``whoami`` says so and every authority is denied
    with reason ``no_identity``, while ``inspect`` still returns the catalog and
    the snapshot, because those are caller-neutral.
  * **Door.** The gateway has already authenticated the ``X-Hearth-Key`` header
    and pushed a ``DispatchIdentity``; the mounted tool reads that rather than
    re-resolving anything.

The key is never logged, never printed, never written to a file, and never put
into a snapshot, a bundle or a history row. ``AuthRegistry`` is constructed with
no ledger and consulted with ``lookup`` (not ``resolve``), so resolving identity
from the CLI writes nothing anywhere.

**WI-G2b, D-112 item 2: the requester of an approval is the authenticated
caller, never an argument.** The WI-G2a candidate's ``request_approval`` took a
``requesting_principal`` dictionary, so a library caller could name a false
requester and then decide its own request — the self-approval check compares the
decider against whatever the request claimed. Two things close that here:

* Only the three resolvers below stamp an identity with this module's private
  attestation mark, so an ``Identity`` assembled by hand or rebuilt from JSON is
  distinguishable from one the registry or the gateway resolved.
* :class:`AuthenticatedCaller` is the type the approval boundary binds, and
  :func:`authenticated_caller` is the only way to get one. It cannot be built
  from a dictionary, a payload, argv, an environment variable, a door-tool
  argument, a history row, or an approval record.
"""

from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from hearth.operator import paths

# The attestation mark. Module-private on purpose: its identity, not its value,
# is what cannot be forged from data. `json.loads` can produce every shape in
# this module except this object.
_ATTESTED_AT_BOUNDARY = object()


@dataclass(frozen=True)
class Identity:
    """A resolved caller, or the reason there is none."""

    caller: Optional[dict] = None
    capabilities: frozenset[str] = field(default_factory=frozenset)
    source: str = "none"
    reason: str = "no_identity"
    # Stamped only by the resolvers below. Out of repr (it is an opaque marker)
    # and out of equality (two identities are the same caller or they are not).
    attestation: Any = field(default=None, repr=False, compare=False)

    @property
    def present(self) -> bool:
        return self.caller is not None

    @property
    def attested(self) -> bool:
        """Did the trusted boundary — the callers registry or the gateway —
        resolve this identity? A hand-built Identity says no."""
        return self.attestation is _ATTESTED_AT_BOUNDARY

    @property
    def caller_id(self) -> str:
        return str(self.caller["id"]) if self.caller else "_anonymous"

    def grants(self, capability: str) -> bool:
        return capability in self.capabilities


class AuthenticatedCaller:
    """The requester an approval request binds: an attested, present caller.

    There is deliberately no constructor that takes a principal. The three
    fields an approval record binds about a requester are READ from the
    authenticated identity, so "who asked for this approval" is a fact of the
    call, not a field of the request (D-112 item 2).
    """

    __slots__ = ("identity",)

    def __init__(self, identity: Identity, *, attestation: Any = None) -> None:
        if attestation is not _ATTESTED_AT_BOUNDARY:
            raise PermissionError(
                "an AuthenticatedCaller is minted only by "
                "hearth.operator.identity.authenticated_caller() from an identity this "
                "module resolved at the trusted boundary; it is never constructed from "
                "request data (D-112 item 2)")
        self.identity = identity

    @property
    def principal(self) -> dict:
        """The three fields an approval record binds about this principal."""
        caller = self.identity.caller or {}
        return {"id": str(caller["id"]),
                "profile": caller.get("profile"),
                "runner_class": caller.get("runner_class")}

    @property
    def caller_id(self) -> str:
        return self.identity.caller_id

    def __repr__(self) -> str:  # never the key, never the capability set
        return (f"AuthenticatedCaller({self.caller_id!r}, "
                f"source={self.identity.source!r})")


def authenticated_caller(identity: Identity) -> AuthenticatedCaller:
    """The authenticated requester context for `identity`, or a loud refusal.

    Refuses anything that is not an `Identity`, an `Identity` this module did
    not resolve (request data wearing a type), and an `Identity` with no caller
    (nobody is asking).
    """
    if not isinstance(identity, Identity):
        raise PermissionError(
            f"a requester is derived from a resolved Identity, not a "
            f"{type(identity).__name__}: the approval boundary takes no caller-supplied "
            "principal (D-112 item 2)")
    if not identity.attested:
        raise PermissionError(
            "this Identity was not resolved at the trusted boundary (no callers-registry "
            "or gateway attestation), so it cannot name the requester of an approval "
            "(D-112 item 2)")
    if not identity.present:
        raise PermissionError(
            f"no caller identity is in force ({identity.reason}); an approval request "
            "names the authenticated caller, and there is none")
    return AuthenticatedCaller(identity, attestation=_ATTESTED_AT_BOUNDARY)


def presented_key() -> Optional[str]:
    """The caller's key from the environment, or None. Never returned to output."""
    value = os.environ.get(paths.API_KEY_ENV)
    return value if value else None


def _capabilities_for(profile_name: Optional[str]) -> frozenset[str]:
    """Resolve a profile name to its fully-inherited capability set.

    An absent profile is not a wide one: ADR-0023 denies a caller that names no
    role, and the gateway labels that caller `unprofiled` in the ledger.
    """
    from hearth.kernel.capabilities import LEGACY_PROFILE, load_profiles

    if not profile_name or profile_name == LEGACY_PROFILE:
        return frozenset()
    profiles = load_profiles(paths.PROFILES_PATH)
    profile = profiles.get(profile_name)
    return frozenset(profile.capabilities) if profile else frozenset()


def resolve_from_env(key: Optional[str] = None) -> Identity:
    """Resolve the CLI caller through the kernel's own registry."""
    presented = key if key is not None else presented_key()
    registry = paths.callers_registry_path()
    source = f"env {paths.API_KEY_ENV} resolved against {paths.repo_relative(registry)}"
    if presented is None:
        return Identity(source="none",
                        reason=f"no_identity: {paths.API_KEY_ENV} is not set",
                        attestation=_ATTESTED_AT_BOUNDARY)
    if not registry.is_file():
        return Identity(source=source,
                        reason=("no_identity: the callers registry is not readable at "
                                f"{registry} (set HEARTH_ROOT to the deployed hearth root "
                                "when running from a worktree)"),
                        attestation=_ATTESTED_AT_BOUNDARY)
    try:
        from hearth.kernel.auth import AuthRegistry

        auth = AuthRegistry(callers_path=registry, profiles_path=paths.PROFILES_PATH)
        caller = auth.lookup(presented)
    except Exception as exc:  # noqa: BLE001 - a broken policy must not print a key
        return Identity(source=source,
                        reason=f"no_identity: registry could not be loaded "
                               f"({type(exc).__name__}: {exc})",
                        attestation=_ATTESTED_AT_BOUNDARY)
    if caller is None:
        return Identity(source=source,
                        reason="no_identity: the presented key is not in the callers registry",
                        attestation=_ATTESTED_AT_BOUNDARY)
    return Identity(
        caller={"id": caller.id, "profile": caller.profile,
                "runner_class": caller.runner_class, "node": caller.node},
        capabilities=_capabilities_for(caller.profile),
        source=source,
        reason="resolved",
        attestation=_ATTESTED_AT_BOUNDARY,
    )


def resolve_from_key(key: Optional[str]) -> Identity:
    """Resolve a caller from a key the caller just typed.

    The same registry lookup `resolve_from_env` performs, named for what it is:
    the approval path hands the credential straight from the interactive prompt
    to the kernel's registry and keeps no copy. (The WI-G2 candidate's CLI called
    a function of this name that did not exist, so the shipped D-112 entry point
    could not record any approval at all.)
    """
    return resolve_from_env(key)


def prompt_for_key(prompt: str = "approver key (input is hidden): ") -> Optional[str]:
    """Read a credential interactively, never echoing it (D-112 item 2).

    From a terminal this is `getpass`, so the characters never reach the screen.
    When stdin is a pipe — how a subprocess test drives the command — the line is
    read from that pipe, which no terminal ever echoes either. The value is
    returned to the caller and never logged, printed, written to a file, or put
    into a history row; there is deliberately no argv flag, no environment
    variable, and no configuration key that can carry it.
    """
    stream = sys.stdin
    if stream is not None and stream.isatty():
        value = getpass.getpass(prompt)
    else:
        print(prompt, end="", file=sys.stderr, flush=True)
        line = stream.readline() if stream is not None else ""
        print("", file=sys.stderr, flush=True)
        value = line.strip()
    return value.strip() or None


def resolve_from_door() -> Identity:
    """Resolve the caller the gateway already authenticated for this call."""
    try:
        from hearth.observation.identity import current_identity
    except Exception as exc:  # noqa: BLE001
        return Identity(reason=f"no_identity: dispatch identity unavailable ({exc})",
                        attestation=_ATTESTED_AT_BOUNDARY)
    dispatch = current_identity()
    if dispatch is None:
        return Identity(reason="no_identity: no gateway caller identity in force",
                        attestation=_ATTESTED_AT_BOUNDARY)
    from hearth.kernel.capabilities import LEGACY_PROFILE

    profile = dispatch.profile if dispatch.profile != LEGACY_PROFILE else None
    return Identity(
        caller={"id": dispatch.caller_id, "profile": profile,
                "runner_class": dispatch.runner_class, "node": dispatch.node},
        capabilities=_capabilities_for(profile),
        source="door: authenticated X-Hearth-Key (DispatchIdentity)",
        reason="resolved",
        attestation=_ATTESTED_AT_BOUNDARY,
    )
