"""The verification ladder (delivery-plan 9): one call runs the rungs in order and says where a reader's time goes.

run_ladder(manifest, brief, output, *, judge_backend, author_backend, generate, revise=None, ...) -> result
triage_summary(result) -> one screen of plain text (what the verdict tool's caller reads)
Runner: python -m hearth.delivery.ladder --work-dir runs/operator/work_<id> --judge-backend omen-vllm --task-id ID

Rungs, named as ``contract.VERIFY_RUNGS`` names them: ``deterministic`` (verify.verify; a failure is reported, never a
stop), ``judge`` (judge_local once per paragraph, against the SOURCE text of every resolved range it cites, plus one
question per ``brief.substance`` against the report and the source; ``pass`` only when every paragraph and criterion is
accepted), ``reviewer`` (one author revision pass on the escalated and rejected claims; only when
``revise`` and ``rerender`` are given). Each rung records {name, state, latency_ms, calls, tokens, reason}; state is
pass | fail | unavailable | not_run, and ``unavailable`` always carries the reason (a rung that raises is unavailable,
the ladder continues). Claim verdicts: accepted (judged supported, p >= accept, no overclaim) | rejected (p <= reject)
| escalated (the band between, an unresolved quote, a judge failure, an inconsistent row, not judged).
Evidence: ``source_text(path, start, end) -> str``, or ``repo`` (``git show <commit>:<path>`` at the brief's source
commit). The model's own quote is never the evidence: with no reader the judge rung is unavailable. A ``fuzzy:<score>``
match is judged on the source lines with the model's quote shown beside them, labelled as approximate. Unresolved
quotes are never judged and never accepted. The judge is never the author's backend: AuthorJudgeError is raised before
any rung runs. The human rung is always ``not_run``: the verdict belongs to a frontier or human caller (ADR-0048).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import contract, judge_local, verify
from .render import _brief_bytes

__all__ = ["run_ladder", "triage_summary", "LadderError"]

ACCEPT, REJECT = 0.8, 0.3
SUBSTANCE_PREFIX = "substance:"
NO_READER = "no source reader (pass repo= or source_text=): a claim is never judged on the model's own quote"
FUZZY_NOTE = ("[The author quoted the lines above as follows; the quote matched them only approximately ({m}). "
              "Judge the claim on the source lines above, not on this quote.]")
# 14,000: the backoff brief's one source (fleet/bankedfire_linux.py, ~11,800 estimated) is judged whole; the largest
# substance prompt it makes is ~47,300 characters, inside the 30B judge's 40,960-token window with the rubric and report.
WHOLE_SOURCE_TOKENS, CHARS_PER_TOKEN = 14000, 3.5
PARAGRAPH_ASK = ("For this item, read the guide above with CLAIM = the whole paragraph below and QUOTE = the EVIDENCE below. "
                 "The EVIDENCE is the source text at every line range the paragraph cites, each labelled path:start-end. "
                 "Question: is every statement in the paragraph true of, and shown by, this EVIDENCE taken together? "
                 "Different statements may be shown by different ranges. A statement that no evidence line shows is not "
                 "supported, however plausible. A quote marked \"not found in the source\" is not evidence.")
SUBSTANCE_ASK = ("For this item, read the guide above with CLAIM = the requirement below and QUOTE = the EVIDENCE below: the "
                 "REPORT, then the SOURCE it describes ({scope}). Question: does the REPORT meet the requirement with "
                 "statements that are true of the SOURCE and complete against it? Check the report against the source, "
                 "never against itself. Not supported: an item the requirement asks for that is in the source and missing "
                 "from the report (\"every endpoint\" means every endpoint in the source); a statement the "
                 "source contradicts; a detail given to the wrong item (one endpoint given another endpoint's parameters "
                 "is an error).{cited}")
SOURCE_ALL = "the whole of every declared source"
SOURCE_CITED = "only the line ranges the report cites; the rest of the source is not shown"
CITED_LIMIT = (" You see only the cited ranges: judge completeness on what they show, and do not assume what unseen lines "
               "contain.")


class LadderError(Exception):
    pass


def _git_reader(repo: str, bdoc: Optional[Mapping[str, Any]]) -> Callable[[str, int, int], str]:
    commits = {s["path"]: s["commit"] for s in (bdoc or {}).get("sources", []) if isinstance(s, dict) and "commit" in s}
    cache: dict = {}

    def read(path: str, start: int, end: int) -> str:
        if path not in commits:
            raise LadderError(f"brief names no source commit for {path}")
        if path not in cache:
            r = subprocess.run(["git", "-C", repo, "show", f"{commits[path]}:{path}"], capture_output=True)
            if r.returncode:
                raise LadderError(f"git show {commits[path]}:{path} failed: {r.stderr.decode(errors='replace').strip()}")
            cache[path] = r.stdout.decode("utf-8", errors="replace").splitlines()
        return "\n".join(cache[path][start - 1:end])
    return read


def _deliverable_text(output: Mapping[str, Any], manifest: Optional[Mapping[str, Any]] = None) -> str:
    parts = [output["summary"]]
    for i, sec in enumerate(output["sections"]):
        parts.append(f"## {sec['heading']}")
        for j, para in enumerate(sec["paragraphs"]):
            parts.append(para["text"])
            if manifest and (manifest.get("form_applied") or {}).get("quote_mode") == "line_reference":
                for c in manifest["claims"]:
                    if c["id"].startswith(f"s{i}.p{j}.q"):
                        parts.append(f"> {c['quote']} ({c.get('quote_reference', '')})" if _resolved(c)
                                     else f"[unresolved reference {c.get('quote_reference', c['quote'])}]")
            else:
                parts += [f"> {q}" for q in para["quotes"]]
    return "\n\n".join(parts)


def _resolved(c: Mapping[str, Any]) -> bool:
    r = c.get("resolved")
    return c["match"] != "missing" and bool(r) and bool(r.get("path"))


def _paragraph_of(claim_id: str) -> str:
    return claim_id.rpartition(".")[0]


def _block(label: str, src: str) -> str:
    return f"[{label}]\n{src}"


def _paragraph_job(para: str, claims: list, ctx) -> Optional[dict]:
    """One judge job for a paragraph: its text as the claim, the source text of each distinct resolved range as evidence
    (in quote order), unresolved candidates explicitly labelled. A candidate never resolves its quote."""
    blocks, seen, missing = [], set(), []
    for c in claims:
        if not _resolved(c):
            missing.append(c["id"].rpartition(".")[2])
            r = c.get("candidate")
            if r:
                key = (r["path"], r["start_line"], r["end_line"])
                src = ctx["read"](*key)
                if not src.strip():
                    raise LadderError(f"{c['id']}: candidate source range is empty")
                blocks.append(_block(f"UNRESOLVED candidate {r['path']}:{r['start_line']}-{r['end_line']}",
                                     f"{src}\nAuthor quote: {c['quote']}\n"
                                     f"Candidate reason: {r['reason']}; this is not a resolved quotation. "
                                     "Judge whether the paragraph is true of these source lines, not whether the quote matched."))
            continue
        r = c["resolved"]
        key = (r["path"], r["start_line"], r["end_line"])
        if key in seen:
            continue
        seen.add(key)
        src = ctx["read"](*key)
        label = f"{r['path']}:{r['start_line']}-{r['end_line']}"
        if not src.strip():
            if c.get("quote_reference") and r["start_line"] == r["end_line"]:
                full = ctx["read"](r["path"], 1, 10 ** 9).splitlines()
                if not 1 <= r["end_line"] <= len(full) or full[r["start_line"] - 1].strip():
                    raise LadderError(f"{c['id']}: empty source range {label} is inconsistent with pinned file")
                src = "Exact whitespace-only source line as JSON: " + json.dumps(full[r["start_line"] - 1])
            else:
                raise LadderError(f"{c['id']}: source range {label} is empty")
        if c["match"].startswith("fuzzy:"):
            src = f"{src}\n\n{FUZZY_NOTE.format(m=c['match'])}\n{c['quote']}"
        blocks.append(_block(label, src))
    if not blocks:
        return None
    blocks += [f"[quote {q}: not found in the source]" for q in missing]
    return {"claim_id": para, "claim": claims[0]["text"], "quote": "\n\n".join(blocks), "instruction": PARAGRAPH_ASK,
            "quote_label": "EVIDENCE"}


def _substance_evidence(manifest, bdoc, ctx) -> tuple:
    """-> (source text, scope phrase, mode). The whole declared sources when they total under ~14,000 tokens, else the
    source text of every cited range."""
    whole = {}
    for s_ in bdoc.get("sources", []):
        text = ctx["read"](s_["path"], 1, 10 ** 9)
        if not text.strip():
            raise LadderError(f"declared source {s_['path']} is empty")
        whole[s_["path"]] = text
    est = sum(len(t) for t in whole.values()) / CHARS_PER_TOKEN
    if whole and est < WHOLE_SOURCE_TOKENS:
        return "\n\n".join(_block(f"{p}", t) for p, t in whole.items()), SOURCE_ALL, f"whole sources (~{est:.0f} tokens)"
    ranges = sorted({(c["resolved"]["path"], c["resolved"]["start_line"], c["resolved"]["end_line"])
                     for c in manifest["claims"] if _resolved(c)})
    if not ranges:
        raise LadderError("no declared source and no resolved quote: nothing to judge the criteria against")
    text = "\n\n".join(_block(f"{p}:{a}-{b}", ctx["read"](p, a, b)) for p, a, b in ranges)
    return text, SOURCE_CITED, f"cited ranges only (~{est:.0f} tokens of declared sources over {WHOLE_SOURCE_TOKENS})"


def _classify(row: dict, accept: float, reject: float) -> tuple:
    """-> (verdict, reason) from a judge row."""
    if row.get("failure"):
        return "escalated", f"judge_failure:{row['failure']} ({_clip(str(row.get('failure_detail')), 90)})"
    p, oc = row["p"], (f" overclaim={row['overclaim']}" if row["overclaim"] else "")
    if row["consistent"] is False:
        return "escalated", f"p={p:.2f}{oc} inconsistent (supported with overclaim)"
    if p <= reject:
        return "rejected", f"p={p:.2f}{oc}"
    if p >= accept and row["supported"] and not row["overclaim"]:
        return "accepted", f"p={p:.2f}"
    return "escalated", f"p={p:.2f}{oc}" + ("" if row["supported"] else " judged not supported")


def _judge_pass(manifest, output, bdoc, ctx, only: Optional[set]) -> tuple:
    """-> (claim records, criterion records, rung). Every claim in scope gets a record; a judge that cannot run makes
    the rung unavailable and leaves resolved claims escalated as not judged."""
    t0 = time.monotonic()
    recs, jobs, paras, mode = {}, [], {}, None
    for c in manifest["claims"]:
        if only is not None and c["id"] not in only:
            continue
        rec = {"claim_id": c["id"], "paragraph": _paragraph_of(c["id"]), "text": c["text"], "match": c["match"], "p": None}
        recs[c["id"]] = {**rec, "verdict": "escalated",
                         "reason": "not judged" if _resolved(c) else "quote_unresolved"}
        paras.setdefault(rec["paragraph"], []).append(c)
    crit = []
    try:
        if ctx["read"] is None:
            raise LadderError(NO_READER)
        for para, cs in paras.items():
            job = _paragraph_job(para, cs, ctx)
            if job:
                jobs.append(job)
        if bdoc and bdoc.get("substance"):
            src, scope, mode = _substance_evidence(manifest, bdoc, ctx)
            body = _deliverable_text(output, manifest)
            for s in bdoc["substance"]:
                jobs.append({"claim_id": SUBSTANCE_PREFIX + s["id"], "claim": f"Requirement: {s['statement']}",
                             "quote": f"REPORT:\n{body}\n\nSOURCE:\n{src}", "quote_label": "EVIDENCE",
                             "instruction": SUBSTANCE_ASK.format(scope=scope,
                                                                 cited=CITED_LIMIT if scope == SOURCE_CITED else "")})
        rows = judge_local.judge_local(jobs, ctx["rubric"], ctx["judge"], ctx["author"], ctx["generate"],
                                       task_id=ctx["task_id"]) if jobs else []
    except judge_local.AuthorJudgeError:
        raise
    except Exception as exc:  # noqa: BLE001 -- named in the rung and the summary banner, never swallowed
        for r in recs.values():
            if r["reason"] == "not judged":
                r["reason"] = "not judged (judge unavailable)"
        return recs, crit, _rung("judge", "unavailable", t0, reason=f"{type(exc).__name__}: {exc}")
    for row in rows:
        verdict, why = _classify(row, ctx["accept"], ctx["reject"])
        cid = row["claim_id"]
        if cid.startswith(SUBSTANCE_PREFIX):
            st = next(s["statement"] for s in bdoc["substance"] if SUBSTANCE_PREFIX + s["id"] == cid)
            crit.append({"claim_id": cid, "text": st, "verdict": verdict, "reason": why, "p": row["p"],
                         "evidence": mode})
        else:
            for c in paras[cid]:  # the verdict is the paragraph's; every resolved quote in it carries it
                if not _resolved(c):
                    if c.get("candidate"):
                        recs[c["id"]].update(candidate_verdict=verdict, candidate_reason=why,
                                              candidate_p=row["p"])
                    continue
                r = recs[c["id"]]
                r.update(verdict=verdict, reason=why + (f" {r['match']}" if r["match"].startswith("fuzzy:") else ""),
                         p=row["p"])
    calls, toks = len(rows), sum((r.get("tokens_in") or 0) + (r.get("tokens_out") or 0) for r in rows)
    failed = [r for r in rows if r.get("failure")]
    if rows and len(failed) == len(rows):
        return recs, crit, _rung("judge", "unavailable", t0, calls, toks,
                                 f"every judge call failed: {sorted({r['failure'] for r in failed})}")
    pv = _paragraph_verdicts(recs.values())
    pc = {v: sum(x == v for x in pv.values()) for v in ("accepted", "escalated", "rejected")}
    cc = {v: sum(r["verdict"] == v for r in crit) for v in ("accepted", "escalated", "rejected")}
    note = "; ".join(x for x in (
        f"paragraphs {len(pv)}: {pc['accepted']} accepted, {pc['escalated']} escalated, {pc['rejected']} rejected",
        f"criteria {len(crit)}: {cc['accepted']} accepted, {cc['escalated']} escalated, {cc['rejected']} rejected"
        if crit else None,
        f"{len(failed)} of {len(rows)} calls failed (escalated)" if failed else None,
        f"{sum(not r['claim_id'].startswith(SUBSTANCE_PREFIX) for r in rows)} paragraph call(s)",
        f"substance judged on {mode}" if mode else None) if x)
    # pass only when every paragraph in scope and every criterion was accepted; an escalation is not a pass
    ok = all(v == "accepted" for v in pv.values()) and all(r["verdict"] == "accepted" for r in crit)
    return recs, crit, _rung("judge", "pass" if ok else "fail", t0, calls, toks, note)


def _paragraph_verdicts(records) -> dict:
    """paragraph id -> its verdict: as open as its worst quote (rejected > escalated > accepted)."""
    rank, pv = ("accepted", "escalated", "rejected"), {}
    for r in records:
        pv[r["paragraph"]] = max(pv.get(r["paragraph"], "accepted"), r["verdict"], key=rank.index)
    return pv


def _rung0(manifest, brief, output) -> tuple:
    t0 = time.monotonic()
    try:
        r0 = verify.verify(manifest, brief, output)["rung0"]
    except Exception as exc:  # noqa: BLE001 -- named, the ladder continues
        why = f"{type(exc).__name__}: {exc}"
        return {"state": "unavailable", "findings": [], "reason": why}, _rung("deterministic", "unavailable", t0,
                                                                                reason=why)
    return r0, _rung("deterministic", r0["state"], t0)


def _rung(name, state, t0, calls=0, tokens=0, reason=None) -> dict:
    r = {"name": name, "state": state, "latency_ms": round((time.monotonic() - t0) * 1000), "calls": calls, "tokens": tokens}
    if reason:
        r["reason"] = reason
    return r


def _objections(records: list) -> list:
    out, seen = [], set()
    for r in records:
        if r["verdict"] == "accepted":
            continue
        cid = r["claim_id"]
        if cid.startswith(SUBSTANCE_PREFIX):
            hint = "Add a paragraph, with an exact quote, that establishes this statement."
        elif r["reason"] == "quote_unresolved":
            hint = "Replace the quote with an exact, longer quote copied from the sources."
        else:  # judged as a paragraph: one objection for the paragraph, not one per quote
            if r["paragraph"] in seen:
                continue
            seen.add(cid := r["paragraph"])
            hint = "Narrow the paragraph to what its quotes show, or change the quotes to ones that support it."
        out.append({"claim_id": cid, "objection": f"{r['verdict']}: {r['reason']} ({r['text'][:160]})", "fix_hint": hint})
    return out


def run_ladder(manifest: Mapping[str, Any], brief: Any, output: Mapping[str, Any], *, judge_backend: str,
               author_backend: str, generate: Callable[..., dict], revise: Optional[Callable[[list], Mapping]] = None,
               rerender: Optional[Callable[[Mapping], Mapping]] = None, source_text: Optional[Callable] = None,
               repo: Optional[str] = None, accept: float = ACCEPT, reject: float = REJECT,
               rubric: Optional[str] = None, task_id: Optional[str] = None) -> dict:
    """``rerender(new_output) -> new manifest`` is needed for rung 2 (the ladder owns no source map): without it a
    given ``revise`` is not run and the reviewer rung is ``unavailable`` with that reason. The revision replaces the
    original only when it validates (output and manifest) and its changed claims were judged; both are kept."""
    if judge_backend == author_backend:
        raise judge_local.AuthorJudgeError(f"judge backend {judge_backend!r} is the author backend")
    if not 0 <= reject < accept <= 1:
        raise LadderError(f"thresholds need 0 <= reject < accept <= 1, got {reject}, {accept}")
    _, bdoc = _brief_bytes(brief)
    ctx = {"read": source_text or (_git_reader(repo, bdoc) if repo else None), "judge": judge_backend,
           "author": author_backend, "generate": generate, "task_id": task_id, "accept": accept, "reject": reject,
           "rubric": rubric if rubric is not None else judge_local.LABEL_GUIDE.read_text()}

    rung0, r0rung = _rung0(manifest, brief, output)
    recs, crit, jrung = _judge_pass(manifest, output, bdoc, ctx, None)
    rungs = [r0rung, jrung]
    original = {"output": output, "manifest": manifest, "rung0": rung0, "claims": list(recs.values()), "criteria": crit}
    revision = None

    t0 = time.monotonic()
    open_ = [r for r in list(recs.values()) + crit if r["verdict"] != "accepted"]
    if revise is None:
        rungs.append(_rung("reviewer", "not_run", t0, reason="no revise callable given"))
    elif jrung["state"] == "unavailable":
        rungs.append(_rung("reviewer", "unavailable", t0, reason="the judge rung is unavailable; nothing to object to"))
    elif rerender is None:
        rungs.append(_rung("reviewer", "unavailable", t0, reason="no rerender callable: the revision cannot be re-checked"))
    elif not open_:
        rungs.append(_rung("reviewer", "not_run", t0, reason="nothing escalated or rejected"))
    else:
        revision = {"objections": _objections(open_), "replaced": False}
        try:  # one pass, one round, never looped
            new_output = revision["output"] = revise(revision["objections"])
            errs = contract.validate_output(new_output)
            new_manifest = rerender(new_output) if not errs else None
            errs = errs or contract.validate_manifest(new_manifest)
        except Exception as exc:  # noqa: BLE001
            revision["error"] = f"{type(exc).__name__}: {exc}"
            rungs.append(_rung("reviewer", "unavailable", t0, reason=f"revision failed: {revision['error']}; original kept"))
        else:
            revision["manifest"] = new_manifest
            if errs:
                revision["errors"] = errs
                rungs.append(_rung("reviewer", "fail", t0, reason=f"revision did not validate ({len(errs)} error(s): "
                                   f"{errs[0]}); original kept"))
            else:
                old = {c["id"]: (c["text"], c["quote"]) for c in manifest["claims"]}
                diff = {c["id"] for c in new_manifest["claims"] if old.get(c["id"]) != (c["text"], c["quote"])}
                dparas = {_paragraph_of(i) for i in diff}  # the judge reads whole paragraphs
                changed = {c["id"] for c in new_manifest["claims"] if _paragraph_of(c["id"]) in dparas}
                n0, _ = _rung0(new_manifest, brief, new_output)
                nrecs, ncrit, nj = _judge_pass(new_manifest, new_output, bdoc, ctx, changed)
                revision.update(changed=sorted(changed), rung0=n0, judge=nj)
                if nj["state"] == "unavailable":
                    rungs.append(_rung("reviewer", "unavailable", t0, nj["calls"], nj["tokens"],
                                       f"re-judging the revision failed: {nj['reason']}; original kept"))
                else:
                    keep = {c["id"]: recs[c["id"]] for c in new_manifest["claims"] if c["id"] not in changed}
                    recs = {c["id"]: keep.get(c["id"]) or nrecs[c["id"]] for c in new_manifest["claims"]}
                    crit, rung0, manifest, output = ncrit, n0, new_manifest, new_output
                    revision.update(replaced=True, claims=list(recs.values()), criteria=ncrit)
                    bad = any(r["verdict"] != "accepted" for r in list(recs.values()) + crit)
                    rungs.append(_rung("reviewer", "fail" if bad else "pass", t0, nj["calls"], nj["tokens"],
                                       f"one revision pass; {len(changed)} changed claim(s) re-judged; revision replaces "
                                       f"the original (rung 0 {n0['state']})"))

    claims = list(recs.values())
    counts = {v: sum(r["verdict"] == v for r in claims) for v in ("accepted", "escalated", "rejected")}
    pv = _paragraph_verdicts(claims)
    pcounts = {v: sum(x == v for x in pv.values()) for v in ("accepted", "escalated", "rejected")}
    ver = {"human": {"state": "not_run"}}
    for r in rungs:
        st = {"unavailable": "unverified"}.get(r["state"], r["state"])
        ver[r["name"]] = {"state": st if st in contract.VERIFY_STATES else "not_run"}
        if r["state"] in ("pass", "fail") and r["name"] != "deterministic":
            ver[r["name"]]["by"] = judge_backend if r["name"] == "judge" else f"{author_backend} revised, {judge_backend} judged"
        if r.get("reason"):
            ver[r["name"]]["note"] = r["reason"]
    ver["deterministic"]["state"] = {"unavailable": "unverified"}.get(rung0["state"], rung0["state"])
    return {"rungs": rungs, "rung0": rung0, "claims": claims, "criteria": crit, "counts": counts,
            "paragraph_counts": pcounts, "repairs": dict(manifest.get("repairs") or {}), "revised": bool(revision and revision["replaced"]),
            "verification": ver, "judge_backend": judge_backend, "author_backend": author_backend,
            "thresholds": {"accept": accept, "reject": reject}, "original": original, "revision": revision}


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _groups(records: list, verdict: str) -> list:
    """Claims of one paragraph share its text: one entry per paragraph, its quotes and their reasons."""
    out: dict = {}
    for r in records:
        if r["verdict"] == verdict:
            para, _, q = r["claim_id"].rpartition(".")
            out.setdefault(para, (r["text"], []))[1].append((q, r["reason"]))
    lines = []
    for para, (text, qs) in out.items():
        why = {w for _, w in qs}
        tag = (f"{' '.join(q for q, _ in qs)}: {why.pop()}" if len(why) == 1 else "; ".join(f"{q} {w}" for q, w in qs))
        lines += [f"  {para} [{tag}]", f"      \"{_clip(text, 150)}\""]
    return lines


def triage_summary(result: Mapping[str, Any]) -> str:
    """Plain text, about 40 lines for a 22-claim delivery: what a reader must look at first, then the rest."""
    L = [f"!! RUNG UNAVAILABLE: {r['name']}: {r['reason']}" for r in result["rungs"] if r["state"] == "unavailable"]
    c, crit, r0 = result["counts"], result["criteria"], result["rung0"]
    fails = [f for f in r0["findings"] if f["severity"] == "fail"]
    met = sum(r["verdict"] == "accepted" for r in crit)
    pc = result["paragraph_counts"]
    L.append(f"Paragraphs {sum(pc.values())} ({len(result['claims'])} quotes): {pc['rejected']} rejected, "
             f"{pc['escalated']} escalated, {pc['accepted']} accepted" +
             (" (after one revision round)" if result["revised"] else "") +
             f" | substance {met} of {len(crit)} met" + ("" if crit else " (not judged)") +
             (" (on cited ranges only)" if any(str(r.get("evidence", "")).startswith("cited") for r in crit) else "") +
             f" | rung 0 {r0['state']} ({len(fails)} fail findings)")
    door = result.get("door_revision")
    if door:  # the door's objection round (submit_local_work revise=True) ran before this ladder saw the answer
        b, a = door.get("before") or {}, door.get("after") or {}
        L.append(f"Door revision: kept {door.get('kept')} ({door.get('reason')}); {door.get('objections')} objection(s); "
                 f"unsupported {b.get('unsupported')} -> {a.get('unsupported')}")
    for v in ("rejected", "escalated"):
        g = _groups(result["claims"], v)
        if g:
            L.append(f"{v.upper()}:")
            L += g[:24] + ([f"  ... {(len(g) - 24) // 2} more paragraphs"] if len(g) > 24 else [])
    for r in crit:
        if r["verdict"] != "accepted":
            L.append(f"NOT MET {r['claim_id'][len(SUBSTANCE_PREFIX):]} ({r['verdict']}, {r['reason']}): {_clip(r['text'], 110)}")
    unres = [f for f in fails if f["kind"] == "quote_unresolved"]
    other = [f for f in r0["findings"] if f["kind"] != "quote_unresolved"]
    if unres:
        L.append(f"Rung 0: {len(unres)} quote(s) unresolved (escalated above, never judged)")
    L += [f"Rung 0 {x['severity'].upper()} {x['kind']} {x['claim_id']}: {_clip(x['detail'], 110)}" for x in other[:5]]
    if len(other) > 5:
        L.append(f"Rung 0: ... {len(other) - 5} more finding(s)")
    rev = result.get("revision")
    if rev:
        L.append(f"Revision: {'replaced the original' if rev['replaced'] else 'original kept'}; "
                 f"{len(rev.get('changed', []))} changed claim(s); {len(rev['objections'])} objection(s) sent")
    acc = [r for r in result["claims"] if r["verdict"] == "accepted"]
    if acc:
        L.append("Accepted quotes: " + _clip(", ".join(r["claim_id"] for r in acc), 200))
        fz = [r["claim_id"] for r in acc if r["match"].startswith("fuzzy:")]
        if fz:
            L.append("  of which fuzzy-matched (judged on the source lines): " + ", ".join(fz))
    L.append("Repairs: " + (", ".join(f"{k} {v}" for k, v in sorted(result["repairs"].items())) or "none"))
    t = result["thresholds"]
    L.append(f"Judge {result['judge_backend']} (accept >= {t['accept']}, reject <= {t['reject']}), author "
             f"{result['author_backend']}. Cost per rung:")
    L += [f"  {r['name']:<13} {r['state']:<11} {r['latency_ms']:>6} ms  {r['calls']} calls  {r['tokens']} tokens"
          for r in result["rungs"]]
    L.append("Human rung: not run (the verdict is a frontier or human caller's, ADR-0048)")
    return "\n".join(L)


async def _door_ladder(manifest, brief, output, judge_backend, repo, task_id) -> dict:
    from hearth.callers.client import HearthClient
    async with HearthClient(key=judge_local._key(), task_id=task_id) as door:
        await judge_local._require_temperature(door)
        loop = asyncio.get_running_loop()

        def gen(**kw):
            return judge_local._door_result(asyncio.run_coroutine_threadsafe(door.call("local_generate", **kw), loop).result())
        return await asyncio.to_thread(run_ladder, manifest, brief, output, judge_backend=judge_backend,
                                       author_backend=manifest["backend"], generate=gen, repo=repo, task_id=task_id)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the ladder on a stored delivery through the door; print the triage.")
    ap.add_argument("--work-dir", required=True, help="holds delivery.json, delivery-output.json, work-manifest.json")
    ap.add_argument("--judge-backend", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo", help="default: the work manifest's repo (read with git show at the brief's commits)")
    ap.add_argument("--json", help="write the full result here")
    a = ap.parse_args(argv)
    d = Path(a.work_dir)
    manifest, output, work = (json.loads((d / n).read_text()) for n in
                              ("delivery.json", "delivery-output.json", "work-manifest.json"))
    res = asyncio.run(_door_ladder(manifest, work["brief"], output, a.judge_backend, a.repo or work["repo"], a.task_id))
    if work.get("revision"):  # delivery.json is the kept answer; say which and why
        res["door_revision"] = {k: v for k, v in work["revision"].items() if k != "files"}
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str))
    print(triage_summary(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
