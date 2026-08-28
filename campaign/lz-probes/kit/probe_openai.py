import json, sys, time, urllib.request

port, model = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 64

payload = {
    "model": model,
    "prompt": "Write a precise two-paragraph technical explanation of why prefix caching reduces time to first token in LLM serving.",
    "max_tokens": n,
    "temperature": 0,
}
if len(sys.argv) > 4 and sys.argv[4] == "ignore_eos":
    payload["ignore_eos"] = True
body = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/v1/completions",
    data=body,
    headers={"Content-Type": "application/json"},
)
t0 = time.perf_counter()
with urllib.request.urlopen(req, timeout=600) as resp:
    r = json.load(resp)
wall = time.perf_counter() - t0
t = r.get("timings", {}) or {}
print(json.dumps({
    "model": model,
    "wall_s": round(wall, 2),
    "decode_tps": round(t.get("predicted_per_second", 0), 1),
    "prefill_tps": round(t.get("prompt_per_second", 0), 1),
    "tokens": r.get("usage", {}).get("completion_tokens"),
}))
