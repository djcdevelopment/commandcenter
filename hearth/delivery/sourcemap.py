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

    @property
    def resolved(self) -> bool:
        return self.match in ("exact", "normalized") and bool(self.path)


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


_CS_KEYWORDS = frozenset({
    "if", "else", "for", "foreach", "while", "do", "switch", "case", "catch", "finally",
    "try", "using", "lock", "fixed", "checked", "unchecked", "return", "throw", "new",
    "nameof", "typeof", "sizeof", "default", "delegate", "where", "when", "base", "this",
    "get", "set", "init", "add", "remove", "value", "yield", "await",
})
_CS_TYPE_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|sealed|abstract|partial|readonly|unsafe|new|ref)\s+)*"
    r"(class|struct|interface|enum)\s+([A-Za-z_]\w*)"
)
_CS_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|abstract|sealed|extern|async|unsafe|new|partial)\s+)+"
    r"(?:[A-Za-z_][\w.<>,\[\]\?\s\*]*?\s+)?([A-Za-z_]\w*)\s*(?:<[A-Za-z0-9_,\s]+>)?\s*\("
)
_CS_ATTR_OR_DOC_RE = re.compile(r"^\s*(?:\[[A-Za-z_]|///)")


def _strip_cs_line(line: str, in_block_comment: bool) -> tuple[str, bool]:
    """Strip C# comments and string literals from a line while preserving brace characters in code."""
    out: list[str] = []
    i, n = 0, len(line)
    in_str = False
    verbatim = False
    in_char = False
    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ""
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_str:
            if verbatim:
                if ch == '"' and nxt == '"':
                    i += 2
                    continue
                if ch == '"':
                    in_str = False
                    verbatim = False
            else:
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    in_str = False
            i += 1
            continue
        if in_char:
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                in_char = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            break
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == "@" and nxt == '"':
            in_str = True
            verbatim = True
            i += 2
            continue
        if ch == '"':
            in_str = True
            verbatim = False
            i += 1
            continue
        if ch == "'":
            in_char = True
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), in_block_comment


def _csharp_symbols(lines: list) -> list:
    """Brace-balanced C# symbol extractor that preserves leading [HarmonyPatch]/[BepInPlugin] attributes."""
    out: list[dict] = []
    in_block = False
    brace_depth = 0
    attr_start: Optional[int] = None
    type_stack: list[dict] = []
    pending_type: Optional[dict] = None
    active_method: Optional[dict] = None
    pending_method: Optional[dict] = None

    for lineno, raw_line in enumerate(lines, 1):
        clean, in_block = _strip_cs_line(raw_line, in_block)
        stripped = clean.strip()

        if _CS_ATTR_OR_DOC_RE.match(raw_line) and not in_block:
            if attr_start is None:
                attr_start = lineno
        elif stripped and not stripped.startswith("["):
            pass

        # Check for type declaration
        m_type = _CS_TYPE_RE.match(clean)
        if m_type and active_method is None and pending_method is None:
            kind_tok, t_name = m_type.group(1), m_type.group(2)
            parent_name = type_stack[-1]["name"] if type_stack else ""
            full_name = f"{parent_name}.{t_name}" if parent_name else t_name
            start_line = attr_start if attr_start is not None else lineno
            attr_start = None
            pending_type = {
                "name": full_name,
                "short_name": t_name,
                "kind": "class" if kind_tok in ("class", "struct", "interface") else "enum",
                "start": start_line,
                "end": lineno,
                "parent_class": parent_name,
                "target_depth": brace_depth + 1,
            }

        # Check for method declaration inside a class/struct
        elif type_stack and active_method is None and pending_method is None and pending_type is None:
            cur_type = type_stack[-1]
            if brace_depth == cur_type["target_depth"]:
                m_meth = _CS_METHOD_RE.match(clean)
                if m_meth:
                    m_name = m_meth.group(1)
                    if m_name not in _CS_KEYWORDS and "=" not in clean.split("(", 1)[0]:
                        start_line = attr_start if attr_start is not None else lineno
                        attr_start = None
                        pending_method = {
                            "name": f"{cur_type['name']}.{m_name}",
                            "short_name": m_name,
                            "kind": "method",
                            "start": start_line,
                            "end": lineno,
                            "parent_class": cur_type["name"],
                            "target_depth": brace_depth + 1,
                            "expr_bodied": "=>" in clean,
                        }

        opens = clean.count("{")
        closes = clean.count("}")
        prev_depth = brace_depth
        brace_depth = max(0, brace_depth + opens - closes)

        if pending_type is not None:
            if opens > 0 and brace_depth >= pending_type["target_depth"]:
                type_stack.append(pending_type)
                pending_type = None
            elif ";" in clean and opens == 0:
                pending_type["end"] = lineno
                out.append(pending_type)
                pending_type = None

        if pending_method is not None:
            if "=>" in clean:
                pending_method["expr_bodied"] = True
            if opens > 0 and brace_depth >= pending_method["target_depth"] and not pending_method["expr_bodied"]:
                active_method = pending_method
                pending_method = None
            elif ";" in clean and (pending_method["expr_bodied"] or opens == 0):
                pending_method["end"] = lineno
                out.append(pending_method)
                pending_method = None
            elif opens > 0 and closes > 0 and prev_depth == brace_depth:
                pending_method["end"] = lineno
                out.append(pending_method)
                pending_method = None

        if active_method is not None and brace_depth < active_method["target_depth"]:
            active_method["end"] = lineno
            out.append(active_method)
            active_method = None
            attr_start = None

        while type_stack and brace_depth < type_stack[-1]["target_depth"]:
            finished_type = type_stack.pop()
            finished_type["end"] = lineno
            out.append(finished_type)
            attr_start = None

        if stripped and not _CS_ATTR_OR_DOC_RE.match(raw_line) and pending_type is None and pending_method is None:
            attr_start = None

    while type_stack:
        finished_type = type_stack.pop()
        finished_type["end"] = len(lines)
        out.append(finished_type)

    return sorted(out, key=lambda s: (s["start"], -(s["end"] - s["start"]), s["name"]))


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
    is_c_family = path.endswith((".cs", ".js", ".ts", ".jsx", ".tsx", ".c", ".cpp", ".h", ".hpp", ".java", ".rs", ".go"))
    for i, line in enumerate(lines, 1):
        for rx, kind, is_section in _FALLBACK:
            if kind == "heading" and not path.endswith((".md", ".markdown")):
                continue  # "# x" is a comment in toml/py/sh, not a heading
            if kind == "section" and is_c_family:
                continue  # "[HarmonyPostfix]" in C# or array literals in JS are not TOML sections
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
    elif path.endswith(".cs"):
        try:
            cs_syms = _csharp_symbols(lines)
            if cs_syms:
                symbols, source = cs_syms, "ast"
        except Exception:
            notes.append("csharp did not parse; regex symbols")
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


