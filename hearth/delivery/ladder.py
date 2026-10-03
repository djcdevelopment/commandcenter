"""The verification ladder (delivery-plan 9): one call runs the rungs in order and says where a reader's time goes.

run_ladder(manifest, brief, output, *, judge_backend, author_backend, generate, revise=None, ...) -> result
triage_summary(result) -> one screen of plain text (what the verdict tool's caller reads)

Rungs, named as ``contract.VERIFY_RUNGS`` names them: ``deterministic`` (verify.verify; a failure is reported, never a
stop), ``judge`` (judge_local on every resolved claim, quote and its source lines, plus one question per
``brief.substance``), ``reviewer`` (one author revision pass on the escalated and rejected claims; only when ``revise``
and ``rerender`` are given). Each rung records {name, state, latency_ms, calls, tokens, reason}; state is pass | fail |
unavailable | not_run, and ``unavailable`` always carries the reason. Claim verdicts: accepted (supported, p >= accept,
no overclaim) | rejected (p <= reject) | escalated (the band between, an unresolved quote, a judge failure, an
inconsistent row). The judge is never the author's backend (judge_local raises AuthorJudgeError; not caught here).
The human rung is always ``not_run``: the verdict tool belongs to a frontier or human caller (ADR-0048).
``source_text(path, start, end) -> str`` supplies a range's lines; with ``repo`` it is ``git show <commit>:<path>`` of
the brief's source commit; with neither the evidence is the claim's own quote and the judge rung says so.
"""
from __future__ import annotations

import subprocess
import time
from typing import Any, Callable, Mapping, Optional

from . import contract, judge_local, verify
from .render import _brief_bytes

__all__ = ["run_ladder", "triage_summary", "LadderError"]

ACCEPT, REJECT = 0.8, 0.3
SUBSTANCE_PREFIX = "substance:"


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


def _deliverable_text(output: Mapping[str, Any]) -> str:
    parts = [output["summary"]]
    for sec in output["sections"]:
        parts.append(f"## {sec['heading']}")
        for para in sec["paragraphs"]:
            parts.append(para["text"])
            parts += [f"> {q}" for q in para["quotes"]]
    return "\n\n".join(parts)


def _tally(rows: list) -> tuple:
    return (len(rows), sum((r.get("tokens_in") or 0) + (r.get("tokens_out") or 0) for r in rows))


def _classify(row: dict, accept: float, reject: float) -> tuple:
    """-> (verdict, reason) from a judge row."""
    if row.get("failure"):
        return "escalated", f"judge_failure:{row['failure']}"
    p = row["p"]
    if row["consistent"] is False:
        return "escalated", "judge_inconsistent: supported with overclaim > 0"
    if p <= reject:
        return "rejected", f"p={p:.2f}" + (f" overclaim={row['overclaim']}" if row["overclaim"] else "")
    if p >= accept and row["supported"] and not row["overclaim"]:
        return "accepted", f"p={p:.2f}"
    return "escalated", f"p={p:.2f}" + (f" overclaim={row['overclaim']}" if row["overclaim"] else "") + \
        ("" if row["supported"] else " label not supported")


def _judge_claims(manifest, output, bdoc, rubric, judge_backend, author_backend, generate, read, accept, reject,
                  only: Optional[set], criteria: bool = True) -> tuple:
    """-> (claim records, criterion records, rows). Raises on a judge call that raises; the caller names the rung."""
    recs, jobs, via_quote = [], [], False
    for c in manifest["claims"]:
        if only is not None and c["id"] not in only:
            continue
        r = c.get("resolved")
        if c["match"] == "missing" or not r or not r.get("path"):
            recs.append({"claim_id": c["id"], "verdict": "escalated", "reason": "quote_unresolved", "p": None})
            continue
        if read is not None:
            src = read(r["path"], r["start_line"], r["end_line"])
        else:
            src, via_quote = c["quote"], True
        jobs.append({"claim_id": c["id"], "claim": c["text"], "quote": src})
    if criteria and bdoc:
        body = _deliverable_text(output)
        for s in bdoc.get("substance", []):
            jobs.append({"claim_id": SUBSTANCE_PREFIX + s["id"], "quote": body,
                         "claim": f"The deliverable establishes this statement: {s['statement']}"})
    rows = judge_local.judge_local(jobs, rubric, judge_backend, author_backend, generate) if jobs else []
    crit = []
    for row in rows:
        verdict, why = _classify(row, accept, reject)
        rec = {"claim_id": row["claim_id"], "verdict": verdict, "reason": why, "p": row["p"],
               "overclaim": row["overclaim"], "needs_calc": row["needs_calc"]}
        (crit if row["claim_id"].startswith(SUBSTANCE_PREFIX) else recs).append(rec)
    return recs, crit, rows, via_quote


