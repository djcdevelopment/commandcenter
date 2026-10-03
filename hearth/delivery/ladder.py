"""The verification ladder (delivery-plan 9): one call runs the rungs in order and says where a reader's time goes.

run_ladder(manifest, brief, output, *, judge_backend, author_backend, generate, revise=None, ...) -> result
triage_summary(result) -> one screen of plain text (what the verdict tool's caller reads)
Runner: python -m hearth.delivery.ladder --work-dir runs/operator/work_<id> --judge-backend omen-vllm --task-id ID

Rungs, named as ``contract.VERIFY_RUNGS`` names them: ``deterministic`` (verify.verify; a failure is reported, never a
stop), ``judge`` (judge_local on every resolved claim against the SOURCE text of its resolved range, plus one question
per ``brief.substance``), ``reviewer`` (one author revision pass on the escalated and rejected claims; only when
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


def _resolved(c: Mapping[str, Any]) -> bool:
    r = c.get("resolved")
    return c["match"] != "missing" and bool(r) and bool(r.get("path"))


def _classify(row: dict, accept: float, reject: float) -> tuple:
    """-> (verdict, reason) from a judge row."""
    if row.get("failure"):
        return "escalated", f"judge_failure:{row['failure']}"
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
    recs, jobs = {}, []
    for c in manifest["claims"]:
        if only is not None and c["id"] not in only:
            continue
        rec = {"claim_id": c["id"], "text": c["text"], "match": c["match"], "p": None}
        recs[c["id"]] = {**rec, "verdict": "escalated",
                         "reason": "not judged" if _resolved(c) else "quote_unresolved"}
    crit = []
    try:
        if ctx["read"] is None:
            raise LadderError(NO_READER)
        for c in manifest["claims"]:
            if c["id"] in recs and _resolved(c):
                r = c["resolved"]
                src = ctx["read"](r["path"], r["start_line"], r["end_line"])
                if not src.strip():
                    raise LadderError(f"{c['id']}: source range {r['path']}:{r['start_line']}-{r['end_line']} is empty")
                if c["match"].startswith("fuzzy:"):
                    src = f"{src}\n\n{FUZZY_NOTE.format(m=c['match'])}\n{c['quote']}"
                jobs.append({"claim_id": c["id"], "claim": c["text"], "quote": src})
        if bdoc:
            body = _deliverable_text(output)
            for s in bdoc.get("substance", []):
                jobs.append({"claim_id": SUBSTANCE_PREFIX + s["id"], "quote": body,
                             "claim": f"The deliverable establishes this statement: {s['statement']}"})
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
            crit.append({"claim_id": cid, "text": st, "verdict": verdict, "reason": why, "p": row["p"]})
        else:
            recs[cid].update(verdict=verdict, reason=why + (f" {recs[cid]['match']}" if
                             recs[cid]["match"].startswith("fuzzy:") else ""), p=row["p"])
    calls, toks = len(rows), sum((r.get("tokens_in") or 0) + (r.get("tokens_out") or 0) for r in rows)
    failed = [r for r in rows if r.get("failure")]
    if rows and len(failed) == len(rows):
        return recs, crit, _rung("judge", "unavailable", t0, calls, toks,
                                 f"every judge call failed: {sorted({r['failure'] for r in failed})}")
    judged = [r for r in list(recs.values()) + crit if r["p"] is not None or r["reason"].startswith("judge_failure")]
    note = f"{len(failed)} of {len(rows)} calls failed (escalated)" if failed else None
    return recs, crit, _rung("judge", "fail" if any(r["verdict"] != "accepted" for r in judged) else "pass", t0,
                             calls, toks, note)


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
    out = []
    for r in records:
        if r["verdict"] == "accepted":
            continue
        cid = r["claim_id"]
        if cid.startswith(SUBSTANCE_PREFIX):
            hint = "Add a paragraph, with an exact quote, that establishes this statement."
        elif r["reason"] == "quote_unresolved":
            hint = "Replace the quote with an exact, longer quote copied from the sources."
        else:
            hint = "Narrow the claim to what its quote shows, or change the quote to one that supports it."
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
                changed = {c["id"] for c in new_manifest["claims"] if old.get(c["id"]) != (c["text"], c["quote"])}
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
            "repairs": dict(manifest.get("repairs") or {}), "revised": bool(revision and revision["replaced"]),
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
    L.append(f"Claims {len(result['claims'])}: {c['rejected']} rejected, {c['escalated']} escalated, {c['accepted']} "
             f"accepted" + (" (after one revision round)" if result["revised"] else "") +
             f" | substance {met} of {len(crit)} met" + ("" if crit else " (not judged)") +
             f" | rung 0 {r0['state']} ({len(fails)} fail findings)")
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
        L.append("Accepted: " + _clip(", ".join(r["claim_id"] for r in acc), 200))
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
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str))
    print(triage_summary(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