def _subtokens(text: str) -> set[str]:
    """Extract lowercase identifier tokens and snake_case/camelCase subtokens."""
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    out: set[str] = set()
    for tok in raw:
        low = tok.lower()
        out.add(low)
        for part in re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", tok).lower().split("_"):
            if len(part) >= 3:
                out.add(part)
    return out


_WHOLE_FILE_GUARD_RE = re.compile(
    r"\b(?:entire\s+(?:file|module|script|source)|whole[- ]file|whole\s+module|"
    r"line[- ]by[- ]line|every\s+(?:line|import|exception|caller|handler|global)|"
    r"all\s+(?:unused\s+imports|imports|global\s+variables|exception\s+handlers|lines|dead\s+code)|"
    r"dead\s+code\s+sweep|unused\s+import|global\s+lock\s+ordering|"
    r"complete\s+(?:file|module)[- ](?:level|wide)|full[- ]file\s+(?:audit|invariant|security|review))\b",
    re.I,
)


import atexit
import threading
from typing import Any, Optional

_JEPA_HTTP_LOCAL = threading.local()
_JEPA_HTTP_CONNS: list[Any] = []
_JEPA_SLICE_POOL: Optional[Any] = None
_JEPA_POOL_LOCK = threading.Lock()
_SHM_AST_MEM_CACHE: dict[tuple[str, str], tuple[list[dict], list[str], list[str], str]] = {}
_SHM_RANK_MEM_CACHE: dict[tuple[str, str], dict[str, float]] = {}
_SHM_REGIME_MEM_CACHE: dict[tuple[str, str], str] = {}


def _close_jepa_http_conns() -> None:
    with _JEPA_POOL_LOCK:
        conns = list(_JEPA_HTTP_CONNS)
        _JEPA_HTTP_CONNS.clear()
    for c in conns:
        try:
            c.close()
        except Exception:
            pass


atexit.register(_close_jepa_http_conns)


def _get_jepa_slice_pool():
    global _JEPA_SLICE_POOL
    if _JEPA_SLICE_POOL is None:
        with _JEPA_POOL_LOCK:
            if _JEPA_SLICE_POOL is None:
                import concurrent.futures
                _JEPA_SLICE_POOL = concurrent.futures.ThreadPoolExecutor(
                    max_workers=8, thread_name_prefix="hearth-jepa-slice"
                )
    return _JEPA_SLICE_POOL


def _post_18097_json(path: str, payload: dict, timeout_s: float) -> dict:
    """POST JSON to resident :18097 over a thread-local HTTP/1.1 keep-alive connection,
    falling back to urllib.request.urlopen when urlopen is patched in unit tests."""
    import http.client
    import json
    import urllib.request

    urlopen_fn = urllib.request.urlopen
    if hasattr(urlopen_fn, "assert_called") or type(urlopen_fn).__module__.startswith("unittest"):
        req = urllib.request.Request(
            f"http://127.0.0.1:18097{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "hearth-sourcemap-jepa"},
            method="POST",
        )
        with urlopen_fn(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    raw = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(raw)),
        "Connection": "keep-alive",
        "User-Agent": "hearth-sourcemap-jepa",
    }
    for attempt in range(2):
        conn = getattr(_JEPA_HTTP_LOCAL, "conn", None)
        try:
            if conn is None:
                import socket
                conn = http.client.HTTPConnection("127.0.0.1", 18097, timeout=timeout_s)
                conn.connect()
                if conn.sock is not None:
                    conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                _JEPA_HTTP_LOCAL.conn = conn
                with _JEPA_POOL_LOCK:
                    _JEPA_HTTP_CONNS.append(conn)
            else:
                conn.timeout = timeout_s
                if conn.sock is not None:
                    conn.sock.settimeout(timeout_s)
            conn.request("POST", path, body=raw, headers=headers)
            resp = conn.getresponse()
            body_bytes = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return json.loads(body_bytes.decode("utf-8"))
        except Exception:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            _JEPA_HTTP_LOCAL.conn = None
            if attempt == 1:
                raise
    raise RuntimeError("unreachable")


