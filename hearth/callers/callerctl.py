"""callerctl: mint, rotate, revoke and list HEARTH caller identities (ADR-0019).

The caller registry (``hearth/var/callers.json``) maps a SECRET to an identity:
the JSON object key IS the key the caller presents in ``X-Hearth-Key``. Editing
that file by hand is how secrets end up in shell history, in a half-written
file, or clobbered by a concurrent edit. This is the supported lifecycle.

    python -m hearth.callers.callerctl mint   --id docker-open-notebook \
        --runner-class local --node omen --profile research \
        --scope C:\\work\\commandcenter\\docs
    python -m hearth.callers.callerctl rotate --id docker-open-notebook
    python -m hearth.callers.callerctl revoke --id docker-open-notebook
    python -m hearth.callers.callerctl list

Safety properties, each deliberate:

  * secrets come from ``secrets.token_hex`` (CSPRNG), never a PRNG or a hash of
    something guessable;
  * the whole read-modify-write runs under an exclusive lock file, so two
    concurrent mints cannot each write a registry missing the other's caller;
  * the replacement is atomic — temp file in the same directory, flushed and
    ``fsync``ed before ``os.replace`` — so a crash mid-write cannot truncate the
    registry and lock every caller out;
  * a timestamped backup is taken BEFORE modification. Backups therefore contain
    only PRIOR secrets, never the newly minted one, and are permission-locked
    the same way the live file is;
  * unrelated entries and UNKNOWN fields are preserved verbatim, so a newer
    callerctl can add metadata without an older one silently dropping it;
  * the new secret is printed exactly once, to stdout, and never written to a
    log, a backup, or ``list`` output. ``list`` shows a fingerprint only;
  * ``rotate`` installs the new secret and drops the old one in the SAME atomic
    replacement — there is no window in which both work;
  * there is NO default profile. Minting an unrestricted caller requires saying
    ``--legacy-unrestricted`` out loud, and rotation never invents one.

The gateway reads the registry at startup, so any change here needs a gateway
restart to take effect. Every command says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hearth.kernel.auth import assert_authority_coherence
from hearth.kernel.capabilities import LEGACY_PROFILE, ProfileError, load_profiles
from hearth.kernel.ledger import RUNNER_CLASSES, REPO_ROOT
from hearth.toolsurface._scope import SCOPE_ENV_VAR, validate_narrowing

DEFAULT_REGISTRY = REPO_ROOT / "hearth" / "var" / "callers.json"
SECRET_BYTES = 32               # 64 hex chars
LOCK_TIMEOUT_S = 10.0
LOCK_POLL_S = 0.05


class CallerCtlError(RuntimeError):
    """Any refusal from this CLI. Printed without a traceback."""


# --- registry file handling ---------------------------------------------------

class _RegistryLock:
    """Exclusive lock guarding the registry's read-modify-write.

    An O_CREAT|O_EXCL sidecar: portable to Windows, and the holder's pid and
    start time are written in so a stuck lock can be diagnosed rather than
    guessed at. Deliberately does NOT auto-break stale locks — silently
    stealing a lock is how two writers end up interleaved.
    """

    def __init__(self, target: Path) -> None:
        self.path = target.with_suffix(target.suffix + ".lock")
        self._fd: Optional[int] = None

    def __enter__(self) -> "_RegistryLock":
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, f"pid={os.getpid()} at={_stamp()}\n".encode("utf-8"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise CallerCtlError(
                        f"registry is locked by another callerctl run: {self.path}\n"
                        f"If no callerctl is running, delete that file and retry."
                    ) from None
                time.sleep(LOCK_POLL_S)

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _restrict_permissions(path: Path) -> Optional[str]:
    """Best-effort lockdown of a secret-bearing file to the current user.

    Windows: break ACL inheritance and grant only the running account. POSIX:
    chmod 600. Returns a note on failure rather than raising — a registry that
    exists with loose ACLs is recoverable; one that failed to write is not.
    """
    try:
        if os.name == "nt":
            account = os.environ.get("USERNAME") or ""
            if not account:
                return "USERNAME unset; ACL not tightened"
            completed = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(F)"],
                capture_output=True, text=True, timeout=20)
            if completed.returncode != 0:
                return f"icacls exited {completed.returncode}: {completed.stderr.strip()[:200]}"
            return None
        os.chmod(path, 0o600)
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{type(exc).__name__}: {exc}"


def _acl_status(path: Path) -> dict[str, str]:
    """Return a metadata-only ACL state for callerctl diagnostics.

    ACL tightening remains best-effort. A failed or unverifiable lockdown is
    deliberately visible to ``callerctl list`` rather than looking identical
    to a secured registry.
    """
    if not path.exists():
        return {"status": "unknown", "detail": "registry missing"}
    try:
        if os.name == "nt":
            account = os.environ.get("USERNAME") or ""
            if not account:
                return {"status": "degraded", "detail": "USERNAME unset"}
            completed = subprocess.run(
                ["icacls", str(path)], capture_output=True, text=True, timeout=20)
            if completed.returncode != 0:
                return {"status": "degraded", "detail": "icacls query failed"}
            output = (completed.stdout or "") + (completed.stderr or "")
            if account.lower() in output.lower() and "(F)" in output:
                return {"status": "secured", "detail": "current account has full control"}
            return {"status": "degraded", "detail": "current account ACL not verified"}
        mode = path.stat().st_mode & 0o777
        return ({"status": "secured", "detail": "mode 600"}
                if mode == 0o600 else
                {"status": "degraded", "detail": f"mode {mode:03o}"})
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "degraded", "detail": f"{type(exc).__name__}"}


def _load_registry(path: Path) -> dict[str, dict]:
    if not path.exists():
        raise CallerCtlError(
            f"caller registry not found: {path}\n"
            f"The gateway is started with --callers pointing at it; create it with a "
            f"single mint if this is a fresh install.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CallerCtlError(f"caller registry is not valid JSON ({path}): {exc}") from exc
    if not isinstance(data, dict):
        raise CallerCtlError(f"caller registry must be a JSON object: {path}")
    return data


def _write_registry(path: Path, data: dict[str, dict]) -> list[str]:
    """Backup, then atomically replace. Returns human-readable notes."""
    notes: list[str] = []
    backup = path.with_name(f"{path.name}.bak-{_stamp()}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    problem = _restrict_permissions(backup)
    if problem:
        notes.append(f"backup permissions not tightened ({problem}): {backup}")
    notes.append(f"backup written: {backup}")

    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    payload = json.dumps(data, indent=2, sort_keys=False) + "\n"
    handle = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(handle, payload.encode("utf-8"))
        os.fsync(handle)          # durable before the rename, not merely buffered
    finally:
        os.close(handle)
    os.replace(temp, path)        # atomic within the same directory
    problem = _restrict_permissions(path)
    if problem:
        notes.append(f"registry permissions not tightened ({problem}): {path}")
    return notes


def _fingerprint(key: str) -> str:
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _find(data: dict[str, dict], caller_id: str) -> Optional[str]:
    """The registry key whose entry has this id, or None."""
    for key, entry in data.items():
        if isinstance(entry, dict) and entry.get("id") == caller_id:
            return key
    return None


# --- validation ---------------------------------------------------------------

def _validate_profile(name: Optional[str], profiles_path: Optional[str]) -> None:
    if name is None:
        return
    if name == LEGACY_PROFILE:
        raise CallerCtlError(
            f"{LEGACY_PROFILE!r} is the ledger label for callers with NO profile; "
            f"it cannot be assigned. Use --legacy-unrestricted to mint such a caller.")
    profiles = load_profiles(profiles_path)
    if name not in profiles:
        raise CallerCtlError(
            f"unknown profile {name!r}; defined in policy: {', '.join(sorted(profiles))}")


def _validate_scope(paths: list[str], label: str = "--file-scope") -> list[str]:
    """Validate a path grant as a narrowing of the CURRENT HEARTH_SCOPE."""
    if not paths:
        return []
    try:
        resolved = validate_narrowing(paths, label=label)
    except ValueError as exc:
        raise CallerCtlError(
            f"{exc}\n"
            f"Note: validated against {SCOPE_ENV_VAR}="
            f"{os.environ.get(SCOPE_ENV_VAR) or '(unset -> repo root)'} as seen by THIS "
            f"process. If that differs from the gateway's, set it here to match.") from exc
    return [str(p) for p in resolved]


# --- commands -----------------------------------------------------------------

def _assert_entry_coherent(entry: dict, profiles_path: Optional[str]) -> None:
    """Refuse an incoherent grant at MINT time, not just at gateway startup, so a
    bad combination is caught while the operator is still looking at it."""
    profile_name = entry.get("profile")
    if profile_name is None:
        return
    profiles = load_profiles(profiles_path)
    assert_authority_coherence(
        entry.get("id", ""), profiles[profile_name],
        tuple(Path(p) for p in entry.get("file_scope", [])) or None,
        tuple(Path(p) for p in entry.get("repo_access", [])) or None)


def cmd_mint(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    _validate_profile(args.profile, args.profiles)
    file_scope = _validate_scope(args.file_scope or [], "--file-scope")
    repo_access = _validate_scope(args.repo_access or [], "--repo-access")

    with _RegistryLock(path):
        data = _load_registry(path)
        if _find(data, args.id) is not None:
            raise CallerCtlError(
                f"caller id {args.id!r} already exists. Minting a second secret for one "
                f"identity would make the ledger ambiguous; use `rotate --id {args.id}` "
                f"to replace its secret.")
        entry: dict[str, Any] = {
            "id": args.id, "runner_class": args.runner_class, "node": args.node}
        if args.profile is not None:
            entry["profile"] = args.profile
        if file_scope:
            entry["file_scope"] = file_scope
        if repo_access:
            entry["repo_access"] = repo_access
        _assert_entry_coherent(entry, args.profiles)
        secret = secrets.token_hex(SECRET_BYTES)
        data[secret] = entry
        notes = _write_registry(path, data)

    _report_secret(secret, entry, args, notes, action="minted")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    _validate_profile(args.profile, args.profiles)
    file_scope = _validate_scope(args.file_scope or [], "--file-scope")
    repo_access = _validate_scope(args.repo_access or [], "--repo-access")

    with _RegistryLock(path):
        data = _load_registry(path)
        old_key = _find(data, args.id)
        if old_key is None:
            raise CallerCtlError(f"no caller with id {args.id!r} to rotate")
        # Preserve the entry verbatim — including fields this version does not
        # know about — and change only what was explicitly asked for. Rotation
        # never invents a profile for a legacy caller (ADR-0019 §6).
        entry = dict(data[old_key])
        if args.profile is not None:
            entry["profile"] = args.profile
        if file_scope:
            entry["file_scope"] = file_scope
        if repo_access:
            entry["repo_access"] = repo_access
        _assert_entry_coherent(entry, args.profiles)
        secret = secrets.token_hex(SECRET_BYTES)
        del data[old_key]          # old secret dies in the SAME replacement
        data[secret] = entry
        notes = _write_registry(path, data)

    _report_secret(secret, entry, args, notes, action="rotated")
    print(f"  prior secret {_fingerprint(old_key)} is now INVALID.")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    with _RegistryLock(path):
        data = _load_registry(path)
        old_key = _find(data, args.id)
        if old_key is None:
            # Distinguish "already gone" from "removed just now" rather than
            # reporting a uniform success that hides a typo'd id.
            print(f"not-found: no caller with id {args.id!r} in {path}")
            print("           (already revoked, or the id never existed)")
            return 1 if args.require_present else 0
        entry = data.pop(old_key)
        notes = _write_registry(path, data)

    print(f"revoked: {entry.get('id')} ({_fingerprint(old_key)}) removed from {path}")
    for note in notes:
        print(f"  {note}")
    print("  Restart the gateway for the revocation to take effect "
          "(the registry is read at startup).")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Metadata only. Secrets never appear here, in any output mode."""
    path = Path(args.registry)
    data = _load_registry(path)
    acl = _acl_status(path)
    rows = []
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        rows.append({
            "id": entry.get("id"),
            "runner_class": entry.get("runner_class"),
            "node": entry.get("node"),
            "profile": entry.get("profile", LEGACY_PROFILE),
            "unrestricted": "profile" not in entry,
            "file_scope": entry.get("file_scope", []),
            "repo_access": entry.get("repo_access", []),
            "key_fingerprint": _fingerprint(key),
            "registry_acl_status": acl["status"],
            "registry_acl_detail": acl["detail"],
        })
    rows.sort(key=lambda r: (r["id"] or ""))

    if args.json:
        # Preserve the historical top-level JSON array shape; ACL state is
        # additive metadata on every row.
        print(json.dumps(rows, indent=2))
        return 0

    print(f"{path}  ({len(rows)} caller(s))")
    print(f"  registry_acl: {acl['status']} ({acl['detail']})\n")
    for row in rows:
        flag = "  [UNRESTRICTED]" if row["unrestricted"] else ""
        print(f"  {row['id']}{flag}")
        print(f"      runner_class : {row['runner_class']}")
        print(f"      node         : {row['node']}")
        print(f"      profile      : {row['profile']}")
        print(f"      file_scope   : {', '.join(row['file_scope']) or '(full HEARTH_SCOPE)'}")
        print(f"      repo_access  : {', '.join(row['repo_access']) or '(none - git falls back to file_scope)'}")
        print(f"      key           : {row['key_fingerprint']}")
    unrestricted = [r["id"] for r in rows if r["unrestricted"]]
    if unrestricted:
        print(f"\n  {len(unrestricted)} caller(s) reach the FULL tool surface: "
              f"{', '.join(unrestricted)}")
    return 0


