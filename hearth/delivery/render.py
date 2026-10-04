"""Quote renderer (delivery-plan task 3): delivery-output.v1 + brief.v2 + source map -> markdown + delivery.v1.

The model wrote prose and exact quotes. This module is the only place those become line ranges:
every quote goes through ``sourcemap.locate(sm, quote, hint=<paragraph text>)``; the paragraph text is the
hint so a repeated quote lands in the symbol the paragraph names. Stdlib only, no I/O, no network.

Public API
----------
``render(output, brief, sm, meta=None) -> (markdown, manifest)``
    ``output``  validated delivery-output.v1 dict (validated here; ContractError otherwise).
    ``brief``   the brief as submitted: ``bytes``/``str`` of the brief.v2 JSON, or of a markdown brief whose
                CCMETA block carries it (preferred: ``brief_sha256`` is over those exact bytes, as
                ``contract.check_manifest_against_brief`` expects). A dict is hashed as compact sorted JSON
                (``json.dumps(sort_keys=True, separators=(",", ":"))``), which will NOT equal the hash of a
                pretty-printed original. Required (ContractError when None).
    ``sm``      a sourcemap.SourceMap.
    ``meta``    optional {model, backend, configuration, environment, aids_used, verification}; absent
                values are the loud placeholder "unknown", never a guess.
``render_with_findings(...) -> (markdown, manifest, findings)``
    Same, plus the human-readable findings that also go into ``verification.deterministic.note``.
``render_legacy(candidate_v1, sm, brief=None, meta=None) -> (markdown, manifest)``
    Compatibility shim for ``local-work-candidate.v1`` (markdown artifacts): bare path, {path, start_line,
    end_line} / {path, start, end} objects and "path:N-M" / "path (lines N-M)" strings. Each citation becomes
    a quote (the cited lines, or the whole file for a bare path) located through the same ``locate``.

Rendering by ``form.citations`` (decision 3): range -> "text (path:s-e, ...)" (identical refs once);
quote -> text, then per quote its lines as a blockquote ("> " on every line) ending "(path:s-e)";
none -> nothing rendered (the manifest still carries every claim and range). A quote that does not resolve
is rendered loudly as "(not found in sources)", never with an invented range; a quoteless paragraph under
range/quote gets "(no supporting quote)". Ranges are always spelled start-end, also for one line.

Manifest: ``form_applied`` is the form the renderer applied (decision A); ``deviations`` lists every
measured departure from form.words / form.sections (decision B). ``verification.deterministic`` is "pass"
only with nothing unsupported and no words deviation under ``enforce: "fail"``; its note is free text
(unsupported claims, deviations, repairs worth reading). Unsupported = every claim whose quote is missing,
except a quoteless paragraph under ``citations: "none"``. Words follow decision 1 via ``contract.count_words``
(computed from the model's text, so a citation the model leaked into prose and the renderer stripped is
still counted). No timestamps: markdown and manifest are a pure function of (output, brief, sm, meta).
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import textwrap
from typing import Any, Mapping, Optional, Union

from . import contract
from .sourcemap import Location, SourceMap, locate, locate_line_reference

__all__ = ["render", "render_with_findings", "render_legacy"]

_ENV_FALLBACK = "unknown"
_MISSING = Location("", 0, 0, "missing", 0)  # a paragraph without a quote / an unresolvable legacy citation
# Citation syntax that leaked into the model's prose: "(path/file.py:12-20)" or "[file.toml:7]".
_LEAKED_CITE = re.compile(r"\s*[\(\[]\s*[\w./-]+\.\w+:\d+(?:-\d+)?\s*[\)\]]")
_STRING_CITATION = re.compile(
    r"^(?P<path>[^:()\s]+)(?::(?P<a>\d+)(?:-(?P<b>\d+))?|\s*\(lines?\s*(?P<c>\d+)(?:\s*-\s*(?P<d>\d+))?\))?$")
_PROSE_LINES = re.compile(r"\blines?\s+\d+(?:\s*-\s*\d+)?", re.I)
_HEADING = re.compile(r"^#{1,6}\s+(\S.*?)\s*#*\s*$", re.M)


def _brief_bytes(brief: Union[bytes, str, Mapping[str, Any], None]) -> tuple:
    """(bytes hashed, parsed brief dict or None). JSON text, or markdown with a CCMETA block; else ContractError."""
    if brief is None:
        return b"", None
    if isinstance(brief, (bytes, str)):
        raw = brief.encode("utf-8") if isinstance(brief, str) else brief
        text = raw.decode("utf-8")
        if text.lstrip().startswith("{"):
            doc = json.loads(text)
        else:
            doc = contract.parse_ccmeta(text)
            if doc is None:
                raise contract.ContractError("brief: neither JSON nor markdown with a CCMETA block")
        return raw, doc
    return json.dumps(brief, sort_keys=True, separators=(",", ":")).encode("utf-8"), dict(brief)


def drop_blank_quotes(output: Any) -> tuple:
    """-> (copy of output without blank quote strings, count dropped). Constrained decoding lets a quotes tail through
    as "" or whitespace; the caller's output is never mutated and a malformed shape passes through for check_output."""
    if not isinstance(output, dict) or not isinstance(output.get("sections"), list):
        return output, 0
    dropped, secs = 0, []
    for sec in output["sections"]:
        if isinstance(sec, dict) and isinstance(sec.get("paragraphs"), list):
            paras = []
            for para in sec["paragraphs"]:
                if isinstance(para, dict) and isinstance(para.get("quotes"), list):
                    kept = [q for q in para["quotes"] if not (isinstance(q, str) and not q.strip())]
                    dropped += len(para["quotes"]) - len(kept)
                    para = {**para, "quotes": kept}
                paras.append(para)
            sec = {**sec, "paragraphs": paras}
        secs.append(sec)
    return {**output, "sections": secs}, dropped


