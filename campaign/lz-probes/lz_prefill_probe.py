"""LZ prefill probe: unique-prefix prompt of ~N tokens -> /completion, cache_prompt off.

Usage: lz_prefill_probe.py <port> <label> <approx_tokens> [n_predict=8] [reps=1]
Builds the prompt from kit/probe-prompt.txt sliced to ~4 chars/token, with a unique
random prefix per rep so the server's prompt cache can never fake a prefill number.
Prints one JSON row per rep (server-internal timings only).
"""
import json
import pathlib
import sys
import urllib.request
import uuid

port = int(sys.argv[1])
label = sys.argv[2]
approx_tokens = int(sys.argv[3])
n_predict = int(sys.argv[4]) if len(sys.argv) > 4 else 8
reps = int(sys.argv[5]) if len(sys.argv) > 5 else 1

corpus = (pathlib.Path(__file__).parent / "kit" / "probe-prompt.txt").read_text(
    encoding="utf-8", errors="replace")

for rep in range(1, reps + 1):
    prefix = f"[probe {uuid.uuid4().hex[:12]}] "
    body_chars = max(0, approx_tokens * 4 - len(prefix))
    prompt = prefix + corpus[:body_chars]
    payload = json.dumps({
        "prompt": prompt,
        "n_predict": n_predict,
        "cache_prompt": False,
        "id_slot": 0,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/completion", data=payload,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        t = json.load(r)["timings"]
    print(json.dumps({
        "label": label, "rep": rep,
        "prompt_n": t["prompt_n"],
        "prefill_tps": round(t["prompt_per_second"], 2),
        "prefill_ms": round(t["prompt_ms"], 1),
        "decode_tps": round(t["predicted_per_second"], 2),
        "predicted_n": t["predicted_n"],
    }))
