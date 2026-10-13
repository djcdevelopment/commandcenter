#!/usr/bin/env python3
"""amux_delivery_sidecar — a counted, offline form-repair derivative of one finished delivery.

Input: a local-work run directory (read only): work-manifest.json, delivery-output.json, delivery.json and
candidate.md. The sidecar verifies the input against the manifest's recorded hashes (output, candidate,
delivery manifest, brief, pinned source files), then writes a NEW directory holding a derivative output,
its rendered markdown and renderer manifest, a copy of the original output, and sidecar-report.json.
The original is never modified; the derivative is never an official verdict (semantic_review_required is
true, official_verdict is null) and mechanical success is not semantic acceptance.

The one repair of this slice, `symbol_scoped_quote`: a short quote that occurs more than once in the pinned
source (the renderer refuses it as short_ambiguous) is replaced by the smallest unique contiguous excerpt of
the one Rust function that (a) is named in the quote's paragraph and (b) is the only such function whose own
body contains the quote exactly once. Anything else stays unresolved and is reported. Prose, headings and
order are never changed. No trimming, no budget change, no inference, no dispatch.

Usage: amux_delivery_sidecar.py RUN_DIR NEW_OUT_DIR
"""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hearth.delivery import contract, render, revision, sourcemap, verify  # noqa: E402

REPORT_SCHEMA = "amux-delivery-sidecar.v1"
REPAIR_KEY = "symbol_scoped_quote"
IMMUTABLE_PARTS = ("runs", "hybrid-context-trial", "omen-c-read", "omen-e-read")
_FN = re.compile(r"(?<![A-Za-z0-9_])fn\s+([A-Za-z_][A-Za-z0-9_]*)")


def _word(ch: str) -> bool:
    """ASCII identifier character; code outside strings and comments is ASCII or the scanner refuses."""
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _cont(ch: str) -> bool:
    """Any character that continues a word in prose or text: letters, marks, digits, underscore (Unicode-aware)."""
    return ch == "_" or unicodedata.category(ch)[0] in "LMN"


class SidecarError(ValueError):
    """The sidecar refuses: input, source or output is not what the run recorded."""


class ScopeParseError(ValueError):
    """The Rust source has a shape the scanner does not trust; no scope is claimed."""


# ---------------------------------------------------------------- Rust function scopes (stdlib scanner)

def mask_rust(text: str) -> str:
    """Same-length copy of `text` with comment and string/char literal contents blanked (newlines kept), so braces
    inside them cannot move a function boundary. Raises ScopeParseError for shapes it does not trust."""
    out = list(text)
    n, i = len(text), 0

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif text.startswith("/*", i):
            depth, j = 1, i + 2
            while depth and j < n:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            if depth:
                raise ScopeParseError("unterminated block comment")
            blank(i, j)
            i = j
        elif not c.isascii():
            raise ScopeParseError(f"non-ASCII code token {c!r} outside a string or comment is not supported")
        elif c.isalpha() or c == "_":
            j = i
            while j < n and _word(text[j]):
                j += 1
            if j == i:   # cannot happen for alpha/_ starts; never loop on an unexpected token
                raise ScopeParseError(f"unsupported token at offset {i}")
            word = text[i:j]
            if word in ("r", "br", "cr") and j < n and text[j] in '"#':
                h = j
                while h < n and text[h] == "#":
                    h += 1
                if h >= n or text[h] != '"':
                    raise ScopeParseError("raw identifier or malformed raw string")
                close = '"' + "#" * (h - j)
                end = text.find(close, h + 1)
                if end < 0:
                    raise ScopeParseError("unterminated raw string")
                blank(h + 1, end)
                i = end + len(close)
            else:
                i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise ScopeParseError("unterminated string")
            blank(i + 1, j)
            i = j + 1
        elif c == "'":
            if text[i + 1:i + 2] == "\\":
                j = i + 3
                while j < n and text[j] != "'":
                    j += 1
                if j >= n:
                    raise ScopeParseError("unterminated char literal")
                blank(i + 1, j)
                i = j + 1
            elif text[i + 2:i + 3] == "'" and text[i + 1:i + 2] not in ("'", ""):
                blank(i + 1, i + 2)
                i += 3
            elif text[i + 1:i + 2].isascii() and (text[i + 1:i + 2].isalpha() or text[i + 1:i + 2] == "_"):
                i += 1   # a lifetime
            else:
                raise ScopeParseError("unrecognised quote shape")
        else:
            i += 1
    return "".join(out)


