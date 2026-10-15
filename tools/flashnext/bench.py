#!/usr/bin/env python3
"""Measure prefill and decode on a running llama-server (cookbook cell shape: pN / g128, n runs).

usage: bench.py --port 8001 --prompt-tokens 512 --gen 128 --runs 3 [--source FILE ...] [--label X]
Prints one JSON row per run and a median summary. Uses /tokenize to size the prompt, /completion
with cache_prompt=false so every run is a cold prefill. Reads timings from the server, not wall clock.
"""
import argparse, json, statistics, sys, time, urllib.request

def post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r:
        return json.load(r)

def build_prompt(port, sources, target):
    text = ""
    for s in sources:
        text += open(s, errors="ignore").read() + "\n\n"
    if target <= 64:
        return "Reply with one short sentence: what is the capital of France?"
    # trim to target tokens by tokenizing once and cutting the text proportionally, then exact-fit
    toks = post(port, "/tokenize", {"content": text})["tokens"]
    if len(toks) < target:
        sys.exit(f"source text has {len(toks)} tokens, need {target}")
    cut = post(port, "/detokenize", {"tokens": toks[: target - 24]})["content"]
    return cut + "\n\nSummarize the text above in three sentences."

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--prompt-tokens", type=int, default=512)
    ap.add_argument("--gen", type=int, default=128)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--source", action="append", default=[])
    ap.add_argument("--label", default="")
    ap.add_argument("--spec", choices=["off", "on"], default="off", help="per-request MTP draft: off sets speculative.n_max=0")
    ap.add_argument("--out", default="", help="append rows to this jsonl file")
    a = ap.parse_args()
    spec = {"speculative.n_max": 0} if a.spec == "off" else {"speculative.n_max": 3, "speculative.n_min": 0, "speculative.p_min": 0.75}
    prompt = build_prompt(a.port, a.source, a.prompt_tokens)
    rows = []
    for i in range(a.warmup + a.runs):
        t0 = time.time()
        r = post(a.port, "/completion", {"prompt": prompt, "n_predict": a.gen, "cache_prompt": False,
                                         "temperature": 0, "stream": False, **spec})
        wall = time.time() - t0
        t = r["timings"]
        row = {"label": a.label, "run": i - a.warmup, "warmup": i < a.warmup,
               "prompt_n": t["prompt_n"], "prefill_tps": round(t["prompt_per_second"], 2),
               "ttft_s": round(t["prompt_ms"] / 1000, 2),
               "gen_n": t["predicted_n"], "decode_tps": round(t["predicted_per_second"], 2),
               "wall_s": round(wall, 1), "text_head": r["content"][:80].replace("\n", " ")}
        for k in ("draft_n", "draft_n_accepted"):
            if k in t: row[k] = t[k]
        print(json.dumps(row), flush=True)
        if a.out: open(a.out, "a").write(json.dumps(row) + "\n")
        if i >= a.warmup: rows.append(row)
    dec = [r["decode_tps"] for r in rows]; pre = [r["prefill_tps"] for r in rows]
    summ = {"label": a.label, "summary": True, "spec": a.spec, "n": len(rows), "prompt_n": rows[0]["prompt_n"],
                      "decode_median": statistics.median(dec), "decode_min": min(dec), "decode_max": max(dec),
                      "prefill_median": statistics.median(pre), "prefill_min": min(pre), "prefill_max": max(pre)}
    print(json.dumps(summ))
    if a.out: open(a.out, "a").write(json.dumps(summ) + "\n")

if __name__ == "__main__":
    main()
