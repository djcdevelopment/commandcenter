"""HEARTH tool-surface sandbox scoping (Stream H-B).

Every path-taking tool resolves its paths against the sandbox root(s) given by the
HEARTH_SCOPE environment variable (default: the repo root this module lives in).
HEARTH_SCOPE may list several roots separated by os.pathsep (";" on Windows). The
FIRST root is primary: relative paths resolve against it, so existing repo-relative
callers are unaffected. Later roots only widen containment — absolute paths under any
listed root are in scope. Any path that resolves OUTSIDE every root is rejected with
ValueError — this is the lockdown seam: tightening (or widening) the sandbox is one
env var, not a code change.

Pure stdlib; no hearth.kernel imports (providers stay kernel-free by contract).
"""

from __future__ import annotations

import os
from pathlib import Path

# Mirrors tools/workflow/corpus_guard.py REPO_ROOT: the repo root this module lives in.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCOPE_ENV_VAR = "HEARTH_SCOPE"


def scope_roots() -> list[Path]:
    """All sandbox roots: HEARTH_SCOPE (os.pathsep-separated) if set, else the repo
    root. Read at call time so the gateway (or a test) can re-scope without
    reimporting. Every listed root must be an existing directory."""
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


def scope_root() -> Path:
    """The primary sandbox root (first entry): relative paths resolve against it."""
    return scope_roots()[0]


def in_any_scope(resolved: Path, roots: list[Path] | None = None) -> bool:
    """True if an already-resolved path sits inside (or is) any sandbox root."""
    for root in roots if roots is not None else scope_roots():
        if resolved == root or resolved.is_relative_to(root):
            return True
    return False


def resolve_in_scope(path: str, root: Path | None = None) -> Path:
    """Resolve `path` (relative to the primary root — or `root` if given — or
    absolute) and refuse anything that escapes every sandbox root. Containment is
    checked on the fully resolved path, so `..` hops and symlinks cannot slip out."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    roots = scope_roots()
    base = root if root is not None else roots[0]
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if root is not None and (resolved == root or resolved.is_relative_to(root)):
        return resolved
    if not in_any_scope(resolved, roots):
        raise ValueError(
            f"path escapes {SCOPE_ENV_VAR} sandbox: {path!r} resolves to {resolved}, "
            f"which is outside {', '.join(str(r) for r in roots)}"
        )
    return resolved
