#!/usr/bin/env python3
"""sync_claude_block.py: copy the Linux offload block into ~/.claude/CLAUDE.md's offload span (no node on this host).

The span is the bytes after the unique `<!-- lab-intent:end -->` line and before the unique
`<!-- source: docs/agents/hearth-offload-block-linux.md` line. The source block is everything in
docs/agents/hearth-offload-block-linux.md before its own `<!-- source:` line. Discipline of sync-offload-block.mjs:
--check writes nothing; --write replaces only the span, refuses a missing or duplicated marker, writes a temp file
and renames it, and prints the sha256 of the bytes outside the span before and after (they must match).
"""
from __future__ import annotations
import hashlib, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BLOCK = REPO / "docs" / "agents" / "hearth-offload-block-linux.md"
TARGET = Path(os.environ.get("CLAUDE_MD", Path.home() / ".claude" / "CLAUDE.md"))
END = "<!-- lab-intent:end -->"
SRC = "<!-- source: docs/agents/hearth-offload-block-linux.md"

def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]

def unique_line(lines: list[str], pred, what: str) -> int:
    hits = [i for i, l in enumerate(lines) if pred(l)]
    if len(hits) != 1:
        sys.exit(f"refused: {what}: {len(hits)} matches (need exactly 1)")
    return hits[0]

def main(argv: list[str]) -> int:
    write = "--write" in argv
    block_lines = BLOCK.read_text(encoding="utf-8").split("\n")
    b_src = unique_line(block_lines, lambda l: l.startswith(SRC), "block source marker")
    body = block_lines[:b_src]
    tgt_lines = TARGET.read_text(encoding="utf-8").split("\n")
    t_end = unique_line(tgt_lines, lambda l: l.strip() == END, "lab-intent end marker")
    t_src = unique_line(tgt_lines, lambda l: l.startswith(SRC), "target source marker")
    if t_src <= t_end:
        sys.exit("refused: target markers out of order")
    head, span, tail = tgt_lines[: t_end + 1], tgt_lines[t_end + 1 : t_src], tgt_lines[t_src:]
    outside_before = sha("\n".join(head + tail).encode())
    same = span == body
    print(f"span: {'in sync' if same else 'DRIFT'} (target {len(span)} lines, block {len(body)} lines); "
          f"block sha {sha(chr(10).join(body).encode())}, span sha {sha(chr(10).join(span).encode())}")
    if not write:
        return 0 if same else 1
    if same:
        print("nothing to write"); return 0
    new = "\n".join(head + body + tail)
    tmp = TARGET.with_name(TARGET.name + ".tmp")
    tmp.write_text(new, encoding="utf-8"); tmp.replace(TARGET)
    after = TARGET.read_text(encoding="utf-8").split("\n")
    a_end = unique_line(after, lambda l: l.strip() == END, "end marker after write")
    a_src = unique_line(after, lambda l: l.startswith(SRC), "source marker after write")
    outside_after = sha("\n".join(after[: a_end + 1] + after[a_src:]).encode())
    print(f"outside-span sha before {outside_before} after {outside_after}: {'unchanged' if outside_before == outside_after else 'CHANGED'}")
    print(f"written: {TARGET}")
    return 0 if outside_before == outside_after else 2

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
