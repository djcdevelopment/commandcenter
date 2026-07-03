"""HEARTH gateway guard hooks (Stream H-A).

Thin enforcement layer run by the gateway BEFORE dispatching a tool. Guards
moved from repo convention into the kernel per HEARTH-FULL-BUILDOUT L0: the
2026-07-02 corpus-overwrite incident is designed out at the single door instead
of patrolled per-projector.

Two checks, both raising GuardRejection (message always starts "guard:"; the
gateway logs the rejection as a ledger event with ok=false):

1. Knowledge-path protection — any tool whose args reference a path under the
   repo's own knowledge/ dir is refused unless the tool is in the registered
   knowledge-tool set (the ONLY writers to knowledge/*.json, per L1).
2. Fixture-taint — registered knowledge tools whose args carry fixture-derived
   source paths are run through tools/workflow/corpus_guard.check_fixture_taint
   (reused, not rewritten), so the incident's exact shape is refused at the
   gateway too.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workflow.corpus_guard import FIXTURE_COMPONENT, FixtureTaintError, check_fixture_taint

DEFAULT_GUARDED_PATTERN = re.compile(r"(record_event|project|knowledge)", re.IGNORECASE)


class GuardRejection(RuntimeError):
    """Raised when a guard refuses a tool dispatch. Message starts with 'guard:'."""


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


class GuardStack:
    """Pre-dispatch guard checks for the gateway.

    `knowledge_tools` is the set of tool names registered as legitimate
    knowledge writers (the gateway derives it from the mounted knowledge
    provider). `guarded_pattern` selects which tool names get the deeper
    fixture-taint check; the knowledge-path refusal applies to every tool.
    """

    def __init__(self, repo_root: Optional[Path | str] = None,
                 knowledge_tools: Iterable[str] = (),
                 guarded_pattern: re.Pattern = DEFAULT_GUARDED_PATTERN) -> None:
        self.repo_root = Path(repo_root).resolve() if repo_root else REPO_ROOT
        self.knowledge_dir = (self.repo_root / "knowledge").resolve()
        self.knowledge_tools = set(knowledge_tools)
        self.guarded_pattern = guarded_pattern

    def register_knowledge_tools(self, names: Iterable[str]) -> None:
        self.knowledge_tools.update(names)

    def _knowledge_paths(self, args: dict) -> list[Path]:
        hits = []
        for text in _iter_strings(args):
            try:
                resolved = (self.repo_root / text).resolve() if not Path(text).is_absolute() \
                    else Path(text).resolve()
            except (OSError, ValueError):
                continue
            if resolved == self.knowledge_dir or self.knowledge_dir in resolved.parents:
                hits.append(resolved)
        return hits

    def check(self, tool: str, args: dict) -> None:
        """Raise GuardRejection if dispatching `tool` with `args` must be refused."""
        knowledge_hits = self._knowledge_paths(args)
        if knowledge_hits and tool not in self.knowledge_tools:
            raise GuardRejection(
                f"guard: tool {tool!r} references knowledge store path "
                f"{knowledge_hits[0]} but is not a registered knowledge tool; "
                f"knowledge/*.json is written only through the knowledge tool family"
            )
        if knowledge_hits and self.guarded_pattern.search(tool):
            fixture_sources = [text for text in _iter_strings(args)
                               if FIXTURE_COMPONENT in Path(text).parts]
            if fixture_sources:
                try:
                    check_fixture_taint(fixture_sources, self.knowledge_dir,
                                        repo_root=self.repo_root)
                except FixtureTaintError as exc:
                    raise GuardRejection(f"guard:fixture-taint: {exc}") from exc
