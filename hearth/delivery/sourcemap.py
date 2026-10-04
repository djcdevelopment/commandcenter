"""Source map: pinned file bytes at a commit with line numbers and a symbol index.

The model never counts lines; the renderer resolves quotes to ``path:start-end``.
Stdlib only (``ast`` for Python, a regex fallback for other text and for Python that
does not parse); rapidfuzz is used for fuzzy scoring only if importable. No network,
no shell: git runs as an argv list.

Public API
----------
``build(repo, commit, paths) -> SourceMap``
    ``git show <commit>:<path>`` per path. Raises ``SourceNotFound`` (names path, commit,
    repo), ``SourceTooLarge`` (a file over 256 KiB) or ``PacketTooLarge`` (files over
    1 MiB in total): the door's ``files=`` limits.
``render_for_model(sm, numbered=True) -> str``
    Deterministic, byte-identical across runs (no timestamps, no run-dependent values) so
    the 27B's prefix cache can reuse it. Raises ``PacketTooLarge`` past 1 MiB rendered.
``locate(sm, quote, threshold=FUZZY_THRESHOLD, hint=None) -> Location``
    ``Location`` carries ``path, start, end, match, occurrences, truncated, candidate``.
    Use named fields or ``loc.as_range()`` for ``(path, start, end, match)``.
    ``loc.ref`` is ``"path:start-end"`` (``"path:N"`` for one line; ``""`` when missing).

Match vocabulary (including historical manifests and candidate matches)
-------------------------------------
``exact``          the quote is a substring of the file text (CRLF folded to LF). Not a repair.
``normalized``     equal after collapsing whitespace runs to one space and folding typographic
                   quotes (U+201C/U+201D to ``"``, U+2018/U+2019 to ``'``). A repair.
``fuzzy:<score>``  candidate similarity (0..1, two decimals), not a resolved match.
                   An elided quote (``...`` or U+2026) is searched only as elided, see below.
``missing``        no exact/normalized evidence; optional candidate stores an unresolved suggestion.
                   ``path == ""``, ``start == end == 0``; ``occurrences`` is N > 1 only
                   for a short ambiguous quote (below).
Normalized matches are repairs. Fuzzy/elided suggestions now return missing with candidate metadata;
their source range is never resolved evidence, regardless of score.

Fuzzy rules (a wrong number is a wrong claim)
- Quotes shorter than ``SHORT_QUOTE_CHARS`` (24, after whitespace normalization) get exact
  or normalized only; no fuzzy. ``SKIP_DAYS = 14`` against ``SKIP_DAYS = 7`` is ``missing``.
- At any length, if the numbers in the quote are not all present in the best window, the
  result is ``missing`` (the best window is not re-chosen to find one that has the number).
- Likewise if an identifier of the quote (3+ letters, digits or underscores with an underscore, a digit or an
  inner capital: `reply_text`, `HTTPStatus`, `utf8`; plain words are English) is not a whole token of the best
  window: ``reply_text`` against a line that says ``res``.
- Likewise if the quote swaps a word: it has a word (3+ characters, case folded) that the window lacks while the
  part of the window it aligns with has a word the quote lacks (``result`` for ``res``). A dropped or an added
  plain word (``Exposes:`` before a docstring line), quote characters and ``true``/``True`` stay fuzzy.

Line-prefix quotes (``truncated_quote`` repair)
- A quote of at least ``TRUNCATED_QUOTE_CHARS`` (12) that the short floor refused as ambiguous, that is the
  beginning of a source line (indentation ignored) and that stops where a string literal of that line begins
  (the next character is ``"`` or ``'``: the 27B cannot write the double quote and stops) resolves to that
  whole line when exactly one line in all sources begins so and every hit of the quote is on it, or exactly
  one inside the symbol the hint selects over the hits (the ``_pick`` rule) and no other hit lies in that
  symbol. A hint that names two blocks equally (``from configuration.day to configuration.tool-night``)
  selects none. Match ``normalized`` with ``Location.truncated = True``; ``occurrences`` is 1. A prefix
  that stops anywhere else (``except (OSError,``) is no evidence in code, where many lines begin alike.

Elided quotes (a quote that is not an exact hit and contains ``...`` or U+2026)
- Split at each ellipsis into segments (normalized as above; empty segments dropped). The
  first segment matches by its longest leading part, middle segments in full and in order,
  the last by its longest trailing part, all as one window inside the smallest symbol that
  holds the first hit (no symbol: up to the next symbol's start). Score = matched characters
  over the non-elided characters; retained as candidate ``fuzzy:<score>`` (claim remains ``missing``, even at 1.00)
  and only at or above the threshold. A quote whose non-elided text is under
  ``SHORT_QUOTE_CHARS`` must match every segment in full. The number rule applies to the
  window. An elided quote never falls through to line-window fuzzy: no window is ``missing``.

Short ambiguous quotes
- A quote with ``quote_chars(q) < SHORT_QUOTE_CHARS`` (whitespace collapsed, HTML entities decoded)
  that occurs more than once, by any pass (an elided one: more than one full window), is ``missing``
  with ``occurrences = N``: a single word read as ``exact`` support for a claim it cannot show. Once is
  still a hit. The contract checks claims with the same ``quote_chars``.

Ambiguity
- ``occurrences`` counts every exact (or, failing that, normalized) hit across the map,
  overlapping hits included. ``fuzzy`` reports 1, ``missing`` 0 (N for a short ambiguous quote).
- With ``occurrences > 1`` and ``hint`` (the claim text around the quote), see ``_pick``: the hit
  inside the smallest symbol named in the hint wins; otherwise the first hit in map order. The
  renderer flags ``occurrences > 1`` either way.

    python -m hearth.delivery.sourcemap <repo> <commit> <path>
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import html
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import NamedTuple, Optional

try:  # optional, never required
    from rapidfuzz import fuzz as _rf_fuzz
except Exception:  # pragma: no cover
    _rf_fuzz = None

FUZZY_THRESHOLD = 0.85
SHORT_QUOTE_CHARS = 24
TRUNCATED_QUOTE_CHARS = 12  # a quote this long that starts one source line may stand for the whole line
# Mirrors hearth/toolsurface/inference.py FILES_PER_FILE_CAP / FILES_TOTAL_CAP (not imported: stdlib only).
FILE_CAP_BYTES = 256 * 1024
PACKET_CAP_BYTES = 1024 * 1024


class SourceMapError(ValueError):
    """Base for named source-map failures."""


class SourceNotFound(SourceMapError):
    """The path (or the commit) does not exist in the repo."""


class SourceTooLarge(SourceMapError):
    """One file exceeds FILE_CAP_BYTES."""


class PacketTooLarge(SourceMapError):
    """The files, or the rendered packet, exceed PACKET_CAP_BYTES."""


@dataclass
class FileMap:
    path: str
    sha256: str                      # of the raw bytes at the commit
    lines: list                      # decoded, CRLF folded to LF; numbering matches git
    symbols: list = field(default_factory=list)  # [{name, kind, start, end}]
    symbols_from: str = "regex"      # "ast" | "regex" (regex when not Python or ast failed)
    notes: list = field(default_factory=list)    # e.g. "non-utf8 bytes replaced", "crlf"


@dataclass
class SourceMap:
    repo: str
    commit: str                      # full sha, resolved once
    files: list = field(default_factory=list)  # [FileMap]

    def get(self, path: str) -> Optional[FileMap]:
        return next((f for f in self.files if f.path == path), None)


class Location(NamedTuple):
    path: str
    start: int
    end: int
    match: str          # exact | normalized | fuzzy:<score> | missing
    occurrences: int    # exact/normalized hits across the map; fuzzy 1; missing 0
    truncated: bool = False  # normalized: the quote is the beginning of the cited line (a repair of its own)

    candidate: Optional[dict] = None  # unresolved suggestion; never a resolved range

    def as_range(self) -> tuple:
        return (self.path, self.start, self.end, self.match)

    @property
    def ref(self) -> str:
        if not self.path:
            return ""
        return f"{self.path}:{self.start}" if self.start == self.end else f"{self.path}:{self.start}-{self.end}"

    @property
    def repaired(self) -> bool:
        return self.match == "normalized" or self.match.startswith("fuzzy:")


_MISSING = Location("", 0, 0, "missing", 0)


def _candidate(sm: SourceMap, loc: Location, reason: str) -> Location:
    if loc.match == "missing":
        return loc
    fm = sm.get(loc.path)
    return Location("", 0, 0, "missing", 0, candidate={
        "path": loc.path, "start_line": loc.start, "end_line": loc.end,
        "source_text": "\n".join(fm.lines[loc.start - 1:loc.end]),
        "match": loc.match, "reason": reason})


def _short_ambiguous(n: int) -> Location:
    """A short quote found more than once is no evidence of any one place: missing, with the hit count."""
    return Location("", 0, 0, "missing", n)


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=120)


def _resolve_commit(repo: str, commit: str) -> str:
    p = _git(repo, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    if p.returncode:
        raise SourceNotFound(f"commit {commit!r} not found in {repo}")
    return p.stdout.decode().strip()


def _git_show(repo: str, commit: str, path: str) -> bytes:
    p = _git(repo, "show", f"{commit}:{path}")
    if p.returncode:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise SourceNotFound(f"path {path!r} not found at commit {commit[:12]} in {repo}: {err}")
    return p.stdout


def _split_lines(text: str) -> list:
    # Split on "\n" only so numbering matches git and editors (str.splitlines also splits \x0c etc.).
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _python_symbols(text: str) -> list:
    tree = ast.parse(text)
    out = []

    def start_of(node):
        decos = [d.lineno for d in getattr(node, "decorator_list", [])]
        return min([node.lineno] + decos)

    def walk(body, prefix, in_class):
        for node in body:
            if isinstance(node, ast.ClassDef):
                name = prefix + node.name
                out.append({"name": name, "kind": "class", "start": start_of(node), "end": node.end_lineno})
                walk(node.body, name + ".", True)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append({"name": prefix + node.name, "kind": "method" if in_class else "function",
                            "start": start_of(node), "end": node.end_lineno})
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        out.append({"name": prefix + t.id, "kind": "const" if t.id.isupper() else "variable",
                                    "start": node.lineno, "end": node.end_lineno})
    walk(tree.body, "", False)
    return sorted(out, key=lambda s: (s["start"], -s["end"], s["name"]))


_FALLBACK = [
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"), "function", False),
    (re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"), "class", False),
    (re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"), "function", False),
    (re.compile(r"^\[\[?([^\]]+)\]\]?\s*$"), "section", True),   # toml/ini tables
    (re.compile(r"^(#{1,6})\s+(.+?)\s*$"), "heading", True),     # markdown
]


def _fallback_symbols(lines: list, path: str = "") -> list:
    """Line/regex symbols for non-Python text; section-like symbols run to the next one."""
    out, last_section = [], None
    for i, line in enumerate(lines, 1):
        for rx, kind, is_section in _FALLBACK:
            if kind == "heading" and not path.endswith((".md", ".markdown")):
                continue  # "# x" is a comment in toml/py/sh, not a heading
            m = rx.match(line)
            if not m:
                continue
            name = m.group(m.lastindex if kind != "heading" else 2).strip()
            if is_section and last_section is not None:
                last_section["end"] = i - 1
            sym = {"name": name, "kind": kind, "start": i, "end": i}
            out.append(sym)
            if is_section:
                last_section = sym
            break
    if last_section is not None:
        last_section["end"] = len(lines)
    return out


def file_map(path: str, data: bytes) -> FileMap:
    """Map one file's raw bytes (exposed so scratch inputs can be mapped without git)."""
    if len(data) > FILE_CAP_BYTES:
        raise SourceTooLarge(f"file {path} size {len(data)} exceeds cap {FILE_CAP_BYTES}")
    notes = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", "replace")
        notes.append("non-utf8 bytes replaced (U+FFFD)")
    if "\r\n" in text:
        notes.append("crlf folded to lf")
    lines = _split_lines(text)
    symbols, source = None, "regex"
    if path.endswith(".py"):
        try:
            symbols, source = _python_symbols("\n".join(lines)), "ast"
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            notes.append("python did not parse; regex symbols")
    if symbols is None:
        symbols = _fallback_symbols(lines, path)
    return FileMap(path=path, sha256=hashlib.sha256(data).hexdigest(), lines=lines,
                   symbols=symbols, symbols_from=source, notes=notes)


