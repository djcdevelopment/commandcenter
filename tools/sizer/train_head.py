"""Lap N2: can a linear head over the MiniLM embedding beat the heuristic at binning output size?

    ~/.venvs/npu/bin/python tools/sizer/train_head.py [--out ~/work/npu-sizer-20260928] [--ir ~/models/minilm-ov/int8]
        [--since 2026-09-23] [--device CPU]

Data: the same ledger join tools/sizer/replay.py uses (request.accepted / job.dispatched /
invocation.succeeded on job_id, prompt by sha), capped rows dropped, label = bin_for_tokens(tokens_out).
Held-out folds are whole `task_id` batches (rows without one are grouped by system-prompt hash), never
rows, so a campaign cannot leak into its own test. The out-of-campaign set is the DeepAgents chore
runs under ~/hearth-production/var/experiments/deepagents (spec task text + result.json word count).
Model: softmax regression over [384-d embedding ++ 5 numeric features], L2, full-batch gradient descent,
numpy only. Writes results.json, folds/, and head.npz ONLY if the head beats the heuristic on the
held-out folds; the service loads head.npz when present.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hearth.sizer.heuristic import BIN_ORDER, LONG_BINS, bin_for_tokens, size_heuristic  # noqa: E402
from tools.sizer.replay import DEFAULT_ARTIFACTS, DEFAULT_EVENTS, load_jobs, read_prompt  # noqa: E402
from tools.sizer.serve import Encoder, numeric_features  # noqa: E402

TOKENS_PER_WORD = 1.4


def instruction(prompt: str) -> str:
    return (prompt.rsplit("</file>", 1)[1] if "</file>" in prompt else prompt)[:4000]


def deepagents_rows(root: Path) -> list[dict]:
    rows = []
    for spec_path in sorted(root.glob("*/spec.json")):
        try:
            spec = json.loads(spec_path.read_text()); state = json.loads((spec_path.parent / "state.json").read_text())
        except (OSError, ValueError):
            continue
        run_dir = state.get("run_dir")
        if state.get("outcome") != "succeeded" or not run_dir:
            continue
        try:
            rj = json.loads((Path(run_dir) / "result.json").read_text())
        except (OSError, ValueError):
            continue
        words = rj.get("report_word_count")
        if not isinstance(words, int) or words <= 0:
            continue
        src = spec.get("source") or ""
        try:
            size = Path(src).stat().st_size
        except OSError:
            size = 0
        text = spec["task"] if not spec.get("max_report_words") else f"max_report_words: {spec['max_report_words']}\n{spec['task']}"
        rows.append({"job_id": spec["id"], "group": "deepagents:" + hashlib.sha1(spec["task"].encode()).hexdigest()[:8],
                     "text": text, "files": [{"path": src, "bytes": size}], "payload_bytes": size + len(spec["task"].encode()),
                     "family": "tool_execution", "tokens_out": int(words * TOKENS_PER_WORD), "set": "deepagents"})
    return rows


def softmax_fit(X, y, k, l2=1e-3, steps=600, lr=0.5):
    import numpy as np
    n, d = X.shape
    W = np.zeros((d, k), dtype=np.float64); b = np.zeros(k, dtype=np.float64)
    Y = np.eye(k)[y]
    # Class weights: the corpus is 80 % xs; without weights the head learns the prior.
    cw = n / (k * np.maximum(np.bincount(y, minlength=k), 1))
    w = cw[y][:, None]
    for _ in range(steps):
        z = X @ W + b; z -= z.max(1, keepdims=True); p = np.exp(z); p /= p.sum(1, keepdims=True)
        g = (p - Y) * w / n
        W -= lr * (X.T @ g + l2 * W); b -= lr * g.sum(0)
    return W, b


def predict(X, W, b):
    import numpy as np
    z = X @ W + b
    return z.argmax(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path.home() / "work" / "npu-sizer-20260928")
    ap.add_argument("--ir", type=Path, default=Path.home() / "models" / "minilm-ov" / "int8")
    ap.add_argument("--device", default="CPU")
    ap.add_argument("--since", default="2026-09-23")
    ap.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    ap.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    ap.add_argument("--deepagents", type=Path, default=Path.home() / "hearth-production" / "var" / "experiments" / "deepagents")
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()
    import numpy as np

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "registration.json").write_text(json.dumps({
        "schema": "npu-sizer-head.v1", "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hypothesis": "a linear head over MiniLM-L6 [1,256] embeddings + the heuristic's numeric features bins tokens_out better than the heuristic on held-out task_id batches, and lifts l/xl recall on the out-of-campaign DeepAgents chores",
        "labels": "bin_for_tokens(tokens_out), capped rows excluded", "split": "whole task_id groups", "ir": str(args.ir),
        "decision_rule": "head.npz is written only if held-out exact accuracy beats the heuristic's; otherwise heuristic-only"}, indent=2))

    jobs = load_jobs(args.events, args.since)
    rows = []
    for job in jobs.values():
        prompt = read_prompt(args.artifacts, job["input"]); res = job["result"]
        if prompt is None or not isinstance(res.get("tokens_out"), int):
            continue
        cap = res.get("max_tokens") or job["policy"].get("max_tokens")
        if isinstance(cap, int) and res["tokens_out"] >= cap:
            continue
        a = job["arguments"]
        group = a.get("task_id") or ("system:" + hashlib.sha1((a.get("system") or "").encode()).hexdigest()[:8])
        rows.append({"job_id": job["job_id"], "group": str(group), "text": instruction(prompt), "files": job["packed_files"],
                     "payload_bytes": job["input"].get("size") or len(prompt.encode()), "family": a.get("task_family"),
                     "tokens_out": res["tokens_out"], "set": "ledger"})
    ooc = deepagents_rows(args.deepagents)
    print(f"ledger rows {len(rows)} in {len({r['group'] for r in rows})} groups; deepagents rows {len(ooc)} in {len({r['group'] for r in ooc})} groups", flush=True)

    enc = Encoder(args.ir, args.device, None, Path.home() / ".cache" / "ov_npu")
    t0 = time.perf_counter()

    def featurize(r):
        h = size_heuristic(r["text"], files=r["files"], payload_bytes=r["payload_bytes"], task_family=r["family"])
        emb = enc.embed(r["text"])
        return np.concatenate([emb, np.asarray(numeric_features(h), dtype=np.float32)]), BIN_ORDER.index(h["output_class"])
    for r in rows + ooc:
        r["x"], r["heur"] = featurize(r); r["y"] = BIN_ORDER.index(bin_for_tokens(r["tokens_out"]))
    embed_s = round(time.perf_counter() - t0, 1)

    X = np.stack([r["x"] for r in rows]); y = np.array([r["y"] for r in rows]); heur = np.array([r["heur"] for r in rows])
    groups = sorted({r["group"] for r in rows}); rng = np.random.default_rng(20260928); rng.shuffle(groups)
    fold_of = {g: i % args.folds for i, g in enumerate(groups)}
    fold_id = np.array([fold_of[r["group"]] for r in rows])
    k = len(BIN_ORDER)
    results = {"rows": len(rows), "groups": len(groups), "embed_s": embed_s, "folds": []}
    pred = np.zeros_like(y)
    (args.out / "folds").mkdir(exist_ok=True)
    for f in range(args.folds):
        tr, te = fold_id != f, fold_id == f
        if te.sum() == 0:
            continue
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6
        W, b = softmax_fit((X[tr] - mu) / sd, y[tr], k)
        p = predict((X[te] - mu) / sd, W, b); pred[te] = p
        fr = {"fold": f, "test_rows": int(te.sum()), "head_exact": round(float((p == y[te]).mean()), 4),
              "heuristic_exact": round(float((heur[te] == y[te]).mean()), 4)}
        results["folds"].append(fr)
        (args.out / "folds" / f"fold{f}.json").write_text(json.dumps(fr, indent=2))
    results["heldout"] = {"head_exact": round(float((pred == y).mean()), 4), "heuristic_exact": round(float((heur == y).mean()), 4),
                          "head_within1": round(float((np.abs(pred - y) <= 1).mean()), 4), "heuristic_within1": round(float((np.abs(heur - y) <= 1).mean()), 4),
                          "head_under": round(float((pred < y).mean()), 4), "heuristic_under": round(float((heur < y).mean()), 4),
                          "true_bins": dict(Counter(BIN_ORDER[i] for i in y)), "head_pred_bins": dict(Counter(BIN_ORDER[i] for i in pred)),
                          "heuristic_pred_bins": dict(Counter(BIN_ORDER[i] for i in heur))}
    # Fit on everything for the out-of-campaign test and the saved head.
    mu, sd = X.mean(0), X.std(0) + 1e-6
    W, b = softmax_fit((X - mu) / sd, y, k)
    if ooc:
        Xo = np.stack([r["x"] for r in ooc]); yo = np.array([r["y"] for r in ooc]); ho = np.array([r["heur"] for r in ooc])
        po = predict((Xo - mu) / sd, W, b)
        long_true = np.isin(yo, [BIN_ORDER.index(x) for x in LONG_BINS])
        results["out_of_campaign"] = {
            "rows": len(ooc), "true_bins": dict(Counter(BIN_ORDER[i] for i in yo)),
            "head_exact": round(float((po == yo).mean()), 4), "heuristic_exact": round(float((ho == yo).mean()), 4),
            "head_within1": round(float((np.abs(po - yo) <= 1).mean()), 4), "heuristic_within1": round(float((np.abs(ho - yo) <= 1).mean()), 4),
            "long_true": int(long_true.sum()),
            "head_long_recall": f"{int((np.isin(po, [3, 4]) & long_true).sum())}/{int(long_true.sum())}",
            "heuristic_long_recall": f"{int((np.isin(ho, [3, 4]) & long_true).sum())}/{int(long_true.sum())}",
            "per_row": [{"job": r["job_id"], "true": BIN_ORDER[r["y"]], "heur": BIN_ORDER[r["heur"]], "head": BIN_ORDER[int(p_)], "words_est": r["tokens_out"]}
                        for r, p_ in zip(ooc, po)],
        }
    beats = results["heldout"]["head_exact"] > results["heldout"]["heuristic_exact"]
    results["decision"] = "head.npz written: head beats the heuristic on held-out folds" if beats else "no head.npz: the heuristic stands"
    if beats:
        np.savez(args.out / "head.npz", W=W.astype(np.float32), b=b.astype(np.float32), mu=mu.astype(np.float32), sd=sd.astype(np.float32))
        results["head_path"] = str(args.out / "head.npz")
    (args.out / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps({k_: v for k_, v in results.items() if k_ != "out_of_campaign"}, indent=2))
    if "out_of_campaign" in results:
        o = dict(results["out_of_campaign"]); rows_ = o.pop("per_row")
        print("out_of_campaign:", json.dumps(o, indent=2))
        for r in rows_[:12]:
            print("  ", r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