def _ref(loc) -> str:
    return f"{loc.path}:{loc.start}-{loc.end}"


def _manifest_base(raw: bytes, meta: Optional[Mapping[str, Any]]) -> dict:
    meta = dict(meta or {})
    model = meta.get("model") or _ENV_FALLBACK
    backend = meta.get("backend") or _ENV_FALLBACK
    aids = ["source_map", "quote_renderer"] + [x for x in (meta.get("aids_used") or [])
                                                  if x not in ("source_map", "quote_renderer")]
    ver = contract.default_verification()
    ver.update(meta.get("verification") or {})
    return {
        "schema": contract.MANIFEST_SCHEMA,
        "brief_sha256": hashlib.sha256(raw).hexdigest(),
        "model": model,
        "backend": backend,
        "configuration": meta.get("configuration") or {"model": model, "seat": backend},
        "environment": meta.get("environment") or _ENV_FALLBACK,
        "aids_used": aids,
        "claims": [], "measures": {"words": 0, "sections": []},
        "repairs": {}, "unsupported": [], "verification": ver,
        "form_applied": None, "deviations": [],
    }


def _form_applied(bdoc: Optional[Mapping[str, Any]], citations: str) -> dict:
    """Decision A. words/sections are null when the brief's form does not set them."""
    raw_form = (bdoc or {}).get("form") or {}
    eff = contract.form_defaults(bdoc or {})
    w = eff.get("words")
    return {"citations": citations,
            "words": {"min": w["min"], "max": w["max"], "enforce": w["enforce"]} if w else None,
            "sections": list(raw_form["sections"]) if "sections" in raw_form else None}


def _claim_row(cid: str, text: str, quote: str, loc) -> dict:
    row = {"id": cid, "text": text, "quote": quote,
           "resolved": None if loc.match == "missing" else
           {"path": loc.path, "start_line": loc.start, "end_line": loc.end},
           "match": loc.match}
    if loc.candidate is not None:
        row["candidate"] = dict(loc.candidate)
    if loc.truncated:
        row["truncated"] = True
    if loc.occurrences > 1:  # exact/normalized hits, or the hits of a short quote that resolved to missing
        row["ambiguous"] = loc.occurrences
    return row