def function_spans(text: str) -> list:
    """[{name, start, end, body}] for every `fn name ... { body }` (offsets into `text`). Declarations without a body
    are skipped. Unbalanced, partially overlapping or generic-brace shapes raise ScopeParseError."""
    masked = mask_rust(text)
    spans = []
    for m in _FN.finditer(masked):
        stack, angle, j, n = [], 0, m.end(), len(masked)
        body = None
        while j < n:
            c = masked[j]
            if c in "([":
                stack.append(c)
            elif c in ")]":
                if not stack or {")": "(", "]": "["}[c] != stack.pop():
                    raise ScopeParseError(f"unbalanced bracket in signature of {m.group(1)}")
            elif not stack and c == "<":
                angle += 1
            elif not stack and c == ">" and masked[j - 1] != "-":
                angle = max(0, angle - 1) if masked[j - 1] != "=" else angle
            elif not stack and c == ";":
                break
            elif not stack and c == "{":
                if angle:
                    raise ScopeParseError(f"brace inside generics in signature of {m.group(1)}")
                body = j
                break
            j += 1
        else:
            raise ScopeParseError(f"signature of {m.group(1)} reaches end of file")
        if body is None:
            continue
        depth, k = 0, body
        while k < n:
            if masked[k] == "{":
                depth += 1
            elif masked[k] == "}":
                depth -= 1
                if not depth:
                    break
            k += 1
        if depth:
            raise ScopeParseError(f"unbalanced body of {m.group(1)}")
        spans.append({"name": m.group(1), "start": m.start(), "end": k + 1, "body": body})
    for a in spans:
        for b in spans:
            if a is not b and a["start"] < b["start"] < a["end"] < b["end"]:
                raise ScopeParseError("partially overlapping function spans")
    return spans


def innermost(spans: list, offset: int) -> Optional[dict]:
    inside = [s for s in spans if s["start"] <= offset < s["end"]]
    return max(inside, key=lambda s: s["start"]) if inside else None


# ---------------------------------------------------------------- the repair

def _occurrences(text: str, needle: str) -> list:
    hits, pos = [], text.find(needle)
    while pos >= 0:
        hits.append(pos)
        pos = text.find(needle, pos + 1)
    return hits


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _named(paragraph: str, name: str) -> bool:
    for m in re.finditer(re.escape(name), paragraph):
        before = paragraph[m.start() - 1] if m.start() else ""
        after = paragraph[m.end()] if m.end() < len(paragraph) else ""
        if not (before and _cont(before)) and not (after and _cont(after)):
            return True
    return False


def _excerpt(texts: dict, path: str, span: dict, hit: int, qlen: int) -> tuple:
    """Shortest contiguous excerpt that contains the owned hit, lies inside the function, occurs exactly once in all
    pinned sources, fits MAX_QUOTE_CHARS, does not start or end on whitespace and does not cut an identifier.
    Equal-length candidates all contain the same owned occurrence, so the earliest start is taken and the count is
    logged. -> (excerpt, tied candidate count) or (None, reason)."""
    text = texts[path]
    he = hit + qlen

    def edge(i: int) -> bool:   # never inside a word or grapheme: no cut between continuing characters, no leading mark
        return i <= 0 or i >= len(text) or not ((_cont(text[i - 1]) and _cont(text[i]))
                                                or unicodedata.category(text[i])[0] == "M")

    for length in range(qlen, contract.MAX_QUOTE_CHARS + 1):
        found = []
        for s in range(max(span["start"], he - length), min(hit, span["end"] - length) + 1):
            e = s + length
            if text[s].isspace() or text[e - 1].isspace() or not edge(s) or not edge(e):
                continue
            cand = text[s:e]
            if sourcemap.quote_chars(cand) <= contract.MAX_QUOTE_CHARS and \
                    sum(len(_occurrences(t, cand)) for t in texts.values()) == 1:
                found.append(cand)
        if found:
            return found[0], len(found)
    return None, f"no unique excerpt inside the function within {contract.MAX_QUOTE_CHARS} characters"


