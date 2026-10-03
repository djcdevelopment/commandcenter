"""Rung 0 of the verification ladder (delivery-plan 9, step 1): what code can catch before a judge or reader spends time.

``verify(manifest, brief, output) -> {"rung0": {"state": "pass" | "fail", "findings": [...]}}``
``verify_legacy(candidate_text, source_text=None) -> same`` for plain markdown (no manifest).

Finding: ``{kind, claim_id, detail, severity}``; the rung fails on any ``fail``. Kinds: ``quote_unresolved`` (fail, read
from the manifest, never re-located), ``form_limit`` (fail, words under enforce fail), ``arithmetic_mismatch`` (fail),
``stated_total`` (fail), ``number_not_in_quote`` (warn), ``number_check_skipped`` (info, legacy without source text).
Arithmetic: every ``a = b`` of an ``=`` chain in prose where a side carries an operator (``+ - − × x * / · ÷``,
parentheses, thousands separators, one unit word such as ``tokens``), recomputed by an evaluator that accepts number
literals and + - * / only. False mismatches fail the rung, so anything ambiguous is skipped: ranges and dates (``9-21``),
hex, lists of readings (``1 / 2 / 3``), a number glued to a unit (``16K``), mixed units, the tail of an expression
the scanner cannot read whole. Numbers: a number a paragraph states (not a date, label, list marker, hyphenated
identifier or small count) that none of its quotes carries, compared numerically after HTML unescape. Stdlib only.
"""
from __future__ import annotations

import ast
import html
import json
import operator
import re
from typing import Any, Mapping, Optional

from . import contract
from .render import _brief_bytes

__all__ = ["verify", "verify_legacy"]

_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
# A number in prose: not glued to a word, a dot or a comma, not the tail of a hyphenated identifier (am4-tool-5070,
# ADR-0048), not followed by more digits of a version or a list.
_NUM_RE = re.compile(rf"(?<![\w.,])(?<![A-Za-z_]-){_NUM}(?!\w|[.,]\d)")
_QNUM_RE = re.compile(r"\d(?:[\d,_]*\d)?(?:\.\d+)?|\d+")  # quote side: liberal (65_536, 4070ti, qwen3.8)
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
_LABEL_RE = re.compile(r"(?:[§#]|\b(?:sections?|steps?|tasks?|rungs?|phases?|laps?|waves?|items?|rules?|criteri(?:on|a)"
                       r"|decisions?|ADRs?|chapters?|parts?|stages?|tiers?|options?|questions?|packages?|epics?))\s*$", re.I)
_LISTNUM_RE = re.compile(r"^\s*(?:#+\s*)?$")
_VALUE_WORDS = frozenset("days hours minutes seconds secs ms tokens bytes slots retries attempts threads workers requests "
                         "seqs leases gpus cards cores bits lines".split())
_COUNT_RE = re.compile(r"\s+([a-z]+s)\b")
# Arithmetic: a run of numbers, operators, parens, '=' and (optionally) one unit word after a number.
_UNITS = r"tokens?|GiB|GB|MiB|MB|KiB|KB|bytes?|seconds?|ms|s|days?|hours?"
_RUN_RE = re.compile(rf"(?<![\w.,%^])[(\d](?:[\d.,()×x*/+\-−·÷=\s]|(?<=[\d\s])(?:{_UNITS})\b)*")
_UNIT_RE = re.compile(rf"(?<=[\d\s])({_UNITS})\b")
_OPCH = "×x*/+-−·÷"
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_MAX_EXPR = 200


def _num(s: str) -> float:
    return float(s.replace(",", "").replace("_", ""))


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    raise ValueError("unsupported")


def _clean(text: str) -> str:
    return html.unescape(text).replace("**", "").replace("`", "").replace("\\*", "*")