def build(repo: str, commit: str, paths: list) -> SourceMap:
    full = _resolve_commit(repo, commit)
    sm = SourceMap(repo=str(repo), commit=full)
    total = 0
    for path in paths:
        data = _git_show(repo, full, path)
        total += len(data)
        if total > PACKET_CAP_BYTES:
            raise PacketTooLarge(f"files total size {total} exceeds total cap {PACKET_CAP_BYTES} at {path}")
        sm.files.append(file_map(path, data))
    return sm


def symbol_index(fm: FileMap) -> list:
    out = []
    for s in fm.symbols:
        span = str(s["start"]) if s["start"] == s["end"] else f"{s['start']}-{s['end']}"
        out.append(f"{s['name']} [{s['kind']}] {span}")
    return out


def render_for_model(sm: SourceMap, numbered: bool = True, symbols: bool = True) -> str:
    """Deterministic text: no timestamps or run-dependent values, so prefixes cache byte-for-byte.
    symbols=False drops the index: a model asked for exact quotes copies index rows as if they were
    source (2026-10-03, work_7a5acea4: 3 of 11 quotes)."""
    parts = []
    for fm in sm.files:
        note = f", {'; '.join(fm.notes)}" if fm.notes else ""
        parts.append(f"=== FILE {fm.path} (sha256 {fm.sha256[:12]}, {len(fm.lines)} lines{note}) ===")
        if symbols:
            parts.append("SYMBOLS (name [kind] line-range):")
            parts.extend("  " + s for s in symbol_index(fm))
        parts.append("CODE:")
        width = max(3, len(str(len(fm.lines))))
        for i, line in enumerate(fm.lines, 1):
            parts.append(f"{i:0{width}d}| {line}" if numbered else line)
        parts.append(f"=== END {fm.path} ===")
    out = "\n".join(parts) + "\n"
    size = len(out.encode("utf-8"))
    if size > PACKET_CAP_BYTES:
        raise PacketTooLarge(f"rendered packet {size} bytes exceeds cap {PACKET_CAP_BYTES}")
    return out


