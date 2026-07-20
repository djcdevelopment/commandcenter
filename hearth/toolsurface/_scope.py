"""HEARTH tool-surface sandbox scoping (Stream H-B; per-caller narrowing ADR-0019).

Every path-taking tool resolves its paths against the sandbox root(s) given by the
HEARTH_SCOPE environment variable (default: the repo root this module lives in).
HEARTH_SCOPE may list several roots separated by os.pathsep (";" on Windows). The
FIRST root is primary: relative paths resolve against it, so existing repo-relative
callers are unaffected. Later roots only widen containment — absolute paths under any
listed root are in scope. Any path that resolves OUTSIDE every root is rejected with
ValueError — this is the lockdown seam: tightening (or widening) the sandbox is one
env var, not a code change.

PER-CALLER NARROWING (ADR-0019 §4): a caller record may carry a ``scope`` that is
NARROWER than HEARTH_SCOPE. The gateway pushes it for the duration of one call via
``caller_scope``; ``scope_roots`` then reports the narrowed set. Narrowing only —
``validate_narrowing`` refuses at registry-load time any caller root not contained by
an env root, so a caller record can never widen the sandbox. This is what keeps
hearth/var (keys, ledger) and fleet/ (inventory) out of a research agent's reach BY
CONSTRUCTION rather than by denylist; a denylist fails open the day a new secret
lands somewhere unlisted.

CONTAINMENT IS FILESYSTEM-SAFE, NOT STRING-SAFE:
  * case and separators normalized (``C:/Work`` == ``c:\\work`` on Windows);
  * prefix comparison respects component boundaries (``C:\\work`` does not contain
    ``C:\\workshop``);
  * ``..`` in an input path is refused outright rather than normalized away;
  * symlinks, junctions and other reparse points are resolved before comparison —
    the nearest EXISTING ancestor is resolved for real (which follows reparse
    points) and the non-existent remainder re-attached, so a write to a not-yet-
    created file is checked against where its parent actually lives;
  * a dangling symlink is followed to its target (``os.path.lexists`` detects it,
    ``resolve`` follows it) so it cannot be used to smuggle a write out of scope;
  * checks run at OPERATION time on every call, not only at config load.

Residual risk, documented rather than papered over: this is not TOCTOU-free. A
reparse point created between the check and the tool's own open() would defeat it.
Closing that needs O_NOFOLLOW-class handles, which stdlib does not offer portably
on Windows; per-operation revalidation is the practical mitigation.

Pure stdlib; no hearth.kernel imports (providers stay kernel-free by contract).
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

# Mirrors tools/workflow/corpus_guard.py REPO_ROOT: the repo root this module lives in.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCOPE_ENV_VAR = "HEARTH_SCOPE"

# Per-call narrowing set by the gateway wrapper. A ContextVar (not an attribute on
# a shared object) so concurrent callers cannot leak scope into each other: set and
# reset inside one synchronous frame is isolated per thread and per task.
_caller_roots: contextvars.ContextVar[Optional[tuple[Path, ...]]] = contextvars.ContextVar(
    "hearth_caller_scope", default=None)

# REPOSITORY authority (ADR-0019). A repository is a NAMED RESOURCE, not an
# ancestor path: git tools legitimately need the repo root, which is exactly the
# ancestor a narrowed file_scope exists to deny. Modelling both as "scope" was a
# category error. A caller's repo_access is therefore a separate grant, resolved
# by its own function, and filesystem authority never implies repository
# authority (or vice versa).
_caller_repos: contextvars.ContextVar[Optional[tuple[Path, ...]]] = contextvars.ContextVar(
    "hearth_caller_repo_access", default=None)


def _norm(path: Path | str) -> str:
    """Case- and separator-normalized form for comparison. On Windows this folds
    case and unifies '/' vs '\\'; on POSIX it is a no-op beyond normpath."""
    return os.path.normcase(os.path.normpath(str(path)))


def _env_roots() -> list[Path]:
    """The roots named by HEARTH_SCOPE (or the repo root), ignoring any narrowing."""
    raw = os.environ.get(SCOPE_ENV_VAR)
    if not raw:
        return [REPO_ROOT]
    roots = [Path(part).resolve() for part in raw.split(os.pathsep) if part.strip()]
    if not roots:
        raise ValueError(f"{SCOPE_ENV_VAR} lists no usable roots: {raw!r}")
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"{SCOPE_ENV_VAR} is not an existing directory: {root}")
    return roots


def scope_roots() -> list[Path]:
    """The sandbox roots in force for the current call: the caller's narrowed set
    if one is pushed, else HEARTH_SCOPE (else the repo root). Read at call time so
    the gateway (or a test) can re-scope without reimporting."""
    narrowed = _caller_roots.get()
    if narrowed is not None:
        return list(narrowed)
    return _env_roots()


def scope_root() -> Path:
    """The primary sandbox root (first entry): relative paths resolve against it."""
    return scope_roots()[0]


def contains(root: Path, target: Path) -> bool:
    """True if `target` is `root` or sits beneath it, comparing on normalized
    components so 'C:\\work' does not appear to contain 'C:\\workshop'."""
    root_norm = _norm(root).rstrip(os.sep)
    target_norm = _norm(target)
    return target_norm == root_norm or target_norm.startswith(root_norm + os.sep)


def in_any_scope(resolved: Path, roots: Optional[list[Path]] = None) -> bool:
    """True if an already-resolved path sits inside (or is) any sandbox root."""
    for root in roots if roots is not None else scope_roots():
        if contains(root, resolved):
            return True
    return False


def _nearest_existing(candidate: Path) -> tuple[Path, list[str]]:
    """Split `candidate` into (nearest existing ancestor, non-existent tail parts).

    Uses lexists, not exists, so a dangling symlink counts as PRESENT and is
    therefore resolved (and followed) rather than treated as a plain new name.
    """
    tail: list[str] = []
    probe = candidate
    while True:
        try:
            if os.path.lexists(probe):
                return probe, tail
        except OSError:
            pass
        parent = probe.parent
        if parent == probe:  # reached the anchor; nothing on this path exists
            return probe, tail
        tail.append(probe.name)
        probe = parent


def real_path_for_check(path: str, base: Optional[Path] = None) -> Path:
    """Resolve `path` to the location containment must be judged against.

    Rejects `..` outright. Resolves the nearest existing ancestor for real (which
    follows symlinks, junctions and other reparse points) and re-attaches any
    non-existent remainder, so a write target that does not exist yet is judged
    against where its parent ACTUALLY lives rather than its nominal spelling.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    candidate = Path(path)
    if any(part == os.pardir for part in candidate.parts):
        raise ValueError(
            f"path must not contain '..' traversal: {path!r} - pass a direct path "
            f"inside the sandbox instead"
        )
    if not candidate.is_absolute():
        candidate = (base if base is not None else scope_roots()[0]) / candidate
    anchor, tail = _nearest_existing(candidate)
    resolved = anchor.resolve()
    for name in reversed(tail):
        resolved = resolved / name
    return Path(os.path.normpath(str(resolved)))


