"""HEARTH caller identity & auth (Stream H-A, frozen contract 3; ADR-0019).

Callers present a key in the X-Hearth-Key HTTP header; the registry file
``hearth/etc/callers.json`` maps key -> {id, runner_class, node}. A caller is
(who) x (runner class) x (node). Unknown or missing keys are rejected AND the
rejection is itself recorded as a ledger event (tool="__auth__", ok=false) —
raw keys are never written to the ledger, only a sha256 digest.

ADR-0019 adds two OPTIONAL fields to a caller record, both absent by default so
every pre-existing caller behaves exactly as before:

  ``profile``  the capability profile this caller is granted (see
               hearth/etc/profiles.toml). ABSENT means legacy-unrestricted: the
               full pre-ADR-0019 tool surface. A profile that grants nothing is
               NOT the same thing — see the two-state note below.
  ``scope``    a NARROWING of HEARTH_SCOPE for this caller's path-taking tools.
               Validated at load to be contained by an env root; a scope that
               would widen the sandbox is a startup error.

Two-state profile distinction (ADR-0019 §5), enforced here:
  * key absent            -> Caller.profile is None -> legacy, everything allowed
  * present but grants [] -> a real Profile with no capabilities -> nothing allowed

Unknown fields in a caller record are IGNORED rather than rejected, so a newer
callerctl can add metadata without breaking an older gateway.

The caller's LEDGER shape stays frozen at exactly {id, runner_class, node}:
``hearth-event.v1`` declares ``additionalProperties: false`` on that object and
the validator asserts exact key equality, so profile attribution rides the
event's own optional top-level ``profile`` field, not the caller object.
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from hearth.kernel.capabilities import (CONTENT_BEARING_CAPABILITIES, LEGACY_PROFILE,
                                        Profile, ProfileError, load_profiles)
from hearth.kernel.ledger import RUNNER_CLASSES, Ledger, new_event
from hearth.toolsurface._scope import contains, validate_narrowing

DEFAULT_CALLERS_PATH = Path(__file__).resolve().parents[1] / "etc" / "callers.json"
AUTH_TOOL = "__auth__"
HEADER_NAME = "X-Hearth-Key"

log = logging.getLogger("hearth.auth")


@dataclass(frozen=True)
class Caller:
    """One authenticated caller identity."""

    id: str
    runner_class: str
    node: str
    profile: Optional[str] = None
    file_scope: Optional[tuple[Path, ...]] = None
    repo_access: Optional[tuple[Path, ...]] = None

    def as_dict(self) -> dict:
        """The FROZEN hearth-event.v1 caller object: exactly {id, runner_class,
        node}. Do not add fields here — the schema sets additionalProperties
        false and the validator asserts exact key equality, so a fourth key
        would invalidate every event written."""
        return {"id": self.id, "runner_class": self.runner_class, "node": self.node}

    @property
    def is_legacy(self) -> bool:
        """True when this caller carries no profile and so reaches everything."""
        return self.profile is None

    @property
    def ledger_profile(self) -> str:
        """What to stamp on the event's `profile` field. Legacy callers are
        named explicitly rather than left null, so 'this ran unrestricted' is a
        greppable fact in the ledger instead of an absence."""
        return self.profile if self.profile is not None else LEGACY_PROFILE


def assert_authority_coherence(caller_id: str, profile: Optional[Profile],
                               file_scope: Optional[tuple[Path, ...]],
                               repo_access: Optional[tuple[Path, ...]],
                               *, label: Optional[str] = None) -> None:
    """Refuse a grant where one authority launders around another's narrowing.

    Authority domains are separate but NOT independent: a content-bearing
    repository capability (git_diff) renders the contents of any changed file in
    a repo, including files a narrowed file_scope denies. Granting both is
    incoherent rather than merely wide, so it is refused at load AND at mint
    time -- the alternative is trusting whoever writes the profile to notice,
    which is exactly the by-convention containment ADR-0019 exists to replace.
    """
    where = label or f"caller {caller_id!r}"
    if profile is None or not file_scope:
        return  # legacy caller, or no filesystem narrowing to launder around
    leaking = sorted(profile.capabilities & CONTENT_BEARING_CAPABILITIES)
    if not leaking:
        return
    escaping = [str(repo) for repo in (repo_access or ())
                if not any(contains(root, repo) for root in file_scope)]
    if escaping:
        raise ProfileError(
            f"{where}: profile {profile.name!r} grants {', '.join(leaking)} (which "
            f"materializes file contents) on {', '.join(escaping)}, which extends "
            f"beyond its file_scope ({', '.join(str(p) for p in file_scope)}). That "
            f"combination would read file contents the filesystem authority denies. "
            f"Either drop the content-bearing capability or widen file_scope to match."
        )


def _key_fingerprint(key: Optional[str]) -> str:
    if key is None:
        return "absent"
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class AuthRegistry:
    """Resolves X-Hearth-Key values to Caller identities from callers.json."""

    def __init__(self, callers_path: Optional[Path | str] = None,
                 ledger: Optional[Ledger] = None,
                 profiles: Optional[Mapping[str, Profile]] = None,
                 profiles_path: Optional[Path | str] = None) -> None:
        self.callers_path = Path(callers_path) if callers_path else DEFAULT_CALLERS_PATH
        self.ledger = ledger
        self.profiles: Mapping[str, Profile] = (
            profiles if profiles is not None else load_profiles(profiles_path))
        self._callers = self._load()

    def _load(self) -> dict[str, Caller]:
        raw = json.loads(self.callers_path.read_text(encoding="utf-8"))
        callers: dict[str, Caller] = {}
        for key, entry in raw.items():
            who = entry.get("id")
            if entry.get("runner_class") not in RUNNER_CLASSES:
                raise ValueError(
                    f"callers.json entry {who!r}: runner_class must be one of {RUNNER_CLASSES}"
                )

            profile: Optional[str] = None
            if "profile" in entry:
                profile = entry["profile"]
                if not isinstance(profile, str) or not profile.strip():
                    raise ProfileError(
                        f"callers.json entry {who!r}: profile must be a non-empty string; "
                        f"OMIT the field entirely for a legacy unrestricted caller"
                    )
                if profile == LEGACY_PROFILE:
                    raise ProfileError(
                        f"callers.json entry {who!r}: {LEGACY_PROFILE!r} is reserved for "
                        f"callers with no profile field and cannot be assigned"
                    )
                if profile not in self.profiles:
                    raise ProfileError(
                        f"callers.json entry {who!r}: unknown profile {profile!r}; "
                        f"defined profiles: {', '.join(sorted(self.profiles)) or '(none)'}"
                    )

            file_scope = self._narrowing(entry, "file_scope", who)
            repo_access = self._narrowing(entry, "repo_access", who)

            caller = Caller(id=entry["id"], runner_class=entry["runner_class"],
                            node=entry["node"], profile=profile,
                            file_scope=file_scope, repo_access=repo_access)
            self._assert_authority_coherence(caller)
            callers[key] = caller
        return callers

    @staticmethod
    def _narrowing(entry: dict, field: str, who: Optional[str]) -> Optional[tuple[Path, ...]]:
        """Parse and validate one path-grant field as a narrowing of HEARTH_SCOPE."""
        if field not in entry:
            return None
        declared = entry[field]
        if isinstance(declared, str):
            declared = [declared]
        if not isinstance(declared, list):
            raise ValueError(
                f"callers.json entry {who!r}: {field} must be a string or list of strings")
        return validate_narrowing(declared, label=f"callers.json entry {who!r} {field}")

    def _assert_authority_coherence(self, caller: Caller) -> None:
        assert_authority_coherence(
            caller.id, self.profile_for(caller), caller.file_scope, caller.repo_access,
            label=f"callers.json entry {caller.id!r}")

    @property
    def legacy_caller_ids(self) -> list[str]:
        """Ids of callers reaching the full surface because they carry no profile.
        The gateway warns about these at startup and doorcheck lists them."""
        return sorted(c.id for c in self._callers.values() if c.is_legacy)

    def profile_for(self, caller: Caller) -> Optional[Profile]:
        """The resolved Profile for a caller, or None for a legacy caller.

        None means "no restriction" and is the ONLY value that means that. A
        caller naming a profile that grants nothing gets a real Profile with an
        empty capability set, which denies everything.
        """
        if caller.profile is None:
            return None
        return self.profiles[caller.profile]

    def resolve(self, key: Optional[str]) -> Optional[Caller]:
        """Return the Caller for `key`, or None after recording the rejection.

        The rejection event carries a fingerprint of the presented key (never the
        key itself) in `error`, under a synthetic __unauthenticated__ identity.
        """
        caller = self._callers.get(key) if key is not None else None
        if caller is not None:
            return caller
        if self.ledger is not None:
            self.ledger.append(new_event(
                {"id": "__unauthenticated__", "runner_class": "human",
                 "node": socket.gethostname()},
                AUTH_TOOL,
                ok=False,
                error=f"auth: unknown or missing {HEADER_NAME} key ({_key_fingerprint(key)})",
            ))
        return None
