"""Delivery contract (delivery-plan task 1, ADR-0054): brief.v2, delivery-output.v1, delivery.v1. Stdlib only.

Three documents, three owners of truth:
  brief.v2           what to deliver: closed substance statements + a machine-readable form spec.
  delivery-output.v1 what the MODEL writes: prose plus exact quotes. Never line numbers, counts or citation syntax.
  delivery.v1        what the RENDERER records: quotes resolved to path:start-end, measures, repairs, ladder state.

Validators return a list of error strings (empty = valid), each naming the path of the offending key
(`brief.form.words.max`, `manifest.claims[2].match`); `check_*` raise ContractError with all of them.
Nothing is coerced: a wrong type is an error, never a default.

Form rules (index "Decisions taken while building", 2026-10-03):
  1. Words = whitespace tokens of the rendered body (summary + paragraph text), excluding headings and the
     citation strings the renderer emits. `count_words(output)` is that rule; `measures.words` records it and
     `form.words` is compared against it.
  2. `form.sections` is advisory: presence and order are measured (`measures.sections`), deviation is recorded;
     it has no `enforce` of its own in v2.
  3. `form.citations` (CITATIONS): "range" -> the renderer emits `path:start-end`; "quote" -> it emits the quote
     text followed by `(path:start-end)`; "none" -> no citation is rendered, the manifest still carries every
     claim and range. Default "quote".
  4. No per-claim substance link in delivery-output.v1: a paragraph's quotes support that paragraph's text.
     The manifest has one claim row per (paragraph, quote); a paragraph with no quote is one row with
     quote "" and match "missing". Suggested id: "s<section>.p<paragraph>.q<quote>".
  5. `sources[]` is authoritative for the renderer and `locate`; the prose header's repo/commit/file lines
     stay for the drain (task 4 generates sources[] from the header at submit time).
  6. A quote found more than once is rendered at one hit with `ambiguous: N` (N >= 2) on the claim and
     counts as an `ambiguous_quote` repair; an unresolved tie keeps the first hit. A quote under 24 chars
     (normalized) never matches fuzzily and, found more than once, is `match: "missing"` with `ambiguous: N`,
     counted as a `short_ambiguous_quote` repair (one word is no evidence of one place); found once, it resolves.

Manifest additions (orchestrator decisions A and B, 2026-10-03, additive):
  A. `form_applied: {citations: range|quote|none, words: {min, max, enforce} | null, sections: [...] | null}`
     is the form the renderer actually applied (the brief's form after defaults; null = the brief set none).
     A missing claim must be in `unsupported`, with one exemption: a quoteless paragraph (quote "") when
     `form_applied.citations == "none"`. A non-empty quote that did not resolve is always unsupported.
  B. `deviations: [{kind: words|sections|other, expected, observed, note?}]` (may be empty): every measured
     departure from form.words / form.sections. `verification.deterministic.note` stays free text. A `words`
     deviation under `form_applied.words.enforce == "fail"` forbids `deterministic: pass`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Union

BRIEF_SCHEMA = "brief.v2"
OUTPUT_SCHEMA = "delivery-output.v1"
MANIFEST_SCHEMA = "delivery.v1"

ENFORCE = ("measure", "fail")          # measure (default) records deviation; fail only where the destination enforces
CITATIONS = ("range", "quote", "none")  # see module docstring, rule 3
STYLES = ("markdown",)
AIDS = frozenset({"source_map", "quote_renderer", "constrained_output", "sidecar", "judge", "reviewer"})
MATCHES = ("exact", "normalized", "missing")  # plus "fuzzy:<score>", score 0..1 with two decimals
# Repair counts the contract knows and cross-checks against claims; the renderer (task 3) may add other
# snake_case keys (heading_added, trailing_prose_removed, citation_syntax_stripped, ...).
REPAIR_KEYS = ("normalized_quote", "fuzzy_quote", "ambiguous_quote", "short_ambiguous_quote")
SHORT_QUOTE_CHARS = 24  # same floor as sourcemap.SHORT_QUOTE_CHARS (normalized length)
VERIFY_RUNGS = ("deterministic", "judge", "reviewer", "human")
VERIFY_STATES = ("pass", "fail", "unverified", "not_run")  # unrun = not_run, judge unavailable = unverified; never pass
DEVIATION_KINDS = ("words", "sections", "other")

# delivery-output.v1 size bounds: shared by validate_output and output_json_schema so a runaway model
# cannot emit unbounded arrays or strings under constrained decoding.
MAX_SUMMARY_CHARS = 1200
MAX_HEADING_CHARS = 120
MAX_TEXT_CHARS = 2400
MAX_QUOTE_CHARS = 400
MAX_SECTIONS = 16
MAX_PARAGRAPHS = 24
MAX_QUOTES = 8

_FUZZY = re.compile(r"^fuzzy:(0\.\d{2}|1\.00)$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_REPAIR_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_CCMETA = re.compile(r"<!--\s*CCMETA\s*(\{.*?\})\s*-->", re.DOTALL)
V1_KEYS = ("builders", "task_class", "est_tokens", "requires", "max_age_s")
V2_KEYS = ("substance", "form", "sources", "aids")


class ContractError(ValueError):
    pass


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _exact(obj: Any, required: set, optional: set, where: str, errs: list) -> bool:
    """True only if obj is a dict with every required key; unexpected keys are reported but do not block."""
    if not isinstance(obj, dict):
        errs.append(f"{where}: must be an object, got {type(obj).__name__}")
        return False
    missing = sorted(required - set(obj))
    errs += [f"{where}: missing {k}" for k in missing]
    errs += [f"{where}: unexpected {k}" for k in sorted(set(obj) - required - optional)]
    return not missing


def parse_ccmeta(text: str) -> Optional[dict]:
    """The JSON object inside the CCMETA comment, or None (no front block = v1 brief, v1 behaviour)."""
    m = _CCMETA.search(text)
    if not m:
        return None
    try:
        doc = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ContractError(f"CCMETA: invalid JSON: {e}") from None
    if not isinstance(doc, dict):
        raise ContractError("CCMETA: must be a JSON object")
    return doc


# ---------------------------------------------------------------- brief.v2
def validate_brief(doc: Any) -> list:
    """brief.v2 front block. Absent `schema` = v1, valid by definition (backwards compatible) unless it carries
    v2 keys. With schema == brief.v2 the v2 keys are validated and v1 keys are kept unchecked."""
    errs: list = []
    if not isinstance(doc, dict):
        return [f"brief: must be an object, got {type(doc).__name__}"]
    if "schema" not in doc:
        return [f"brief.{k}: v2 key needs schema {BRIEF_SCHEMA}" for k in V2_KEYS if k in doc]
    if doc["schema"] != BRIEF_SCHEMA:
        return [f"brief.schema: must be {BRIEF_SCHEMA}, got {doc['schema']!r}"]
    _exact(doc, {"schema", "substance"}, set(V1_KEYS) | {"form", "sources", "aids"}, "brief", errs)
    subs = doc.get("substance")
    if not isinstance(subs, list) or not subs:
        errs.append("brief.substance: non-empty list required")
    else:
        seen: set = set()
        for i, s in enumerate(subs):
            w = f"brief.substance[{i}]"
            if not _exact(s, {"id", "statement"}, set(), w, errs):
                continue
            if not (isinstance(s["id"], str) and _ID.match(s["id"])):
                errs.append(f"{w}.id: must match {_ID.pattern}")
            elif s["id"] in seen:
                errs.append(f"{w}.id: duplicate {s['id']}")
            else:
                seen.add(s["id"])
            if not _is_str(s["statement"]):
                errs.append(f"{w}.statement: non-empty text required")
    form = doc.get("form")
    if "form" in doc:
        errs += _validate_form(form)
    if "sources" in doc:
        srcs = doc["sources"]
        if not isinstance(srcs, list) or not srcs:
            errs.append("brief.sources: non-empty list required when present")
        else:
            for i, s in enumerate(srcs):
                w = f"brief.sources[{i}]"
                if not _exact(s, {"path", "commit"}, set(), w, errs):
                    continue
                p = s["path"]
                if not _is_str(p) or p.startswith("/") or ".." in p.split("/"):
                    errs.append(f"{w}.path: relative path without .. required")
                if not (isinstance(s["commit"], str) and _COMMIT.match(s["commit"])):
                    errs.append(f"{w}.commit: 7-40 lowercase hex required")
    if "aids" in doc:
        aids = doc["aids"]
        if not isinstance(aids, list):
            errs.append("brief.aids: list required")
        else:
            for i, a in enumerate(aids):
                if a not in AIDS:
                    errs.append(f"brief.aids[{i}]: unknown aid {a!r}; one of {sorted(AIDS)}")
                elif aids.index(a) != i:
                    errs.append(f"brief.aids[{i}]: duplicate {a!r}")
    if isinstance(form, dict) and form.get("citations") in ("quote", "range") and not doc.get("sources"):
        errs.append(f"brief.form.citations: {form['citations']!r} needs brief.sources[] to resolve against")
    return errs


def _validate_form(form: Any) -> list:
    errs: list = []
    if not _exact(form, set(), {"words", "citations", "sections", "style"}, "brief.form", errs):
        return errs
    if "words" in form:
        w = form["words"]
        if _exact(w, {"max"}, {"min", "enforce"}, "brief.form.words", errs):
            lo, hi = w.get("min", 0), w["max"]
            if not _is_int(lo) or lo < 0:
                errs.append("brief.form.words.min: int >= 0 required")
            if not _is_int(hi) or hi < 1:
                errs.append("brief.form.words.max: int >= 1 required")
            elif _is_int(lo) and lo > hi:
                errs.append(f"brief.form.words: min {lo} exceeds max {hi}")
            if "enforce" in w and w["enforce"] not in ENFORCE:
                errs.append(f"brief.form.words.enforce: one of {ENFORCE}, got {w['enforce']!r}")
    if "citations" in form and form["citations"] not in CITATIONS:
        errs.append(f"brief.form.citations: one of {CITATIONS}, got {form['citations']!r}")
    if "sections" in form:
        sec = form["sections"]
        if not isinstance(sec, list):
            errs.append("brief.form.sections: list of headings required")
        else:
            for i, s in enumerate(sec):
                if not _is_str(s):
                    errs.append(f"brief.form.sections[{i}]: non-empty heading required")
                elif sec.index(s) != i:
                    errs.append(f"brief.form.sections[{i}]: duplicate {s!r}")
    if "style" in form and form["style"] not in STYLES:
        errs.append(f"brief.form.style: one of {STYLES}, got {form['style']!r}")
    return errs


def form_defaults(brief: Mapping[str, Any]) -> dict:
    """Effective form spec of a VALIDATED brief: words.min=0, words.enforce=measure, citations=quote,
    sections=[] (advisory, rule 2), style=markdown. `words` stays absent when the brief sets no limit."""
    f = dict(brief.get("form") or {})
    if "words" in f:
        f["words"] = {"min": 0, "enforce": "measure", **f["words"]}
    f.setdefault("citations", "quote")
    f.setdefault("sections", [])
    f.setdefault("style", "markdown")
    return f


# ------------------------------------------------------ delivery-output.v1
def _bounded_str(v: Any, limit: int, where: str, errs: list) -> None:
    if not _is_str(v):
        errs.append(f"{where}: non-empty text required")
    elif len(v) > limit:
        errs.append(f"{where}: {len(v)} chars exceeds {limit}")


def _bounded_list(v: Any, lo: int, hi: int, where: str, errs: list) -> bool:
    if not isinstance(v, list):
        errs.append(f"{where}: list required")
        return False
    if not lo <= len(v) <= hi:
        errs.append(f"{where}: {len(v)} items, {lo}..{hi} allowed")
    return True


def validate_output(doc: Any) -> list:
    """What the model writes: {summary, sections: [{heading, paragraphs: [{text, quotes: [str]}]}]}.
    Prose and exact quotes only: no line numbers, no counts, no citation syntax (closed objects). Citation
    syntax leaking into `text` is a renderer repair (task 3), not a shape error here."""
    errs: list = []
    if not _exact(doc, {"summary", "sections"}, set(), "output", errs):
        return errs
    _bounded_str(doc["summary"], MAX_SUMMARY_CHARS, "output.summary", errs)
    secs = doc["sections"]
    if not _bounded_list(secs, 1, MAX_SECTIONS, "output.sections", errs):
        return errs
    for i, s in enumerate(secs):
        w = f"output.sections[{i}]"
        if not _exact(s, {"heading", "paragraphs"}, set(), w, errs):
            continue
        _bounded_str(s["heading"], MAX_HEADING_CHARS, f"{w}.heading", errs)
        if not _bounded_list(s["paragraphs"], 1, MAX_PARAGRAPHS, f"{w}.paragraphs", errs):
            continue
        for j, p in enumerate(s["paragraphs"]):
            pw = f"{w}.paragraphs[{j}]"
            if not _exact(p, {"text", "quotes"}, set(), pw, errs):
                continue
            _bounded_str(p["text"], MAX_TEXT_CHARS, f"{pw}.text", errs)
            if _bounded_list(p["quotes"], 0, MAX_QUOTES, f"{pw}.quotes", errs):
                for k, q in enumerate(p["quotes"]):
                    _bounded_str(q, MAX_QUOTE_CHARS, f"{pw}.quotes[{k}]", errs)
    return errs


def output_json_schema() -> dict:
    """The JSON Schema handed to the engine (response_format: json_schema, xgrammar). Closed objects, no $ref,
    length bounds without `pattern` (vLLM 0.30 rejects pattern+length on one string), array bounds on every
    list. Mirrors validate_output; the validator is stricter only in rejecting whitespace-only strings."""
    def s(hi: int) -> dict:
        return {"type": "string", "minLength": 1, "maxLength": hi}

    para = {"type": "object", "additionalProperties": False, "required": ["text", "quotes"],
            "properties": {"text": s(MAX_TEXT_CHARS),
                           "quotes": {"type": "array", "maxItems": MAX_QUOTES, "items": s(MAX_QUOTE_CHARS)}}}
    sec = {"type": "object", "additionalProperties": False, "required": ["heading", "paragraphs"],
           "properties": {"heading": s(MAX_HEADING_CHARS),
                          "paragraphs": {"type": "array", "minItems": 1, "maxItems": MAX_PARAGRAPHS, "items": para}}}
    return {"type": "object", "additionalProperties": False, "required": ["summary", "sections"],
            "properties": {"summary": s(MAX_SUMMARY_CHARS),
                           "sections": {"type": "array", "minItems": 1, "maxItems": MAX_SECTIONS, "items": sec}}}


def count_words(output: Mapping[str, Any]) -> int:
    """Rule 1: whitespace tokens of summary + every paragraph text. Headings and the citation strings the
    renderer emits are excluded, so this is computed from the validated model output, before rendering."""
    texts = [output["summary"]] + [p["text"] for s in output["sections"] for p in s["paragraphs"]]
    return sum(len(t.split()) for t in texts)


# -------------------------------------------------------------- delivery.v1
def default_verification() -> dict:
    """The ladder before anything ran: every rung not_run. There is no default that reads as pass."""
    return {rung: {"state": "not_run"} for rung in VERIFY_RUNGS}


def validate_manifest(doc: Any) -> list:
    errs: list = []
    req = {"schema", "brief_sha256", "model", "backend", "configuration", "environment", "aids_used",
           "claims", "measures", "repairs", "unsupported", "verification", "form_applied", "deviations"}
    if not _exact(doc, req, set(), "manifest", errs):
        return errs
    fa = doc["form_applied"]
    cites = None
    if _exact(fa, {"citations", "words", "sections"}, set(), "manifest.form_applied", errs):
        cites = fa["citations"]
        if cites not in CITATIONS:
            errs.append(f"manifest.form_applied.citations: one of {CITATIONS}, got {cites!r}")
        w = fa["words"]
        if w is not None and _exact(w, {"min", "max", "enforce"}, set(), "manifest.form_applied.words", errs):
            if not (_is_int(w["min"]) and _is_int(w["max"]) and 0 <= w["min"] <= w["max"]):
                errs.append(f"manifest.form_applied.words: ints 0 <= min <= max required, got {w['min']!r}..{w['max']!r}")
            if w["enforce"] not in ENFORCE:
                errs.append(f"manifest.form_applied.words.enforce: one of {ENFORCE}, got {w['enforce']!r}")
        sec = fa["sections"]
        if sec is not None and not (isinstance(sec, list) and all(_is_str(x) for x in sec)):
            errs.append("manifest.form_applied.sections: null or a list of headings required")
    devs = doc["deviations"]
    if not isinstance(devs, list):
        errs.append("manifest.deviations: list required (empty when nothing deviated)")
        devs = []
    for i, d in enumerate(devs):
        w = f"manifest.deviations[{i}]"
        if not _exact(d, {"kind", "expected", "observed"}, {"note"}, w, errs):
            continue
        if d["kind"] not in DEVIATION_KINDS:
            errs.append(f"{w}.kind: one of {DEVIATION_KINDS}, got {d['kind']!r}")
        if "note" in d and not _is_str(d["note"]):
            errs.append(f"{w}.note: non-empty text required when present")
    if doc["schema"] != MANIFEST_SCHEMA:
        errs.append(f"manifest.schema: must be {MANIFEST_SCHEMA}, got {doc['schema']!r}")
    if not (isinstance(doc["brief_sha256"], str) and _SHA.match(doc["brief_sha256"])):
        errs.append("manifest.brief_sha256: 64 lowercase hex chars required")
    for k in ("model", "backend", "environment"):
        if not _is_str(doc[k]):
            errs.append(f"manifest.{k}: non-empty text required")
    if not isinstance(doc["configuration"], dict) or not doc["configuration"]:
        errs.append("manifest.configuration: non-empty object required (model x seat x context x profile)")
    aids = doc["aids_used"]
    if not isinstance(aids, list):
        errs.append("manifest.aids_used: list required")
    else:
        errs += [f"manifest.aids_used[{i}]: unknown aid {a!r}" for i, a in enumerate(aids) if a not in AIDS]

    ids: list = []
    tally = {k: 0 for k in REPAIR_KEYS}
    missing_ids: set = set()
    exempt_ids: set = set()  # decision A: quoteless paragraphs under citations "none"
    claims = doc["claims"]
    if not isinstance(claims, list):
        errs.append("manifest.claims: list required")
        claims = []
    for i, c in enumerate(claims):
        w = f"manifest.claims[{i}]"
        if not _exact(c, {"id", "text", "quote", "resolved", "match"}, {"ambiguous"}, w, errs):
            continue
        cid, m, r = c["id"], c["match"], c["resolved"]
        if not (isinstance(cid, str) and _ID.match(cid)):
            errs.append(f"{w}.id: must match {_ID.pattern}")
        elif cid in ids:
            errs.append(f"{w}.id: duplicate {cid}")
        ids.append(cid)
        if not _is_str(c["text"]):
            errs.append(f"{w}.text: non-empty text required")
        if not isinstance(c["quote"], str):
            errs.append(f"{w}.quote: string required ('' for a paragraph without a quote)")
        if not (m in MATCHES or (isinstance(m, str) and _FUZZY.match(m))):
            errs.append(f"{w}.match: exact|normalized|fuzzy:<0.00..1.00>|missing, got {m!r}")
            continue
        if m == "missing":
            if cites == "none" and c["quote"] == "":
                exempt_ids.add(cid)
            else:
                missing_ids.add(cid)
            if r is not None:
                errs.append(f"{w}.resolved: must be null when match is missing")
        else:
            if c["quote"] == "":
                errs.append(f"{w}.match: an empty quote can only be missing")
            if r is None:
                errs.append(f"{w}.resolved: required when match is {m}")
            elif _exact(r, {"path", "start_line", "end_line"}, set(), f"{w}.resolved", errs):
                if not _is_str(r["path"]):
                    errs.append(f"{w}.resolved.path: non-empty text required")
                st, en = r["start_line"], r["end_line"]
                if not (_is_int(st) and _is_int(en) and 1 <= st <= en):
                    errs.append(f"{w}.resolved: 1 <= start_line <= end_line required, got {st!r}-{en!r}")
            tally["normalized_quote"] += m == "normalized"
            tally["fuzzy_quote"] += m.startswith("fuzzy:")
        if "ambiguous" in c:
            a = c["ambiguous"]
            if not (_is_int(a) and a >= 2):
                errs.append(f"{w}.ambiguous: int >= 2 (occurrences) required, got {a!r}")
            elif m == "missing":
                if isinstance(c["quote"], str) and len(" ".join(c["quote"].split())) < SHORT_QUOTE_CHARS:
                    tally["short_ambiguous_quote"] += 1
                else:
                    errs.append(f"{w}.ambiguous: a missing quote is ambiguous only when under {SHORT_QUOTE_CHARS} characters")
            elif m not in ("exact", "normalized"):
                errs.append(f"{w}.ambiguous: only exact, normalized or short missing quotes can be ambiguous, match is {m}")
            elif len(" ".join(c["quote"].split())) < SHORT_QUOTE_CHARS:
                errs.append(f"{w}.ambiguous: a quote under {SHORT_QUOTE_CHARS} characters found more than once must be missing")
            else:
                tally["ambiguous_quote"] += 1

    meas = doc["measures"]
    if _exact(meas, {"words", "sections"}, set(), "manifest.measures", errs):
        if not (_is_int(meas["words"]) and meas["words"] >= 0):
            errs.append("manifest.measures.words: int >= 0 required")
        if not isinstance(meas["sections"], list):
            errs.append("manifest.measures.sections: list of headings required")
        else:
            errs += [f"manifest.measures.sections[{i}]: non-empty heading required"
                     for i, s in enumerate(meas["sections"]) if not _is_str(s)]

    rep = doc["repairs"]
    if not isinstance(rep, dict):
        errs.append("manifest.repairs: object of counts required")
    else:
        for k, v in rep.items():
            if not (isinstance(k, str) and _REPAIR_KEY.match(k)):
                errs.append(f"manifest.repairs: key {k!r} must be snake_case")
            elif not (_is_int(v) and v >= 0):
                errs.append(f"manifest.repairs.{k}: non-negative int required, got {v!r}")
        for k in REPAIR_KEYS:  # counted repairs must agree with the claims they count
            got = rep.get(k, 0)
            if _is_int(got) and got != tally[k]:
                errs.append(f"manifest.repairs.{k}: {got} but claims show {tally[k]}")

    uns = doc["unsupported"]
    if not isinstance(uns, list):
        errs.append("manifest.unsupported: list of claim ids required")
        uns = []
    else:
        errs += [f"manifest.unsupported[{i}]: {u!r} is not a claim id" for i, u in enumerate(uns) if u not in ids]
        if missing_ids - set(uns):  # a missing quote is always unsupported; the renderer may not hide it
            errs.append(f"manifest.unsupported: must include missing-quote claims {sorted(missing_ids - set(uns))}")

    ver = doc["verification"]
    if _exact(ver, set(VERIFY_RUNGS), set(), "manifest.verification", errs):
        for rung in VERIFY_RUNGS:
            w = f"manifest.verification.{rung}"
            v = ver[rung]
            if not _exact(v, {"state"}, {"note", "by"}, w, errs):
                continue
            if v["state"] not in VERIFY_STATES:
                errs.append(f"{w}.state: one of {VERIFY_STATES}, got {v['state']!r}")
            for k in ("note", "by"):
                if k in v and not _is_str(v[k]):
                    errs.append(f"{w}.{k}: non-empty text required when present")
            # held-out rule: no judge or reviewer shares a backend or model with the arm it scores
            if rung in ("judge", "reviewer") and v.get("by") in (doc["model"], doc["backend"]):
                errs.append(f"{w}.by: {v['by']!r} is the arm under review (held-out panel rule)")
        det = ver.get("deterministic")
        if isinstance(det, dict) and det.get("state") == "pass" and uns:
            errs.append(f"manifest.verification.deterministic.state: pass with unsupported claims {uns}")
        fw = fa.get("words") if isinstance(fa, dict) else None
        if (isinstance(det, dict) and det.get("state") == "pass" and isinstance(fw, dict)
                and fw.get("enforce") == "fail" and any(isinstance(d, dict) and d.get("kind") == "words" for d in devs)):
            errs.append("manifest.verification.deterministic.state: pass with a words deviation under enforce fail")
    return errs


def check_manifest_against_brief(manifest: Mapping[str, Any], brief: Union[bytes, str]) -> list:
    """brief_sha256 must be the sha256 of the exact brief bytes as submitted (str is UTF-8 encoded)."""
    data = brief.encode("utf-8") if isinstance(brief, str) else brief
    sha = hashlib.sha256(data).hexdigest()
    got = manifest.get("brief_sha256")
    return [] if got == sha else [f"manifest.brief_sha256: {got} but the brief hashes to {sha}"]


def check_manifest_against_output(manifest: Mapping[str, Any], output: Mapping[str, Any]) -> list:
    """Manifest built from this output: one claim per (paragraph, quote) in order, a quoteless paragraph as
    quote '' (rule 4); measures.words == count_words (rule 1); measures.sections == headings in order (rule 2)."""
    errs: list = []
    want = [(p["text"], q) for s in output["sections"] for p in s["paragraphs"] for q in (p["quotes"] or [""])]
    got = [(c["text"], c["quote"]) for c in manifest["claims"]]
    if got != want:
        for i, (g, x) in enumerate(zip(got, want)):
            if g != x:
                errs.append(f"manifest.claims[{i}]: (text, quote) differs from output paragraph/quote #{i}")
                break
        if len(got) != len(want):
            errs.append(f"manifest.claims: {len(got)} rows, output has {len(want)} paragraph/quote pairs")
    words = count_words(output)
    if manifest["measures"]["words"] != words:
        errs.append(f"manifest.measures.words: {manifest['measures']['words']} but output counts {words}")
    heads = [s["heading"] for s in output["sections"]]
    if manifest["measures"]["sections"] != heads:
        errs.append(f"manifest.measures.sections: {manifest['measures']['sections']} but output has {heads}")
    return errs


def _raise(errs: list, what: str) -> None:
    if errs:
        raise ContractError(f"{what}: " + "; ".join(errs))


def check_brief(doc: Any) -> None:
    _raise(validate_brief(doc), "brief")


def check_output(doc: Any) -> None:
    _raise(validate_output(doc), "delivery-output")


def check_manifest(doc: Any) -> None:
    _raise(validate_manifest(doc), "delivery manifest")


# ----------------------------------------------------------------- selfcheck
EXAMPLES = Path(__file__).resolve().parent / "examples"
NIGHT_BRIEF = Path.home() / "work/daily-night-work-prep/briefs/2026-10-01-audit-bankedfire-backoff.md"


def _load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text())


def _must(cond: Any, msg: str) -> None:
    if not cond:
        raise ContractError(f"selfcheck: {msg}")


def _schema_walk(node: Any, path: str = "$"):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _schema_walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _schema_walk(v, f"{path}[{i}]")


def selfcheck(out=None) -> None:
    out = out or sys.stdout
    say = lambda s: print(s, file=out)  # noqa: E731
    brief_bytes = (EXAMPLES / "example.brief.v2.json").read_bytes()
    brief = json.loads(brief_bytes)
    check_brief(brief)
    say(f"ok  example.brief.v2.json: {len(brief['substance'])} substance, form={form_defaults(brief)}")
    output = _load("example.delivery-output.v1.json")
    check_output(output)
    say(f"ok  example.delivery-output.v1.json: {len(output['sections'])} sections, {count_words(output)} words (rule 1)")
    manifest = _load("example.delivery.v1.json")
    check_manifest(manifest)
    _raise(check_manifest_against_brief(manifest, brief_bytes), "manifest vs brief")
    _raise(check_manifest_against_output(manifest, output), "manifest vs output")
    say(f"ok  example.delivery.v1.json: {len(manifest['claims'])} claims, repairs={manifest['repairs']}, "
        f"unsupported={manifest['unsupported']}, form_applied.citations={manifest['form_applied']['citations']}, "
        f"deviations={len(manifest['deviations'])}; brief sha256 and output words/sections/claims agree")

    sch = output_json_schema()
    for path, node in _schema_walk(sch):
        _must("$ref" not in node and "pattern" not in node and "format" not in node, f"{path}: $ref/pattern/format")
        if node.get("type") == "object":
            _must(node.get("additionalProperties") is False, f"{path}: object not closed")
        if node.get("type") == "array":
            _must("maxItems" in node, f"{path}: unbounded array")
        if node.get("type") == "string":
            _must("maxLength" in node, f"{path}: unbounded string")
    say("ok  output_json_schema(): closed objects, no $ref/pattern, every array and string bounded")

    # negative controls: the validators must actually reject
    def mutated(doc, fn):
        d = json.loads(json.dumps(doc)); fn(d); return d
    controls = [
        ("output line-number field", validate_output,
         mutated(output, lambda d: d["sections"][0]["paragraphs"][0].__setitem__("start_line", 3))),
        ("output quotes over MAX_QUOTES", validate_output,
         mutated(output, lambda d: d["sections"][0]["paragraphs"][0].__setitem__("quotes", ["x" * 30] * (MAX_QUOTES + 1)))),
        ("manifest missing with a range", validate_manifest,
         mutated(manifest, lambda d: d["claims"][0].__setitem__("match", "missing"))),
        ("manifest ambiguous on missing", validate_manifest,
         mutated(manifest, lambda d: (d["claims"][-1].__setitem__("ambiguous", 2),
                                      d["claims"][-1].__setitem__("quote", "x" * 30)))),
        ("manifest short ambiguous quote resolved", validate_manifest,
         mutated(manifest, lambda d: d["claims"][3].update(quote="ab", ambiguous=2))),
        ("manifest repair count disagrees", validate_manifest,
         mutated(manifest, lambda d: d["repairs"].__setitem__("normalized_quote", 0))),
        ("manifest judge is the arm", validate_manifest,
         mutated(manifest, lambda d: d["verification"].__setitem__("judge", {"state": "pass", "by": d["backend"]}))),
        ("manifest deterministic pass with unsupported", validate_manifest,
         mutated(manifest, lambda d: d["verification"]["deterministic"].__setitem__("state", "pass"))),
        ("manifest missing key", validate_manifest, mutated(manifest, lambda d: d.pop("verification"))),
        ("manifest without form_applied", validate_manifest, mutated(manifest, lambda d: d.pop("form_applied"))),
        ("manifest bad deviation kind", validate_manifest,
         mutated(manifest, lambda d: d["deviations"].append({"kind": "tone", "expected": 1, "observed": 2}))),
        ("manifest missing quote hidden under citations none", validate_manifest,
         mutated(manifest, lambda d: (d["form_applied"].__setitem__("citations", "none"), d.__setitem__("unsupported", []),
                                      d["verification"].__setitem__("deterministic", {"state": "pass"})))),
        ("manifest quoteless paragraph hidden under citations quote", validate_manifest,
         mutated(manifest, lambda d: (d["claims"][-1].__setitem__("quote", ""), d.__setitem__("unsupported", []),
                                      d["verification"].__setitem__("deterministic", {"state": "pass"})))),
        ("manifest pass with words deviation under enforce fail", validate_manifest,
         mutated(manifest, lambda d: (d["form_applied"]["words"].__setitem__("enforce", "fail"),
                                      d["deviations"].append({"kind": "words", "expected": {"min": 80, "max": 320}, "observed": 400}),
                                      d["claims"].pop(), d.__setitem__("unsupported", []),
                                      d["verification"].__setitem__("deterministic", {"state": "pass"})))),
        ("brief bad enforce", validate_brief, mutated(brief, lambda d: d["form"]["words"].__setitem__("enforce", "sometimes"))),
        ("brief bad citations", validate_brief, mutated(brief, lambda d: d["form"].__setitem__("citations", "lines"))),
        ("brief v2 key without schema", validate_brief, {"builders": ["x"], "substance": []}),
    ]
    for name, fn, doc in controls:
        _must(fn(doc), f"{name} accepted")
    _must(check_manifest_against_brief(manifest, brief_bytes + b" "), "sha mismatch accepted")
    _must(check_manifest_against_output(manifest, mutated(output, lambda d: d.__setitem__("summary", "x"))),
          "word-count mismatch accepted")
    _must(validate_brief({"builders": ["x"]}) == [], "v1 front block must stay valid")
    _must(default_verification() == {r: {"state": "not_run"} for r in VERIFY_RUNGS}, "default ladder")
    exempt = mutated(manifest, lambda d: (d["form_applied"].__setitem__("citations", "none"), d["claims"][-1].__setitem__("quote", ""),
                                          d.__setitem__("unsupported", []), d["verification"].__setitem__("deterministic", {"state": "pass"})))
    _must(validate_manifest(exempt) == [], f"quoteless paragraph under citations none must be exempt: {validate_manifest(exempt)}")
    say(f"ok  {len(controls) + 2} negative controls rejected; quoteless paragraph exempt only under citations none; "
        "v1 block valid; default ladder all not_run")

    rt = _load("backoff.brief.v2.json")
    check_brief(rt)
    if NIGHT_BRIEF.exists():
        text = NIGHT_BRIEF.read_text()
        v1 = parse_ccmeta(text)
        _must(v1 is not None and validate_brief(v1) == [], "v1 CCMETA missing or no longer valid")
        lost = [k for k in v1 if rt.get(k) != v1[k]]
        _must(not lost, f"round trip dropped v1 keys: {lost}")
        commit = re.search(r"^commit: (\w+)$", text, re.M)
        paths = re.search(r"^paths: \[(.+)\]$", text, re.M)
        _must(commit and commit.group(1) == rt["sources"][0]["commit"], "commit differs from sources[0]")
        _must(paths and paths.group(1) == rt["sources"][0]["path"], "paths differ from sources[0]")
        _must(f"under {rt['form']['words']['max']} words" in text, "word limit not in brief text")
        head = text.split("\n---")[0]
        _must("criteria:" in head, "no criteria block in brief header")
        crit = len(re.findall(r"^  - ", head.split("criteria:")[1], re.M))
        _must(crit == len(rt["substance"]) + 2, f"criteria {crit} vs substance {len(rt['substance'])}+2 form criteria")
        say(f"ok  backoff.brief.v2.json round-trips {NIGHT_BRIEF.name}: v1 keys {sorted(v1)} kept, "
            f"{len(rt['substance'])} substance + words<={rt['form']['words']['max']} + cites-only-source")
    else:
        say(f"SKIPPED round-trip comparison: {NIGHT_BRIEF} not found (the JSON itself validated)")
    say("selfcheck passed")


if __name__ == "__main__":
    selfcheck()