def _report_secret(secret: str, entry: dict, args: argparse.Namespace,
                   notes: list[str], *, action: str) -> None:
    """Emit the secret exactly once. Never logged, never echoed elsewhere."""
    if args.secret_file:
        target = Path(args.secret_file)
        target.write_text(secret + "\n", encoding="utf-8")
        problem = _restrict_permissions(target)
        print(f"{action}: {entry['id']}")
        print(f"  secret written to {target}"
              + (f"  (permissions not tightened: {problem})" if problem else ""))
    else:
        print(f"{action}: {entry['id']}")
        print("  ---------------- SECRET (shown once) ----------------")
        print(f"  {secret}")
        print("  -----------------------------------------------------")
    print(f"  profile      : {entry.get('profile', LEGACY_PROFILE)}"
          + ("  [UNRESTRICTED - full tool surface]" if "profile" not in entry else ""))
    print(f"  file_scope   : {', '.join(entry.get('file_scope', [])) or '(full HEARTH_SCOPE)'}")
    print(f"  repo_access  : {', '.join(entry.get('repo_access', [])) or '(none)'}")
    print(f"  fingerprint  : {_fingerprint(secret)}")
    for note in notes:
        print(f"  {note}")
    print("  Inject at runtime (env var / Docker secret). Do NOT bake it into an image,")
    print("  commit it, or paste it into a compose file. Restart the gateway to load it.")


