#!/usr/bin/env python3
"""Hermes-style fixed prefix on a vLLM seat: cold vs warm TTFT, and two concurrent conversations on the same prefix.

usage: prefix_probe.py --url http://127.0.0.1:18091 --model qwen3.8-27b --tok-url http://127.0.0.1:8001 --prefix-tokens 13900 --source FILE --out results.jsonl
Needs OMEN_API_KEY in the environment for the seat. The prefix is built from real tool schemas plus lab text so it tokenizes like an agent prompt.
"""
import argparse, json, os, threading, time, urllib.request
HDR = {"Content-Type": "application/json", **({"Authorization": "Bearer " + os.environ["OMEN_API_KEY"]} if os.environ.get("OMEN_API_KEY") else {})}
def post(url, path, body):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(), headers=HDR); return json.load(urllib.request.urlopen(req, timeout=900))
def stream(url, model, prompt, n=64):
    body = {"model": model, "prompt": prompt, "max_tokens": n, "temperature": 0, "ignore_eos": True, "stream": True}
    req = urllib.request.Request(url + "/v1/completions", data=json.dumps(body).encode(), headers=HDR)
    t0 = time.time(); ttft = None
    with urllib.request.urlopen(req, timeout=900) as r:
        for line in r:
            if line.startswith(b"data:") and b"[DONE]" not in line:
                if json.loads(line[5:]).get("choices", [{}])[0].get("text") and ttft is None: ttft = time.time() - t0
    return round(ttft, 2), round(time.time() - t0, 2)
TOOLS = [{"name": n, "description": f"Tool {n}: {d}", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}, "start_line": {"type": "integer"}, "content": {"type": "string"}}, "required": ["path"]}}
         for n, d in [("read_file", "read a file"), ("write_file", "write a file"), ("grep", "search files"), ("list_dir", "list a directory"), ("run_tests", "run the suite"), ("git_status", "working tree"), ("git_diff", "show a diff"), ("web_search", "search the web"), ("submit_execution", "queue a job"), ("watch_execution", "poll a job")]]
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--url", required=True); ap.add_argument("--tok-url", required=True); ap.add_argument("--model", default="x")
    ap.add_argument("--prefix-tokens", type=int, default=13900); ap.add_argument("--source", required=True); ap.add_argument("--out", default=""); ap.add_argument("--label", default="hermes-prefix")
    a = ap.parse_args()
    schema_text = "SYSTEM: You are an agent. Tools:\n" + json.dumps(TOOLS * 6, indent=1) + "\nRules:\n"
    text = open(a.source, errors="ignore").read()
    toks = post(a.tok_url, "/tokenize", {"model": a.model, "prompt": schema_text + text})["tokens"]
    prefix = post(a.tok_url, "/detokenize", {"model": a.model, "tokens": toks[: a.prefix_tokens]})["prompt"] + "\n\nUSER: "
    qs = ["Which tool would you call first to find where 'archive' is defined, and why? One sentence.",
          "List the three tools you would use to run and inspect the test suite. One line.",
          "What is the safest tool for checking the working tree before editing? One sentence."]
    rows = []
    def rec(kind, ttft, wall, **kw): r = {"label": a.label, "kind": kind, "prefix_tokens": a.prefix_tokens, "ttft_s": ttft, "wall_s": wall, **kw}; rows.append(r); print(json.dumps(r), flush=True)
    rec("cold", *stream(a.url, a.model, prefix + qs[0]))
    rec("warm", *stream(a.url, a.model, prefix + qs[1]))
    rec("warm2", *stream(a.url, a.model, prefix + qs[2]))
    res = [None, None]
    def w(i): res[i] = stream(a.url, a.model, prefix + qs[i] + " Also mention the repo name.")
    th = [threading.Thread(target=w, args=(i,)) for i in range(2)]; t0 = time.time(); [t.start() for t in th]; [t.join() for t in th]
    rec("two-concurrent", [r[0] for r in res], [r[1] for r in res], both_wall=round(time.time() - t0, 2))
    if a.out: open(a.out, "a").write("".join(json.dumps(r) + "\n" for r in rows))
if __name__ == "__main__": main()
