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


class CarryError(ValueError):
    pass


# ------------------------------------------------------------------ split
def _blocks_of(draft: str) -> list:
    """(kind, raw) in order, kinds heading | item | paragraph | table; raws join to the draft."""
    lines = draft.splitlines(keepends=True)
    starts, i, n = [], 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        prev_blank = i == 0 or not lines[i - 1].strip()
        if s.startswith("#") or (prev_blank and len(s) <= 30 and not re.search(r"[.;]$", s) and not s.startswith(("- ", "* ", "|"))):
            starts.append((i, "heading")); i += 1
        elif s.startswith(("- ", "* ")) or re.match(r"\d+[.)]\s", s):
            starts.append((i, "item")); i += 1
            while i < n and lines[i].strip() and lines[i][:1].isspace():
                i += 1                                        # indented continuation
        elif s.startswith("|"):
            starts.append((i, "table")); i += 1
            while i < n and lines[i].strip().startswith("|"):
                i += 1
        else:
            starts.append((i, "paragraph")); i += 1
            while i < n and lines[i].strip() and not lines[i].lstrip().startswith(("- ", "* ", "#")):
                i += 1
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
    bound = r"(?<=[.!?])\s+|\n+" if kind == "table" else r"(?<=[.!?])\s+"
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
_NUM = r"\d+(?:\s*[-\u2013\u2192]\s*L?\d+)?"
_REF = rf"(?:(?:lines?|L)\s*)?{_NUM}"
_SEP = r"\s*(?:,|;|\band\b|\bvs\.?|\bto\b|&)\s*"
_PAREN = re.compile(rf"\s*\(\s*{_REF}(?:{_SEP}{_REF})*\s*\)", re.I)
_INLINE = re.compile(rf"(?:\s+(?:see|at|in|on|from|per|around|near))?\s+\b(?:[Ll]ines?\s+{_NUM}(?:{_SEP}{_NUM})*|L{_NUM})(?![\w-])")
_START_INLINE = re.compile(rf"^(?:(?:see|at|in|on|from|per)\s+)?(?:[Ll]ines?\s+{_NUM}(?:{_SEP}{_NUM})*|L{_NUM})(?![\w-])\s*", re.I)
_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[A-Za-z_][\w.-]*\.(?:py|md|toml|json|yaml|yml|txt|sh|cfg|ini|js|ts|rs|go|c|h|cpp|service|conf|cmd)):(\d+(?:-\d+)?)(?![\w:])")
_PROTECT = re.compile(r"`[^`\n]*`|\"[^\"\n]*\"|“[^”\n]*”")
_NO_REF_BEFORE = re.compile(r"(?:^|[:;,(]|\b(?:and|or|then|but))\s*$")   # "(1) first, (2) second": an enumerator, not a reference


def _blocked(spans: list, a: int, b: int) -> bool:
    return any(a < e and s < b for s, e in spans)


def strip_line_references(text: str) -> tuple:
    spans = [m.span() for m in _PROTECT.finditer(text)]
    cuts = []                                                 # (start, end, reference as written, replacement)
    for m in _PAREN.finditer(text):
        body = m.group(0).strip()[1:-1].strip()
        nums = [int(x) for x in re.findall(r"\d+", body)]
        bare = not re.search(r"[A-Za-z]", body)
        before = text[:m.start()]
        if _blocked(spans, *m.span()):
            continue
        if bare and (max(nums) >= 10000 or _NO_REF_BEFORE.search(before)):
            continue                                          # a sizing value or an enumerator, not a line reference
        cuts.append((m.start(), m.end(), body, ""))
    m = _START_INLINE.match(text)
    if m and not _blocked(spans, *m.span()) and not any(a < m.end() for a, _, _, _ in cuts):
        cuts.append((0, m.end(), re.search(r"(?:[Ll]ines?\s+|L)\d.*$", m.group(0).strip()).group(0), ""))
    for m in _INLINE.finditer(text):
        ref = re.search(r"(?:[Ll]ines?\s+|L)\d.*$", m.group(0).strip()).group(0)
        if not _blocked(spans, *m.span()) and not any(a < m.end() and m.start() < b for a, b, _, _ in cuts):
            cuts.append((m.start(), m.end(), ref, ""))
    for m in _PATH.finditer(text):
        if not _blocked(spans, *m.span()) and not any(a < m.end() and m.start() < b for a, b, _, _ in cuts):
            cuts.append((m.start(2) - 1, m.end(2), m.group(2), ""))
    cuts.sort()
    out, last, removed = [], 0, []
    for a, b, ref, _ in cuts:
        if a < last:
            continue
        out.append(text[last:a]); last = b; removed.append(ref)
    out.append(text[last:])
    s = "".join(out)
    if removed:
        s = re.sub(r"[ \t]{2,}", " ", s)
        s = re.sub(r"\s+([,;:.!?)\]])", r"\1", s)
        s = re.sub(r"([(\[])\s+", r"\1", s)
        s = re.sub(r"^[\s,;:]+", "", s)
        s = re.sub(r"([,;])\s*([.!?])", r"\2", s)
        s = re.sub(r",\s*,", ",", s)
        s = s.strip()
    return s, removed