def _segment(seg: str) -> Optional[tuple]:
    """-> (value, has operator, decimals) or None when the segment is not plain arithmetic (a range 9-21, a date, hex,
    a list of readings 1 / 2 / 3, a stray comma, anything the evaluator refuses)."""
    if not seg or len(seg) > _MAX_EXPR or re.search(r"\d-\d|\b0x", seg):
        return None
    src = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", seg)
    for a, b in (("×", "*"), ("x", "*"), ("·", "*"), ("÷", "/"), ("−", "-")):
        src = src.replace(a, b)
    ops = re.findall(r"(?<=[\d)\s])[*/+\-](?=[\s\d(.\-])", src)
    if "," in src or (ops and set(ops) == {"/"} and len(ops) > 1):
        return None
    try:
        val = _eval(ast.parse(src.strip(), mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, RecursionError):
        return None  # not arithmetic: nothing to check, by design
    num = re.fullmatch(rf"\s*[-+−]?({_NUM})\s*", seg)
    dec = len(num.group(1).split(".")[1]) if num and "." in num.group(1) else 0
    return val, num is None, dec


def _balance(seg: str) -> str:
    seg = seg.strip()
    while seg.startswith("(") and seg.count("(") > seg.count(")"):
        seg = seg[1:].strip()
    while seg.endswith(")") and seg.count(")") > seg.count("("):
        seg = seg[:-1].strip()
    return seg


def _arithmetic(text: str) -> list:
    """-> [(left as written, right as written, left value, right value, decimals)] for every ``a = b`` in a chain of
    ``=`` where at least one side has an operator and the two disagree beyond the stated precision (the plain side
    may be truncated or rounded). ``= 90%`` also matches a ratio of 0.90."""
    out = []
    t = _clean(text)
    for m in _RUN_RE.finditer(t):
        before = t[max(0, m.start() - 200):m.start()].rstrip()
        line_start = before.rfind("\n") + 1
        if before and before[-1] in _OPCH + "%^" and before[line_start:].strip() not in ("-", "*", "+"):
            continue  # the tail of an expression we cannot read whole (30% x 100 = 30)
        glued = m.end() < len(t) and (t[m.end()].isalnum() or t[m.end()] in "%^_")
        pct = m.end() < len(t) and t[m.end()] == "%"
        pieces = re.split(r"[.,;:](?=\s|$)|\n\s*[-*+]\s", m.group(0))
        for n, piece in enumerate(pieces):
            last = n == len(pieces) - 1
            units = set(_UNIT_RE.findall(piece))
            if "=" not in piece or len(units) > 1:
                continue
            segs = [_balance(s) for s in _UNIT_RE.sub(" ", piece).strip().rstrip(_OPCH + " \n").split("=")]
            vals = [_segment(s) for s in segs]
            if last and glued and not pct:
                vals[-1] = None
            for k in range(len(segs) - 1):
                a, b = vals[k], vals[k + 1]
                if a is None or b is None or not (a[1] or b[1]):
                    continue
                (lv, _, _), (rv, rop, dec) = (a, b) if not b[1] or a[1] else (b, a)
                tol = max(10 ** -dec, 1e-3 * abs(lv)) if not rop else 1e-6 * max(1.0, abs(lv))
                ok = abs(lv - rv) < tol or (last and pct and k == len(segs) - 2 and abs(lv - rv / 100) < max(
                    10 ** -(dec + 2), 1e-3 * abs(lv)))
                if not ok:
                    expr, stated = (segs[k], segs[k + 1]) if not b[1] or a[1] else (segs[k + 1], segs[k])
                    expr = re.sub(r"\s+", " ", expr).replace("( ", "(").replace(" )", ")")
                    if pct and last and k == len(segs) - 2:
                        stated, lv = stated + "%", lv * 100
                    out.append((expr, stated, lv, dec))
    return out


def _fmt(v: float, dec: int) -> str:
    return f"{v:,.{dec}f}"


def _numbers(text: str) -> list:
    """Numbers a paragraph states: not dates or times, section/step labels, list markers, or small counts (``3
    endpoints``; a value word such as ``2 slots`` or ``7 days`` stays checked)."""
    t = _DATE_RE.sub(lambda m: " " * len(m.group(0)), _clean(text))
    out = []
    for m in _NUM_RE.finditer(t):
        s, before = m.group(0), t[max(0, m.start() - 200):m.start()]
        if _LABEL_RE.search(before) or (_LISTNUM_RE.match(before[before.rfind("\n") + 1:]) and
                                        re.match(r"[.)]\s", t[m.end():m.end() + 2])):
            continue
        w = _COUNT_RE.match(t, m.end())
        if w and "," not in s and "." not in s and int(s) <= 12 and w.group(1).lower() not in _VALUE_WORDS:
            continue
        out.append((s, _num(s), t[m.end():m.end() + 1] == "%"))
    return out


def _quote_numbers(text: str) -> set:
    t = _clean(text)
    have = {_num(x) for x in _QNUM_RE.findall(t)}
    return have | {float(x) for x in re.findall(r"\d+", t)}


_WORDS = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
# A count, a short plural noun phrase, then ':' / 'namely' / 'which are' and a list to the end of the sentence.
_TOTAL_RE = re.compile(rf"\b(\d{{1,2}}|{'|'.join(_WORDS)})\s+(?:[\w/-]+\s+){{0,3}}[\w/-]+s\b(?:\s*\([^()]*\))?(?:\s*:|,?\s+namely\b:?|,?\s+which are\b:?)\s+([^\n;:]+)", re.I)
_SENT_END = re.compile(r"\.(?=\s+[A-Z]|\s*$)")


_COUNT2_RE = re.compile(rf"\b(\d{{1,2}}|{'|'.join(_WORDS)})\s+(?:[\w/-]+\s+){{0,3}}([\w-]+s)\.(?=\s)", re.I)
_SPLIT_SENT = re.compile(r"(?<=\.)\s+(?=[A-Z])")


def _split_items(lst: str) -> list:
    """Top-level comma split (not inside () [] backticks or quotes)."""
    out, cur, depth, q = [], "", 0, None
    for ch in lst:
        if q:
            q = None if ch == q else q
        elif ch in "`\"":
            q = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "," and not depth:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    return out + [cur]


def _totals(text: str) -> list:
    """(stated, listed, sentence) where a count governs a colon-introduced full list that has another length."""
    t, fs = html.unescape(text).replace("**", ""), []
    for m in _TOTAL_RE.finditer(t):
        lst = _SENT_END.split(m.group(2), 1)[0].strip().rstrip(".")
        if lst.count("`") % 2 or lst.count('"') % 2:
            continue
        items = [x.strip() for x in _split_items(lst)]
        if len(items) > 1:
            if len(items) < 3 or not re.match(r"(?:and|or)\s+\S", items[-1], re.I):
                continue
            items[-1] = re.sub(r"^(?:and|or)\s+", "", items[-1], flags=re.I)
            if any(re.search(r"\s(?:and|or)\s", re.sub(r"\([^)]*\)|`[^`]*`", "", x)) for x in items):
                continue
        else:
            items = [x.strip() for x in re.split(r"\s+(?:and|or)\s+", re.sub(r"\([^)]*\)|`[^`]*`", lambda g: g.group(0).replace(" ", "\0"), lst))]
            if len(items) != 2:
                continue
            items = [x.replace("\0", " ") for x in items]
        if any(not x or len(x.split()) > 8 for x in items):
            continue
        stated = int(m.group(1)) if m.group(1).isdigit() else _WORDS.index(m.group(1).lower()) + 1
        if stated != len(items):
            start = t.rfind(". ", 0, m.start()) + 2 if ". " in t[:m.start()] else 0
            fs.append((stated, len(items), t[start:m.start(2) + len(lst)].strip()))
    for m in _COUNT2_RE.finditer(t):  # "six endpoints. The GET endpoints /a, /b, and /c ... The POST endpoints /d, /e, and /f ..."
        rest, lists = _SPLIT_SENT.split(t[m.end():].strip()), []
        for sent in rest:
            g = re.match(rf"The (?:[\w-]+ ){{0,2}}{m.group(2)} ((?:[/\w.-]+, )+)(?:and|or) ([/\w.-]+)", sent)
            if not g:
                break
            lists.append(g.group(1).count(",") + 1)
        if len(lists) >= 2:
            stated = int(m.group(1)) if m.group(1).isdigit() else _WORDS.index(m.group(1).lower()) + 1
            if stated != sum(lists):
                fs.append((stated, sum(lists), t[max(t.rfind(". ", 0, m.start()) + 2, 0) if ". " in t[:m.start()] else 0:m.end()].strip() + " " + " ".join(rest[:len(lists)])))
    return fs


def _para_findings(cid: str, text: str, evidence: Optional[str]) -> list:
    fs = [{"kind": "arithmetic_mismatch", "claim_id": cid, "severity": "fail",
           "detail": f"{expr} = {stated} stated; recomputed {_fmt(val, dec)}{'%' if stated.endswith('%') else ''}"} for expr, stated, val, dec in _arithmetic(text)]
    fs += [{"kind": "stated_total", "claim_id": cid, "severity": "fail",
            "detail": f"the sentence {json.dumps(sent)} says {n} but lists {k}; count the listed items and state the number that matches, or do not state a total"}
           for n, k, sent in _totals(text)]
    if evidence is not None:
        have = _quote_numbers(evidence)
        seen = set()
        for s, v, pct in _numbers(text):
            if v in have or v in seen or (pct and round(v / 100, 6) in have):
                continue
            seen.add(v)
            fs.append({"kind": "number_not_in_quote", "claim_id": cid, "severity": "warn",
                       "detail": f"{s} is stated in the paragraph and appears in none of its quotes"})
    return fs


def _result(findings: list) -> dict:
    fail = any(f["severity"] == "fail" for f in findings)
    return {"rung0": {"state": "fail" if fail else "pass", "findings": findings}}


def verify(manifest: Mapping[str, Any], brief: Any, output: Mapping[str, Any]) -> dict:
    contract.check_output(output)
    fs: list = []
    claims = {c["id"]: c for c in manifest["claims"]}
    for cid in manifest.get("unsupported", []):
        c = claims.get(cid)
        why = ("paragraph has no quote" if c is not None and not c["quote"] else
               f"quote under {contract.SHORT_QUOTE_CHARS} characters found {c['ambiguous']} times, too short to place"
               if c is not None and c.get("ambiguous") else
               f"{c['candidate']['reason']}: candidate location is not resolved evidence"
               if c is not None and c.get("candidate") else "quote not located in the sources")
        fs.append({"kind": "quote_unresolved", "claim_id": cid, "severity": "fail", "detail": why})
    for cid, c in claims.items():
        if c["match"] == "missing" and cid not in manifest.get("unsupported", []) and c["quote"]:
            fs.append({"kind": "quote_unresolved", "claim_id": cid, "severity": "fail",
                       "detail": "quote not located in the sources"})
    fw = (manifest.get("form_applied") or {}).get("words")
    if brief is not None:
        _, bdoc = _brief_bytes(brief)
        eff = contract.form_defaults(bdoc or {}).get("words")
        fw = {**(fw or {}), "enforce": eff["enforce"]} if eff else fw
    if fw and fw.get("enforce") == "fail":
        for d in manifest.get("deviations", []):
            if d["kind"] == "words":
                fs.append({"kind": "form_limit", "claim_id": "words", "severity": "fail",
                           "detail": f"words {d['observed']} outside {d['expected']['min']}-{d['expected']['max']} (enforce fail)"})
    fs += _para_findings("summary", output["summary"], None)
    for i, sec in enumerate(output["sections"]):
        for j, para in enumerate(sec["paragraphs"]):
            quotes = para["quotes"]
            if (manifest.get("form_applied") or {}).get("quote_mode") == "line_reference":
                quotes = [c["quote"] for cid, c in claims.items()
                          if cid.startswith(f"s{i}.p{j}.q") and c.get("resolved")]
            fs += _para_findings(f"s{i}.p{j}.q0", para["text"], "\n".join(quotes))
    return _result(fs)


def verify_legacy(candidate_text: str, source_text: Optional[str] = None) -> dict:
    """Checks 3 and 4 on plain markdown. ``source_text`` (the cited lines) enables the number check; without it
    only arithmetic runs and an ``info`` finding ``number_check_skipped`` says so."""
    fs: list = []
    for n, para in enumerate(p for p in re.split(r"\n\s*\n", candidate_text) if p.strip()):
        fs += _para_findings(f"p{n}", para, source_text)
    if source_text is None:
        fs.append({"kind": "number_check_skipped", "claim_id": "document", "severity": "info",
                   "detail": "no source text given: numbers were not compared with any quote"})
    return _result(fs)
