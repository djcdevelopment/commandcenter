#!/usr/bin/env python3
"""sync_claude_block.py: copy a Linux agent-doc span into a synced file (no node on this host).

Spans (same discipline as sync-offload-block.mjs: --check writes nothing; --write replaces only the span, refuses a
missing or duplicated marker, writes a temp file and renames it, prints the sha256 of the bytes outside the span before
and after, which must match):
  default   the offload span: the lines after the unique `<!-- lab-intent:end -->` line and before the unique
            `<!-- source: docs/agents/hearth-offload-block-linux.md` line; source = the block file before its own
            `<!-- source:` line.
  --method  the hearth-method span: the lines between `<!-- hearth-method:begin -->` and `<!-- hearth-method:end -->`;
            source = docs/agents/hearth-method.md with trailing blank lines removed.
  --target PATH  the synced file (default ~/.claude/CLAUDE.md; ~/.codex/AGENTS.md carries the method span too).
"""
from __future__ import annotations
import hashlib, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BLOCK = REPO / "docs" / "agents" / "hearth-offload-block-linux.md"
METHOD = REPO / "docs" / "agents" / "hearth-method.md"
END = "<!-- lab-intent:end -->"
SRC = "<!-- source: docs/agents/hearth-offload-block-linux.md"
M_BEGIN, M_END = "<!-- hearth-method:begin -->", "<!-- hearth-method:end -->"

def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]

def unique_line(lines: list[str], pred, what: str) -> int:
    hits = [i for i, l in enumerate(lines) if pred(l)]
    if len(hits) != 1:
        sys.exit(f"refused: {what}: {len(hits)} matches (need exactly 1)")
    return hits[0]

def bounds(lines: list[str], method: bool) -> tuple[int, int]:
    """(first span line, first line after the span): exclusive of both markers."""
    if method:
        b = unique_line(lines, lambda l: l.strip() == M_BEGIN, "hearth-method begin marker")
        e = unique_line(lines, lambda l: l.strip() == M_END, "hearth-method end marker")
    else:
        b = unique_line(lines, lambda l: l.strip() == END, "lab-intent end marker")
        e = unique_line(lines, lambda l: l.startswith(SRC), "target source marker")
    if e <= b:
        sys.exit("refused: target markers out of order")
    return b + 1, e

def source_body(method: bool) -> list[str]:
    if method:
        body = METHOD.read_text(encoding="utf-8").split("\n")
        while body and body[-1] == "":
            body.pop()
        return body
    lines = BLOCK.read_text(encoding="utf-8").split("\n")
    return lines[: unique_line(lines, lambda l: l.startswith(SRC), "block source marker")]

def main(argv: list[str]) -> int:
    write, method = "--write" in argv, "--method" in argv
    target = Path(argv[argv.index("--target") + 1]) if "--target" in argv else Path(os.environ.get("CLAUDE_MD", Path.home() / ".claude" / "CLAUDE.md"))
    body = source_body(method)
    lines = target.read_text(encoding="utf-8").split("\n")
    s, e = bounds(lines, method)
    head, span, tail = lines[:s], lines[s:e], lines[e:]
    outside_before = sha("\n".join(head + tail).encode())
    same = span == body
    name = "method" if method else "offload"
    print(f"{target} {name} span: {'in sync' if same else 'DRIFT'} (target {len(span)} lines, source {len(body)} lines); "
          f"source sha {sha(chr(10).join(body).encode())}, span sha {sha(chr(10).join(span).encode())}")
    if not write:
        return 0 if same else 1
    if same:
        print("nothing to write"); return 0
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text("\n".join(head + body + tail), encoding="utf-8"); tmp.replace(target)
    after = target.read_text(encoding="utf-8").split("\n")
    s2, e2 = bounds(after, method)
    outside_after = sha("\n".join(after[:s2] + after[e2:]).encode())
    print(f"outside-span sha before {outside_before} after {outside_after}: {'unchanged' if outside_before == outside_after else 'CHANGED'}")
    return 0 if outside_before == outside_after else 2

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