def _tally(claims: list) -> dict:
    rep = {"normalized_quote": 0, "fuzzy_quote": 0, "ambiguous_quote": 0, "short_ambiguous_quote": 0, "truncated_quote": 0}
    for c in claims:
        rep["normalized_quote"] += c["match"] == "normalized" and not c.get("truncated")
        rep["truncated_quote"] += bool(c.get("truncated"))
        rep["fuzzy_quote"] += c["match"].startswith("fuzzy:")
        rep["ambiguous_quote"] += "ambiguous" in c and c["match"] != "missing"
        rep["short_ambiguous_quote"] += "ambiguous" in c and c["match"] == "missing"
    return rep


def _deviations(fa: Mapping[str, Any], words: int, heads: list) -> list:
    """Decision B: words against form.words, sections presence + order against form.sections."""
    devs = []
    w = fa["words"]
    if w and not w["min"] <= words <= w["max"]:
        devs.append({"kind": "words", "expected": {"min": w["min"], "max": w["max"]}, "observed": words,
                     "note": f"enforce {w['enforce']}"})
    want = fa["sections"] or []
    if want and heads != want:
        missing = [h for h in want if h not in heads]
        extra = [h for h in heads if h not in want]
        present = [h for h in heads if h in want]
        parts = ([f"missing {missing}"] if missing else []) + ([f"unrequested {extra}"] if extra else []) + \
            (["order differs"] if present != [h for h in want if h in heads] else [])
        devs.append({"kind": "sections", "expected": list(want), "observed": list(heads),
                     "note": "; ".join(parts) or "duplicate headings"})
    return devs


def _sources_label(sm: SourceMap) -> str:
    return ", ".join(f.path for f in sm.files) + f"@{sm.commit[:7]}"


def _finish(man: dict, sm: SourceMap, claims: list, extra_repairs: dict, findings: list,
            unsupported: list, empty_quote_note: str = "paragraph has no quote") -> None:
    rep = {k: v for k, v in {**_tally(claims), **extra_repairs}.items() if v}
    man["claims"], man["repairs"], man["unsupported"] = claims, rep, unsupported
    fw = man["form_applied"]["words"]
    enforce_fail = bool(fw) and fw["enforce"] == "fail" and any(d["kind"] == "words" for d in man["deviations"])
    by_id = {c["id"]: c for c in claims}
    notes = [f"{u}: " + (empty_quote_note if not by_id[u]["quote"] else
                         f"quote under {contract.SHORT_QUOTE_CHARS} characters found {by_id[u]['ambiguous']} times"
                         if "ambiguous" in by_id[u] else
                         f"quote not found in {_sources_label(sm)}") for u in unsupported]
    notes += [f"{d['kind']} deviation: expected {d['expected']}, observed {d['observed']} ({d['note']})"
              for d in man["deviations"]]
    notes += findings
    det: dict = {"state": "fail" if (unsupported or enforce_fail) else "pass"}
    if notes:
        det["note"] = "; ".join(notes)
    man["verification"]["deterministic"] = det


def _quote_lines(quote: str, cite: str, sm: Optional[SourceMap] = None, row: Optional[dict] = None) -> list:
    """Resolved quote: the source text of the range, dedented (never the model's quote characters); missing: the
    model's text with HTML entities decoded."""
    res = row["resolved"] if row else None
    fm = sm.get(res["path"]) if (sm and res) else None
    if fm is not None:
        source = "\n".join(fm.lines[res["start_line"] - 1:res["end_line"]])
        quote = source if row and "quote_reference" in row else textwrap.dedent(source)
    else:
        quote = html.unescape(quote)
    lines = quote.split("\n") if row and "quote_reference" in row else [ln.rstrip() for ln in quote.strip("\n").split("\n")]
    lines[-1] = f"{lines[-1]} ({cite})"
    return ["> " + ln if ln.strip() else ">" for ln in lines]


