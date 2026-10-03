"""Local judge (delivery task 8): a non-author model answers one closed question per claim, schema-constrained.

judge_local(claims, criteria, backend, author_backend, generate=...) -> [{claim_id, supported, p, overclaim, needs_calc}]
Runner: python -m hearth.delivery.judge_local --calibration items.jsonl --backend omen-vllm --out table.md --task-id ID
"""
import argparse, asyncio, hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path

LABEL_GUIDE = Path.home() / "work/delivery-plan/calibration/label-guide.md"
DEFAULT_CALIBRATION = Path.home() / "work/delivery-plan/calibration/items.jsonl"
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["supported", "p", "overclaim", "needs_calc"],
          "properties": {"supported": {"type": "boolean"},
                         "p": {"type": "number", "minimum": 0, "maximum": 1},
                         "overclaim": {"type": "integer", "enum": [0, 1, 2]},
                         "needs_calc": {"type": "boolean"}}}
# The label guide's three labels, expressed through the schema's fields (task 8; task 7's overclaim scale).
ANSWER_FORMAT = """Give your one label as one JSON object, using these fields exactly:
- label supported: "supported": true, "overclaim": 0
- label unsupported: "supported": false, "overclaim": 0
- label overclaim: "supported": false, "overclaim": 1 or 2
"overclaim" says how far the claim goes beyond the true, narrower statement the quote makes (only for the overclaim label):
  1 = the claim generalizes: a stronger scope than the quote shows.
  2 = the claim asserts a guarantee the quote does not give ("always", "ensures", "guarantees", "reliably"), or a
      conclusion the quote only hints at.
"p": your probability, from 0 to 1, that the label is supported.
"needs_calc": the numeric flag defined above (true or false)."""
KEYS = ("supported", "p", "overclaim", "needs_calc")
BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001)]
# Named failures. A door refusal or an engine cut-off is not parse noise: it is classified from the door's error_code
# when present, else from the error text the door projects (execute_sync carries only `error`, which starts with the code).
FAILURES = (("truncated", ("structured_output_truncated",)),
            ("schema_not_applied", ("structured_output_invalid",)),
            ("refused", ("structured_outputs_unsupported", "policy_refusal", "routing_refusal", "payload_over_budget")))


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


def failure_kind(res: dict) -> str:
    """The name of a not-ok generate result: truncated | schema_not_applied | refused | call_failed."""
    hay = f"{res.get('error_code') or ''} {res.get('error') or ''}".lower()
    return next((name for name, marks in FAILURES if any(m in hay for m in marks)), "call_failed")


def verdict_label(row: dict):
    """The label a parsed row states: supported | unsupported | overclaim (None when the row did not parse)."""
    if row.get("failure"):
        return None
    return "supported" if row["supported"] else "overclaim" if row["overclaim"] else "unsupported"


def judge_local(claims, criteria, backend, author_backend, generate, task_id=None, max_tokens=256) -> list[dict]:
    """claims: [{claim_id, claim, quote}]; criteria: the rubric text (or list of strings), given verbatim.
    generate(prompt=, backend=, response_schema=, temperature=, max_tokens=, task_id=) -> local_generate result dict.
    Each row: claim_id, supported, p, overclaim, needs_calc (None when failure is set), plus ok, failure
    (truncated | schema_not_applied | refused | call_failed | parse), failure_detail, consistent, raw, latency_s,
    duration_ms, tokens_in, tokens_out, backend (as the door reported it), model, routed_by, response_schema_sha256."""
    if backend == author_backend:
        raise AuthorJudgeError(f"judge backend {backend!r} is the author backend")
    rubric = criteria if isinstance(criteria, str) else "\n".join(criteria)
    rows = []
    for c in claims:
        t0 = time.monotonic()
        res = generate(prompt=prompt_for(c, rubric), backend=backend, response_schema=SCHEMA, temperature=0.0,
                       max_tokens=max_tokens, task_id=task_id)
        row = {"claim_id": c["claim_id"], "supported": None, "p": None, "overclaim": None, "needs_calc": None,
               "ok": res.get("ok") is True, "failure": None, "failure_detail": None, "consistent": None,
               "raw": res.get("text"), "latency_s": round(time.monotonic() - t0, 3),
               **{k: res.get(k) for k in ("duration_ms", "tokens_in", "tokens_out", "backend", "model", "routed_by",
                                          "response_schema_sha256")}}
        if res.get("ok") is not True:
            row["failure"], row["failure_detail"] = failure_kind(res), str(res.get("error") or res.get("error_code"))
        elif row["backend"] not in (None, backend):
            row["failure"], row["failure_detail"] = "wrong_backend", f"asked {backend!r}, door ran {row['backend']!r}"
        else:
            try:
                row.update(_parse(res.get("text")))
                row["consistent"] = not (row["supported"] and row["overclaim"])
            except (ValueError, TypeError) as exc:
                row["failure"], row["failure_detail"] = "parse", f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return rows