def repair_quotes(output: dict, sm, claims: list) -> tuple:
    """-> (derivative output, changes, unresolved). Only claims the renderer left short_ambiguous are candidates."""
    derived = copy.deepcopy(output)
    texts = {fm.path: "\n".join(fm.lines) for fm in sm.files}
    scopes: dict = {}
    changes, unresolved = [], []
    for row in claims:
        if row["match"] != "missing" or not row.get("ambiguous"):
            continue
        i, j, k = (int(x[1:]) for x in row["id"].split("."))
        para = derived["sections"][i]["paragraphs"][j]
        # claim ids count the quotes AFTER the renderer drops blank ones; keep the original index for the edit
        kept = [x for x, q in enumerate(para["quotes"]) if not (isinstance(q, str) and not q.strip())]
        idx = kept[k]
        original = para["quotes"][idx]
        needle = original.replace("\r\n", "\n").strip("\n")
        base = {"claim_id": row["id"], "quote_index": idx, "claim_quote_index": k, "quote": original}

        def refuse(reason: str, **more: Any) -> None:
            unresolved.append({**base, "reason": reason, **more})

        hits = [(p, o) for p, t in texts.items() for o in _occurrences(t, needle)]
        if len(hits) < 2 or not needle.strip():
            refuse(f"exact source occurrences: {len(hits)}")
            continue
        bad = [p for p in {p for p, _ in hits} if not p.endswith(".rs")]
        if bad:
            refuse("unsupported source language for symbol scoping", paths=sorted(bad))
            continue
        try:
            for p in {p for p, _ in hits}:
                if p not in scopes:
                    scopes[p] = function_spans(texts[p])
        except ScopeParseError as exc:
            refuse(f"uncertain scope parse: {exc}")
            continue
        owned: dict = {}
        for p, o in hits:
            s = innermost(scopes[p], o)
            if s is not None and _named(para["text"], s["name"]):
                owned.setdefault((p, s["start"], s["end"], s["name"]), []).append(o)
        if not owned:
            refuse("no function named in the paragraph owns an occurrence")
            continue
        if len(owned) > 1:
            refuse("several named functions own occurrences", symbols=sorted(f"{p}:{n}" for p, _, _, n in owned))
            continue
        (path, s_start, s_end, name), offsets = next(iter(owned.items()))
        if len(offsets) != 1:
            refuse(f"duplicate occurrences inside {name}", symbol=name, count=len(offsets))
            continue
        span = next(s for s in scopes[path] if s["start"] == s_start and s["end"] == s_end)
        new, tied = _excerpt(texts, path, span, offsets[0], len(needle))
        if new is None:
            refuse(tied, symbol=name)
            continue
        loc = sourcemap.locate(sm, new, hint=para["text"])
        want = _line_of(texts[path], offsets[0])
        if loc.match != "exact" or loc.occurrences != 1 or loc.path != path or not loc.start <= want <= loc.end:
            refuse("replacement did not resolve exactly to the owned occurrence", symbol=name)
            continue
        para["quotes"][idx] = new
        fm = sm.get(path)
        changes.append({**base, "repair": REPAIR_KEY, "new_quote": new, "equal_length_candidates": tied, "symbol": name, "path": path,
                        "symbol_range": [_line_of(texts[path], s_start), _line_of(texts[path], s_end - 1)],
                        "original_occurrences": [{"path": hp, "line": _line_of(texts[hp], o), "source_sha256": sm.get(hp).sha256}
                                 for hp, o in sorted(hits)],
                        "resolved_range": [loc.start, loc.end], "source_sha256": fm.sha256})
    return derived, changes, unresolved


