"""Carried-draft delivery helpers (lap 17, package B1): pure functions, no I/O, no model calls. Stdlib only.

A model writes a working draft in plain prose; code splits it into blocks and keeps the text; a second pass only attaches
exact source quotes to each block; `assemble` builds the delivery-output.v1 document the renderer resolves. No line number
the model wrote reaches the output: `strip_line_references` removes them and lists what it removed.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .contract import MAX_HEADING_CHARS, MAX_PARAGRAPHS, MAX_QUOTE_CHARS, MAX_QUOTES, MAX_SECTIONS, MAX_TEXT_CHARS

LEAD_HEADING = "Report"
# The heading of the report part of a working draft ("verified notes, then the report"); also a heading when it is a line
# of its own inside a paragraph block ("...last note.\nReport:\nThe audit...").
_REPORT = r"(?:(?:final|draft|corrected|revised|written|audit|the)\s+)?report(?:\s*\([^()\n]*\))?"
_REPORT_HEADING = re.compile(_REPORT + r"\s*(?:[:–—-](?:(?!.*\bnotes?\b)[^\n]*)?)?", re.I)
_REPORT_LINE = re.compile(r"(?:\*\*|__)?" + _REPORT + r"\s*:?\s*(?:\*\*|__)?:?", re.I)


class CarryError(ValueError):
    pass


# ------------------------------------------------------------------ split
def _blocks_of(draft: str) -> list:
    """(kind, raw) in order, kinds heading | item | paragraph | table | code; raws join to the draft."""
    lines = draft.splitlines(keepends=True)
    starts, i, n = [], 0, len(lines)
    item = re.compile(r"(?:[-*+]|\d+[.)])\s")
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        prev_blank = i == 0 or not lines[i - 1].strip()
        if s.startswith(("```", "~~~")):                      # a fenced block is one block, fences included
            starts.append((i, "code")); fence = s[:3]; i += 1
            while i < n and not lines[i].strip().startswith(fence):
                i += 1
            i += 1
        elif _REPORT_LINE.fullmatch(s):
            starts.append((i, "heading")); i += 1
        elif s.startswith("#") or (prev_blank and len(s) <= 30 and not re.search(r"[.;]$", s) and not item.match(s)
                                   and not s.startswith("|") and not re.fullmatch(r"([-*_])(?:\s*\1){2,}", s)):
            starts.append((i, "heading" if s.startswith("#") else "heading?")); i += 1
        elif item.match(s):
            starts.append((i, "item")); i += 1
            while i < n and lines[i].strip() and lines[i][:1].isspace():
                i += 1                                        # indented continuation
        elif s.startswith("|"):
            starts.append((i, "table")); i += 1
            while i < n and lines[i].strip().startswith("|"):
                i += 1
        else:
            starts.append((i, "paragraph")); i += 1
            while (i < n and lines[i].strip() and not item.match(lines[i].lstrip()) and not _REPORT_LINE.fullmatch(lines[i].strip())
                   and not lines[i].lstrip().startswith(("#", "|", "```", "~~~"))):
                i += 1
    for k, (li, kind) in enumerate(starts):                   # a short line with no text under it is a sentence, not a heading
        if kind == "heading?":
            nxt = starts[k + 1][1] if k + 1 < len(starts) else None
            starts[k] = (li, "paragraph" if nxt in (None, "heading", "heading?") else "heading")
    pos = [0]
    for ln in lines:
        pos.append(pos[-1] + len(ln))
    out = []
    for k, (li, kind) in enumerate(starts):
        a = 0 if k == 0 else pos[li]                          # leading blank lines belong to the first block
        b = len(draft) if k + 1 == len(starts) else pos[starts[k + 1][0]]
        out.append((kind, draft[a:b]))
    return out


def _pieces(raw: str, kind: str, limit: int) -> list:
    """raw cut into parts whose stripped text is at most `limit`; parts join to raw. Sentence ends first (line ends too for
    a table), then a space for a sentence that is still too long."""
    if len(raw.strip()) <= limit:
        return [raw]
    bound = r"(?<=[.!?])\s+|\n+" if kind in ("table", "code") else r"(?<=[.!?])\s+"
    segs, last = [], 0
    for m in re.finditer(bound, raw):
        segs.append(raw[last:m.end()]); last = m.end()
    segs.append(raw[last:])
    flat = []
    for sg in segs:                                           # a sentence over the limit: hard-split at a space
        while len(sg.strip()) > limit:
            k = sg.rfind(" ", 0, limit)
            cut = k + 1 if k > 0 else limit
            flat.append(sg[:cut]); sg = sg[cut:]
        flat.append(sg)
    parts, cur = [], ""
    for sg in flat:
        if cur and len((cur + sg).strip()) > limit:
            parts.append(cur); cur = sg
        else:
            cur += sg
    parts.append(cur)
    return parts


def _heading_text(raw: str) -> str:
    t = raw.strip()
    t = re.sub(r"^#+\s*", "", t)
    t = re.sub(r"\s*#+$", "", t)
    t = re.sub(r"^(\*\*|__|\*|_)(.+?)\1$", r"\2", t.strip())
    return t.strip()


def split_draft(draft: str) -> list:
    blocks = []
    for kind, raw in _blocks_of(draft):
        if kind == "heading":
            blocks.append({"id": len(blocks) + 1, "kind": "heading", "raw": raw, "text": _heading_text(raw)})
            continue
        for part in _pieces(raw, kind, MAX_TEXT_CHARS):
            blocks.append({"id": len(blocks) + 1, "kind": "text", "raw": part, "text": part.strip()})
    if "".join(b["raw"] for b in blocks) != draft:
        raise CarryError("split_draft: the blocks do not join to the draft")
    return blocks


# ------------------------------------------------------------------ line references
# A number is removed only when its form says it is a line reference. A value the model states ("slots (2)", "default (8)",
# "(16384)", a year, "(1) ... (2)" enumerators, "2-3 days") stays: a false removal changes what the report says.
_NUM = r"\d+(?:\s*(?:->|[-–→])\s*L?\d+)?"
_SEP = r"\s*(?:,|;|\band\b|\bvs\.?|\bto\b|&)\s*"
_EXT = r"(?:py|md|toml|json|yaml|yml|txt|sh|cfg|ini|js|ts|rs|go|c|h|cpp|service|conf|cmd)"
_FILEW = rf"(?:TOML|toml|source|src|[\w./-]+\.{_EXT})"
_ITEM = re.compile(rf"(?:(?P<label>(?!(?:lines?|L)\b)[A-Za-z][\w./-]*)\s+)?(?P<line>(?:lines?|L)\s*)?{_NUM}(?:\s*,\s*{_NUM})*", re.I)
_PAREN = re.compile(r"\s*\(([^()\n]*\d[^()\n]*)\)")
_SLASH = r"(?:/L?\d+(?:\s*[-–]\s*L?\d+)?)*"   # "L141/143", "L232-234/251": the 27B joins alternative lines with a slash
_LREF = r"(?:[Ll]ines?\s+" + _NUM + r"(?:" + _SEP + _NUM + r")*|L\d{2,}(?:\s*[-–]\s*L?\d+)?" + _SLASH + r"|[LS]\d+\s*[-–]\s*\d+" + _SLASH + r")(?![\w-])"
_INLINE = re.compile(r"(?:\s+(?:see|at|in|on|from|per|around|near))?\s+\b" + _LREF)
_START_INLINE = re.compile(r"^(?:(?:[Ss]ee|[Aa]t|[Ii]n|[Oo]n|[Ff]rom|[Pp]er)\s+)?" + _LREF)
_FILE_NUMS = re.compile(rf"\b{_FILEW}\s+(\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*)(?![\w.-])")   # "TOML 17, 32"
_ZERO = re.compile(r"(?<![\w.:/-])0\d\d(?:\s*(?:->|[-–→/])\s*0\d\d)?(?!\w|\.\d|-\d)")   # "017/032", "009-022": zero-padded
_RANGE = re.compile(r"(?<![\w.:/=-])(\d+)\s*[-–]\s*(\d+)(?!\w|\.\d|[%-])")
_PATH = re.compile(rf"(?<![\w/.-])((?:[\w.-]+/)*[A-Za-z_][\w.-]*\.{_EXT}):(\d+(?:-\d+)?)(?![\w:])")
_PROTECT = re.compile(r"```[\s\S]*?```|`[^`\n]*`|\"[^\"\n]*\"|“[^”\n]*”")
_NO_REF_BEFORE = re.compile(r"(?:^|[:;,(]|\b(?:and|or|then|but))\s*$")   # "(1) first, (2) second": an enumerator, not a reference
_COUNT_NOUN = re.compile(r"(?:^|[_-])(?:slots?|seats?|lanes?|threads?|workers?|seqs|sequences|replicas|gpus|cards|days|hours|minutes|"
                         r"seconds|items|batch(?:es)?|quotes|paragraphs|sections|words|calls|retries|attempts|steps|rounds|requests|"
                         r"leases|blocks|briefs|runs|claims|backends|files|tokens|bytes|default|limit|port|count|total|size|len|ctx|"
                         r"k|n|t|temperature|GB|MB|KB|GiB|MiB|ms|s|%)$", re.I)
_PARAM = re.compile(r"(?:^|_)(?:tokens|slots|seqs|len|size|bytes|limit|days|ctx|port|count|seconds|threads|workers)$", re.I)
_PREP = re.compile(r"\b(?:at|in|see|per|from|on)\s+$")


def _blocked(spans: list, a: int, b: int) -> bool:
    return any(a < e and s < b for s, e in spans)


def _word_before(text: str, at: int) -> str:
    m = re.search(r"(\S+)\s*$", text[:at])
    return m.group(1).rstrip(",;:") if m else ""


def _paren_is_reference(body: str, text: str, at: int) -> bool:
    """Only line references inside: "(17)", "(663-683)", "(160, 174-180)", "(lines 16-22)", "(TOML 17, 32)", "(18 -> 33)",
    "(day line 17; tool-night line 32)", "(day 17; tool-night 32)". Not "(2)" after a count noun ("slots (2)"), an
    enumerator, a year, a number of five digits or more, or a label that is not a file ("(default 6)", "(day 17)")."""
    items = [_ITEM.fullmatch(p) for p in re.split(r"\s*(?:;|\bvs\.?|\band\b|\bto\b|&)\s*", body.strip()) if p]
    if not items or not all(items):
        return False
    labels = [m.group("label") for m in items]
    listed = len(items) > 1 and ";" in body and all(labels)
    for m, lab in zip(items, labels):
        if lab and not m.group("line") and (_COUNT_NOUN.search(lab) or not (re.fullmatch(_FILEW, lab) or listed)):
            return False
    if any(m.group("line") for m in items) or any(labels):
        return True
    nums = [int(x) for x in re.findall(r"\d+", body)]
    if max(nums) >= 10000:
        return False                                          # a sizing value: "(65536)"
    if len(nums) > 1:
        return True                                           # a list or range, even after a parameter name ("max_tokens (486-488)")
    n = nums[0]
    if 1900 <= n <= 2099 or _NO_REF_BEFORE.search(text[:at]) or _COUNT_NOUN.search(_word_before(text, at)):
        return False                                          # a year; "(1) first, (2) second"; "slots (2)", "default (8)"
    return not (n <= 9 and all(re.search(rf"\(\s*{k}\s*\)", text) for k in range(1, max(n, 2) + 1)))   # "a (1) ... b (2)"


_INT_GROUP = re.compile(r"\d+(?:\s*[-\u2013]\s*\d+)?(?:\s*[,;]\s*\d+(?:\s*[-\u2013]\s*\d+)?)*")
_CODE_NAME = re.compile(r"`?([A-Za-z_][\w.]*?)(\(\))?`?$")   # the name written right before the group: "max_tokens", "`f()`"


def _is_source_line_reference(body: str, text: str, at: int, sources: dict) -> bool:
    """An all-integer group right after a code name ("collect_backends (192)", "max_tokens (487)": an underscore, a dot or
    an inner capital, or a call "f()") is a line reference when every integer N is 10 or more, a declared file has the
    name on line N-1, N or N+1, and no declared line holding the name also holds N (then N may be its value). A prose
    word next to the group never qualifies ("retries (3)", "the drain (7)"), nor a value under 10 ("parallel_slots (2)")."""
    m = _CODE_NAME.search(text[:at].rstrip())
    if not _INT_GROUP.fullmatch(body.strip()) or not m or not (m.group(2) or re.search(r"[A-Za-z0-9]_[A-Za-z0-9]|\w\.\w|[a-z][A-Z]", m.group(1))):
        return False
    name = re.compile(rf"(?<!\w){re.escape(m.group(1).rstrip('.'))}(?!\w)")
    for n in map(int, re.findall(r"\d+", body)):
        value = re.compile(rf"(?<![\w.]){n}(?![\w.])")
        if n < 10 or any(name.search(l) and value.search(l) for lines in sources.values() for l in lines):
            return False
        if not any(n <= len(lines) and any(name.search(l) for l in lines[n - 2:n + 1]) for lines in sources.values()):
            return False
    return True


def _join(left: str, right: str) -> str:
    """Close the gap a removal left; only the seam is touched (no doubled space, no orphan punctuation)."""
    lt, rt = left.rstrip(), right.lstrip()
    if not lt.strip() or re.fullmatch(r"\s*(?:[-*+]|\d+[.)])", lt):   # the start of the text or of a list item
        rt = re.sub(r"^[,;:.]+\s*", "", rt)
        return lt + (" " if lt.strip() and rt else "") + rt
    if not rt or rt[0] in ",;:.!?)]":
        return (lt[:-1] if lt[-1] in ",;:" and rt else lt) + rt
    if lt[-1] in "([":
        return lt + rt
    gap = left[len(lt):] + right[:len(right) - len(rt)]
    return lt + ("\n" + gap.rsplit("\n", 1)[1] if "\n" in gap else " " if gap else "") + rt   # a line break in a table stays


def strip_line_references(text: str, sources: Optional[dict] = None) -> tuple:
    """`sources` (declared path -> its lines at the pinned commit) adds one form: a parenthesised group of integers that
    names a line holding an identifier of its own sentence ("collect_backends (192)")."""
    spans = [m.span() for m in _PROTECT.finditer(text)]
    cuts: list = []                                           # (start, end, reference as written)

    def cut(a: int, b: int, ref: str) -> None:
        if not _blocked(spans, a, b) and not any(x < b and a < y for x, y, _ in cuts):
            cuts.append((a, b, ref))
    for m in _PAREN.finditer(text):
        if _paren_is_reference(m.group(1), text, m.start()):
            cut(m.start(), m.end(), m.group(1).strip())
        elif sources and _is_source_line_reference(m.group(1), text, m.start(), sources):
            cut(m.start(), m.end(), m.group(1).strip())
    for m in [_START_INLINE.match(text)] + list(_INLINE.finditer(text)):
        if m:
            cut(m.start(), m.end(), re.search(r"(?:[Ll]ines?\s+|[LS])\d.*$", m.group(0).strip()).group(0))
    for m in _PATH.finditer(text):
        cut(m.start(2) - 1, m.end(2), m.group(2))
    for m in _FILE_NUMS.finditer(text):
        cut(m.start(1), m.end(1), m.group(1))
    for m in _ZERO.finditer(text):
        cut(m.start(), m.end(), m.group(0))
    for m in _RANGE.finditer(text):                           # "at 469-510", "collect_backends 191-210"; not "2-3 days"
        lo, hi = int(m.group(1)), int(m.group(2))
        after = re.match(r"\s*([A-Za-z%]+)", text[m.end():])
        if hi <= lo or hi >= 10000 or lo >= 1900 or (after and _COUNT_NOUN.search(after.group(1))):
            continue
        before, p = _word_before(text, m.start()), _PREP.search(text[:m.start()])
        if p:
            cut(p.start(), m.end(), m.group(0))
        elif re.search(r"[A-Za-z]_[A-Za-z]|\(\)$", before) and not _PARAM.search(before):
            cut(m.start(), m.end(), m.group(0))
    cuts.sort()
    pieces, last = [], 0
    for a, b, _ in cuts:
        pieces.append(text[last:a]); last = b
    pieces.append(text[last:])
    s = pieces[0]
    for p in pieces[1:]:
        s = _join(s, p)
    return (s.strip() if cuts else s), [r for _, _, r in cuts]


# ------------------------------------------------------------------ attach
def report_part(blocks: list) -> tuple:
    """(carried, notes, found): the report starts at the LAST heading that names the report ("Report", "Final report",
    "Report (draft)", "Report: <title>"; not "Report notes", "Notes for the report" or "Limits of this report"); that
    heading and everything after it is carried, everything before it is notes. No such heading: all blocks, [], False
    (the caller fails the work: the notes cannot be told from the report)."""
    at = None
    for i, b in enumerate(blocks):
        if b["kind"] == "heading" and _REPORT_HEADING.fullmatch(b["text"].strip(" #*_\t")):
            at = i
    if at is None:
        return list(blocks), [], False
    return blocks[at:], blocks[:at], True


def format_notes(notes: list) -> str:
    return "".join(b["raw"] for b in notes).strip() + "\n"


_ABBREV = re.compile(r"(?:^|[\s(\"'`.])(?:e\.g|i\.e|etc|vs|cf|approx|fig|no|dr|mr|mrs|ms|st|inc|ca|resp|incl|al|[A-Za-z])\.$", re.I)
_LISTISH = re.compile(r"(?:[-*+]|\d+[.)])\s|\||```|~~~")
_END = re.compile(r"[.!?]\s+(?=[A-Z])")


def _inside(text: str) -> list:
    """Per character: inside backticks, parentheses, brackets, double or curly quotes, or 'single quotes' (opened after a
    non-word character, closed before one: an apostrophe inside a word is no quote). An unclosed one blocks the rest."""
    mask, depth, tick, dq, cq, cs, sq = [], 0, False, False, False, False, False
    for i, ch in enumerate(text):
        if ch == "`":
            tick = not tick
        elif not tick:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(0, depth - 1)
            elif ch == '"':
                dq = not dq
            elif ch in "\u201c\u201d":
                cq = ch == "\u201c"
            elif ch == "\u2018" or (ch == "\u2019" and cs):
                cs = ch == "\u2018"
            elif ch == "'" and (not text[i + 1:i + 2].isalnum() if sq else not text[i - 1:i].isalnum()):
                sq = not sq
        mask.append(tick or depth > 0 or dq or cq or cs or sq)
    return mask


def _sentences(raw: str) -> list:
    """raw cut after a . ! ? that is followed by space and a capital; not after an abbreviation or single letter, not inside
    backticks, parentheses or quotation marks; a list item, table, code fence or one-line sentence stays whole. Parts join to raw."""
    if _LISTISH.match(raw.lstrip()):
        return [raw]
    mask, cuts = _inside(raw), []
    for m in _END.finditer(raw):
        if not mask[m.start()] and not _ABBREV.search(raw[:m.start() + 1]) and not (m.start() and mask[m.start() - 1]):
            cuts.append(m.end())
    return [raw[a:b] for a, b in zip([0] + cuts, cuts + [len(raw)])]


def units(blocks: list) -> list:
    """The attach units of the text blocks, in order: {id "<block id>.<n>", block, kind "text", raw, text}."""
    out = []
    for b in blocks:
        if b["kind"] == "text":
            out += [{"id": f"{b['id']}.{n}", "block": b["id"], "kind": "text", "raw": r, "text": r.strip()}
                    for n, r in enumerate(_sentences(b["raw"]), 1)]
    return out


def batches(blocks: list, size: int = 8) -> list:
    todo = units(blocks)
    return [todo[i:i + size] for i in range(0, len(todo), size)]


def format_blocks(batch: list) -> str:
    """The attacher sees each unit with the model's line references (attach rule 3: they guide it to the source line);
    only `assemble` strips them from the delivered text."""
    return "\n".join(f"[block {b['id']}]\n{b['text']}\n" for b in batch)


def parse_attach(answer: str, ids: list) -> tuple:
    """`ids` are unit ids ("3.2"; a block id is matched as its string and returned as given). Also counted: `quote_over_limit_dropped` (a quote over
    MAX_QUOTE_CHARS is dropped, not fatal: the answer is whole). A unit with neither a quote nor "(none)" is a cut-off answer."""
    want = {str(i): i for i in ids}
    got, cur, repairs, done = {}, None, {"json_unescaped_quote": 0, "quote_over_limit_dropped": 0}, set()
    for ln in answer.splitlines():
        m = re.match(r"^\[block (\d+(?:\.\d+)?)\]\s*$", ln.strip())
        if m:
            cur = m.group(1)
            if cur in got:
                raise CarryError(f"parse_attach: block {cur} answered twice")
            got[cur] = []
        elif ln.startswith("> ") and ln[2:].strip():
            if cur is None:
                raise CarryError("parse_attach: a quote before any [block N] line")
            q = ln[2:]
            if len(q) > 1 and q[0] == '"' and q[-1] == '"':   # a JSON string literal instead of the plain line: undone and counted
                try:
                    q = json.loads(q)
                    repairs["json_unescaped_quote"] += 1
                except ValueError:
                    pass
            done.add(cur)
            if len(q) > MAX_QUOTE_CHARS:
                repairs["quote_over_limit_dropped"] += 1
                continue
            got[cur].append(q)
        elif ln.strip() == "(none)" and cur is not None:
            done.add(cur)
        elif ln.strip():
            raise CarryError(f"parse_attach: unparsed line {ln[:60]!r}")
    if set(got) != set(want):
        raise CarryError(f"parse_attach: asked for blocks {list(want)}, answer holds {list(got)}")
    if set(got) - done:
        raise CarryError(f"parse_attach: blocks {sorted(set(got) - done)} have neither a quote nor (none): a cut-off answer")
    return {want[k]: v for k, v in got.items()}, repairs


# ------------------------------------------------------------------ assemble
def assemble(blocks: list, quotes: dict, *, statements: Optional[int] = None, notes_blocks: int = 0,
             sources: Optional[dict] = None) -> tuple:
    """`statements` is accepted for the service's call shape and unused: the summary states no count of statements.
    `notes_blocks` (blocks before the report heading, not carried) only adds a repair count. `sources`: see strip_line_references."""
    sections, refs, known = [], [], set()
    stripped = beyond = with_quotes = total = paras = by_sentence = 0
    heading, dropped = None, []
    for b in blocks:
        if b["kind"] == "heading":
            text, removed = strip_line_references(b["text"], sources)
            stripped += len(removed)
            refs += [{"block": b["id"], "reference": r} for r in removed]
            if text.strip(" #*_:-"):
                heading = text.rstrip(" :")[:MAX_HEADING_CHARS - len(" (continued 99)")]   # "Report:" is the heading "Report"
                sections.append({"heading": heading, "paragraphs": []})
            continue
        if not sections:
            heading = LEAD_HEADING
            sections.append({"heading": heading, "paragraphs": []})
        if len(sections[-1]["paragraphs"]) >= MAX_PARAGRAPHS:
            k = sum(1 for s in sections if s.get("_base") == heading) + 1
            sections.append({"heading": f"{heading} (continued)" if k == 1 else f"{heading} (continued {k})", "paragraphs": [], "_base": heading})
        parts = units([b])
        known |= {u["id"] for u in parts}
        own = [str(x) for x in quotes.get(b["id"]) or quotes.get(str(b["id"])) or []]   # a work stored before sentence units: the whole block
        merged = list(dict.fromkeys(own + [x for u in parts for x in quotes.get(u["id"]) or []]))
        groups = []                                           # a sentence with no quote of its own joins the one before it
        for u in parts:
            if quotes.get(u["id"]) or not groups:
                groups.append([u])
            else:
                groups[-1].append(u)
        if len(groups) > 1 and not quotes.get(groups[0][0]["id"]):   # leading quote-less sentences join the next
            groups[:2] = [groups[0] + groups[1]]
        split = not own and len(merged) > MAX_QUOTES and len(groups) > 1 and len(sections[-1]["paragraphs"]) + len(groups) <= MAX_PARAGRAPHS
        for g in (groups if split else [[b]]):
            key = g[0]["id"]
            text, removed = strip_line_references("".join(u["raw"] for u in g).strip() if split else b["text"], sources)
            stripped += len(removed)
            refs += [{"block": b["id"], "reference": r, **({"unit": key} if split else {})} for r in removed]
            if not re.sub(r"^(?:[-*+]|\d+[.)])(?=\s|$)|[\W_]", "", text):   # nothing left but a list mark or punctuation
                dropped.append(key)
                continue
            q = list(dict.fromkeys(x for u in g for x in quotes.get(u["id"]) or [])) if split else merged
            beyond += max(0, len(q) - MAX_QUOTES)
            q = q[:MAX_QUOTES]
            with_quotes += bool(q); total += len(q); paras += 1
            sections[-1]["paragraphs"].append({"text": text, "quotes": q}); sections[-1].setdefault("_ids", []).append(key)
        by_sentence += split
    lost = sorted(k for k in map(str, quotes) if "." in k and k not in known)   # a block key outside the report: notes, as before
    if lost:
        raise CarryError(f"assemble: quotes for sentence units the draft does not hold: {lost}")
    empty = [s for s in sections if not s["paragraphs"]]
    pid, k = {}, 0
    for s_ in sections:
        if s_["paragraphs"]:
            pid.update({bid: f"s{k}.p{j}" for j, bid in enumerate(s_["_ids"])}); k += 1
    sections = [{k: v for k, v in s.items() if not k.startswith("_")} for s in sections if s["paragraphs"]]
    if not sections:
        raise CarryError("assemble: the draft holds no paragraph")
    if len(sections) > MAX_SECTIONS:
        raise CarryError(f"assemble: {len(sections)} sections, at most {MAX_SECTIONS}")
    summary = (f"Carried from the model's working draft: {paras} paragraphs in {len(sections)} sections; "
               f"{total} source quotes attached by a second pass.")
    report = {"blocks": len(blocks), "paragraphs": paras, "paragraphs_with_quotes": with_quotes, "quotes": total,
              "repairs": {"line_reference_stripped": stripped, "quotes_beyond_cap_dropped": beyond,
                          "blocks_empty_after_stripping_dropped": len(dropped), "headings_without_paragraphs_dropped": len(empty)},
              "line_references": refs, "paragraph_ids": pid}
    if by_sentence:
        report["repairs"]["paragraphs_split_by_sentence"] = by_sentence
    if notes_blocks:
        report["repairs"]["notes_blocks_not_carried"] = notes_blocks
    if empty:
        report["headings_without_paragraphs_dropped"] = [s["heading"] for s in empty]
    if dropped:
        report["blocks_empty_after_stripping_dropped"] = dropped
    return {"summary": summary, "sections": sections}, report


# ------------------------------------------------------------------ agreement
def line_reference_agreement(report: dict, manifest: dict) -> list:
    """Each stripped reference against the ranges that quotes of the same paragraph resolved to. `report["paragraph_ids"]`
    (an addition to the interface's report: {block id, or unit id when the block was split: "s<i>.p<j>"}) names each paragraph; claim ids are s<i>.p<j>.q<k>."""
    pid = report["paragraph_ids"]
    out = []
    for r in report["line_references"]:                       # "160, 174-180" names two spans; "18 -> 33" two lines
        named = [(int(a), int(b or a)) for a, b in re.findall(r"(\d+)(?:\s*[-\u2013]\s*[LS]?(\d+))?", r["reference"])]
        spans = [(c["resolved"]["start_line"], c["resolved"]["end_line"]) for c in manifest["claims"]
                 if r.get("unit", r["block"]) in pid and c["id"].rsplit(".", 1)[0] == pid[r.get("unit", r["block"])] and c.get("resolved")]
        out.append({"block": r["block"], **({"unit": r["unit"]} if "unit" in r else {}), "reference": r["reference"], "agrees": None if not (named and spans) else
                    all(any(a <= hi and lo <= b for a, b in spans) for lo, hi in named)})
    return out