def _rung(name, state, t0, calls=0, tokens=0, reason=None) -> dict:
    r = {"name": name, "state": state, "latency_ms": round((time.monotonic() - t0) * 1000), "calls": calls, "tokens": tokens}
    if reason:
        r["reason"] = reason
    return r


def _objections(claims: list, crit: list, texts: Mapping[str, str]) -> list:
    out = []
    for r in claims + crit:
        if r["verdict"] == "accepted":
            continue
        cid = r["claim_id"]
        if cid.startswith(SUBSTANCE_PREFIX):
            hint = "Add a paragraph, with an exact quote, that establishes this statement."
        elif r["reason"] == "quote_unresolved":
            hint = "Replace the quote with an exact, longer quote copied from the sources."
        else:
            hint = "Narrow the claim to what its quote shows, or change the quote to one that supports it."
        out.append({"claim_id": cid, "objection": f"{r['verdict']}: {r['reason']}" +
                    (f" ({texts[cid][:160]})" if cid in texts else ""), "fix_hint": hint})
    return out


def run_ladder(manifest: Mapping[str, Any], brief: Any, output: Mapping[str, Any], *, judge_backend: str,
               author_backend: str, generate: Callable[..., dict], revise: Optional[Callable[[list], Mapping]] = None,
               rerender: Optional[Callable[[Mapping], Mapping]] = None, source_text: Optional[Callable] = None,
               repo: Optional[str] = None, accept: float = ACCEPT, reject: float = REJECT,
               rubric: Optional[str] = None, task_id: Optional[str] = None) -> dict:
    """``rerender(new_output) -> new manifest`` is needed for rung 2 (the ladder owns no source map): without it a
    given ``revise`` is not run and the reviewer rung is ``unavailable`` with that reason."""
    if not 0 <= reject < accept <= 1:
        raise LadderError(f"thresholds need 0 <= reject < accept <= 1, got {reject}, {accept}")
    _, bdoc = _brief_bytes(brief)
    read = source_text or (_git_reader(repo, bdoc) if repo else None)
    rubric = rubric if rubric is not None else judge_local.LABEL_GUIDE.read_text()
    rungs: list = []

    t0 = time.monotonic()
    rung0 = verify.verify(manifest, brief, output)["rung0"]
    rungs.append(_rung("deterministic", rung0["state"], t0))

    claims: list = []
    crit: list = []
    t0 = time.monotonic()
    try:
        claims, crit, rows, via_quote = _judge_claims(manifest, output, bdoc, rubric, judge_backend, author_backend,
                                                      generate, read, accept, reject, None)
    except judge_local.AuthorJudgeError:
        raise
    except Exception as exc:  # noqa: BLE001 -- named in the rung and the summary, never swallowed
        rows, via_quote = [], False
        rungs.append(_rung("judge", "unavailable", t0, reason=f"{type(exc).__name__}: {exc}"))
    else:
        calls, toks = _tally(rows)
        failed = [r for r in rows if r.get("failure")]
        if rows and len(failed) == len(rows):
            rungs.append(_rung("judge", "unavailable", t0, calls, toks,
                               f"every judge call failed: {sorted({r['failure'] for r in failed})}"))
        else:
            bad = any(r["verdict"] != "accepted" for r in claims + crit)
            note = "evidence was the claim's own quote (no source reader given)" if via_quote else None
            if failed:
                note = (note + "; " if note else "") + f"{len(failed)} of {len(rows)} calls failed (escalated)"
            rungs.append(_rung("judge", "fail" if bad else "pass", t0, calls, toks, note))
    judged = rungs[-1]["state"] != "unavailable"

    revised = False
    t0 = time.monotonic()
    open_ = [r for r in claims + crit if r["verdict"] != "accepted"]
    if revise is None:
        rungs.append(_rung("reviewer", "not_run", t0, reason="no revise callable given"))
    elif not judged:
        rungs.append(_rung("reviewer", "unavailable", t0, reason="the judge rung is unavailable; nothing to object to"))
    elif rerender is None:
        rungs.append(_rung("reviewer", "unavailable", t0, reason="no rerender callable: the revision cannot be re-checked"))
    elif not open_:
        rungs.append(_rung("reviewer", "not_run", t0, reason="nothing escalated or rejected"))
    else:
        texts = {c["id"]: c["text"] for c in manifest["claims"]}
        new_output = revise(_objections(claims, crit, texts))  # one pass, one round, never looped
        new_manifest = rerender(new_output)
        old = {c["id"]: (c["text"], c["quote"]) for c in manifest["claims"]}
        changed = {c["id"] for c in new_manifest["claims"] if old.get(c["id"]) != (c["text"], c["quote"])}
        t_det = time.monotonic()
        rung0 = verify.verify(new_manifest, brief, new_output)["rung0"]
        rungs[0] = _rung("deterministic", rung0["state"], t_det,
                         reason="re-run after the revision (first-pass latency not kept)")
        keep = [r for r in claims if r["claim_id"] in {c["id"] for c in new_manifest["claims"]} - changed]
        try:
            nclaims, ncrit, nrows, nvia = _judge_claims(new_manifest, new_output, bdoc, rubric, judge_backend,
                                                        author_backend, generate, read, accept, reject, changed)
        except judge_local.AuthorJudgeError:
            raise
        except Exception as exc:  # noqa: BLE001
            rungs.append(_rung("reviewer", "unavailable", t0, reason=f"re-check failed: {type(exc).__name__}: {exc}"))
        else:
            revised, manifest, output = True, new_manifest, new_output
            claims, crit = keep + nclaims, ncrit
            calls, toks = _tally(nrows)
            bad = any(r["verdict"] != "accepted" for r in claims + crit)
            rungs.append(_rung("reviewer", "fail" if bad else "pass", t0, calls, toks,
                               f"one revision pass; {len(changed)} changed claim(s) re-judged"))

    counts = {v: sum(r["verdict"] == v for r in claims) for v in ("accepted", "escalated", "rejected")}
    ver = {"deterministic": {"state": rung0["state"]}, "human": {"state": "not_run"}}
    for n in ("judge", "reviewer"):
        r = next(x for x in rungs if x["name"] == n)
        ver[n] = {"state": r["state"] if r["state"] in contract.VERIFY_STATES else "not_run"}
        if r["state"] != "not_run" and judged and r["state"] != "unavailable":
            ver[n]["by"] = judge_backend if n == "judge" else author_backend
        if r.get("reason"):
            ver[n]["note"] = r["reason"]
    return {"rungs": rungs, "rung0": rung0, "claims": claims, "criteria": crit, "counts": counts,
            "repairs": dict(manifest.get("repairs") or {}), "revised": revised, "verification": ver,
            "judge_backend": judge_backend, "author_backend": author_backend}