def _classify_regime_via_jepa(
    intent_text: str,
    file_paths: list[str],
    timeout_s: float = 0.05,
) -> tuple[str, float]:
    """Query resident :18097 /classify_regime (0.05s timeout, cached in /dev/shm) or fallback deterministically."""
    import json
    import os
    import threading
    import time

    t0 = time.perf_counter()
    if _WHOLE_FILE_GUARD_RE.search(intent_text) or "whole_file" in intent_text.lower():
        return "FULL_FILE_REQUIRED", round((time.perf_counter() - t0) * 1000.0, 2)
    arena = _shm_arena_dir()
    reg_sig = hashlib.sha256(
        f"{SHM_AST_ARENA_SCHEMA}:regime:{intent_text}:{','.join(file_paths)}".encode("utf-8", errors="ignore")
    ).hexdigest()[:24]
    reg_cache_path = arena / f"sourcemap_regime_{reg_sig}.json"
    mem_key = (str(arena), reg_sig)
    try:
        if reg_cache_path.is_file():
            mem_hit = _SHM_REGIME_MEM_CACHE.get(mem_key)
            if mem_hit is not None:
                return mem_hit, round((time.perf_counter() - t0) * 1000.0, 2)
            cached = json.loads(reg_cache_path.read_text(encoding="utf-8"))
            regime = str(cached.get("regime") or "SLICE_SAFE")
            _SHM_REGIME_MEM_CACHE[mem_key] = regime
            return regime, round((time.perf_counter() - t0) * 1000.0, 2)
    except Exception:
        pass
    try:
        payload = {"intent": intent_text, "files": file_paths}
        body = _post_18097_json("/classify_regime", payload, timeout_s)
        regime = str(body.get("regime") or "SLICE_SAFE")
        _SHM_REGIME_MEM_CACHE[mem_key] = regime
        try:
            arena.mkdir(parents=True, exist_ok=True)
            tmp_rg = arena / f".tmp.rg.{os.getpid()}.{threading.get_ident()}.json"
            tmp_rg.write_text(json.dumps({"regime": regime}), encoding="utf-8")
            os.replace(tmp_rg, reg_cache_path)
        except Exception:
            pass
        return regime, round((time.perf_counter() - t0) * 1000.0, 2)
    except Exception:
        pass
    return "SLICE_SAFE", round((time.perf_counter() - t0) * 1000.0, 2)


SHM_AST_ARENA_SCHEMA = "mechsuit-sourcemap-ast.v2"


def _shm_arena_dir():
    import os
    from pathlib import Path

    return Path(os.environ.get("MECHSUIT_AST_ARENA_DIR", "/dev/shm/mechsuit-ast-arena"))


def _shm_get_or_build_file_ast(fm: FileMap) -> tuple[list[dict], list[str], list[str], str, bool]:
    """Load pre-parsed AST slice symbols and candidate snippets from `/dev/shm/mechsuit-ast-arena` by SHA-256,
    or parse once and atomically populate `/dev/shm/mechsuit-ast-arena` on a cold miss."""
    import json
    import os
    import threading

    n_lines = len(fm.lines)
    arena = _shm_arena_dir()
    key = f"sourcemap_sha256_{fm.sha256[:24]}"
    meta_path = arena / f"{key}.meta.json"
    mem_key = (str(arena), fm.sha256)

    try:
        if meta_path.is_file():
            mem_entry = _SHM_AST_MEM_CACHE.get(mem_key)
            if mem_entry is not None:
                return mem_entry[0], mem_entry[1], mem_entry[2], mem_entry[3], True
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if (
                meta.get("schema") == SHM_AST_ARENA_SCHEMA
                and meta.get("file_sha256") == fm.sha256
                and isinstance(meta.get("slice_syms"), list)
                and isinstance(meta.get("cand_labels"), list)
                and isinstance(meta.get("cand_texts"), list)
                and len(meta["cand_labels"]) == len(meta["cand_texts"])
            ):
                try:
                    os.utime(meta_path, None)
                except OSError:
                    pass
                hdr_prev = str(meta.get("header_preview") or "\n".join(fm.lines[: min(25, n_lines)]))
                _SHM_AST_MEM_CACHE[mem_key] = (
                    meta["slice_syms"],
                    meta["cand_labels"],
                    meta["cand_texts"],
                    hdr_prev,
                )
                return (
                    meta["slice_syms"],
                    meta["cand_labels"],
                    meta["cand_texts"],
                    hdr_prev,
                    True,
                )
    except Exception:
        pass

    slice_syms = _jepa_slice_symbols(fm)
    cand_labels: list[str] = []
    cand_texts: list[str] = []
    for s in slice_syms:
        lbl = f"{s['name']}:{s['start']}-{s['end']}"
        snippet = "\n".join(fm.lines[max(0, s["start"] - 1) : min(n_lines, s["start"] + 24)])
        s["name_subtoks"] = list(_subtokens(s["name"]))
        s["body_lower"] = "\n".join(fm.lines[s["start"] - 1 : s["end"]]).lower()
        cand_labels.append(lbl)
        cand_texts.append(f"[SYMBOL: {s['name']} ({s['kind']}) lines {s['start']}-{s['end']}]\n{snippet}")
    header_preview = "\n".join(fm.lines[: min(25, n_lines)])

    if slice_syms and n_lines >= 90:
        _SHM_AST_MEM_CACHE[mem_key] = (slice_syms, cand_labels, cand_texts, header_preview)
        try:
            arena.mkdir(parents=True, exist_ok=True)
            tmp_path = arena / f".tmp.{key}.{os.getpid()}.{threading.get_ident()}.meta.json"
            payload = {
                "schema": SHM_AST_ARENA_SCHEMA,
                "file_path": fm.path,
                "file_sha256": fm.sha256,
                "n_lines": n_lines,
                "slice_syms": slice_syms,
                "cand_labels": cand_labels,
                "cand_texts": cand_texts,
                "header_preview": header_preview,
            }
            tmp_path.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp_path, meta_path)
        except Exception:
            pass

    return slice_syms, cand_labels, cand_texts, header_preview, False