# ---------------------------------------------------------------- measurement and orchestration

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _measure(manifest: dict, rung0: dict) -> dict:
    words = manifest["form_applied"].get("words")
    dev = [d for d in manifest["deviations"] if d["kind"] == "words"]
    return {"words": manifest["measures"]["words"], "word_limit": words, "enforce": (words or {}).get("enforce"),
            "words_over_max": max(0, manifest["measures"]["words"] - words["max"]) if words else 0,
            "word_deviations": dev, "sections": manifest["measures"]["sections"],
            "unsupported": list(manifest["unsupported"]), "repairs": dict(manifest["repairs"]),
            "deterministic": manifest["verification"]["deterministic"],
            "rung0": rung0["rung0"]["state"], "rung0_findings": rung0["rung0"]["findings"]}


def _render(output: dict, brief: dict, sm, meta: dict) -> tuple:
    md, man, _ = render.render_with_findings(output, brief, sm, meta)
    contract.check_manifest(man)
    return md, man, verify.verify(man, brief, output)


def derive(output: dict, brief: dict, sm, meta: Optional[dict] = None) -> dict:
    """Pure core: everything the sidecar writes, computed from a verified output/brief/source map."""
    meta = {**(meta or {})}
    base_md, base_man, base_v = _render(output, brief, sm, meta)
    derived, changes, unresolved = repair_quotes(output, sm, base_man["claims"])
    if changes:
        meta["aids_used"] = list(dict.fromkeys([*meta.get("aids_used", []), "sidecar"]))
    md, man, v = _render(derived, brief, sm, meta)
    if changes:
        man["repairs"][REPAIR_KEY] = len(changes)
        contract.check_manifest(man)
    return {"output": derived, "markdown": md, "manifest": man, "changes": changes, "unresolved": unresolved,
            "before": _measure(base_man, base_v), "after": _measure(man, v),
            "prose_unchanged": revision.same_prose(output, derived),
            "headings_unchanged": revision.same_headings(output, derived),
            "paragraph_order_unchanged": list(revision.prose(output)) == list(revision.prose(derived))}


def _read(run: Path, name: str) -> bytes:
    try:
        return (run / name).read_bytes()
    except OSError as exc:
        raise SidecarError(f"cannot read {name} in {run}: {exc}") from exc


def check_destination(run: Path, out: Path) -> Path:
    run, out = run.resolve(), Path(os.path.abspath(out))
    out = Path(os.path.realpath(out.parent)) / out.name
    if out == run or run in out.parents or out in run.parents:
        raise SidecarError("output overlaps the input run directory")
    if out.exists() or out.is_symlink():
        raise SidecarError(f"output already exists: {out}")
    if any(part in IMMUTABLE_PARTS or part.startswith("jepa-") for part in out.parts):
        raise SidecarError(f"output is inside an immutable location: {out}")
    return out