def triage_summary(result: Mapping[str, Any]) -> str:
    """Plain text, under 40 lines: what a reader looks at instead of the document."""
    L = []
    unavail = [r for r in result["rungs"] if r["state"] == "unavailable"]
    for r in unavail:
        L.append(f"!! RUNG UNAVAILABLE: {r['name']}: {r['reason']}")
    c = result["counts"]
    ids = lambda v: ", ".join(r["claim_id"] for r in result["claims"] if r["verdict"] == v) or "-"
    judged = next(r for r in result["rungs"] if r["name"] == "judge")["state"] != "unavailable"
    L.append("Claims: not judged (judge rung unavailable)" if not judged else f"Claims: {c['accepted']} accepted, {c['escalated']} escalated, {c['rejected']} rejected"
             + (" (after one revision round)" if result["revised"] else ""))
    for v in ("escalated", "rejected"):
        for r in result["claims"]:
            if r["verdict"] == v:
                L.append(f"  {v.upper()} {r['claim_id']}: {r['reason']}")
    if judged:
        L.append(f"  accepted ids: {ids('accepted')}")
    crit = result["criteria"]
    if crit:
        met = [r for r in crit if r["verdict"] == "accepted"]
        L.append(f"Substance criteria: {len(met)} of {len(crit)} met")
        for r in crit:
            if r["verdict"] != "accepted":
                L.append(f"  NOT MET {r['claim_id'][len(SUBSTANCE_PREFIX):]}: {r['verdict']}, {r['reason']}")
    else:
        L.append("Substance criteria: not judged")
    f = result["rung0"]["findings"]
    L.append(f"Rung 0 (deterministic): {result['rung0']['state']}, {len(f)} finding(s)")
    L += [f"  {x['severity'].upper()} {x['kind']} {x['claim_id']}: {x['detail']}" for x in f[:8]]
    if len(f) > 8:
        L.append(f"  ... {len(f) - 8} more")
    rep = result["repairs"]
    L.append("Repairs: " + (", ".join(f"{k} {v}" for k, v in sorted(rep.items())) or "none"))
    L.append(f"Judge {result['judge_backend']}, author {result['author_backend']}. Cost per rung:")
    for r in result["rungs"]:
        L.append(f"  {r['name']:<13} {r['state']:<11} {r['latency_ms']:>6} ms  {r['calls']} calls  {r['tokens']} tokens")
    L.append("Human rung: not run (the verdict is a frontier or human caller's, ADR-0048)")
    return "\n".join(L)
