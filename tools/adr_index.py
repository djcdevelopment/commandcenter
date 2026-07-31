"""Build a machine-readable index of every ADR-style decision record under C:\\work.

Why this exists: there are 105 ADR-style files across 12 directories in four naming
conventions, and the bare numbers COLLIDE -- `0001` names nine different decisions and
`0013` names four. "See ADR 0013" is therefore not a resolvable reference, which makes it
an active hazard for any agent following a citation.

This is deliberately ADDITIVE. It renames nothing and moves nothing: renaming would break
inbound references across 105 files plus prose (including three dead
`file:///d:/work/game/docs/adrs/...` links in Lumberjacks/docs/godot-client-plan.md), and
the rename cannot even be scoped safely until this index shows what points where.

Stable citation key: "<repo-relative-dir>#<number>", e.g.
"commandcenter/docs/adr#0013" -- unambiguous across every register.

Usage:
    python -m tools.adr_index                 # write JSON + Markdown
    python -m tools.adr_index --check         # exit 1 if the index is stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

WORK_ROOT = Path("C:/work")
DEFAULT_OUT_JSON = WORK_ROOT / "handoffs" / "decision-architecture" / "adr-index.json"
DEFAULT_OUT_MD = WORK_ROOT / "handoffs" / "decision-architecture" / "adr-index.md"

CONTRACT_VERSION = "adr-index.v1"

# Directory names that hold decision records, in any repo.
DIR_NAMES = {"adr", "adrs", "decisions"}

# Paths that are never a first-party register: dependency trees, agent worktrees
# (which mirror a real register and would inflate every count ~3x), vendored upstream
# projects, and test fixtures.
EXCLUDE_PARTS = ("node_modules", ".git", "worktrees", "upstream", "fixtures",
                 "site-packages", ".venv", "venv", "dist", "build")

# The four live spellings. Order matters: the most specific pattern wins.
FILENAME_PATTERNS = (
    ("adr-prefixed", re.compile(r"^(?P<kind>ADR|PDR)-(?P<num>\d{3,4})-(?P<slug>.+)\.md$", re.I)),
    ("pd-prefixed", re.compile(r"^(?P<kind>pd)-(?P<num>\d{1,4})-(?P<slug>.+)\.md$", re.I)),
    ("numbered", re.compile(r"^(?P<num>\d{3,4})-(?P<slug>.+)\.md$")),
)

STATUS_RE = re.compile(r"^\s*[-*]?\s*\**status\**\s*[:|]\s*(?P<status>[^\n|]+)", re.I | re.M)
TITLE_RE = re.compile(r"^#\s+(?P<title>.+)$", re.M)

# Status lines in this corpus run on into review notes and follow-up prose. Keep the
# verdict, drop the essay.
_STATUS_TAIL_RE = re.compile(r"\s*(?:\*\*Review by\*\*|—|--|;|\.\s).*$", re.S)


def _clean(raw: str, limit: int = 120) -> str | None:
    """Normalize a captured heading: strip markdown noise and U+FFFD, collapse
    whitespace, bound the length. Does NOT strip at an em-dash -- titles legitimately
    read 'ADR-0001 — Trust scoring on a manifest transport'."""
    text = raw.replace("�", "").replace("**", "").strip(" *#\t")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text or None


def _clean_status(raw: str, limit: int = 48) -> str | None:
    """Status lines here run on into review notes and follow-up prose. Keep the verdict,
    drop the essay -- so an agent scanning statuses sees 'Accepted', not a paragraph."""
    text = _clean(raw, limit=400) or ""
    text = _STATUS_TAIL_RE.sub("", text).strip(" -—:")
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text or None


def _excluded(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    return any(marker in lowered for marker in EXCLUDE_PARTS)


def find_registers(root: Path = WORK_ROOT) -> list[Path]:
    """Every first-party directory holding decision records."""
    registers: set[Path] = set()
    for candidate in root.rglob("*"):
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        if candidate.name.lower() not in DIR_NAMES or _excluded(candidate):
            continue
        if any(_classify(child.name) for child in candidate.glob("*.md")):
            registers.add(candidate)
    return sorted(registers)


def _classify(filename: str) -> tuple[str, str, str] | None:
    """(convention, zero-stripped number, slug) or None when the name is not a record."""
    for convention, pattern in FILENAME_PATTERNS:
        match = pattern.match(filename)
        if match:
            return convention, match.group("num").lstrip("0") or "0", match.group("slug")
    return None


def read_record(path: Path, register: Path, root: Path) -> dict | None:
    classified = _classify(path.name)
    if classified is None:
        return None
    convention, number, slug = classified
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        text = ""
    title_match = TITLE_RE.search(text)
    status_match = STATUS_RE.search(text)
    register_key = register.relative_to(root).as_posix()
    # U+FFFD means the file is not valid UTF-8 -- in this corpus that is a reliable tell
    # that the document was written programmatically through a mis-encoding path
    # (CP1252 em-dashes are the common case). Surfaced, not silently swallowed.
    encoding_damaged = "�" in text
    return {
        "key": f"{register_key}#{number}",
        "register": register_key,
        "number": number,
        "raw_filename": path.name,
        "convention": convention,
        "slug": slug,
        "title": _clean(title_match.group("title")) if title_match else None,
        "status": _clean_status(status_match.group("status")) if status_match else None,
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size if path.is_file() else 0,
        "encoding_damaged": encoding_damaged,
        "sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16],
    }


def build_index(root: Path = WORK_ROOT) -> dict:
    registers = find_registers(root)
    records: list[dict] = []
    for register in registers:
        for path in sorted(register.glob("*.md")):
            record = read_record(path, register, root)
            if record is not None:
                records.append(record)

    by_number: dict[str, list[str]] = {}
    for record in records:
        by_number.setdefault(record["number"], []).append(record["key"])
    collisions = {number: sorted(keys) for number, keys in by_number.items() if len(keys) > 1}

    by_hash: dict[str, list[str]] = {}
    for record in records:
        by_hash.setdefault(record["sha256"], []).append(record["key"])
    duplicates = {digest: sorted(keys) for digest, keys in by_hash.items() if len(keys) > 1}

    register_summary = []
    for register in registers:
        register_key = register.relative_to(root).as_posix()
        owned = [r for r in records if r["register"] == register_key]
        register_summary.append({
            "register": register_key,
            "count": len(owned),
            "conventions": sorted({r["convention"] for r in owned}),
            "numbers": sorted({r["number"] for r in owned}, key=lambda n: int(n)),
            "has_readme": (register / "README.md").is_file(),
        })

    return {
        "contract_version": CONTRACT_VERSION,
        "root": root.as_posix(),
        "register_count": len(registers),
        "record_count": len(records),
        # No wall clock: this document must be byte-stable for an unchanged tree so
        # --check means "content drifted", not "time passed".
        "corpus_digest": "sha256:" + hashlib.sha256(
            "\n".join(sorted(f"{r['key']}:{r['sha256']}" for r in records)).encode()
        ).hexdigest(),
        "collision_count": len(collisions),
        "encoding_damaged_count": sum(1 for r in records if r["encoding_damaged"]),
        "registers": register_summary,
        "collisions": collisions,
        "identical_content": duplicates,
        "records": sorted(records, key=lambda r: (r["register"], int(r["number"]))),
    }


def render_markdown(index: dict) -> str:
    lines = [
        "# ADR Index",
        "",
        f"**{index['record_count']} decision records across {index['register_count']} registers.** "
        "Generated by `python -m tools.adr_index` — do not hand-edit.",
        "",
        "Cite records as `<register>#<number>`. A bare number is **not** a resolvable "
        f"reference: {index['collision_count']} numbers are used by more than one register.",
        "",
        "## Registers",
        "",
        "| Register | Records | Conventions | README |",
        "|---|---|---|---|",
    ]
    for entry in index["registers"]:
        lines.append(
            f"| `{entry['register']}` | {entry['count']} | {', '.join(entry['conventions'])} | "
            f"{'yes' if entry['has_readme'] else '**none**'} |"
        )

    if index["collisions"]:
        lines += ["", "## Colliding numbers", "",
                  "Each of these numbers names more than one decision. Always cite the full key.",
                  ""]
        for number, keys in sorted(index["collisions"].items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- **{number}** — {len(keys)} records: " + ", ".join(f"`{k}`" for k in keys))

    if index["identical_content"]:
        lines += ["", "## Byte-identical duplicates", "",
                  "Same content in more than one register — stale copies, not independent decisions.",
                  ""]
        for _, keys in sorted(index["identical_content"].items()):
            lines.append("- " + ", ".join(f"`{k}`" for k in keys))

    lines += ["", "## Records", "", "| Key | Title | Status | Path |", "|---|---|---|---|"]
    for record in index["records"]:
        title = (record["title"] or record["slug"]).replace("|", "\\|")
        status = (record["status"] or "—").replace("|", "\\|")[:40]
        lines.append(f"| `{record['key']}` | {title} | {status} | `{record['path']}` |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.adr_index")
    parser.add_argument("--root", type=Path, default=WORK_ROOT)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if the written index differs from the current tree")
    args = parser.parse_args(argv)

    index = build_index(args.root)
    payload = json.dumps(index, indent=2) + "\n"
    markdown = render_markdown(index)

    if args.check:
        stale = []
        if not args.out_json.is_file() or args.out_json.read_text(encoding="utf-8") != payload:
            stale.append(str(args.out_json))
        if not args.out_md.is_file() or args.out_md.read_text(encoding="utf-8") != markdown:
            stale.append(str(args.out_md))
        if stale:
            print("adr-index: STALE — regenerate with `python -m tools.adr_index`")
            for path in stale:
                print(f"  {path}")
            return 1
        print(f"adr-index: current ({index['record_count']} records, "
              f"{index['register_count']} registers)")
        return 0

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(payload, encoding="utf-8")
    args.out_md.write_text(markdown, encoding="utf-8")
    print(f"adr-index: {index['record_count']} records, {index['register_count']} registers, "
          f"{index['collision_count']} colliding numbers")
    print(f"  {args.out_json}")
    print(f"  {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
