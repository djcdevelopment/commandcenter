"""HEARTH tool provider: filesystem (Stream H-B).

Pure typed functions over stdlib only — the gateway wraps each with auth, provenance,
and the ledger. All paths are sandboxed to HEARTH_SCOPE (see _scope.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from hearth.toolsurface._scope import resolve_in_scope, scope_root


def read_file(path: str, max_bytes: int = 200_000) -> dict:
    """Read a UTF-8 text file inside the sandbox, truncated to max_bytes."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    target = resolve_in_scope(path)
    if not target.is_file():
        raise ValueError(f"not a file: {path}")
    raw = target.read_bytes()
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    return {
        "path": str(target),
        "size": len(raw),
        "returned_bytes": min(len(raw), max_bytes),
        "truncated": truncated,
        "content": text,
    }


def write_file(path: str, content: str, create_dirs: bool = False) -> dict:
    """Write UTF-8 text to a file inside the sandbox, optionally creating parent dirs."""
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    target = resolve_in_scope(path)
    if not target.parent.is_dir():
        if not create_dirs:
            raise ValueError(f"parent directory does not exist (pass create_dirs=True): {target.parent}")
        target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    data = content.encode("utf-8")
    target.write_bytes(data)
    return {
        "path": str(target),
        "bytes_written": len(data),
        "created": not existed,
        "overwrote": existed,
    }


def list_dir(path: str = ".") -> dict:
    """List a directory inside the sandbox: name, kind, and size for each entry."""
    target = resolve_in_scope(path)
    if not target.is_dir():
        raise ValueError(f"not a directory: {path}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
        entries.append(
            {
                "name": child.name,
                "kind": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            }
        )
    return {"path": str(target), "count": len(entries), "entries": entries}


def glob_files(pattern: str, root: str = ".") -> dict:
    """Glob for files under a sandbox directory (recursive patterns like **/*.py allowed)."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("pattern must be a non-empty string")
    if Path(pattern).is_absolute() or pattern.startswith(".."):
        raise ValueError("pattern must be relative to root, not absolute or parent-escaping")
    base = resolve_in_scope(root)
    if not base.is_dir():
        raise ValueError(f"root is not a directory: {root}")
    sandbox = scope_root()
    matches = []
    for hit in sorted(base.glob(pattern)):
        resolved = hit.resolve()
        if resolved != sandbox and not resolved.is_relative_to(sandbox):
            continue  # a symlinked match must not leak paths outside the sandbox
        if resolved.is_file():
            matches.append(str(resolved.relative_to(sandbox)))
    return {"root": str(base), "pattern": pattern, "count": len(matches), "matches": matches}


def get_tools() -> list[Callable]:
    return [read_file, write_file, list_dir, glob_files]