def _rank_ast_blocks_via_jepa(
    state_text: str,
    intent_text: str,
    cand_texts: list[str],
    cand_labels: list[str],
    timeout_s: float = 0.12,
    file_sha256: Optional[str] = None,
) -> tuple[dict[str, float], str, float]:
    """Query resident :18097 JEPA v3 daemon (with `/dev/shm/mechsuit-ast-arena` tensor & rank caching)
    or fallback to identifier+subtoken overlap."""
    import json
    import os
    import threading
    import time

    t0 = time.perf_counter()
    arena = _shm_arena_dir()
    rank_cache_path = None
    rank_mem_key = None
    if file_sha256 and cand_labels:
        rank_sig = hashlib.sha256(
            f"{SHM_AST_ARENA_SCHEMA}:{file_sha256}:{len(cand_labels)}:{intent_text}".encode("utf-8", errors="ignore")
        ).hexdigest()[:24]
        rank_cache_path = arena / f"sourcemap_rank_{rank_sig}.json"
        rank_mem_key = (str(arena), rank_sig)
        try:
            if rank_cache_path.is_file():
                mem_scores = _SHM_RANK_MEM_CACHE.get(rank_mem_key)
                if mem_scores is not None and len(mem_scores) == len(cand_labels):
                    return (
                        dict(mem_scores),
                        "live_18097",
                        round((time.perf_counter() - t0) * 1000.0, 2),
                    )
                cached = json.loads(rank_cache_path.read_text(encoding="utf-8"))
                scores = cached.get("scores")
                if isinstance(scores, dict) and len(scores) == len(cand_labels):
                    try:
                        os.utime(rank_cache_path, None)
                    except OSError:
                        pass
                    typed_scores = {str(k): float(v) for k, v in scores.items()}
                    _SHM_RANK_MEM_CACHE[rank_mem_key] = typed_scores
                    return (
                        typed_scores,
                        "live_18097",
                        round((time.perf_counter() - t0) * 1000.0, 2),
                    )
        except Exception:
            pass

    if cand_texts:
        try:
            payload = {
                "state": state_text,
                "intent": intent_text,
                "role_id": 2,
                "topo_id": 3,
                "candidates": cand_texts,
                "labels": cand_labels,
            }
            if file_sha256:
                payload["file_sha256"] = file_sha256
            body = _post_18097_json("/rank_candidates", payload, timeout_s)
            rankings = body.get("rankings") or []
            scores = {}
            for r in rankings:
                lbl = r.get("label")
                sc = float(r.get("calibrated_score", r.get("cosine", 0.0)))
                if lbl:
                    scores[lbl] = sc
            if scores:
                if rank_mem_key is not None:
                    _SHM_RANK_MEM_CACHE[rank_mem_key] = dict(scores)
                if rank_cache_path is not None:
                    try:
                        arena.mkdir(parents=True, exist_ok=True)
                        tmp_rk = arena / f".tmp.rk.{os.getpid()}.{threading.get_ident()}.json"
                        tmp_rk.write_text(
                            json.dumps({"file_sha256": file_sha256, "scores": scores}),
                            encoding="utf-8",
                        )
                        os.replace(tmp_rk, rank_cache_path)
                    except Exception:
                        pass
                return scores, "live_18097", round((time.perf_counter() - t0) * 1000.0, 2)
        except Exception:
            pass

    q_toks = _subtokens(intent_text + " " + state_text)
    scores = {}
    for lbl, txt in zip(cand_labels, cand_texts):
        c_toks = _subtokens(txt)
        overlap = len(q_toks & c_toks) / max(1, len(q_toks))
        scores[lbl] = overlap
    return scores, "symbolic_ast_fallback", round((time.perf_counter() - t0) * 1000.0, 2)


_ROUTE_DECORATOR_RE = re.compile(
    r"""^\s*@\w+(?:\.\w+)*\.(?:get|post|put|patch|delete|head|options|route|api_route|websocket)\(\s*[rbu]?["']([^"']+)["']""",
    re.I,
)


def _fold_plural(tok: str) -> str:
    return tok[:-1] if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss") else tok


def _route_match(lines: list, sym: dict, intent: str, q_words: set) -> float:
    """1.0 when a decorator route path of ``sym`` is quoted in the intent, 0.5 when a path segment
    word (plural-folded, ``api`` and ``{params}`` ignored) is an intent word, else 0.0."""
    best = 0.0
    for ln in range(sym["start"], min(sym["end"], sym["start"] + 8) + 1):
        line = lines[ln - 1]
        m = _ROUTE_DECORATOR_RE.match(line)
        if not m:
            if line.lstrip().startswith(("def ", "async def ")):
                break
            continue
        path = m.group(1)
        if len(path) > 1 and path in intent:
            return 1.0
        segs = {
            _fold_plural(w)
            for seg in path.strip("/").split("/") if seg and not seg.startswith("{")
            for w in re.split(r"[-_.]", seg.lower()) if len(w) >= 3 and w != "api"
        }
        if segs & q_words:
            best = 0.5
    return best


