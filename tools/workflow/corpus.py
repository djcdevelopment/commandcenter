"""Canonical Corpus enumerator (CQRS/ES standardization, plan step 4).

Root cause of the 2026-07-02 corpus-overwrite incident: "the corpus" was ambient —
callers passed a `sources` list (default `["runs"]`), and `collect_event_files`
(tools/workflow/project_capacity.py) did an unguarded `rglob("events.jsonl")` under
each. A run over an incomplete tree looked entirely legitimate and silently
materialized a smaller corpus, clobbering knowledge/*.json with less evidence.

This module makes "all events under a root" a first-class, fingerprinted value:
`Corpus.enumerate(root)` is now the ONLY place `rglob("events.jsonl")` lives for
this path. It is pure (no writes), stdlib-only, and deterministic — the digest is
content-shaped (relative path + line count per file), never mtime/size-based, so
it is stable across checkouts, clones, and re-runs over identical content.

Existing call sites (`collect_event_files`, `project()`) keep working unchanged;
this is additive plumbing they can opt into stamping corpus provenance from.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

# Match the field name workflow events already use (see fixtures/workflow/*.jsonl
# and tools/workflow/project_state.py's read_events): "timestamp", ISO-8601.
_TIMESTAMP_FIELD = "timestamp"


@dataclass(frozen=True)
class Corpus:
    root: Path
    event_files: tuple[Path, ...]  # sorted, deterministic order
    event_count: int               # total non-blank event lines across all files
    watermark: str | None          # max "timestamp" value seen across all events
    corpus_digest: str             # "sha256:..." over sorted (relpath, line_count) pairs

    @staticmethod
    def enumerate(root: Path) -> "Corpus":
        """Enumerate every events.jsonl under `root` and fingerprint the result.

        The ONLY rglob("events.jsonl") call for this code path. Deterministic:
        - event_files is sorted by relative path string.
        - corpus_digest is computed over (relpath, line_count) pairs, sorted the
          same way — no mtime, no size, no absolute paths — so two clones/checkouts
          of the identical content produce the identical digest.
        - Blank lines are skipped for both event_count and watermark parsing.
        - watermark is the lexical/ISO max of every parseable "timestamp" field
          found across all events; None if no event anywhere carries one.

        The watermark max is LEXICAL, which orders correctly only while every
        stamp shares one format -- and this corpus does not: all four spellings
        (`.ffffff+00:00`, `.ffffffZ`, `+00:00`, `Z`) are live in runs/ today. It
        happens to be right anyway (lexical max == parsed-instant max, verified
        2026-07-18) because a flip needs two events sharing a second but spelled
        differently. See hearth/projection/rebuild.py::_aggregate_corpus for the
        full finding, the other affected sites, and why fixing them is its own
        change rather than a drive-by.
        """
        root = Path(root)
        if root.is_dir():
            found = sorted(root.rglob("events.jsonl"), key=lambda p: p.relative_to(root).as_posix())
        elif root.is_file():
            found = [root]
        else:
            found = []

        event_files: list[Path] = []
        digest_parts: list[tuple[str, int]] = []
        total_events = 0
        watermark: str | None = None

        for event_file in found:
            try:
                relpath = event_file.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                relpath = event_file.name

            line_count = 0
            for raw_line in event_file.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                line_count += 1
                total_events += 1
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                ts = event.get(_TIMESTAMP_FIELD)
                if isinstance(ts, str) and (watermark is None or ts > watermark):
                    watermark = ts

            event_files.append(event_file)
            digest_parts.append((relpath, line_count))

        digest_parts.sort(key=lambda part: part[0])
        hasher = hashlib.sha256()
        for relpath, line_count in digest_parts:
            hasher.update(f"{relpath}:{line_count}\n".encode("utf-8"))
        corpus_digest = f"sha256:{hasher.hexdigest()}"

        return Corpus(
            root=root,
            event_files=tuple(event_files),
            event_count=total_events,
            watermark=watermark,
            corpus_digest=corpus_digest,
        )
