"""The Brief — one contract for a unit of unattended work.

A brief is what the backlog drain dispatches. It is a *document*: a CCMETA
header (rendered by ``task_lane._ccmeta_header`` — the same function
``submit_task`` uses, imported rather than re-implemented, so the format can
never drift) followed by the prompt body. That makes an authored brief a plain
``.md`` file on disk, and a refined/candidate brief the same shape built in
memory.

Three properties this module owes the rest of B-03:

  * **Round-trip.** ``parse(brief.render()).render() == brief.render()``, byte
    for byte, for every source. The header is the serialization; the fields the
    header cannot carry (``slug``/``title``/``source``/``source_ref`` — they are
    provenance, not conductor instructions) are derived on parse and do not
    affect the rendered bytes.
  * **Absent optionals are omitted** (B-01's rule). A brief with no task_class,
    est_tokens, requires or max_age_s renders the BARE header
    ``{"builders": [...]}`` — byte-identical to what ``_ccmeta_header`` has
    always emitted for a plain submit.
  * **submit_kwargs() is exactly submit_task's keyword surface.** No key that
    ``task_lane.submit_task`` does not accept, so a dispatcher can splat it.
    ``hearth/tests/backlog/test_briefs.py`` introspects the real signature.

Security invariants (B-03): ``slug`` and the derived ``plan_id_hint`` are
restricted to ``[A-Za-z0-9._-]``; ``requires`` globs go through B-01's
``validate_requires`` (no absolute paths, no drive letters, no ``..``);
``max_age_s`` through ``validate_max_age_s``. Every rule runs in
``__post_init__``, so an invalid Brief cannot exist — there is no "validate it
later" window in which a bad value could reach the conductor's inbox.

Stdlib + hearth.toolsurface.task_lane + tools.workflow.assay_acceptance only.
No hearth.kernel import (this package is dispatch-shaped data, not kernel).
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hearth.toolsurface.task_lane import (  # noqa: E402
    DEFAULT_BUILDERS,
    _ccmeta_header,
    _validate_est_tokens,
    estimate_tokens,
)
from hearth.toolsurface.task_expectations import (  # noqa: E402
    validate_max_age_s,
    validate_requires,
)
from tools.workflow.assay_acceptance import _CCMETA_RE  # noqa: E402

# The closed set of places a brief may come from. Anything else is a
# programming error, not a new source: adding one means adding a generator in
# sources.py and a priority rung in select.py, and both are deliberate edits.
SOURCES = ("authored", "refined", "candidate")

# The charset a slug / plan_id_hint may use. Deliberately narrow: these strings
# become FILENAMES (dispatched/<plan_id>.md) and a remote path in the
# conductor's inbox, so anything that could be read as a separator, a shell
# metacharacter or a traversal segment is out.
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")

MAX_SLUG_CHARS = 80
MAX_PLAN_ID_HINT_CHARS = 60
MAX_TITLE_CHARS = 120


def safe_slug(text: str, max_len: int = MAX_SLUG_CHARS) -> str:
    """Collapse ``text`` into the ``[A-Za-z0-9._-]`` charset.

    Case and underscores are PRESERVED (unlike ``task_lane._slugify``, which
    lowercases and drops them): a candidate_id like ``bbb_high`` has to stay
    recognisable in a plan_id and in a ledger row, and losing the underscore
    silently renames the thing being dispatched. Runs of unsafe characters
    collapse to a single ``-``; leading/trailing punctuation is trimmed so a
    slug can never start with ``.`` or ``-`` (a dotfile / an argv flag).
    """
    if not isinstance(text, str):
        raise ValueError("safe_slug() needs a string")
    collapsed = _SAFE_RE.sub("-", text).strip("-._")
    if len(collapsed) > max_len:
        collapsed = collapsed[:max_len].rstrip("-._")
    return collapsed or "brief"


def _first_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:MAX_TITLE_CHARS]
    return ""


@dataclass(frozen=True)
class Brief:
    """One unit of unattended work, whatever produced it.

    ``builders=None`` means "let submit_task choose" — the rendered header
    still names ``task_lane.DEFAULT_BUILDERS`` because the header field is not
    optional (the conductor reads it as the builder pin, and a ``null`` there
    is not a shape it understands). ``requires=()`` means "no declared
    deliverables"; it renders as an ABSENT key, never an empty list.
    """

    slug: str
    title: str
    body: str
    builders: Optional[tuple[str, ...]]
    task_class: Optional[str]
    est_tokens: Optional[int]
    requires: tuple[str, ...]
    max_age_s: Optional[int]
    source: str
    source_ref: str

    def __post_init__(self) -> None:
        # Validate on CONSTRUCTION, not on demand: an invalid Brief must not be
        # constructible at all, or there is a window in which one reaches a
        # dispatcher that never called validate().
        self.validate()

    # -- validation ---------------------------------------------------------

    def validate(self) -> "Brief":
        """Run every rule; raise ValueError naming the first violation.

        Idempotent and side-effect free — ``__post_init__`` calls it, and a
        caller may call it again after ``dataclasses.replace``.
        """
        if not isinstance(self.slug, str) or not _SAFE_SLUG_RE.match(self.slug):
            raise ValueError(
                f"slug must match [A-Za-z0-9._-]+ ; got {self.slug!r}")
        if self.slug.strip("-._") != self.slug or not self.slug.strip("-._"):
            # "..", ".hidden", "-flag": inside the charset but still a traversal
            # segment, a dotfile, or something argv would read as an option once
            # the slug becomes a filename or a plan_id.
            raise ValueError(
                f"slug must not begin or end with '.', '-' or '_' ; got {self.slug!r}")
        if len(self.slug) > MAX_SLUG_CHARS:
            raise ValueError(f"slug must be at most {MAX_SLUG_CHARS} characters")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must be a non-empty string")
        if not isinstance(self.body, str) or not self.body.strip():
            raise ValueError("body must be a non-empty string")
        if self.builders is not None:
            if (not isinstance(self.builders, tuple) or not self.builders
                    or not all(isinstance(b, str) and b.strip() for b in self.builders)):
                raise ValueError(
                    "builders must be None or a non-empty tuple of non-empty strings")
        if self.task_class is not None and (
                not isinstance(self.task_class, str) or not self.task_class.strip()):
            raise ValueError("task_class must be a non-empty string when provided")
        # B-01's own validators, imported not copied: one definition of what a
        # legal requires-glob and a legal lifetime are.
        _validate_est_tokens(self.est_tokens)
        if not isinstance(self.requires, tuple):
            raise ValueError("requires must be a tuple of relative glob strings")
        if self.requires:
            validate_requires(list(self.requires))
        validate_max_age_s(self.max_age_s)
        if self.source not in SOURCES:
            raise ValueError(
                f"source must be one of {SOURCES}; got {self.source!r}")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise ValueError("source_ref must be a non-empty string")
        return self

    # -- serialization ------------------------------------------------------

    def render(self) -> str:
        """CCMETA header + body, exactly as an authored ``.md`` file on disk."""
        return _ccmeta_header(
            list(self.builders) if self.builders else list(DEFAULT_BUILDERS),
            task_class=self.task_class,
            est_tokens=self.est_tokens,
            est_tokens_source=None,
            requires=list(self.requires) or None,
            max_age_s=self.max_age_s,
        ) + self.body

    def plan_id_hint(self) -> str:
        """``<source>-<slug>``, safe-charset, bounded.

        The source rides the hint on purpose: a plan_id is the only provenance
        marker that survives into the conductor's run directory, so
        ``hearth-candidate-...`` vs ``hearth-authored-...`` is readable there
        without joining back to the ledger.
        """
        return safe_slug(f"{self.source}-{self.slug}", max_len=MAX_PLAN_ID_HINT_CHARS)

    def submit_kwargs(self) -> dict:
        """Exactly the keyword surface of ``task_lane.submit_task``.

        ``est_tokens`` is derived from the RENDERED prompt (header included)
        when the brief does not declare one, so the number the ledger records
        and the number the conductor is told describe the same bytes. A brief
        that DOES declare est_tokens keeps it verbatim and it rides the header.
        """
        prompt = self.render()
        est = (self.est_tokens if self.est_tokens is not None
               else estimate_tokens(prompt, self.task_class))
        return {
            "prompt": prompt,
            "builders": list(self.builders) if self.builders else None,
            "plan_id_hint": self.plan_id_hint(),
            "task_class": self.task_class,
            "est_tokens": est,
            "requires": list(self.requires) or None,
            "max_age_s": self.max_age_s,
        }


def parse(text: str, *, source: str = "authored",
          source_ref: Optional[str] = None,
          slug: Optional[str] = None) -> Brief:
    """Parse a rendered brief back into a ``Brief``. Fails loudly.

    ``parse(text)`` is the whole contract; the keyword-only arguments let the
    authored source attach the provenance the document itself cannot carry
    (the filename it came from). They do NOT affect the rendered bytes, so the
    round-trip property holds either way.

    Raises ValueError on: no CCMETA header, a header that is not a JSON object,
    a header with no ``builders`` key, and — via ``Brief.__post_init__`` — any
    invalid field or an empty body.
    """
    if not isinstance(text, str):
        raise ValueError("parse() needs a string")
    match = _CCMETA_RE.search(text)
    if not match:
        raise ValueError("brief has no CCMETA header")
    try:
        meta = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"brief CCMETA header is not valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError(
            f"brief CCMETA header must be a JSON object, got {type(meta).__name__}")
    if "builders" not in meta:
        raise ValueError("brief CCMETA header has no 'builders' key")
    builders = meta["builders"]
    if (not isinstance(builders, list) or not builders
            or not all(isinstance(b, str) and b.strip() for b in builders)):
        raise ValueError(
            "brief CCMETA 'builders' must be a non-empty list of non-empty strings")

    body = text[match.end():]
    # _ccmeta_header ends its output with a newline; consume exactly that one
    # so render(parse(x)) is byte-identical to x.
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]

    requires = meta.get("requires")
    if requires is not None and not isinstance(requires, list):
        raise ValueError("brief CCMETA 'requires' must be a list when present")

    title = _first_line(body) or "untitled brief"
    resolved_slug = slug or safe_slug(title)
    return Brief(
        slug=resolved_slug,
        title=title,
        body=body,
        builders=tuple(builders),
        task_class=meta.get("task_class"),
        est_tokens=meta.get("est_tokens"),
        requires=tuple(requires or ()),
        max_age_s=meta.get("max_age_s"),
        source=source,
        source_ref=source_ref or resolved_slug,
    )
