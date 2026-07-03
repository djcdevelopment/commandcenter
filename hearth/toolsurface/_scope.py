"""HEARTH tool-surface sandbox scoping (Stream H-B).

Every path-taking tool resolves its paths against a single sandbox root, given by the
HEARTH_SCOPE environment variable (default: the repo root this module lives in). Any
path that resolves OUTSIDE the scope is rejected with ValueError — this is the lockdown
seam: when H2 lands, tightening the sandbox is one env var, not a code change.

Pure stdlib; no hearth.kernel imports (providers stay kernel-free by contract).
"""

from __future__ import annotations

import os
from pathlib import Path

# Mirrors tools/workflow/corpus_guard.py REPO_ROOT: the repo root this module lives in.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCOPE_ENV_VAR = "HEARTH_SCOPE"


def scope_root() -> Path:
    """The sandbox root: HEARTH_SCOPE if set, else the repo root. Read at call time so
    the gateway (or a test) can re-scope without reimporting."""
    raw = os.environ.get(SCOPE_ENV_VAR)
    root = Path(raw).resolve() if raw else REPO_ROOT
    if not root.is_dir():
        raise ValueError(f"{SCOPE_ENV_VAR} is not an existing directory: {root}")
    return root


def resolve_in_scope(path: str, root: Path | None = None) -> Path:
    """Resolve `path` (relative to the scope root, or absolute) and refuse anything that
    escapes the sandbox. Containment is checked on the fully resolved path, so `..`
    hops and symlinks cannot slip out."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    base = root if root is not None else scope_root()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if resolved != base and not resolved.is_relative_to(base):
        raise ValueError(
            f"path escapes {SCOPE_ENV_VAR} sandbox: {path!r} resolves to {resolved}, "
            f"which is outside {base}"
        )
    return resolved
