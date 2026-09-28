"""Replay the request sizer over the execution ledger's real prompts (ADR-0050, lap N0).

Joins ``request.accepted`` / ``job.dispatched`` / ``invocation.succeeded`` on ``job_id``, reads
each prompt from the content-addressed artifact store, sizes it with the heuristic (or the
loopback service with ``--mode npu``) and scores the bin against the true ``tokens_out``. Rows
that hit their ``max_tokens`` cap are censored: the true output was at least the cap, so they are
reported apart and excluded from accuracy.

    python tools/sizer/replay.py [--since 2026-09-23] [--mode heuristic|npu] [--json out.json]
        [--csv out.csv] [--limit N] [--events PATH] [--artifacts PATH]

Read-only: it never writes to the ledger or the artifact store.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hearth.sizer import BIN_ORDER, LONG_BINS, bin_for_tokens, size_request  # noqa: E402

DEFAULT_EVENTS = Path(os.environ.get("HEARTH_EXECUTION_DIR", Path.home() / "hearth-production/var/execution")) / "events.ndjson"
DEFAULT_ARTIFACTS = Path(os.environ.get("HEARTH_ARTIFACT_DIR", Path.home() / "hearth-production/var/execution/artifacts"))
TEXT_OPERATIONS = {"llm.chat", "inference.generate"}


def load_jobs(events: Path, since: str) -> dict[str, dict]:
    jobs: dict[str, dict] = {}
    with events.open("r", encoding="utf-8") as fh:
        for line in fh:
            if '"request.accepted"' not in line and '"invocation.succeeded"' not in line and '"job.dispatched"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("event_type")
            job_id = e.get("job_id")
            if not job_id:
                continue
            if kind == "request.accepted":
                if (e.get("timestamp") or "") < since or e.get("operation") not in TEXT_OPERATIONS:
                    continue
                d = e.get("desired") or {}
                jobs[job_id] = {
                    "job_id": job_id, "at": e.get("timestamp"), "operation": e.get("operation"),
                    "arguments": d.get("arguments") or {}, "packed_files": d.get("packed_files") or [],
                    "input": d.get("input_artifact") or {}, "policy": d.get("policy") or {},
                }
            elif job_id in jobs and kind == "job.dispatched":
                jobs[job_id]["dispatched"] = e.get("observed") or {}
            elif job_id in jobs and kind == "invocation.succeeded":
                jobs[job_id]["result"] = e.get("observed") or {}
    return {k: v for k, v in jobs.items() if v.get("result")}


def read_prompt(artifacts: Path, meta: dict) -> str | None:
    sha = meta.get("sha256")
    if not sha:
        return None
    path = artifacts / "objects" / sha[:2] / sha
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="2026-09-23")
    ap.add_argument("--mode", default="heuristic", choices=("heuristic", "npu"))
    ap.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    ap.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--csv", type=Path)
    args = ap.parse_args()

    jobs = load_jobs(args.events, args.since)
    rows = []
    for n, job in enumerate(sorted(jobs.values(), key=lambda j: j["at"])):
        if args.limit and n >= args.limit:
            break
        prompt = read_prompt(args.artifacts, job["input"])
        if prompt is None:
            continue
        res = job["result"]
        tokens_out = res.get("tokens_out")
        if not isinstance(tokens_out, int):
            continue
        cap = res.get("max_tokens") or job["policy"].get("max_tokens")
        capped = isinstance(cap, int) and tokens_out >= cap
        family = job["arguments"].get("task_family")
        answer = size_request(prompt, system=job["arguments"].get("system"), files=job["packed_files"],
                              payload_bytes=job["input"].get("size") or len(prompt.encode("utf-8")),
                              task_family=family, mode=args.mode) or {}
        true_bin = bin_for_tokens(tokens_out)
        rows.append({
            "job_id": job["job_id"], "at": job["at"], "backend": res.get("backend"), "family": family,
            "task_id": job["arguments"].get("task_id"), "tokens_in": res.get("tokens_in"), "tokens_out": tokens_out,
            "cap": cap, "capped": capped, "true_bin": true_bin, "pred_bin": answer.get("output_class"),
            "expected": answer.get("expected_output_tokens"), "confidence": answer.get("confidence"),
            "signals": answer.get("signals"), "source": answer.get("source"), "ms": answer.get("ms"),
            "prompt_bytes": job["input"].get("size"),
        })

    scored = [r for r in rows if not r["capped"]]
    exact = sum(1 for r in scored if r["pred_bin"] == r["true_bin"])
    within1 = sum(1 for r in scored if abs(BIN_ORDER.index(r["pred_bin"]) - BIN_ORDER.index(r["true_bin"])) <= 1)
    under = sum(1 for r in scored if BIN_ORDER.index(r["pred_bin"]) < BIN_ORDER.index(r["true_bin"]))
    long_true = [r for r in scored if r["true_bin"] in LONG_BINS]
    long_recall = sum(1 for r in long_true if r["pred_bin"] in LONG_BINS)
    long_pred = [r for r in scored if r["pred_bin"] in LONG_BINS]
    long_precision = sum(1 for r in long_pred if r["true_bin"] in LONG_BINS)
    confusion = collections.Counter((r["true_bin"], r["pred_bin"]) for r in scored)
    by_signal = collections.defaultdict(lambda: [0, 0])
    for r in scored:
        key = (r["signals"] or ["?"])[0].split(":")[0]
        by_signal[key][1] += 1
        by_signal[key][0] += r["pred_bin"] == r["true_bin"]
    ms = sorted(r["ms"] for r in rows if isinstance(r["ms"], (int, float)))

    summary = {
        "mode": args.mode, "since": args.since, "jobs_joined": len(jobs), "rows": len(rows), "scored": len(scored),
        "capped_excluded": len(rows) - len(scored),
        "exact": exact, "exact_pct": round(100 * exact / max(1, len(scored)), 1),
        "within_one_bin_pct": round(100 * within1 / max(1, len(scored)), 1),
        "under_reserved": under, "under_reserved_pct": round(100 * under / max(1, len(scored)), 1),
        "long_true": len(long_true), "long_recall": f"{long_recall}/{len(long_true)}",
        "long_pred": len(long_pred), "long_precision": f"{long_precision}/{len(long_pred)}",
        "ms_p50": ms[len(ms) // 2] if ms else None, "ms_max": ms[-1] if ms else None,
        "by_first_signal": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_signal.items())},
        "true_bins": dict(collections.Counter(r["true_bin"] for r in scored)),
        "pred_bins": dict(collections.Counter(r["pred_bin"] for r in scored)),
    }
    print(json.dumps(summary, indent=2))
    print("\nconfusion (true -> pred):")
    print("      " + "".join(f"{b:>6}" for b in BIN_ORDER))
    for t in BIN_ORDER:
        print(f"{t:>5} " + "".join(f"{confusion.get((t, p), 0):>6}" for p in BIN_ORDER))
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    if args.csv:
        import csv
        with args.csv.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["job_id"])
            w.writeheader()
            for r in rows:
                w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