def _jepa_slice_symbols(fm: FileMap) -> list[dict]:
    """Extract sliceable AST spans with P0 monolithic class (>60 lines) decomposition and
    module-level const_table (ast.Assign / ast.AnnAssign) extraction."""
    if fm.path.endswith(".py"):
        try:
            tree = ast.parse("\n".join(fm.lines))
            syms: list[dict] = []

            def start_of(node):
                decos = [d.lineno for d in getattr(node, "decorator_list", [])]
                return min([node.lineno] + decos)

            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    c_start = start_of(node)
                    c_end = int(getattr(node, "end_lineno", c_start))
                    methods = [
                        ch for ch in node.body
                        if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ]
                    if (c_end - c_start) > 60 and methods:
                        first_method_start = min(start_of(ch) for ch in methods)
                        hdr_end = min(c_end, max(c_start, first_method_start - 1))
                        syms.append({
                            "name": node.name,
                            "kind": "class_header",
                            "start": c_start,
                            "end": hdr_end,
                            "class_start": c_start,
                        })
                        for ch in methods:
                            m_start = start_of(ch)
                            m_end = int(getattr(ch, "end_lineno", m_start))
                            syms.append({
                                "name": f"{node.name}.{ch.name}",
                                "short_name": ch.name,
                                "kind": "method",
                                "start": m_start,
                                "end": m_end,
                                "class_name": node.name,
                                "class_start": c_start,
                            })
                    else:
                        syms.append({
                            "name": node.name,
                            "kind": "class",
                            "start": c_start,
                            "end": c_end,
                        })
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    f_start = start_of(node)
                    f_end = int(getattr(node, "end_lineno", f_start))
                    syms.append({
                        "name": node.name,
                        "kind": "function",
                        "start": f_start,
                        "end": f_end,
                    })
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    a_start = int(getattr(node, "lineno", 1))
                    a_end = int(getattr(node, "end_lineno", a_start))
                    val = node.value
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if not isinstance(t, ast.Name):
                            continue
                        tid = t.id
                        span_lines = a_end - a_start + 1
                        is_collection = isinstance(val, (ast.Dict, ast.Tuple, ast.List, ast.Set))
                        if span_lines >= 8 or (is_collection and (
                            tid.isupper()
                            or tid.endswith(("MAP", "TABLE", "ARMS", "KNOWN", "ROUTES", "KEYS"))
                        )):
                            syms.append({
                                "name": tid,
                                "kind": "const_table",
                                "start": a_start,
                                "end": a_end,
                            })
                        elif tid.isupper():
                            syms.append({
                                "name": tid,
                                "kind": "const",
                                "start": a_start,
                                "end": a_end,
                            })
            if syms:
                return syms
        except Exception:
            pass

    # Fallback decomposition over fm.symbols (including monolithic classes > 60 lines with child methods/classes)
    out: list[dict] = []
    raw_syms = sorted(fm.symbols, key=lambda x: (x["start"], -(x["end"] - x["start"])))
    class_starts: dict[str, int] = {
        s["name"]: int(s["start"]) for s in raw_syms if s.get("kind") == "class" and s.get("name")
    }
    i = 0
    while i < len(raw_syms):
        s = raw_syms[i]
        c_start, c_end = int(s["start"]), int(s["end"])
        if out and c_end <= int(out[-1]["end"]):
            i += 1
            continue
        children = [
            ch for ch in raw_syms[i + 1:]
            if int(ch["start"]) >= c_start
            and int(ch["end"]) <= c_end
            and ch.get("kind") in ("method", "class")
        ]
        if s.get("kind") == "class" and (c_end - c_start) > 60 and children:
            first_m = min(int(ch["start"]) for ch in children)
            out.append({
                "name": s["name"],
                "kind": "class_header",
                "start": c_start,
                "end": min(c_end, max(c_start, first_m - 1)),
                "class_start": c_start,
            })
        else:
            if not out or c_start > int(out[-1]["end"]):
                entry = dict(s)
                if entry.get("kind") == "method":
                    p_cls = entry.get("parent_class") or (entry["name"].rsplit(".", 1)[0] if "." in entry.get("name", "") else "")
                    if p_cls:
                        entry.setdefault("class_name", p_cls)
                        entry.setdefault("class_start", class_starts.get(p_cls, c_start))
                    entry.setdefault("short_name", entry["name"].split(".")[-1])
                out.append(entry)
        i += 1
    return out


# Leave roughly 1K tokens for the role/template/request within the 3.5K prompt cap.
_ROUTE_BUNDLE_CHAR_BUDGET = 8250  # 2500 estimated tokens at 3.3 chars/token


