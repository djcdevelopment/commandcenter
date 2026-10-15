#!/usr/bin/env python3
"""Engine A/B on identical prompt shapes through the OpenAI-compatible /v1/completions on both engines.

One streamed request per run: TTFT = time to the first streamed token (prefill), decode = (gen-1)/(wall-TTFT).
Prompts are sized with llama-server's /tokenize (same Qwen3.8 tokenizer on both) and each run uses a fresh
offset so no prefix cache can hit.
usage: bench_engine.py --engine vllm|llama --url http://127.0.0.1:18091 --tok-url http://127.0.0.1:8001 --model qwen3.8-27b
       --source FILE [--source FILE] --cells 512:3,8192:2,30000:1 --out results.jsonl --label X
"""
import argparse, json, time, urllib.request

import os
HDR = {"Content-Type": "application/json", **({"Authorization": "Bearer " + os.environ["OMEN_API_KEY"]} if os.environ.get("OMEN_API_KEY") else {})}
def post(url, path, body, timeout=1800):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(), headers=HDR)
    return json.load(urllib.request.urlopen(req, timeout=timeout))

def stream_complete(url, model, prompt, n):
    body = {"model": model, "prompt": prompt, "max_tokens": n, "temperature": 0, "ignore_eos": True, "stream": True}
    req = urllib.request.Request(url + "/v1/completions", data=json.dumps(body).encode(), headers=HDR)
    t0 = time.time(); ttft = None; ntok = 0
    with urllib.request.urlopen(req, timeout=1800) as r:
        for line in r:
            if not line.startswith(b"data:"): continue
            payload = line[5:].strip()
            if payload == b"[DONE]": break
            d = json.loads(payload)
            ch = d.get("choices", [{}])[0]
            if ch.get("text"):
                if ttft is None: ttft = time.time() - t0
                ntok += 1
    return ttft, time.time() - t0, ntok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True); ap.add_argument("--url", required=True); ap.add_argument("--tok-url", required=True)
    ap.add_argument("--model", default="x"); ap.add_argument("--source", action="append", required=True)
    ap.add_argument("--cells", default="512:3,8192:2,30000:1"); ap.add_argument("--gen", type=int, default=128)
    ap.add_argument("--out", default=""); ap.add_argument("--label", default="")
    a = ap.parse_args()
    text = "".join(open(s, errors="ignore").read() + "\n\n" for s in a.source)
    vtok = a.tok_url.rstrip("/").endswith(("18091", "18092", "18095", "18096"))  # vLLM tokenizer shape
    toks = post(a.tok_url, "/tokenize", {"model": a.model, "prompt": text} if vtok else {"content": text})["tokens"]
    for cell in a.cells.split(","):
        ntok, runs = (int(x) for x in cell.split(":"))
        for run in range(runs):
            off = run * 35000
            if off + ntok > len(toks): raise SystemExit(f"source too short for cell {ntok} run {run} ({len(toks)} tokens)")
            seg = toks[off: off + ntok - 20]
            p = (post(a.tok_url, "/detokenize", {"model": a.model, "tokens": seg})["prompt"] if vtok else post(a.tok_url, "/detokenize", {"tokens": seg})["content"]) + "\n\nSummarize the text above in three sentences."
            ttft, wall, gn = stream_complete(a.url, a.model, p, a.gen)
            row = {"label": a.label, "engine": a.engine, "cell": ntok, "run": run, "prompt_tokens_target": ntok,
                   "ttft_s": round(ttft, 2), "prefill_tps": round(ntok / ttft, 1), "gen_chunks": gn, "wall_s": round(wall, 2),
                   "decode_tps": round((a.gen - 1) / max(wall - ttft, 1e-6), 2)}
            print(json.dumps(row), flush=True)
            if a.out: open(a.out, "a").write(json.dumps(row) + "\n")

if __name__ == "__main__": main()