def resolve_in_scope(path: str, root: Optional[Path] = None) -> Path:
    """Resolve `path` (relative to the primary root — or `root` if given — or
    absolute) and refuse anything that escapes every sandbox root in force.

    Containment is checked on the fully resolved path at OPERATION time, so `..`
    hops, symlinks, junctions and not-yet-created targets cannot slip out, and a
    caller's narrowed scope applies to the call that is actually running.
    """
    roots = scope_roots()
    base = root if root is not None else roots[0]
    resolved = real_path_for_check(path, base=base)
    if root is not None and contains(root, resolved):
        return resolved
    if not in_any_scope(resolved, roots):
        raise ValueError(
            f"path escapes {SCOPE_ENV_VAR} sandbox: {path!r} resolves to {resolved}, "
            f"which is outside {', '.join(str(r) for r in roots)}"
        )
    return resolved


def validate_narrowing(roots: Sequence[str | Path], *, label: str = "scope") -> tuple[Path, ...]:
    """Validate a caller's declared scope as a NARROWING of HEARTH_SCOPE.

    Every declared root must exist and be contained by some env root. Raises
    ValueError otherwise — callers of this (the auth registry) treat that as a
    startup error, so a scope that would widen the sandbox stops the gateway
    rather than silently taking effect.
    """
    if not roots:
        raise ValueError(f"{label}: declared but empty; omit the field instead")
    env = _env_roots()
    narrowed: list[Path] = []
    for entry in roots:
        text = str(entry)
        if not text.strip():
            raise ValueError(f"{label}: contains an empty path")
        if os.pardir in Path(text).parts:
            raise ValueError(f"{label}: must not contain '..': {text!r}")
        resolved = Path(text).resolve()
        if not resolved.is_dir():
            raise ValueError(f"{label}: not an existing directory: {resolved}")
        if not any(contains(root, resolved) for root in env):
            raise ValueError(
                f"{label}: {resolved} is not inside {SCOPE_ENV_VAR} "
                f"({', '.join(str(r) for r in env)}) - a caller scope may only "
                f"NARROW the sandbox, never widen it"
            )
        narrowed.append(resolved)
    return tuple(narrowed)


def resolve_repo(path: str) -> Path:
    """Resolve a repository path under the REPOSITORY authority.

    When the caller declares ``repo_access``, the requested repo must sit inside
    one of the granted repositories — filesystem authority is not consulted at
    all, so a caller narrowed to ``docs/`` can still be granted the repo root for
    git metadata without that grant leaking into ``read_file``.

    When the caller declares NO repo_access, this falls back to filesystem
    containment, which is both the pre-ADR-0019 behavior (legacy callers are
    unaffected) and correctly fail-closed for a profiled caller: to use git you
    must be granted a repository, not merely happen to have a wide file_scope.
    """
    granted = _caller_repos.get()
    if granted is None:
        return resolve_in_scope(path)
    resolved = real_path_for_check(path, base=granted[0])
    for repo in granted:
        if contains(repo, resolved):
            return resolved
    raise ValueError(
        f"repository not granted: {path!r} resolves to {resolved}, which is outside "
        f"this caller's repo_access ({', '.join(str(r) for r in granted)})"
    )


@contextlib.contextmanager
def caller_repo_access(repos: Optional[Iterable[Path]]) -> Iterator[None]:
    """Push a caller's granted repositories for the duration of one call."""
    if repos is None:
        yield
        return
    token = _caller_repos.set(tuple(repos))
    try:
        yield
    finally:
        _caller_repos.reset(token)


@contextlib.contextmanager
def caller_scope(roots: Optional[Iterable[Path]]) -> Iterator[None]:
    """Push a caller's narrowed roots for the duration of one call.

    `None` leaves the ambient HEARTH_SCOPE in force (legacy callers, and profiled
    callers that declare no scope). Always reset in a finally, so a raising tool
    cannot leak one caller's scope into the next call on this thread.
    """
    if roots is None:
        yield
        return
    token = _caller_roots.set(tuple(roots))
    try:
        yield
    finally:
        _caller_roots.reset(token)
