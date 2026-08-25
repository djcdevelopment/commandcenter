"""Path containment for the BF6 media root -- a separate authority domain.

WHY NOT JUST WIDEN HEARTH_SCOPE
-------------------------------
The obvious move is to append ``E:\\BF6-Highlights`` to ``HEARTH_SCOPE`` and reuse
``resolve_in_scope``. That would work, and it would also hand every
``unrestricted`` and ``builder`` caller read/write access to the media volume for
the sake of one operation. The capability taxonomy already models filesystem,
repository and gateway as distinct authorities (hearth.kernel.capabilities);
media is a fourth, and it gets its own resolver with its own root.

So this module borrows the *primitives* from ``_scope`` -- ``real_path_for_check``
(which rejects ``..`` and resolves junctions/symlinks before judging) and
``contains`` (which compares on component boundaries so ``C:\\work`` does not
appear to contain ``C:\\workshop``) -- while supplying its own root. It never
reads ``HEARTH_SCOPE`` and never widens it.

WHAT THIS ADDS OVER `_scope`
----------------------------
1. **UNC is refused explicitly.** ``_scope`` refuses ``\\\\server\\share\\x`` only
   *incidentally*, because it fails containment against the configured roots. If
   someone ever puts a UNC path in a root, that incidental protection evaporates.
   Here it is a stated rule, checked before anything else.
2. **Subtrees are typed, and ``raw`` is read-only.** The system's strongest
   structural guarantee is that raw footage cannot be modified -- on AM4 that is
   enforced by a read-only cifs mount, but OMEN owns ``E:`` natively and has no
   such protection. A write resolution against ``raw`` therefore raises here,
   reproducing the guarantee on the side of the boundary that lacks it.
"""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
from typing import Optional

from hearth.toolsurface._scope import contains, real_path_for_check

MEDIA_ROOT_ENV = "HEARTH_MEDIA_ROOT"
DEFAULT_MEDIA_ROOT = r"E:\BF6-Highlights"

# The only directories a render job may name. `logs` is deliberately absent: it
# is OMEN-local capture diagnostics that the render lane has no business in.
READABLE_SUBTREES = ("raw", "work", "drafts", "approved", "_bench")

# Everything except `raw`. Raw is the immutable source of truth; see the module
# docstring for why that is enforced here and not only by the AM4 mount.
WRITABLE_SUBTREES = ("work", "drafts", "approved", "_bench")


class MediaPathError(ValueError):
    """A path was refused by the media authority. Callers must fail closed."""


def media_root() -> Path:
    """The configured media root, resolved.

    Deliberately NOT cached: tests point this at a temporary directory, and a
    module-level cache would leak one test's root into the next.
    """
    configured = os.environ.get(MEDIA_ROOT_ENV) or DEFAULT_MEDIA_ROOT
    return Path(configured).expanduser()


def _reject_unc(path: str) -> None:
    """Refuse UNC paths outright, whatever the root happens to be today."""
    if path.startswith("\\\\") or path.startswith("//"):
        raise MediaPathError(
            "UNC paths are never permitted in the media root: %r" % (path,)
        )
    # PureWindowsPath spots \\?\UNC\... and similar spellings that a plain
    # prefix check would miss.
    drive = PureWindowsPath(path).drive
    if drive.startswith("\\\\") or drive.startswith("//"):
        raise MediaPathError(
            "UNC paths are never permitted in the media root: %r" % (path,)
        )


def _reject_drive_relative(path: str) -> None:
    """Refuse 'C:foo' -- a drive with no root, which resolves per-process CWD."""
    pure = PureWindowsPath(path)
    if pure.drive and not pure.root:
        raise MediaPathError(
            "drive-relative paths are ambiguous and not permitted: %r" % (path,)
        )


def subtree_of(relative: str) -> str:
    """The declared subtree (first component) of a media-relative path."""
    if not isinstance(relative, str) or not relative.strip():
        raise MediaPathError("media path must be a non-empty string")
    first = PureWindowsPath(relative.replace("/", "\\")).parts
    if not first:
        raise MediaPathError("media path must name a subtree: %r" % (relative,))
    name = first[0].strip("\\/")
    if name not in READABLE_SUBTREES:
        raise MediaPathError(
            "unknown media subtree %r; expected one of %s"
            % (name, ", ".join(READABLE_SUBTREES))
        )
    return name


def resolve_media(relative: str, *, mode: str = "read", root: Optional[Path] = None) -> Path:
    """Resolve a media-root-relative path, refusing anything that escapes it.

    ``relative`` names its subtree first, e.g. ``raw/<session>/<file>.mkv`` --
    the same spelling the render-request sidecar carries across the machine
    boundary, so no translation happens between validation and use.

    ``mode`` is ``read`` or ``write``; ``write`` against ``raw`` is refused.

    Absolute paths are accepted only if they land inside the media root, so a
    caller cannot smuggle in ``C:\\Windows\\...`` or a second drive.
    """
    if mode not in ("read", "write"):
        raise MediaPathError("mode must be 'read' or 'write', got %r" % (mode,))
    if not isinstance(relative, str) or not relative.strip():
        raise MediaPathError("media path must be a non-empty string")

    _reject_unc(relative)
    _reject_drive_relative(relative)

    subtree = subtree_of(relative)
    if mode == "write" and subtree not in WRITABLE_SUBTREES:
        raise MediaPathError(
            "%r is read-only; refusing a write resolution against it. Raw footage "
            "is immutable by design." % (subtree,)
        )

    base = Path(root) if root is not None else media_root()
    # real_path_for_check rejects '..' and resolves reparse points before we
    # judge containment, so a junction inside the root cannot point outside it.
    try:
        resolved = real_path_for_check(relative, base=base)
    except ValueError as exc:
        raise MediaPathError(str(exc)) from exc

    base_resolved = real_path_for_check(str(base), base=base)
    if not contains(base_resolved, resolved):
        raise MediaPathError(
            "path escapes the media root: %r resolves to %s, which is outside %s"
            % (relative, resolved, base_resolved)
        )
    return resolved


def relative_to_media(path: Path, *, root: Optional[Path] = None) -> str:
    """Express a resolved path back as a media-relative one, POSIX-separated.

    Used when handing a location back across the sidecar boundary: AM4 must
    never receive an OMEN-absolute path, exactly as the worker's container-
    absolute ``/data/...`` paths are meaningless on OMEN.
    """
    base = Path(root) if root is not None else media_root()
    base_resolved = real_path_for_check(str(base), base=base)
    target = real_path_for_check(str(path), base=base)
    if not contains(base_resolved, target):
        raise MediaPathError(
            "path is not inside the media root: %s" % (target,)
        )
    return target.relative_to(base_resolved).as_posix()
