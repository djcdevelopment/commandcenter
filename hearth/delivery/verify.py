"""Rung 0 of the verification ladder (delivery-plan 9, step 1): what code can catch before a judge or reader spends time.

``verify(manifest, brief, output) -> {"rung0": {"state": "pass" | "fail", "findings": [...]}}``
``verify_legacy(candidate_text, source_text=None) -> same`` for plain markdown (no manifest).

Finding: ``{kind, claim_id, detail, severity}``. Kinds: ``quote_unresolved`` (fail), ``form_limit`` (fail, words under
enforce fail), ``arithmetic_mismatch`` (fail), ``number_not_in_quote`` (severity ``warn``: never fails the rung alone).
Arithmetic: ``a op b = c`` and ``N x ( ... ) = M`` in prose (thousands separators, x / x / *), recomputed with a
restricted evaluator. Numbers: a number in a paragraph that no quote of that paragraph carries. Stdlib only, no I/O.
"""
from __future__ import annotations

import ast
import html
import operator
import re
from typing import Any, Mapping, Optional

from . import contract
from .render import _brief_bytes

__all__ = ["verify", "verify_legacy"]

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_NUM_RE = re.compile(rf"(?<![\w.]){_NUM}(?![\w]|\.\d)")
# a run of digits/operators/parens/spaces, then "= number"; leftmost start, so the run is maximal
_EXPR_RE = re.compile(rf"([\d(][\d.,\s×x*/+\-−()]*?)\s*=\s*(-?(?:{_NUM}))(?![\w])")
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    raise ValueError("unsupported")


def _clean(text: str) -> str:
    return text.replace("**", "").replace("`", "")


def _arithmetic(text: str) -> list:
    """-> [(expression as written, stated, computed, decimals)] for every parseable mismatching ``expr = number``."""
    out = []
    for m in _EXPR_RE.finditer(_clean(text)):
        raw, stated_s = m.group(1).strip(), m.group(2)
        if not re.search(r"[×x*/+\-−]", raw.lstrip("-")) or raw.count("(") != raw.count(")"):
            continue
        src = re.sub(r"(?<=\d),(?=\d{3}\b)", "", raw).replace("×", "*").replace("x", "*").replace("−", "-")
        try:
            val = _eval(ast.parse(src, mode="eval"))
        except (SyntaxError, ValueError, ZeroDivisionError):
            continue
        stated = _num(stated_s)
        dec = len(stated_s.split(".")[1]) if "." in stated_s else 0
        if abs(val - stated) > max(0.5 * 10 ** -dec, 1e-3 * abs(val)):
            out.append((raw, stated_s, val, dec))
    return out


def _fmt(v: float, dec: int) -> str:
    return f"{v:,.{dec}f}"


def _numbers(text: str) -> list:
    return [(m.group(0), _num(m.group(0))) for m in _NUM_RE.finditer(_clean(text))]


def _para_findings(cid: str, text: str, evidence: Optional[str]) -> list:
    fs = [{"kind": "arithmetic_mismatch", "claim_id": cid, "severity": "fail",
           "detail": f"{raw} = {stated} stated; recomputed {_fmt(val, dec)}"} for raw, stated, val, dec in _arithmetic(text)]
    if evidence is not None:
        have = {v for _, v in _numbers(html.unescape(evidence))}
        seen = set()
        for s, v in _numbers(text):
            if v not in have and v not in seen:
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
        why = "paragraph has no quote" if c is not None and not c["quote"] else "quote not located in the sources"
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
            fs += _para_findings(f"s{i}.p{j}.q0", para["text"], "\n".join(para["quotes"]))
    return _result(fs)


def verify_legacy(candidate_text: str, source_text: Optional[str] = None) -> dict:
    """Checks 3 and 4 on plain markdown. ``source_text`` (the cited lines) enables the number check; without it
    only arithmetic runs and the result says so in ``numbers``."""
    fs: list = []
    for n, para in enumerate(p for p in re.split(r"\n\s*\n", candidate_text) if p.strip()):
        fs += _para_findings(f"p{n}", para, source_text)
    res = _result(fs)
    res["rung0"]["numbers"] = "checked" if source_text is not None else "skipped: no source text given"
    return res