def render_with_findings(output: Mapping[str, Any], brief: Union[bytes, str, Mapping[str, Any]], sm: SourceMap,
                         meta: Optional[Mapping[str, Any]] = None) -> tuple:
    if brief is None:
        raise contract.ContractError("render: the brief is required (brief_sha256 and form come from it)")
    output, blank = drop_blank_quotes(output)
    contract.check_output(output)
    raw, bdoc = _brief_bytes(brief)
    contract.check_brief(bdoc)
    form = contract.form_defaults(bdoc)
    style, line_reference = form["citations"], form["quote_mode"] == "line_reference"
    man = _manifest_base(raw, meta)
    man["form_applied"] = _form_applied(bdoc, style)
    if line_reference:
        man["form_applied"]["quote_mode"] = "line_reference"
        if "line_reference" not in man["aids_used"]:
            man["aids_used"].append("line_reference")
        allowed = {s["path"] for s in bdoc["sources"] if sm.commit.startswith(s["commit"])}
        reference_map = SourceMap(sm.repo, sm.commit, [f for f in sm.files if f.path in allowed])

    md = [output["summary"].strip(), ""]
    claims: list = []
    unsupported: list = []
    stripped = 0
    for i, sec in enumerate(output["sections"]):
        md += [f"## {sec['heading'].strip()}", ""]
        for j, para in enumerate(sec["paragraphs"]):
            text = para["text"]
            shown, n = _LEAKED_CITE.subn("", text)
            shown = shown.strip()
            stripped += n
            if not para["quotes"]:
                row = _claim_row(f"s{i}.p{j}.q0", text, "", _MISSING)
                claims.append(row)
                if style != "none":  # decision A: exempt only under citations "none"
                    unsupported.append(row["id"])
                    shown += " (no supporting quote)"
                md += [shown, ""]
                continue
            rows = []
            for k, q in enumerate(para["quotes"]):
                loc = locate_line_reference(reference_map, q) if line_reference else locate(sm, q, hint=text)
                echoed = sm.get(loc.path).lines[loc.start - 1] if line_reference and loc.match != "missing" else q
                row = _claim_row(f"s{i}.p{j}.q{k}", text, echoed, loc)
                if line_reference:
                    row["quote_reference"] = q
                rows.append((row, loc))
            claims += [r for r, _ in rows]
            unsupported += [r["id"] for r, _ in rows if r["match"] == "missing"]
            cites = [_ref(l) if l.match != "missing" else f"found {l.occurrences} times, too short to place"
                     if l.occurrences > 1 else
                     f"unresolved {l.candidate['reason']}; candidate {l.candidate['path']}:{l.candidate['start_line']}-{l.candidate['end_line']}"
                     if l.candidate else "not found in sources" for _, l in rows]
            if style == "range" and not line_reference:
                md.append(f"{shown} ({', '.join(dict.fromkeys(cites))})")
            elif style == "quote" or line_reference:
                md.append(shown)
                seen = set()
                for (r, _), cite in zip(rows, cites):
                    key = json.dumps(r["resolved"], sort_keys=True) if r["resolved"] else None
                    if key is None or key not in seen:  # two quotes on one range print it once
                        md += _quote_lines(r["quote"], cite, sm, r)
                    seen.add(key)
            else:
                md.append(shown)
            md.append("")

    words = contract.count_words(output)
    heads = [s["heading"] for s in output["sections"]]
    man["measures"] = {"words": words, "sections": heads}
    man["deviations"] = _deviations(man["form_applied"], words, heads)
    findings: list = []
    if stripped:
        findings.append(f"{stripped} citation(s) written by the model removed from the prose")
    _finish(man, sm, claims, {"citation_syntax_stripped": stripped, "empty_quote_dropped": blank}, findings, unsupported)
    return "\n".join(md).rstrip() + "\n", man, findings


def render(output: Mapping[str, Any], brief: Union[bytes, str, Mapping[str, Any]], sm: SourceMap,
           meta: Optional[Mapping[str, Any]] = None) -> tuple:
    md, man, _ = render_with_findings(output, brief, sm, meta)
    return md, man


# ------------------------------------------------------------ legacy shim
def _legacy_quote(cit: Any) -> tuple:
    """-> (original spelling, path, start, end, repair key or None); start/end None = whole file."""
    if isinstance(cit, str):
        m = _STRING_CITATION.match(cit.strip())
        if m and (m.group("a") or m.group("c")):
            a = int(m.group("a") or m.group("c"))
            b = m.group("b") or m.group("d")
            return cit, m.group("path"), a, int(b) if b else a, "string_citation_range"
        return cit, cit.strip(), None, None, "bare_citation_expanded"
    if isinstance(cit, dict):
        keys = set(cit)
        if keys == {"path"}:
            return json.dumps(cit, sort_keys=True), str(cit["path"]), None, None, "bare_citation_expanded"
        for a, b in (("start_line", "end_line"), ("start", "end")):
            if keys == {"path", a, b}:
                return json.dumps(cit, sort_keys=True), str(cit["path"]), cit[a], cit[b], None
    return json.dumps(cit, sort_keys=True, default=str), "", None, None, "invalid_citation_shape"