# Typographic quotes a model substitutes for ASCII ones; folded on both sides, so a source
# that really contains them still matches. One char to one char: offsets stay aligned.
_QUOTE_FOLD = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})
_QUOTE_DROP = str.maketrans("", "", "\"'")
_ELLIPSIS = re.compile(r"\.\.\.|\u2026")


def _norm_with_map(text: str):
    """Collapse whitespace runs to one space, fold typographic quotes, and strip; map each
    normalized char to its source offset."""
    chars, idx, prev_space = [], [], True
    for i, c in enumerate(text.translate(_QUOTE_FOLD)):
        if c.isspace():
            if not prev_space:
                chars.append(" "); idx.append(i)
            prev_space = True
        else:
            chars.append(c); idx.append(i); prev_space = False
    while chars and chars[-1] == " ":
        chars.pop(); idx.pop()
    return "".join(chars), idx


def _norm(s: str) -> str:
    return _norm_with_map(s)[0]


def quote_chars(quote: str) -> int:
    """Length the SHORT_QUOTE_CHARS floor applies to (locate and contract.validate_manifest share it)."""
    return len(_norm(html.unescape(quote)))


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _find_all(hay: str, needle: str) -> list:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


_NUM = re.compile(r"\d+(?:\.\d+)?")


def _numbers_ok(nquote: str, window: str) -> bool:
    return not (Counter(_NUM.findall(nquote)) - Counter(_NUM.findall(window)))