# ------------------------------------------------------------------ attach
def batches(blocks: list, size: int = 6) -> list:
    todo = [b for b in blocks if b["kind"] == "text"]
    return [todo[i:i + size] for i in range(0, len(todo), size)]


def format_blocks(batch: list) -> str:
    return "\n".join(f"[block {b['id']}]\n{strip_line_references(b['text'])[0]}\n" for b in batch)


def parse_attach(answer: str, ids: list) -> tuple:
    got, cur, repairs = {}, None, {"json_unescaped_quote": 0}
    for ln in answer.splitlines():
        m = re.match(r"^\[block (\d+)\]\s*$", ln.strip())
        if m:
            cur = int(m.group(1))
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
            if len(q) > MAX_QUOTE_CHARS:
                raise CarryError(f"parse_attach: a quote of {len(q)} characters in block {cur} (limit {MAX_QUOTE_CHARS})")
            got[cur].append(q)
        elif ln.strip() and ln.strip() != "(none)":
            raise CarryError(f"parse_attach: unparsed line {ln[:60]!r}")
    if set(got) != set(ids):
        raise CarryError(f"parse_attach: asked for blocks {sorted(ids)}, answer holds {sorted(got)}")
    return got, repairs


# ------------------------------------------------------------------ assemble
def assemble(blocks: list, quotes: dict, *, statements: Optional[int] = None) -> tuple:
    """`statements` is accepted for the service's call shape and unused: the summary states no count of statements."""
    sections, refs = [], []
    stripped = beyond = with_quotes = total = paras = 0
    heading = None
    for b in blocks:
        if b["kind"] == "heading":
            heading = b["text"][:MAX_HEADING_CHARS]
            sections.append({"heading": heading, "paragraphs": []})
            continue
        if not sections:
            heading = LEAD_HEADING
            sections.append({"heading": heading, "paragraphs": []})
        if len(sections[-1]["paragraphs"]) >= MAX_PARAGRAPHS:
            k = sum(1 for s in sections if s.get("_base") == heading) + 1
            sections.append({"heading": f"{heading} (continued)" if k == 1 else f"{heading} (continued {k})", "paragraphs": [], "_base": heading})
        text, removed = strip_line_references(b["text"])
        if not text:
            raise CarryError(f"assemble: block {b['id']} is empty once its line references are removed")
        stripped += len(removed)
        refs += [{"block": b["id"], "reference": r} for r in removed]
        q = list(quotes.get(b["id"]) or [])
        beyond += max(0, len(q) - MAX_QUOTES)
        q = q[:MAX_QUOTES]
        with_quotes += bool(q); total += len(q); paras += 1
        sections[-1]["paragraphs"].append({"text": text, "quotes": q}); sections[-1].setdefault("_ids", []).append(b["id"])
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
              "repairs": {"line_reference_stripped": stripped, "quotes_beyond_cap_dropped": beyond},
              "line_references": refs, "paragraph_ids": pid}
    if empty:
        report["headings_without_paragraphs_dropped"] = [s["heading"] for s in empty]
    return {"summary": summary, "sections": sections}, report


# ------------------------------------------------------------------ agreement
def line_reference_agreement(report: dict, manifest: dict) -> list:
    """Each stripped reference against the ranges that quotes of the same paragraph resolved to. `report["paragraph_ids"]`
    (an addition to the interface's report: {block id: "s<i>.p<j>"}) names each block's paragraph; claim ids are s<i>.p<j>.q<k>."""
    pid = report["paragraph_ids"]
    out = []
    for r in report["line_references"]:
        nums = [int(x) for x in re.findall(r"\d+", r["reference"])]
        if not nums:
            out.append({"block": r["block"], "reference": r["reference"], "agrees": None}); continue
        lo, hi = nums[0], (nums[1] if len(nums) > 1 else nums[0])
        spans = [(c["resolved"]["start_line"], c["resolved"]["end_line"]) for c in manifest["claims"]
                 if c["id"].rsplit(".", 1)[0] == pid[r["block"]] and c.get("resolved")]
        out.append({"block": r["block"], "reference": r["reference"],
                    "agrees": None if not spans else any(a <= hi and lo <= b for a, b in spans)})
    return out
