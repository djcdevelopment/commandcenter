"""Local judge (delivery task 8): a non-author model answers one closed question per claim, schema-constrained.

judge_local(claims, criteria, backend, author_backend, generate=...) -> [{claim_id, supported, p, overclaim, needs_calc}]
Runner: python -m hearth.delivery.judge_local --calibration items.jsonl --backend omen-vllm --out table.md [--limit N]
"""
import argparse, asyncio, hashlib, json, time
from collections import defaultdict
from pathlib import Path

LABEL_GUIDE = Path.home() / "work/delivery-plan/calibration/label-guide.md"
DEFAULT_CALIBRATION = Path.home() / "work/delivery-plan/calibration/items.jsonl"
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["supported", "p", "overclaim", "needs_calc"],
          "properties": {"supported": {"type": "boolean"},
                         "p": {"type": "number", "minimum": 0, "maximum": 1},
                         "overclaim": {"type": "integer", "enum": [0, 1, 2]},
                         "needs_calc": {"type": "boolean"}}}
ANSWER_FORMAT = """Answer as one JSON object with these keys:
- supported: true only if the label is "supported", otherwise false.
- p: your probability (0 to 1) that the label is "supported".
- overclaim: 0 if the claim does not go beyond the quote, 1 if the quote supports a narrower version and the claim goes somewhat further, 2 if the claim clearly goes further ("always", "ensures", "guarantees", wider scope).
- needs_calc: true if the verdict depends on checking a number or arithmetic (the guide's numeric flag)."""
KEYS = ("supported", "p", "overclaim", "needs_calc")
BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001)]


class JudgeError(Exception):
    pass


class AuthorJudgeError(JudgeError):
    """The judge backend is the backend that wrote the deliverable."""


def prompt_for(claim: dict, rubric: str) -> str:
    return f"{rubric.strip()}\n\n{ANSWER_FORMAT}\n\nCLAIM:\n{claim['claim']}\n\nQUOTE:\n{claim['quote']}\n"


def _parse(text) -> dict:
    """Strict: the reply must be a JSON object with exactly the schema's types. Raises ValueError, never repairs."""
    obj = json.loads(text)
    if not isinstance(obj, dict) or set(obj) != set(KEYS):
        raise ValueError(f"keys {sorted(obj) if isinstance(obj, dict) else type(obj).__name__}")
    if not isinstance(obj["supported"], bool) or not isinstance(obj["needs_calc"], bool):
        raise ValueError("supported/needs_calc not boolean")
    p, oc = obj["p"], obj["overclaim"]
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
        raise ValueError(f"p {p!r}")
    if isinstance(oc, bool) or oc not in (0, 1, 2):
        raise ValueError(f"overclaim {oc!r}")
    return {k: obj[k] for k in KEYS}


