import json, sys, time, urllib.request

port = sys.argv[1]
prompt_file = sys.argv[2]
n_predict = int(sys.argv[3]) if len(sys.argv) > 3 else 8

with open(prompt_file, "r", encoding="utf-8") as f:
    prompt = f.read()

body = json.dumps({
    "prompt": prompt,
    "n_predict": n_predict,
    "cache_prompt": True,
    "id_slot": 0,
    "temperature": 0,
}).encode("utf-8")

req = urllib.request.Request(
    f"http://127.0.0.1:{port}/completion",
    data=body,
    headers={"Content-Type": "application/json"},
)
t0 = time.perf_counter()
with urllib.request.urlopen(req, timeout=600) as resp:
    r = json.load(resp)
wall = time.perf_counter() - t0
t = r.get("timings", {})
print(json.dumps({
    "wall_s": round(wall, 2),
    "prompt_n": t.get("prompt_n"),
    "prompt_ms": round(t.get("prompt_ms", 0)),
    "prefill_tps": round(t.get("prompt_per_second", 0), 1),
    "decode_tps": round(t.get("predicted_per_second", 0), 1),
    "tokens_cached": r.get("tokens_cached"),
    "content_head": (r.get("content") or "")[:60],
}))
