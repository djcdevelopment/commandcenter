"""LZ8 greedy probe: fixed prompt, temp 0, cache off -> prints JSON {content, timings}.

Usage: lz8_greedy.py <port> <label> [n_predict=64]
The prompt is the canonical kit canary prompt (probe_openai.py lineage) so outputs are
byte-comparable across expert-placement configs.
"""
import json
import sys
import urllib.request

port = int(sys.argv[1])
label = sys.argv[2]
n_predict = int(sys.argv[3]) if len(sys.argv) > 3 else 64

PROMPT = ("Write a precise two-paragraph technical explanation of why prefix caching "
          "reduces time to first token in LLM serving.")

payload = json.dumps({
    "prompt": PROMPT,
    "n_predict": n_predict,
    "cache_prompt": False,
    "id_slot": 0,
    "temperature": 0,
}).encode()
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/completion", data=payload,
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=1800) as r:
    j = json.load(r)
t = j["timings"]
print(json.dumps({
    "label": label,
    "prompt_n": t["prompt_n"],
    "prefill_tps": round(t["prompt_per_second"], 2),
    "decode_tps": round(t["predicted_per_second"], 2),
    "predicted_n": t["predicted_n"],
    "content": j["content"],
}))