# --- entry point --------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="callerctl", description="HEARTH caller identity lifecycle (ADR-0019)")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY),
                        help=f"caller registry path (default {DEFAULT_REGISTRY})")
    parser.add_argument("--profiles", default=None,
                        help="profile policy path (default hearth/etc/profiles.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_grant_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--profile", default=None,
                       help="capability profile to grant (see hearth/etc/profiles.toml)")
        p.add_argument("--legacy-unrestricted", action="store_true", default=False,
                       help="deliberately create/keep a caller with NO profile, reaching "
                            "the full tool surface. Must be stated explicitly.")
        p.add_argument("--file-scope", action="append", default=None, dest="file_scope",
                       help="FILESYSTEM authority: narrow this caller's file reach "
                            "(read_file/write_file/glob/list); repeatable. Must be inside "
                            "HEARTH_SCOPE.")
        p.add_argument("--repo-access", action="append", default=None, dest="repo_access",
                       help="REPOSITORY authority: grant git tools on this repository; "
                            "repeatable. Independent of --file-scope -- a repo grant does "
                            "not widen file reach, and vice versa.")
        p.add_argument("--secret-file", default=None,
                       help="write the secret to this file instead of stdout")

    mint = sub.add_parser("mint", help="create a new caller identity")
    mint.add_argument("--id", required=True)
    mint.add_argument("--runner-class", required=True, choices=list(RUNNER_CLASSES))
    mint.add_argument("--node", required=True)
    add_grant_flags(mint)
    mint.set_defaults(func=cmd_mint)

    rotate = sub.add_parser("rotate", help="replace a caller's secret")
    rotate.add_argument("--id", required=True)
    add_grant_flags(rotate)
    rotate.set_defaults(func=cmd_rotate)

    revoke = sub.add_parser("revoke", help="remove a caller identity")
    revoke.add_argument("--id", required=True)
    revoke.add_argument("--require-present", action="store_true", default=False,
                        help="exit non-zero if the caller was already absent")
    revoke.set_defaults(func=cmd_revoke)

    listing = sub.add_parser("list", help="list caller metadata (never secrets)")
    listing.add_argument("--json", action="store_true", default=False)
    listing.set_defaults(func=cmd_list)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # No default profile, ever. An unrestricted caller must be asked for by name
    # so it cannot be created by forgetting a flag (ADR-0019 §3/§6).
    if args.command in ("mint", "rotate"):
        if args.profile is None and not args.legacy_unrestricted:
            if args.command == "mint":
                parser.error(
                    "mint requires --profile <name>, or --legacy-unrestricted to "
                    "deliberately create a caller with the full tool surface. There is "
                    "no default profile.")
        if args.profile is not None and args.legacy_unrestricted:
            parser.error("--profile and --legacy-unrestricted are mutually exclusive")

    try:
        return int(args.func(args))
    except (CallerCtlError, ProfileError) as exc:
        print(f"callerctl: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