def judge_local(claims, criteria, backend, author_backend, generate, task_id=None, max_tokens=256) -> list[dict]:
    """claims: [{claim_id, claim, quote}]; criteria: the rubric text (or list of strings), given verbatim.
    generate(prompt=, backend=, response_schema=, temperature=, max_tokens=, task_id=) -> local_generate result dict.
    Each row: claim_id, supported, p, overclaim, needs_calc (None when parse_failure is set), plus ok, parse_failure,
    raw, latency_s, tokens_in, tokens_out, backend."""
    if backend == author_backend:
        raise AuthorJudgeError(f"judge backend {backend!r} is the author backend")
    rubric = criteria if isinstance(criteria, str) else "\n".join(criteria)
    rows = []
    for c in claims:
        t0 = time.time()
        res = generate(prompt=prompt_for(c, rubric), backend=backend, response_schema=SCHEMA, temperature=0.0,
                       max_tokens=max_tokens, task_id=task_id)
        row = {"claim_id": c["claim_id"], "supported": None, "p": None, "overclaim": None, "needs_calc": None,
               "ok": bool(res.get("ok")), "parse_failure": None, "raw": res.get("text"),
               "latency_s": round(time.time() - t0, 3), "tokens_in": res.get("tokens_in"),
               "tokens_out": res.get("tokens_out"), "backend": res.get("backend") or backend}
        if not res.get("ok"):
            row["parse_failure"] = f"call not ok: {res.get('error_code') or res.get('error')}"
        else:
            try:
                row.update(_parse(res.get("text")))
            except (ValueError, TypeError) as exc:
                row["parse_failure"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return rows


def split_of(quote: str, n: int = 5) -> int:
    """Items that share a quote share a split."""
    return int(hashlib.sha256(quote.encode()).hexdigest(), 16) % n


def _stats(pairs):
    """pairs: [(item, row)] with parsed rows only."""
    n = len(pairs)
    if not n:
        return {"n": 0}
    hit = sum(r["supported"] == (i["label"] == "supported") for i, r in pairs)
    brier = sum((r["p"] - (i["label"] == "supported")) ** 2 for i, r in pairs) / n
    return {"n": n, "acc": hit / n, "brier": brier}


def reliability_table(items, rows) -> str:
    by = {r["claim_id"]: r for r in rows}
    pairs_all = [(i, by[i["id"]]) for i in items if i["id"] in by]
    ok = [(i, r) for i, r in pairs_all if not r["parse_failure"]]
    cols = {"all": ok, "claude-labelled": [(i, r) for i, r in ok if i["labelled_by"] != "derek-pending"],
            "derek-pending": [(i, r) for i, r in ok if i["labelled_by"] == "derek-pending"],
            "numeric": [(i, r) for i, r in ok if i.get("numeric")]}
    st = {k: _stats(v) for k, v in cols.items()}
    f = lambda v, d=3: "-" if v is None else f"{v:.{d}f}"
    out = ["# Local judge reliability", "",
           f"Items {len(pairs_all)}; parsed {len(ok)}; parse failures {len(pairs_all) - len(ok)}.", "",
           "| metric | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols),
           "| n | " + " | ".join(str(st[k]["n"]) for k in cols) + " |",
           "| accuracy (supported vs not) | " + " | ".join(f(st[k].get("acc")) for k in cols) + " |",
           "| Brier | " + " | ".join(f(st[k].get("brier")) for k in cols) + " |", "",
           "## Observed supported rate per predicted p bin (all parsed)", "",
           "| bin | n | observed supported rate | mean p |", "|---|---|---|---|"]
    for lo, hi in BINS:
        b = [(i, r) for i, r in ok if lo <= r["p"] < hi]
        out.append(f"| {lo:.1f}-{min(hi, 1.0):.1f} | {len(b)} | "
                   f"{f(sum(i['label'] == 'supported' for i, _ in b) / len(b) if b else None)} | "
                   f"{f(sum(r['p'] for _, r in b) / len(b) if b else None)} |")
    out += ["", "## Overclaim: label (rows) by predicted overclaim 0/1/2 (columns)", "",
            "| label | 0 | 1 | 2 |", "|---|---|---|---|"]
    for lab in ("supported", "unsupported", "overclaim"):
        out.append(f"| {lab} | " + " | ".join(str(sum(i["label"] == lab and r["overclaim"] == v for i, r in ok))
                                            for v in (0, 1, 2)) + " |")
    nc = [(i, r) for i, r in ok if i.get("numeric") is not None]
    out += ["", f"needs_calc agrees with numeric flag: {sum(bool(i['numeric']) == r['needs_calc'] for i, r in nc)}/{len(nc)}",
            "", "## Parse failures", ""]
    pf = [r for r in rows if r["parse_failure"]]
    out += [f"- {r['claim_id']}: {r['parse_failure']}" for r in pf] or ["none"]
    lat = sorted(r["latency_s"] for r in rows)
    tin = sum(r["tokens_in"] or 0 for r in rows)
    tout = sum(r["tokens_out"] or 0 for r in rows)
    out += ["", "## Latency and tokens", "",
            f"- latency s: median {f(lat[len(lat) // 2] if lat else None, 2)}, max {f(lat[-1] if lat else None, 2)}",
            f"- tokens in {tin}, out {tout}", "", "## Splits (items sharing a quote share a split)", "",
            "| split | n | accuracy |", "|---|---|---|"]
    sp = defaultdict(list)
    for i, r in ok:
        sp[split_of(i["quote"])].append((i, r))
    for s in sorted(sp):
        out.append(f"| {s} | {len(sp[s])} | {f(_stats(sp[s])['acc'])} |")
    out += ["", "## Per item", "", "| id | label | labelled_by | supported | p | overclaim | needs_calc | parse |",
            "|---|---|---|---|---|---|---|---|"]
    for i, r in pairs_all:
        out.append(f"| {i['id']} | {i['label']} | {i['labelled_by']} | {r['supported']} | {r['p']} | {r['overclaim']} | "
                   f"{r['needs_calc']} | {r['parse_failure'] or 'ok'} |")
    return "\n".join(out) + "\n"


def _key() -> str:
    doc = json.loads(Path.home().joinpath(".claude.json").read_text())
    return doc["mcpServers"]["hearth"]["headers"]["X-Hearth-Key"]


def _body(res: dict) -> dict:
    if res.get("structured") is not None:
        s = res["structured"]
        return s.get("result", s) if isinstance(s, dict) else s
    return json.loads(res.get("text") or "null")


async def _run(items, backend, author_backend, rubric, task_id, concurrency):
    from hearth.callers.client import HearthClient
    sem = asyncio.Semaphore(concurrency)
    async with HearthClient(key=_key(), task_id=task_id) as client:
        loop = asyncio.get_running_loop()

        async def one(item):
            async with sem:
                def gen(**kw):
                    res = asyncio.run_coroutine_threadsafe(client.call("local_generate", **kw), loop).result()
                    body = _body(res)
                    return body if isinstance(body, dict) else {"ok": False, "error": "non-object reply", "text": res.get("text")}
                claim = {"claim_id": item["id"], "claim": item["claim"], "quote": item["quote"]}
                return (await asyncio.to_thread(judge_local, [claim], rubric, backend, author_backend, gen, task_id))[0]
        return await asyncio.gather(*[one(i) for i in items])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibration", default=str(DEFAULT_CALIBRATION))
    ap.add_argument("--backend", default="omen-vllm")
    ap.add_argument("--author-backend", default="omen-dense-27b")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--task-id", default=None)
    a = ap.parse_args(argv)
    if a.backend == a.author_backend:
        raise AuthorJudgeError(f"judge backend {a.backend!r} is the author backend")
    items = [json.loads(l) for l in Path(a.calibration).read_text().splitlines() if l.strip()]
    if a.limit:
        items = items[:a.limit]
    rows = asyncio.run(_run(items, a.backend, a.author_backend, LABEL_GUIDE.read_text(), a.task_id, a.concurrency))
    Path(a.out).write_text(reliability_table(items, rows))
    Path(a.out).with_suffix(".rows.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
