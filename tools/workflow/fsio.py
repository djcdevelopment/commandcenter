"""Atomic JSON writes (CQRS/ES standardization plan, step 2).

The 2026-07-02 corpus-overwrite incident makes knowledge/*.json integrity critical, but the
integrity gap it exposed is broader than the guard: every knowledge writer today is an
in-place `write_text` — a crash mid-write (process kill, disk full, power loss) leaves a
torn/truncated file on disk, with no override or audit trail able to save it because the
bytes themselves are corrupt.

`atomic_write_json` closes that gap the standard way: serialize, write to a sibling `.tmp`
file, then `os.replace(tmp, path)`. `os.replace` is atomic on POSIX and — unlike
`Path.rename` — also atomic (and overwrite-capable) on Windows, so this is safe on every
platform this repo runs on.

This module deliberately has no imports from `hearth/` or `tools/workflow/*` beyond the
stdlib, so both families (tools/workflow/*.py and hearth/projection/*.py,
hearth/toolsurface/*.py) can import it without risking a circular import.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path: Path, doc: dict, *, indent: int = 2) -> None:
    """Write `doc` as pretty JSON to `path` atomically.

    Matches the serialization every existing writer in this repo already uses:
    `json.dumps(doc, indent=2)` plus a trailing newline, encoded utf-8. Writes first to
    `path` with an added `.tmp` suffix, then atomically replaces `path` with it via
    `os.replace` — so a crash mid-write leaves either the old file or the new file intact,
    never a torn one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(doc, indent=indent) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
