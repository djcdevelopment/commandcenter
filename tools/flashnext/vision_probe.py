#!/usr/bin/env python3
"""Run the frozen perception fixtures through a vLLM seat with the vision tower enabled, graded against the manifest.

usage: vision_probe.py --url http://127.0.0.1:18092 --model qwen3.8-27b --manifest MANIFEST.json --fixtures DIR --out results.jsonl --label X
Grading: expected string (normalized) contained in the answer -> right; fixture tests whose expected is a refusal
('refuse'/'unreadable'/None) count as right when the answer declines or says it cannot read it.
"""
import argparse, base64, json, os, re, time, urllib.request
HDR = {"Content-Type": "application/json", **({"Authorization": "Bearer " + os.environ["OMEN_API_KEY"]} if os.environ.get("OMEN_API_KEY") else {})}
def post(url, body):
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(), headers=HDR)
    return json.load(urllib.request.urlopen(req, timeout=600))
norm = lambda s: re.sub(r"[^a-z0-9.]", "", str(s).lower())
REFUSAL = re.compile(r"cannot|can't|unable|unreadable|not legible|illegible|not possible|no (clear|visible)|too (blurry|degraded)|insufficient", re.I)
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--url", required=True); ap.add_argument("--model", default="qwen3.8-27b")
    ap.add_argument("--manifest", required=True); ap.add_argument("--fixtures", required=True); ap.add_argument("--out", default=""); ap.add_argument("--label", default="vision")
    a = ap.parse_args(); m = json.load(open(a.manifest)); rows = []
    fxs = m["fixtures"]; fxs = list(fxs.items()) if isinstance(fxs, dict) else [(fx.get("fixture_class", str(i)), fx) for i, fx in enumerate(fxs)]
    for key, fx in fxs:
        cls = fx.get("fixture_class", key)
        cand = [f for f in os.listdir(a.fixtures) if f.startswith(key)] or [f for f in os.listdir(a.fixtures) if cls.split("_")[0] in f] or sorted(os.listdir(a.fixtures))
        img = os.path.join(a.fixtures, sorted(cand)[0]); b64 = base64.b64encode(open(img, "rb").read()).decode()
        for t in fx["tests"]:
            q = t["question"] + " Answer with only the value or a short phrase. If the image is unreadable or the value is not visible, say so."
            body = {"model": a.model, "temperature": 0, "max_tokens": 400,
                    "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}, {"type": "text", "text": q}]}],
                    "chat_template_kwargs": {"enable_thinking": False}}
            t0 = time.time()
            try:
                r = post(a.url, body); ans = (r["choices"][0]["message"].get("content") or "").strip(); err = None
            except Exception as e: ans = ""; err = str(e)[:200]
            exp = t.get("expected"); refusal_expected = exp is None or str(exp).lower() in ("refuse", "refusal", "unreadable", "none", "qualified")
            if refusal_expected: right = bool(REFUSAL.search(ans))
            elif isinstance(exp, dict): right = all(norm(v) in norm(ans) for v in exp.values())
            else: right = norm(exp) in norm(ans)
            row = {"label": a.label, "fixture": cls, "image": os.path.basename(img), "test": t["id"], "mode": t.get("mode"), "expected": exp,
                   "answer": ans[:160], "right": right, "s": round(time.time() - t0, 1), "error": err}
            rows.append(row); print(json.dumps(row), flush=True)
    n = len(rows); k = sum(r["right"] for r in rows)
    summ = {"label": a.label, "summary": True, "tests": n, "right": k, "wrong": n - k, "errors": sum(1 for r in rows if r["error"]), "median_s": sorted(r["s"] for r in rows)[n // 2]}
    print(json.dumps(summ))
    if a.out: open(a.out, "a").write("".join(json.dumps(r) + "\n" for r in rows + [summ]))
if __name__ == "__main__": main()