_IDENT = re.compile(r"[A-Za-z0-9_]*[A-Za-z_][A-Za-z0-9_]*")
_INNER_CAP = re.compile(r"(?<=[A-Za-z0-9])[A-Z]")


def _is_identifier(t: str) -> bool:
    """Identifier-shaped: has an underscore, a digit or a capital after the first character. A plain word
    (`text`, `Exposes`) is English until shown otherwise: a fuzzy quote may still change a plain word and
    the score prices it."""
    return len(t) >= 3 and (any(c in t for c in "_0123456789") or bool(_INNER_CAP.search(t)))


def _identifiers_ok(nquote: str, window: str) -> bool:
    """Every identifier of the quote must be a whole token of the matched window: `reply_text` is not `res`
    (2026-10-03, work_8168fd9f, fuzzy:0.91 to the line that says `"text": res`)."""
    have = set(_IDENT.findall(window))
    return all(t in have for t in set(_IDENT.findall(nquote)) if _is_identifier(t))


def _words_ok(nquote: str, window: str) -> bool:
    """No swapped word: a word of the quote absent from the window and, in the aligned part of the window, a word
    absent from the quote (`result` for `res`). Words of 3+ characters, case folded (the 8B writes `true`)."""
    blocks = [b for b in difflib.SequenceMatcher(None, window, nquote, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        return False
    lo, hi = blocks[0].a, blocks[-1].a + blocks[-1].size
    words = lambda s: {t.lower() for t in _IDENT.findall(s) if len(t) >= 3}  # noqa: E731
    span = {m.group().lower() for m in _IDENT.finditer(window) if lo <= m.start() and m.end() <= hi and len(m.group()) >= 3}
    return not (words(nquote) - words(window) and span - words(nquote))


def _score(window: str, nquote: str) -> float:
    if _rf_fuzz is not None:
        return _rf_fuzz.partial_ratio(nquote, window) / 100.0
    # difflib ratio over the aligned span of the window, so characters dropped from the quote
    # cost as much as characters added (a pure containment ratio scores a deletion 1.00).
    blocks = [b for b in difflib.SequenceMatcher(None, window, nquote, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        return 0.0
    matched = sum(b.size for b in blocks)
    span = blocks[-1].a + blocks[-1].size - blocks[0].a
    return 2.0 * matched / (len(nquote) + span)


def _pick_best(cands: list, hint: Optional[str]):
    """(index, symbol, tied) of the candidate the hint selects, or None when no named symbol selects one;
    tied: another named symbol as rare holds a candidate on another line (only size or order chose between them).
    cands: [(FileMap, start, end)]. Prefer a hit inside a symbol named in the hint.
    A symbol is named when any dotted prefix or dotted part of its name is a whole word of the hint
    (`configuration.day.backends` matches "configuration.day" and "day"). A name is worth less the more
    candidates it names: one that names every candidate (`configuration`) tells nothing and is ignored, even
    when another candidate sits outside every symbol. Rank: rarest name, then smallest symbol, then the
    first hit."""
    if len(cands) > 1 and hint:
        def named(sym):
            parts = sym["name"].split(".")
            names = {".".join(parts[:k]) for k in range(1, len(parts) + 1)} | set(parts)
            return {n for n in names if len(n) > 1 and re.search(r"(?<![\w])" + re.escape(n) + r"(?!\w)", hint)}
        held = []  # per candidate: [(symbol, matched names)]
        for fm, s, e in cands:
            held.append([(sym, named(sym)) for sym in fm.symbols if sym["start"] <= s and e <= sym["end"]])
        seen = Counter(n for h in held for n in set().union(*(ns for _, ns in h)))  # candidates per name
        best, ranks = None, []
        for i, h in enumerate(held):
            for sym, names in h:
                rare = min((seen[n] for n in names), default=len(cands))
                if rare < len(cands):
                    key = (rare, sym["end"] - sym["start"], i, sym)
                    ranks.append(key)
                    if best is None or key[:3] < best[:3]:
                        best = key
        if best is not None:
            at = cands[best[2]][:2]
            tied = any(k[0] == best[0] and k[3] is not best[3] and cands[k[2]][:2] != at for k in ranks)
            return best[2], best[3], tied
    return None


def _nearest(cands: list, near: Optional[list]) -> int:
    """Index of the hit nearest the anchors, or 0 (the first hit) with no anchors or no hit in an anchor's file.
    Rank: inside the smallest symbol that holds an anchor, then the smallest line distance to any anchor, then the
    earlier hit. near: [(path, line)]."""
    def smallest(fm, line):
        inside = [y for y in fm.symbols if y["start"] <= line <= y["end"]]
        return min(inside, key=lambda y: y["end"] - y["start"])["name"] if inside else None
    best, at = None, 0
    for i, (fm, s, e) in enumerate(cands):
        here = smallest(fm, s)
        for path, line in near or ():
            if path == fm.path:
                key = (here is None or here != smallest(fm, line), 0 if s <= line <= e else min(abs(s - line), abs(e - line)))
                if best is None or key < best:
                    best, at = key, i
    return at


def _pick(cands: list, hint: Optional[str], near: Optional[list] = None):
    """The hit the hint selects (see _pick_best); no named symbol: the hit nearest the anchors (``near``), else the first."""
    best = _pick_best(cands, hint)
    return cands[best[0]] if best else cands[_nearest(cands, near)]


def _prefix_line(texts: list, q: str, chars: int, hint: Optional[str], hits: list,
                 near: Optional[list] = None) -> Optional[Location]:
    """A quote of >= TRUNCATED_QUOTE_CHARS that begins a source line (indentation ignored) and stops where a string
    literal of it begins resolves to that whole line when it is the only place the quote can mean: no other hit of
    the quote (``hits``: the exact/normalized cands the short floor refused) lies elsewhere, or none elsewhere
    inside the symbol the hint selects over those hits. The 27B stops a quote at the first double quote of a TOML
    line (2026-10-03 sizing runs: `am4-vllm = { status = `, 22 rejections)."""
    nq = _norm(q)
    if min(len(nq), chars) < TRUNCATED_QUOTE_CHARS:  # chars: quote_chars of the quote as written, the contract's measure
        return None
    cut = lambda n: n.startswith(nq) and n[len(nq):].lstrip()[:1] in ('"', "'")  # noqa: E731
    pc = [(fm, i, i) for fm, _ in texts for i, line in enumerate(fm.lines, 1) if cut(_norm(line))]
    if not pc:
        return None
    scope = lambda c: True  # noqa: E731
    best = _pick_best(hits, hint)
    if best is not None:
        if best[2]:  # the paragraph names several blocks alike: no one line is the one it means
            return None
        fm0, sym = hits[best[0]][0], best[1]
        scope = lambda c: c[0] is fm0 and sym["start"] <= c[1] and c[2] <= sym["end"]  # noqa: E731
    elif near and len(hits) > 1:  # no symbol named: the hit nearest the anchors, and the smallest symbol holding it
        fm0, h0s, h0e = hits[_nearest(hits, near)]
        inside = [y for y in fm0.symbols if y["start"] <= h0s and h0e <= y["end"]]
        if inside and any(p == fm0.path for p, _ in near):
            sym = min(inside, key=lambda y: y["end"] - y["start"])
            scope = lambda c: c[0] is fm0 and sym["start"] <= c[1] and c[2] <= sym["end"]  # noqa: E731
    inside = [c for c in pc if scope(c)]
    if len(inside) != 1 or any(h[1:] != (inside[0][1],) * 2 for h in hits if scope(h)):
        return None
    fm, line, _ = inside[0]
    return Location(fm.path, line, line, "normalized", 1, True)


def locate_line_reference(sm: SourceMap, reference: str) -> Location:
    """Resolve an exact path:N within this pinned map; never repair or search a bad reference."""
    parsed = re.fullmatch(r"([^:\s]+):([1-9][0-9]*)", reference)
    if not parsed:
        return _MISSING
    path, number = parsed.groups()
    if path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/")):
        return _MISSING
    fm = sm.get(path)
    line = int(number)
    if fm is None or not 1 <= line <= len(fm.lines):
        return _MISSING
    return Location(path, line, line, "exact", 1)


def locate(sm: SourceMap, quote: str, threshold: float = FUZZY_THRESHOLD,
           hint: Optional[str] = None, near: Optional[list] = None) -> Location:
    """Resolve a quote to a Location(path, start, end, match, occurrences). near: [(path, line)] anchors; where no
    symbol named in the hint decides between several hits, the hit nearest an anchor in the same file wins.

    match is exact | normalized | missing; fuzzy/elided matches are unresolved
    candidates, retained for a judge. Normalized matches are counted repairs. See the module docstring for the short-quote, number and hint rules.
    """
    q = quote.replace("\r\n", "\n").strip("\n")
    if not q.strip():
        return _MISSING
    texts = [(fm, "\n".join(fm.lines)) for fm in sm.files]

    cands = []
    for fm, text in texts:
        for pos in _find_all(text, q):
            cands.append((fm, _line_of(text, pos), _line_of(text, pos + len(q) - 1)))
    chars = quote_chars(q)
    short = chars < SHORT_QUOTE_CHARS
    if cands:
        if short and len(cands) > 1:
            return _prefix_line(texts, q, chars, hint, cands, near) or _short_ambiguous(len(cands))
        fm, s, e = _pick(cands, hint, near)
        return Location(fm.path, s, e, "exact", len(cands))

    # Under a JSON schema the 27B writes &quot; &gt; &apos; for the characters themselves (2026-10-03,
    # work_a59bad05: 3 of 5 quotes). Unescaping is a normalization, counted like the quote fold.
    q = html.unescape(q)
    nq = _norm(q)
    for fm, text in texts:
        ntext, idx = _norm_with_map(text)
        for pos in _find_all(ntext, nq):
            cands.append((fm, _line_of(text, idx[pos]), _line_of(text, idx[pos + len(nq) - 1])))
    if cands:
        if short and len(cands) > 1:
            return _prefix_line(texts, q, chars, hint, cands, near) or _short_ambiguous(len(cands))
        fm, s, e = _pick(cands, hint, near)
        return Location(fm.path, s, e, "normalized", len(cands))

    # Neither model writes a literal double quote inside a JSON string: the 8B swaps it for ' or drops
    # it (2026-10-03, work_d8f8c68f: 11 of 18 quotes). Compare with quote characters removed on both sides.
    sq = nq.translate(_QUOTE_DROP)
    if len(sq) >= SHORT_QUOTE_CHARS:
        for fm, text in texts:
            ntext, idx = _norm_with_map(text)
            keep = [i for i, c in enumerate(ntext) if c not in "\"'"]
            stext = "".join(ntext[i] for i in keep)
            for pos in _find_all(stext, sq):
                cands.append((fm, _line_of(text, idx[keep[pos]]), _line_of(text, idx[keep[pos + len(sq) - 1]])))
        if cands:
            fm, s, e = _pick(cands, hint, near)
            return Location(fm.path, s, e, "normalized", len(cands))

    if _ELLIPSIS.search(q):
        return _candidate(sm, _locate_elided(texts, q, threshold), "elided_quote")
    if len(nq) < SHORT_QUOTE_CHARS:
        return _MISSING  # short quotes: one changed character is a different claim
    n = max(1, len(q.split("\n")))
    best = (0.0, None, 0, 0, "")
    for fm, _ in texts:
        total = len(fm.lines)
        nlines = [_norm(l) for l in fm.lines]
        for size in sorted({n, n + 1}):
            for s in range(0, max(1, total - size + 1)):
                window = " ".join(nlines[s:s + size]).strip()
                if not window:
                    continue
                sc = _score(window, nq)
                if sc > best[0]:
                    best = (sc, fm.path, s + 1, min(total, s + size), window)
    if best[1] is not None and best[0] >= threshold and _numbers_ok(nq, best[4]) and _identifiers_ok(nq, best[4]) \
            and _words_ok(nq, best[4]):
        return _candidate(sm, Location(best[1], best[2], best[3], f"fuzzy:{best[0]:.2f}", 1), "fuzzy_quote")
    return _MISSING


def _symbol_bound(fm: FileMap, line: int) -> int:
    """Last line of the smallest symbol holding ``line``; outside every symbol, the line
    before the next symbol starts (or the end of the file)."""
    inside = [sym for sym in fm.symbols if sym["start"] <= line <= sym["end"]]
    if inside:
        return min(inside, key=lambda sym: sym["end"] - sym["start"])["end"]
    later = [sym["start"] for sym in fm.symbols if sym["start"] > line]
    return (min(later) - 1) if later else len(fm.lines)


def _locate_elided(texts: list, q: str, threshold: float) -> Location:
    """Match a quote with an ellipsis as one window; see the module docstring."""
    raw = _ELLIPSIS.split(q)
    segs = [_norm(part) for part in raw]
    segs = [seg for seg in segs if seg]
    if not segs:
        return _MISSING
    total = sum(len(seg) for seg in segs)
    full_only = total < SHORT_QUOTE_CHARS
    # Which end of a lone segment is anchored: text before a trailing ellipsis keeps its
    # start; text after a leading ellipsis keeps its end.
    lone_tail = len(segs) == 1 and not _norm(raw[0])
    best = None   # (score, -order, path, start, end)
    order = 0
    for fm, text in texts:
        ntext, idx = _norm_with_map(text)
        if not ntext:
            continue

        def first_at(needle: str, lo: int, hi_line: int):
            pos = ntext.find(needle, lo)
            if pos < 0 or _line_of(text, idx[pos + len(needle) - 1]) > hi_line:
                return None
            return pos

        head = segs[0]
        if lone_tail:
            # One segment after a leading ellipsis: its longest trailing part, anywhere.
            k = len(head)
            while k > 0 and ntext.find(head[-k:]) < 0:
                k -= 1
            if k == 0 or (full_only and k < len(head)):
                continue
            for pos in _find_all(ntext, head[-k:]):
                end = pos + k
                window = ntext[pos:end]
                score = k / total
                if score >= threshold and _numbers_ok(" ".join(segs), window):
                    cand = (score, -order, fm.path, _line_of(text, idx[pos]), _line_of(text, idx[end - 1]))
                    order += 1
                    if best is None or cand[:2] > best[:2]:
                        best = cand
            continue
        k0 = len(head)
        while k0 > 0 and ntext.find(head[:k0]) < 0:
            k0 -= 1
        if k0 == 0 or (full_only and k0 < len(head)):
            continue
        for pos0 in _find_all(ntext, head[:k0]):
            first_line = _line_of(text, idx[pos0])
            bound = _symbol_bound(fm, first_line)
            cursor, matched, ok = pos0 + k0, k0, True
            for mid in segs[1:-1]:
                at = first_at(mid, cursor, bound)
                if at is None:
                    ok = False
                    break
                cursor, matched = at + len(mid), matched + len(mid)
            if not ok:
                continue
            if len(segs) > 1:
                tail = segs[-1]
                k, at = len(tail), None
                while k > 0:
                    at = first_at(tail[-k:], cursor, bound)
                    if at is not None:
                        break
                    k -= 1
                if at is None or (full_only and k < len(tail)):
                    continue
                cursor, matched = at + k, matched + k
            window = ntext[pos0:cursor]
            score = matched / total
            if score < threshold or not _numbers_ok(" ".join(segs), window):
                continue
            cand = (score, -order, fm.path, first_line, _line_of(text, idx[cursor - 1]))
            order += 1
            if best is None or cand[:2] > best[:2]:
                best = cand
    if best is None:
        return _MISSING
    if order > 1 and quote_chars(q) < SHORT_QUOTE_CHARS:  # every window is a full match here (full_only)
        return _short_ambiguous(order)
    return Location(best[2], best[3], best[4], f"fuzzy:{best[0]:.2f}", 1)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print("usage: python -m hearth.delivery.sourcemap <repo> <commit> <path>", file=sys.stderr)
        return 2
    sm = build(argv[0], argv[1], [argv[2]])
    fm = sm.files[0]
    print(f"{fm.path} @ {sm.commit[:12]} sha256 {fm.sha256[:12]} {len(fm.lines)} lines")
    print("\n".join(symbol_index(fm)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