def split_of(quote: str) -> str:
    """Two splits by quote: items that share a quote share a split (fit thresholds on one, check on the other)."""
    return "AB"[int(hashlib.sha256(quote.encode()).hexdigest(), 16) % 2]


def _stats(pairs):
    """pairs: [(item, row)] with parsed rows only. Accuracy is supported vs not (an overclaim label is 'not');
    Brier is on p against the same outcome."""
    n = len(pairs)
    if not n:
        return {"n": 0}
    hit = sum(r["supported"] == (i["label"] == "supported") for i, r in pairs)
    brier = sum((r["p"] - (i["label"] == "supported")) ** 2 for i, r in pairs) / n
    lab = sum(verdict_label(r) == i["label"] for i, r in pairs)
    return {"n": n, "acc": hit / n, "brier": brier, "acc3": lab / n}


def reliability_table(items, rows, meta=None) -> str:
    by = {r["claim_id"]: r for r in rows}
    pairs_all = [(i, by[i["id"]]) for i in items if i["id"] in by]
    ok = [(i, r) for i, r in pairs_all if not r["failure"]]
    groups = {"all": lambda i: True, "settled": lambda i: i["labelled_by"] != "derek-pending",
              "derek-pending": lambda i: i["labelled_by"] == "derek-pending", "numeric": lambda i: bool(i.get("numeric")),
              "split A": lambda i: split_of(i["quote"]) == "A", "split B": lambda i: split_of(i["quote"]) == "B"}
    asked = {k: [(i, r) for i, r in pairs_all if g(i)] for k, g in groups.items()}
    cols = {k: [(i, r) for i, r in v if not r["failure"]] for k, v in asked.items()}
    st = {k: _stats(v) for k, v in cols.items()}
    f = lambda v, d=3: "-" if v is None else f"{v:.{d}f}"
    kinds = sorted({r["failure"] for _, r in pairs_all if r["failure"]})
    out = ["# Local judge reliability", ""]
    out += [f"- {k}: {v}" for k, v in (meta or {}).items()]
    out += ["", f"Items {len(pairs_all)}; parsed {len(ok)}; failures {len(pairs_all) - len(ok)}"
            + (" (" + ", ".join(f"{k} {sum(r['failure'] == k for _, r in pairs_all)}" for k in kinds) + ")"
               if kinds else "") + ". Failures are excluded from every metric below and listed by name.",
            "derek-pending labels are provisional; settled = every other item. Splits A/B are by quote hash.", "",
            "| metric | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols),
            "| items asked | " + " | ".join(str(len(asked[k])) for k in cols) + " |",
            "| parsed (n) | " + " | ".join(str(st[k]["n"]) for k in cols) + " |",
            "| accuracy, supported vs not | " + " | ".join(f(st[k].get("acc")) for k in cols) + " |",
            "| Brier (p vs label = supported) | " + " | ".join(f(st[k].get("brier")) for k in cols) + " |",
            "| three-label agreement | " + " | ".join(f(st[k].get("acc3")) for k in cols) + " |", "",
            "## Observed supported rate per predicted p bin (all parsed)", "",
            "| bin | n | observed supported rate | mean p |", "|---|---|---|---|"]
    for lo, hi in BINS:
        b = [(i, r) for i, r in ok if lo <= r["p"] < hi]
        out.append(f"| {lo:.1f}-{min(hi, 1.0):.1f} | {len(b)} | "
                   f"{f(sum(i['label'] == 'supported' for i, _ in b) / len(b) if b else None)} | "
                   f"{f(sum(r['p'] for _, r in b) / len(b) if b else None)} |")
    out += ["", "## Label (rows) by stated label (columns)", "",
            "| label | supported | unsupported | overclaim |", "|---|---|---|---|"]
    for lab in ("supported", "unsupported", "overclaim"):
        out.append(f"| {lab} | " + " | ".join(str(sum(i["label"] == lab and verdict_label(r) == v for i, r in ok))
                                            for v in ("supported", "unsupported", "overclaim")) + " |")
    out += ["", "## Overclaim: label (rows) by predicted overclaim 0/1/2 (columns)", "",
            "| label | 0 | 1 | 2 |", "|---|---|---|---|"]
    for lab in ("supported", "unsupported", "overclaim"):
        out.append(f"| {lab} | " + " | ".join(str(sum(i["label"] == lab and r["overclaim"] == v for i, r in ok))
                                            for v in (0, 1, 2)) + " |")
    nc = [(i, r) for i, r in ok if i.get("numeric") is not None]
    out += ["", f"Inconsistent rows (supported true with overclaim > 0; scored on supported): "
                f"{sum(r['consistent'] is False for _, r in ok)}",
            f"needs_calc agrees with numeric flag: {sum(bool(i['numeric']) == r['needs_calc'] for i, r in nc)}/{len(nc)}",
            "", "## Failures", ""]
    pf = [r for _, r in pairs_all if r["failure"]]
    out += [f"- {r['claim_id']}: {r['failure']}: {r['failure_detail']}" for r in pf] or ["none"]
    lat = sorted(r["latency_s"] for _, r in pairs_all if r["latency_s"] is not None)
    tin = sum(r["tokens_in"] or 0 for _, r in pairs_all)
    tout = sum(r["tokens_out"] or 0 for _, r in pairs_all)
    out += ["", "## Latency and tokens", "",
            f"- latency s (caller wall): median {f(lat[len(lat) // 2] if lat else None, 2)}, "
            f"max {f(lat[-1] if lat else None, 2)}",
            f"- tokens in {tin}, out {tout} (parsed calls only report tokens)",
            f"- backends reported: {sorted({str(r['backend']) for _, r in pairs_all})}; "
            f"models: {sorted({str(r['model']) for _, r in pairs_all})}",
            "", "## Per item", "",
            "| id | label | labelled_by | split | supported | p | overclaim | needs_calc | stated | failure |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in pairs_all:
        out.append(f"| {i['id']} | {i['label']} | {i['labelled_by']} | {split_of(i['quote'])} | {r['supported']} | "
                   f"{r['p']} | {r['overclaim']} | {r['needs_calc']} | {verdict_label(r)} | {r['failure'] or '-'} |")
    return "\n".join(out) + "\n"


def _key() -> str:
    doc = json.loads(Path.home().joinpath(".claude.json").read_text())
    return doc["mcpServers"]["hearth"]["headers"]["X-Hearth-Key"]


def _door_result(res: dict) -> dict:
    """HearthClient.call -> the local_generate result dict. A tool error (isError: the door raised) is a named
    not-ok result, never a JSON decode of the error text."""
    if not res.get("ok"):
        return {"ok": False, "error": f"tool_error: {res.get('text')}"}
    s = res.get("structured")
    body = (s.get("result", s) if isinstance(s, dict) else s) if s is not None else json.loads(res.get("text") or "null")
    if not isinstance(body, dict):
        return {"ok": False, "error": f"non-object tool result: {type(body).__name__}"}
    return body


async def _run(items, backend, author_backend, rubric, task_id, concurrency, rows_path, client=None):
    sem = asyncio.Semaphore(concurrency)

    async def one(item):
        claim = {"claim_id": item["id"], "claim": item["claim"], "quote": item["quote"]}
        async with sem:
            def gen(**kw):
                return _door_result(asyncio.run_coroutine_threadsafe(client.call("local_generate", **kw), loop).result())
            try:
                row = (await asyncio.to_thread(judge_local, [claim], rubric, backend, author_backend, gen, task_id))[0]
            except Exception as exc:  # noqa: BLE001 -- recorded as a named failure row; the others' rows survive
                row = {"claim_id": item["id"], "supported": None, "p": None, "overclaim": None, "needs_calc": None,
                       "ok": False, "failure": "transport", "failure_detail": f"{type(exc).__name__}: {exc}",
                       "consistent": None, "raw": None, "latency_s": None, "duration_ms": None, "tokens_in": None,
                       "tokens_out": None, "backend": None, "model": None, "routed_by": None,
                       "response_schema_sha256": None}
        with rows_path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        return row

    if client is None:
        from hearth.callers.client import HearthClient
        async with HearthClient(key=_key(), task_id=task_id) as door:
            return await _run(items, backend, author_backend, rubric, task_id, concurrency, rows_path, door)
    loop = asyncio.get_running_loop()
    await _require_temperature(client)
    return await asyncio.gather(*[one(i) for i in items])


async def _require_temperature(client):
    """The door's tool layer drops an unknown argument silently, so temperature=0.0 must be a declared parameter."""
    tool = next((t for t in await client.list_tools() if t["name"] == "local_generate"), None)
    if tool is None or "temperature" not in (tool["input_schema"].get("properties") or {}):
        raise JudgeError("the door's local_generate does not declare temperature; temperature=0.0 would be dropped")


def main(argv=None, client=None) -> int:
    from hearth.toolsurface.inference import TASK_ID_PATTERN, response_schema_digest
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibration", default=str(DEFAULT_CALIBRATION))
    ap.add_argument("--backend", default="omen-vllm")
    ap.add_argument("--author-backend", default="omen-dense-27b")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--task-id", required=True)
    a = ap.parse_args(argv)
    if a.backend == a.author_backend:
        raise AuthorJudgeError(f"judge backend {a.backend!r} is the author backend")
    if not TASK_ID_PATTERN.match(a.task_id):
        raise JudgeError("task_id must be 1-128 characters of [A-Za-z0-9._:-]")
    cal = Path(a.calibration).read_bytes()
    items = [json.loads(l) for l in cal.decode().splitlines() if l.strip()]
    if a.limit:
        items = items[:a.limit]
    guide = LABEL_GUIDE.read_text()
    out = Path(a.out)
    rows_path = out.with_suffix(".rows.jsonl")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows_path.write_text("")
    rows = asyncio.run(_run(items, a.backend, a.author_backend, guide, a.task_id, a.concurrency, rows_path, client))
    meta = {"started": started, "judge backend": a.backend, "author backend (refused)": a.author_backend,
            "task_id": a.task_id, "temperature": 0.0, "max_tokens": 256, "concurrency": a.concurrency,
            "response_schema_sha256": response_schema_digest(SCHEMA),
            "calibration": f"{a.calibration} sha256 {hashlib.sha256(cal).hexdigest()[:16]}",
            "label guide sha256": hashlib.sha256(guide.encode()).hexdigest()[:16], "rows": str(rows_path)}
    out.write_text(reliability_table(items, rows, meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