def run(run_dir: Path, out_dir: Path) -> dict:
    run_dir = run_dir.resolve()
    out = check_destination(run_dir, out_dir)
    raw_manifest = _read(run_dir, "work-manifest.json")
    manifest = json.loads(raw_manifest)
    raw_output, raw_cand, raw_delivery = (_read(run_dir, n) for n in
                                          ("delivery-output.json", "candidate.md", "delivery.json"))
    refs = manifest.get("delivery_artifacts") or {}
    for label, raw in (("output", raw_output), ("candidate", raw_cand), ("manifest", raw_delivery)):
        want = (refs.get(label) or {}).get("sha256")
        if want != _sha(raw):
            raise SidecarError(f"{label} bytes do not match the work manifest hash ({_sha(raw)} != {want})")
    brief = manifest.get("brief")
    if not isinstance(brief, dict):
        raise SidecarError("work manifest carries no brief")
    brief_sha = _sha(render._brief_bytes(brief)[0])
    if brief_sha != manifest.get("brief_sha256"):
        raise SidecarError(f"brief hash {brief_sha} does not match the work manifest {manifest.get('brief_sha256')}")
    paths, base = manifest.get("declared_paths"), manifest.get("base_commit")
    if not (isinstance(paths, list) and paths and isinstance(base, str)):
        raise SidecarError("work manifest lacks declared_paths/base_commit")
    try:
        sm = sourcemap.build(manifest["repo"], base, paths)
    except (sourcemap.SourceMapError, OSError) as exc:
        raise SidecarError(f"pinned source unreadable: {exc}") from exc
    recorded = {f["path"]: f["sha256"] for f in (manifest.get("source") or {}).get("files", [])}
    for fm in sm.files:
        if recorded.get(fm.path) != fm.sha256:
            raise SidecarError(f"source {fm.path} hash {fm.sha256} does not match the run's {recorded.get(fm.path)}")
    original = json.loads(raw_output)
    stored = json.loads(raw_delivery)
    meta = {k: stored.get(k) for k in ("model", "backend", "configuration", "environment", "aids_used")
            if stored.get(k)}
    result = derive(original, brief, sm, meta)

    out.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(out)   # exclusive: an existing destination raises
    files = {"original/delivery-output.json": raw_output,
             "delivery-output.derivative.json": (json.dumps(result["output"], indent=2, ensure_ascii=False) + "\n").encode(),
             "candidate.derivative.md": result["markdown"].encode(),
             "delivery.derivative.json": (json.dumps(result["manifest"], indent=2, ensure_ascii=False) + "\n").encode()}
    (out / "original").mkdir()
    for name, data in files.items():
        with open(out / name, "xb") as fh:
            fh.write(data)
    diff = "".join(difflib.unified_diff(
        json.dumps(original, indent=2, ensure_ascii=False).splitlines(True),
        json.dumps(result["output"], indent=2, ensure_ascii=False).splitlines(True),
        "original/delivery-output.json", "delivery-output.derivative.json"))
    report = {
        "schema": REPORT_SCHEMA, "semantic_review_required": True, "official_verdict": None,
        "note": "Mechanical form repair only. Substance is not judged and the original candidate is unchanged.",
        "input": {"run_dir": str(run_dir), "work_id": manifest.get("work_id"), "repo": manifest["repo"],
                  "base_commit": base, "brief_sha256": brief_sha, "work_manifest_sha256": _sha(raw_manifest),
                  "original_output_sha256": _sha(raw_output), "original_candidate_sha256": _sha(raw_cand),
                  "original_delivery_sha256": _sha(raw_delivery),
                  "sources": [{"path": f.path, "sha256": f.sha256} for f in sm.files]},
        "output": {"dir": str(out), "files": {n: _sha(d) for n, d in files.items() if n != "original/delivery-output.json"},
                   "original_copy_sha256": _sha(raw_output)},
        "repairs": {REPAIR_KEY: len(result["changes"])},
        "changes": result["changes"], "unresolved": result["unresolved"],
        "before": result["before"], "after": result["after"],
        "prose_unchanged": result["prose_unchanged"], "headings_unchanged": result["headings_unchanged"],
        "paragraph_order_unchanged": result["paragraph_order_unchanged"], "output_diff": diff}
    with open(out / "sidecar-report.json", "xb") as fh:
        fh.write((json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode())
    return report


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("out_dir", type=Path, help="must not exist; created exclusively")
    ns = ap.parse_args(argv)
    try:
        report = run(ns.run_dir, ns.out_dir)
    except (SidecarError, contract.ContractError, FileExistsError, ValueError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    b, a = report["before"], report["after"]
    print(f"wrote {report['output']['dir']}: {report['repairs']} unresolved={len(report['unresolved'])} "
          f"unsupported {len(b['unsupported'])}->{len(a['unsupported'])} words {b['words']}->{a['words']} "
          f"deterministic {b['deterministic']['state']}->{a['deterministic']['state']} (semantic review required)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