def render_legacy(candidate: Mapping[str, Any], sm: SourceMap, brief: Union[bytes, str, Mapping[str, Any], None] = None,
                  meta: Optional[Mapping[str, Any]] = None) -> tuple:
    """local-work-candidate.v1 (markdown content) -> (content, delivery.v1 manifest). The content is not
    edited: the legacy candidate carries its own prose. ``form_applied.citations`` is "range" (the candidate's
    own path/range citations; nothing is re-rendered) and ``words``/``sections`` come from the brief when given.
    Words here = whitespace tokens of the whole content (headings and tables included), not decision 1.
    A cited range is taken verbatim from the pinned file, so it always resolves when in bounds: the shim proves
    the lines exist, not that they support the prose; ``citation_range_relocated`` counts citations whose text
    ``locate`` places elsewhere (the same text sits earlier in the map). Prose line references ("lines 12-25")
    are flagged in the note and never resolved."""
    content = candidate.get("content")
    if not isinstance(content, str):
        raise contract.ContractError("legacy candidate: content must be text (markdown artifact)")
    cits = candidate.get("citations")
    if not isinstance(cits, list):
        raise contract.ContractError("legacy candidate: citations must be a list")
    raw, bdoc = _brief_bytes(brief)
    if bdoc is not None:
        contract.check_brief(bdoc)
    man = _manifest_base(raw, meta)
    man["form_applied"] = _form_applied(bdoc, "range")
    summary = str(candidate.get("summary") or "").strip() or "legacy candidate"
    claims: list = []
    extra: dict = {}
    relocated = 0
    for n, cit in enumerate(cits):
        orig, path, a, b, repair = _legacy_quote(cit)
        fm = sm.get(path) if path else None
        quote = ""
        loc = _MISSING
        if fm is not None and fm.lines:
            lo, hi = (1, len(fm.lines)) if a is None else (a, b)
            if contract._is_int(lo) and contract._is_int(hi) and 1 <= lo <= hi <= len(fm.lines):
                # locate() strips leading/trailing "\n" only, so empty (not whitespace-only) edge lines move
                # the range; trim them the same way before comparing, or every such citation reads relocated.
                while lo < hi and fm.lines[lo - 1] == "":
                    lo += 1
                while hi > lo and fm.lines[hi - 1] == "":
                    hi -= 1
                quote = "\n".join(fm.lines[lo - 1:hi])
                loc = locate(sm, quote)
                if loc.match == "missing" and loc.occurrences > 1:  # short repeated line: the range names the place
                    loc = Location(path, lo, hi, "exact", 1)
                if loc.match != "missing" and (loc.path, loc.start, loc.end) != (path, lo, hi):
                    relocated += 1
        if repair:
            extra[repair] = extra.get(repair, 0) + 1
        claims.append(_claim_row(f"c{n}", f"{summary} [cites {orig}]", quote, loc))
    if relocated:
        extra["citation_range_relocated"] = relocated
    unsupported = [c["id"] for c in claims if c["match"] == "missing"]
    words = len(content.split())
    heads = [h.strip() for h in _HEADING.findall(content)]
    man["measures"] = {"words": words, "sections": heads}
    man["deviations"] = _deviations(man["form_applied"], words, heads)
    findings = ["legacy shim: words = whitespace tokens of the whole content (headings and tables included)"]
    if bdoc is None:
        findings.append("no brief: brief_sha256 is the sha256 of empty input")
    prose = _PROSE_LINES.findall(content)
    if prose:
        findings.append(f"{len(prose)} line reference(s) written in prose ({'; '.join(prose[:4])}) are unchecked")
    _finish(man, sm, claims, extra, findings, unsupported,
            empty_quote_note="citation does not resolve (path not in the source map, or range out of bounds)")
    return content, man