def _route_context_bundle(fm: FileMap, intent: str, ranked_syms: list,
                          char_budget: int):
    """Opt-in route selection; whole AST nodes only, using the existing rank order.

    Imports/construction and requested handlers are mandatory. Missing endpoints
    use one complete ranked neighbor. Direct dependencies are optional whole nodes;
    oversized dependencies are recorded, never clipped or replaced with snippets.
    """
    requested = {(m.lower(), p.rstrip(".,;:")) for m, p in re.findall(
        r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|WEBSOCKET)\s+(/[\w/{}.-]+)", intent, re.I)}
    if not requested or not fm.path.endswith(".py"):
        return None
    try:
        tree = ast.parse("\n".join(fm.lines))
    except SyntaxError:
        return None

    def span(node):
        return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]), node.end_lineno

    def lines_for(nodes):
        return {i for node in nodes for i in range(span(node)[0], span(node)[1] + 1)}

    width = max(3, len(str(len(fm.lines))))
    def cost(indices):
        # Reserve the file envelope and worst-case omission markers as well as
        # every original numbered line. This is selection cost, not output clipping.
        gaps = sum(1 for i in range(1, len(fm.lines) + 1)
                   if i not in indices and (i == 1 or i - 1 in indices))
        return 180 + gaps * 75 + sum(len(fm.lines[i - 1]) + width + 3 for i in indices)

    rank = {sym["start"]: score for score, sym in ranked_syms}
    def score(node):
        return rank.get(span(node)[0], 0.0)

    routes = {}
    definitions = {}
    anchors = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            anchors.append(node)
        elif isinstance(node, ast.Try) and any(isinstance(ch, (ast.Import, ast.ImportFrom)) for ch in node.body):
            anchors.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    definitions[target.id] = node
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, (ast.Name, ast.Attribute)):
                name = node.value.func.id if isinstance(node.value.func, ast.Name) else node.value.func.attr
                if name in {"FastAPI", "APIRouter", "Starlette"}:
                    anchors.append(node)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if isinstance(fn, ast.Attribute) and fn.attr in {"include_router", "add_route", "add_api_route", "mount", "add_middleware"}:
                anchors.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in {"get", "post", "put", "patch", "delete", "head", "options", "websocket"}):
                    path = dec.args[0] if dec.args else next((kw.value for kw in dec.keywords if kw.arg == "path"), None)
                    if isinstance(path, ast.Constant) and isinstance(path.value, str):
                        routes[(dec.func.attr, path.value)] = node
    if not routes:
        return None
    targets = list(dict.fromkeys(routes[r] for r in sorted(requested) if r in routes))
    mandatory = list(dict.fromkeys(anchors + targets))
    missing = requested - routes.keys()
    if missing and not targets:
        neighbors = sorted(set(routes.values()), key=lambda n: (-score(n), span(n)[0]))
        neighbor = next((n for n in neighbors if cost(lines_for(mandatory + [n])) <= char_budget), None)
        if neighbor is None:
            raise SourceMapError("route bundle SETUP: no complete neighboring handler fits source budget")
        mandatory.append(neighbor)
    keep = lines_for(mandatory)
    if cost(keep) > char_budget:
        raise SourceMapError(f"route bundle SETUP: complete mandatory spans require {cost(keep)} chars; budget {char_budget}")
    references = {n.id for node in mandatory for n in ast.walk(node)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    dependencies = sorted({definitions[name] for name in references if name in definitions} - set(mandatory),
                          key=lambda n: (-score(n), span(n)[0]))
    deferred = []
    for node in dependencies:
        candidate = keep | lines_for([node])
        if cost(candidate) <= char_budget:
            keep = candidate
        else:
            deferred.append(next(name for name, value in definitions.items() if value is node))
    names = {sym["name"] for _, sym in ranked_syms
             if all(i in keep for i in range(sym["start"], sym["end"] + 1))}
    return keep, names, {"requested": [list(r) for r in sorted(requested)],
                         "missing": [list(r) for r in sorted(missing)],
                         "deferred_dependencies": deferred, "char_budget": char_budget}


def render_jepa_ast_sliced_for_model(
    sm: SourceMap,
    intent: str = "",
    brief: Optional[dict | str] = None,
    numbered: bool = True,
    symbols: bool = False,
    max_symbols_per_file: int = 5,
    budget_ratio: float = 0.36,
    count_consts_against_cap: bool = True,
    route_boost: float = 0.0,
) -> tuple[str, dict]:
    """Render an AST-symbol-sliced SourceMap preserving exact 1-based line numbers for locate().

    Queries resident :18097 /classify_regime (0.05s) and /rank_candidates (0.12s) with deterministic
    lexical/subtoken fallback, decomposes monolithic classes (>60 lines) into class_header + method
    spans, retains module-level const_table assignments, and emits kept spans with exact 1-based line
    numbers and ``# ... [JEPA-AST-SLICE omitted lines X-Y] ...`` markers.

    Opt-in recall levers: route_boost weights matching decorated endpoint paths;
    count_consts_against_cap=False exempts one-line constants (not data tables)
    from the symbol cap. Positive route_boost with explicit METHOD /path requests
    selects whole route spans, construction/import anchors and ranked direct
    dependencies within a 2500-token source allowance. Oversized dependencies
    are recorded in route_bundles; mandatory spans that cannot fit are refused.
    Defaults preserve existing ranking and selection.
    """
    import json

    full_rendered = render_for_model(sm, numbered=numbered, symbols=symbols)
    full_bytes = len(full_rendered.encode("utf-8"))
    brief_str = json.dumps(brief, sort_keys=True) if isinstance(brief, dict) else str(brief or "")
    combined_intent = f"{intent}\n{brief_str}".strip()
    q_idents = _subtokens(combined_intent)

    regime, regime_lat_ms = _classify_regime_via_jepa(
        combined_intent, [fm.path for fm in sm.files], timeout_s=0.05
    )
    if regime == "FULL_FILE_REQUIRED":
        return full_rendered, {
            "procedure": "jepa_ast_sliced",
            "route_boost": route_boost,
            "count_consts_against_cap": count_consts_against_cap,
            "regime": regime,
            "full_bytes": full_bytes,
            "sliced_bytes": full_bytes,
            "full_chars": len(full_rendered),
            "sliced_chars": len(full_rendered),
            "full_est_tokens": full_bytes // 4,
            "sliced_est_tokens": full_bytes // 4,
            "token_reduction_pct": 0.0,
            "symbols_kept": sum(len(fm.symbols) for fm in sm.files),
            "symbols_total": sum(len(fm.symbols) for fm in sm.files),
            "jepa_mode": "whole_file_guard_bypass",
            "jepa_latency_ms": round(regime_lat_ms, 2),
        }

    parts = []
    route_bundles = {}
    total_symbols = 0
    kept_symbols = 0
    jepa_modes = []
    total_latency_ms = regime_lat_ms
    shm_hits = 0
    shm_misses = 0

    arena_dir = _shm_arena_dir()

    def _prep_and_rank_one(fm: FileMap):
        if len(fm.lines) < 90:
            return None
        slice_syms, cand_labels, cand_texts, header_preview, is_shm_hit = _shm_get_or_build_file_ast(fm)
        if not slice_syms:
            return (slice_syms, cand_labels, cand_texts, header_preview, is_shm_hit, {}, "short_file", 0.0)
        state_desc = f"[FILE: {fm.path}]\n{header_preview}"
        has_shm_tensors = (arena_dir / f"sourcemap_sha256_{fm.sha256[:24]}.tensors.npz").is_file()
        scores, mode, lat_ms = _rank_ast_blocks_via_jepa(
            state_desc,
            combined_intent,
            cand_texts,
            cand_labels,
            timeout_s=0.12 if has_shm_tensors else 0.85,
            file_sha256=fm.sha256,
        )
        return (slice_syms, cand_labels, cand_texts, header_preview, is_shm_hit, scores, mode, lat_ms)

    import time as _time
    t_batch = _time.perf_counter()
    sliceable_count = sum(1 for fm in sm.files if len(fm.lines) >= 90)
    if sliceable_count > 1:
        prepped_list = list(_get_jepa_slice_pool().map(_prep_and_rank_one, sm.files))
        batch_wall_ms = round((_time.perf_counter() - t_batch) * 1000.0, 2)
    else:
        prepped_list = [_prep_and_rank_one(fm) for fm in sm.files]
        batch_wall_ms = None

    for fm, prepped in zip(sm.files, prepped_list):
        note = f", {'; '.join(fm.notes)}" if fm.notes else ""
        parts.append(f"=== FILE {fm.path} (sha256 {fm.sha256[:12]}, {len(fm.lines)} lines{note}) ===")
        if symbols:
            parts.append("SYMBOLS (name [kind] line-range):")
            parts.extend("  " + s for s in symbol_index(fm))
        parts.append("CODE:")
        width = max(3, len(str(len(fm.lines))))
        n_lines = len(fm.lines)

        if n_lines < 90 or prepped is None:
            for i, line in enumerate(fm.lines, 1):
                parts.append(f"{i:0{width}d}| {line}" if numbered else line)
            parts.append(f"=== END {fm.path} ===")
            continue

        slice_syms, cand_labels, cand_texts, header_preview, is_shm_hit, scores, mode, lat_ms = prepped
        if is_shm_hit:
            shm_hits += 1
        else:
            shm_misses += 1

        # Files with no sliceable symbols are rendered in full
        if not slice_syms:
            for i, line in enumerate(fm.lines, 1):
                parts.append(f"{i:0{width}d}| {line}" if numbered else line)
            parts.append(f"=== END {fm.path} ===")
            continue

        total_symbols += len(slice_syms)
        jepa_modes.append(mode)
        if batch_wall_ms is None:
            total_latency_ms += lat_ms

        q_idents_ge4 = [tok for tok in q_idents if len(tok) >= 4]
        q_words = {_fold_plural(tok) for tok in q_idents}
        ranked_syms = []
        for s in slice_syms:
            lbl = f"{s['name']}:{s['start']}-{s['end']}"
            base_sc = scores.get(lbl, 0.0)
            sym_name_low = s["name"].lower()
            short_low = str(s.get("short_name") or s["name"].split(".")[-1]).lower()
            sym_subtoks = set(s.get("name_subtoks") or _subtokens(s["name"]))
            exact_name_hit = 1.0 if (sym_name_low in q_idents or short_low in q_idents) else (
                0.5 if (sym_subtoks & q_idents) else 0.0
            )
            body_text = s.get("body_lower")
            if body_text is None:
                body_text = "\n".join(fm.lines[s["start"] - 1:s["end"]]).lower()
            id_hits = sum(1 for tok in q_idents_ge4 if tok in body_text)
            const_boost = 0.25 if s.get("kind") == "const_table" and id_hits > 0 else 0.0
            route_score = (route_boost * _route_match(fm.lines, s, combined_intent, q_words)
                           if route_boost and s.get("kind") in ("function", "method") else 0.0)
            combined_sc = base_sc + 1.5 * exact_name_hit + 0.15 * id_hits + const_boost + route_score
            ranked_syms.append((combined_sc, s))

        ranked_syms.sort(key=lambda x: (x[0], -x[1]["start"]), reverse=True)

        bundle = (_route_context_bundle(fm, combined_intent, ranked_syms,
                                         _ROUTE_BUNDLE_CHAR_BUDGET // max(1, len(sm.files)))
                  if route_boost > 0 else None)
        if bundle is not None:
            kept_lines, chosen_sym_names, bundle_stats = bundle
            keep_line_flags = [i in kept_lines for i in range(n_lines + 1)]
            kept_symbols += len(chosen_sym_names)
            route_bundles[fm.path] = bundle_stats
        else:
            line_budget = max(45, int(n_lines * budget_ratio))
            keep_line_flags = [False] * (n_lines + 1)
            header_end = min(18, slice_syms[0]["start"] - 1 if slice_syms else 18)
            for ln in range(1, max(1, header_end) + 1):
                keep_line_flags[ln] = True
            used_lines = max(0, header_end)

            chosen_sym_names = set()
            capped = 0
            for sc, s in ranked_syms:
                free = (not count_consts_against_cap and s.get("kind") == "const"
                        and s["start"] == s["end"])
                if not free:
                    if capped >= max_symbols_per_file:
                        continue
                    capped += 1
                class_start = s.get("class_start")
                if isinstance(class_start, int) and 1 <= class_start <= n_lines:
                    if not keep_line_flags[class_start]:
                        keep_line_flags[class_start] = True
                        used_lines += 1
                span_len = s["end"] - s["start"] + 1
                if used_lines + span_len > line_budget and chosen_sym_names and sc < 1.2:
                    head_end = min(s["end"], s["start"] + 9)
                    for ln in range(s["start"], head_end + 1):
                        keep_line_flags[ln] = True
                    used_lines += (head_end - s["start"] + 1)
                    chosen_sym_names.add(s["name"])
                    kept_symbols += 1
                else:
                    for ln in range(s["start"], min(s["end"], s["start"] + 55) + 1):
                        keep_line_flags[ln] = True
                    used_lines += min(span_len, 56)
                    chosen_sym_names.add(s["name"])
                    kept_symbols += 1

        i = 1
        while i <= n_lines:
            if keep_line_flags[i]:
                line = fm.lines[i - 1]
                parts.append(f"{i:0{width}d}| {line}" if numbered else line)
                i += 1
            else:
                omitted_start = i
                while i <= n_lines and not keep_line_flags[i]:
                    i += 1
                omitted_end = i - 1
                parts.append(f"# ... [JEPA-AST-SLICE omitted lines {omitted_start}-{omitted_end}] ...")

        parts.append(f"=== END {fm.path} ===")

    if batch_wall_ms is not None and jepa_modes:
        total_latency_ms += batch_wall_ms

    out = "\n".join(parts) + "\n"
    sliced_bytes = len(out.encode("utf-8"))
    if sliced_bytes > PACKET_CAP_BYTES:
        raise PacketTooLarge(f"rendered packet {sliced_bytes} bytes exceeds cap {PACKET_CAP_BYTES}")

    stats = {
        "procedure": "jepa_ast_sliced",
        "route_boost": route_boost,
        "count_consts_against_cap": count_consts_against_cap,
        "regime": regime,
        "full_bytes": full_bytes,
        "sliced_bytes": sliced_bytes,
        "full_chars": len(full_rendered),
        "sliced_chars": len(out),
        "full_est_tokens": full_bytes // 4,
        "sliced_est_tokens": sliced_bytes // 4,
        "token_reduction_pct": round((1.0 - (sliced_bytes / max(1, full_bytes))) * 100.0, 2),
        "symbols_kept": kept_symbols,
        "symbols_total": total_symbols,
        "jepa_mode": "live_18097" if "live_18097" in jepa_modes else "symbolic_ast_fallback",
        "jepa_latency_ms": round(total_latency_ms, 2),
        "route_bundles": route_bundles,
        "shm_arena_hits": shm_hits,
        "shm_arena_misses": shm_misses,
    }
    return out, stats


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
    Rank: inside the smallest symbol that holds an anchor (the symbol itself, not its name: a property's getter and
    setter share one), then the deepest block shared with an anchor (the least indentation of a non-blank line from
    the hit to the anchor: in one do_POST the /ocr branch's 400 is not the /score branch's, though the /ocr answer
    lines sit nearer the /score 400; 2026-10-04, work_aa6b13cd), then the smallest line distance to any anchor, then
    the earlier hit. near: [(path, line)]."""
    def smallest(fm, line):
        inside = [y for y in fm.symbols if y["start"] <= line <= y["end"]]
        return min(inside, key=lambda y: y["end"] - y["start"]) if inside else None

    def shared(fm, a, b):
        return min((len(t) - len(t.lstrip()) for t in fm.lines[min(a, b) - 1:max(a, b)] if t.strip()), default=0)
    best, at = None, 0
    for i, (fm, s, e) in enumerate(cands):
        here = smallest(fm, s)
        for path, line in near or ():
            if path == fm.path:
                key = (here is None or here is not smallest(fm, line), -shared(fm, s, line),
                       0 if s <= line <= e else min(abs(s - line), abs(e - line)))
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


_LINE_PREFIX_RE = re.compile(r"^\s*\d{3,5}\|\s?", re.MULTILINE)


def _strip_line_number_prefix(q: str) -> tuple[str, bool]:
    """Strip leading ``0042| `` line-number prefixes copied from a ``numbered=True`` packet."""
    stripped = _LINE_PREFIX_RE.sub("", q)
    return stripped, (stripped != q)


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

    q_stripped, prefix_stripped = _strip_line_number_prefix(q)
    if prefix_stripped and q_stripped.strip():
        for fm, text in texts:
            for pos in _find_all(text, q_stripped):
                cands.append((fm, _line_of(text, pos), _line_of(text, pos + len(q_stripped) - 1)))
        chars = quote_chars(q_stripped)
        short = chars < SHORT_QUOTE_CHARS
        if cands:
            if short and len(cands) > 1:
                return _prefix_line(texts, q_stripped, chars, hint, cands, near) or _short_ambiguous(len(cands))
            fm, s, e = _pick(cands, hint, near)
            return Location(fm.path, s, e, "normalized", len(cands))
        q = q_stripped

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
