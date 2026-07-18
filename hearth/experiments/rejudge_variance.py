"""rejudge_variance — Experiment 1: gate-vote sizing / variance decomposition.

We have 192 scored planning "finals," each judged ONCE per rubric — so we have
no measurement of WITHIN-RUBRIC SAMPLING VARIANCE, the uncertainty a single
quality-gate judge vote actually carries. This script re-scores each final K
times under the SAME rubric (NEUTRAL_JUDGE) and SAME judge model, records every
raw score, and reports the sampling std — which then plugs into the standard
K >= (z*noise/effect)^2 vote-count estimator to turn "~9 votes" from a guess
into a measured number. Read-only over the study: does NOT regenerate any
planning output, only re-scores already-produced finals.

Reuses matrix.py's exact rubric text and score parser (_SCORE_PROMPT,
_parse_score, NEUTRAL_JUDGE) so the rubric itself is identical to every other
rejudge pass in this study; only the sampling axis (repeat calls) is new.

NOTE on temperature: hearth.toolsurface.inference.local_generate does not
expose a temperature knob, and probing showed Ollama's un-pinned default for
this judge prompt is near-deterministic (repeat calls returned bit-identical
"SCORE: N" even with different random seeds) -- the numeric-only completion
is extremely low-entropy for this model. To actually sample the variance this
experiment is measuring, we bypass local_generate for the scoring call and
POST directly to the resolved Ollama endpoint with an explicit temperature and
a fresh random seed per rep (same endpoint-resolution convention as
inference.py: HEARTH_OLLAMA env var, else 127.0.0.1:11434). This is the ONLY
difference from the standard local_generate path; everything else (prompt
template, rubric, score parsing) is untouched.

    python -m hearth.experiments.rejudge_variance <study_dir> [--k 6] [--per-arm N] [--temperature 1.0]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

from hearth.experiments.matrix import PROMPTS, NEUTRAL_JUDGE, _SCORE_PROMPT, _parse_score
from hearth.toolsurface.inference import DEFAULT_ENDPOINT, ENDPOINT_ENV_VAR

MODEL = "qwen3-coder:30b"
JUDGE_LABEL = "neutral"


def _endpoint() -> str:
    return os.environ.get(ENDPOINT_ENV_VAR, DEFAULT_ENDPOINT).rstrip("/")


def _score_once(final: str, prompt: str, temperature: float, timeout_s: int = 180,
                model: str = MODEL) -> tuple[int | None, str | None]:
    """One judge call with an explicit temperature + fresh random seed, so repeat
    calls on the SAME final actually sample instead of collapsing to one token
    path. Returns (score, error)."""
    payload = {
        "model": model,
        "prompt": _SCORE_PROMPT.format(prompt=prompt, response=final),
        "system": NEUTRAL_JUDGE,
        "stream": False,
        "options": {
            "num_predict": 300,
            "temperature": temperature,
            "seed": random.randint(1, 2_000_000_000),
        },
    }
    req = urllib.request.Request(
        f"{_endpoint()}/api/generate", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"non-JSON response: {exc}"
    return _parse_score(body.get("response", "")), None


def _load_ok_rows(study_dir: str) -> list[dict]:
    path = os.path.join(study_dir, "rows.jsonl")
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return [r for r in rows if r.get("ok") and r.get("final")]


def _select_cells(rows: list[dict], per_arm: int, seed: int = 20260710) -> list[dict]:
    """Stratified fallback subsample: N cells per arm, deterministic given seed."""
    if not per_arm:
        return rows
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_arm[r.get("variant") or "baseline"].append(r)
    rng = random.Random(seed)
    out: list[dict] = []
    for arm in sorted(by_arm):
        arm_rows = sorted(by_arm[arm], key=lambda r: r["cell_id"])
        rng.shuffle(arm_rows)
        out.extend(arm_rows[:per_arm])
    return out


def _mean(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 3) if v else None


def _analyze(by_cell: dict[str, list[int]], cell_meta: dict[str, tuple[str, int]],
             K: int, effect_prior: float = 2.8, z: float = 2.0,
             between_rubric_arm_std: float = 4.15,
             between_rubric_percell_std: float = 6.5) -> dict:
    # within-cell sampling std, overall + per arm
    percell_std = {cid: statistics.stdev(s) for cid, s in by_cell.items() if len(s) >= 2}
    by_arm_std: dict[str, list[float]] = defaultdict(list)
    for cid, s in percell_std.items():
        variant, _laps = cell_meta[cid]
        by_arm_std[variant].append(s)
    mean_within_cell_std = _mean(list(percell_std.values()))
    mean_within_cell_std_by_arm = {a: _mean(v) for a, v in sorted(by_arm_std.items())}

    # pooled raw scores per arm (all K reps, all cells in that arm) for the
    # arm-mean difference + its SE from sampling noise alone.
    by_arm_scores: dict[str, list[int]] = defaultdict(list)
    for cid, s in by_cell.items():
        variant, _laps = cell_meta[cid]
        by_arm_scores[variant].extend([x for x in s if x is not None])

    baseline = by_arm_scores.get("baseline", [])
    concise = by_arm_scores.get("concise-author", [])
    arm_means = {a: _mean(v) for a, v in sorted(by_arm_scores.items())}
    effect_measured = None
    se_diff = None
    if baseline and concise and mean_within_cell_std:
        effect_measured = round(_mean(concise) - _mean(baseline), 3)
        se_diff = round(mean_within_cell_std * math.sqrt(1 / len(baseline) + 1 / len(concise)), 3)

    def _votes(noise):
        if not noise:
            return None
        return math.ceil((z * noise / effect_prior) ** 2)

    votes_measured = _votes(mean_within_cell_std)
    votes_between_rubric_arm = _votes(between_rubric_arm_std)
    votes_between_rubric_percell = _votes(between_rubric_percell_std)

    return {
        "k_reps": K,
        "n_cells": len(by_cell),
        "n_cells_with_std": len(percell_std),
        "mean_within_cell_sampling_std": mean_within_cell_std,
        "mean_within_cell_sampling_std_by_arm": mean_within_cell_std_by_arm,
        "arm_means_neutral_pooled": arm_means,
        "arm_n_scores": {a: len(v) for a, v in sorted(by_arm_scores.items())},
        "effect_measured_concise_minus_baseline": effect_measured,
        "effect_prior_used_for_vote_calc": effect_prior,
        "se_of_effect_from_sampling_noise": se_diff,
        "z_for_vote_estimator": z,
        "between_rubric_std_reference": {
            "arm_level": between_rubric_arm_std,
            "per_cell": between_rubric_percell_std,
            "source": "existing rejudge_rows.jsonl (directness-default/completeness/neutral spread), "
                      "supplied as the contrast prior for this experiment",
        },
        "required_gate_votes": {
            "using_measured_within_cell_sampling_std": votes_measured,
            "using_between_rubric_std_arm_level": votes_between_rubric_arm,
            "using_between_rubric_std_per_cell": votes_between_rubric_percell,
        },
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("study_dir")
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--per-arm", type=int, default=0,
                    help="stratified fallback: N cells per arm x --k reps (0 = all cells)")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--checkpoint-every", type=int, default=24)
    args = ap.parse_args(argv)

    rows = _load_ok_rows(args.study_dir)
    cells = _select_cells(rows, args.per_arm)
    K = args.k

    out_path = os.path.join(args.study_dir, "variance_rejudge.jsonl")
    summary_path = os.path.join(args.study_dir, "variance_summary.json")
    mode = f"stratified per-arm={args.per_arm}" if args.per_arm else "full"
    print(f"variance sweep: {len(cells)} cells x K={K} = {len(cells) * K} calls "
          f"({mode}, temperature={args.temperature})", flush=True)

    by_cell: dict[str, list[int]] = defaultdict(list)
    cell_meta: dict[str, tuple[str, int]] = {}
    n_calls = 0
    n_errors = 0
    t0 = time.monotonic()

    with open(out_path, "w", encoding="utf-8") as f:
        for i, r in enumerate(cells, 1):
            cell_id = r["cell_id"]
            variant = r.get("variant") or "baseline"
            laps = r.get("laps")
            prompt = PROMPTS.get(r["prompt_id"], "")
            cell_meta[cell_id] = (variant, laps)
            for rep in range(K):
                score, error = _score_once(r["final"], prompt, args.temperature)
                n_calls += 1
                if error:
                    n_errors += 1
                f.write(json.dumps({
                    "cell_id": cell_id, "variant": variant, "prompt_id": r["prompt_id"],
                    "laps": laps, "rep": rep, "score": score, "error": error,
                }) + "\n")
                if score is not None:
                    by_cell[cell_id].append(score)
            f.flush()
            if i % args.checkpoint_every == 0 or i == len(cells):
                elapsed = time.monotonic() - t0
                print(f"  [{i}/{len(cells)} cells, {n_calls} calls, {n_errors} errors, "
                      f"{elapsed / 60:.1f} min elapsed]", flush=True)
                partial = _analyze(by_cell, cell_meta, K)
                partial["progress"] = {"cells_done": i, "cells_total": len(cells),
                                       "calls_done": n_calls, "errors": n_errors,
                                       "elapsed_s": round(elapsed, 1)}
                with open(summary_path, "w", encoding="utf-8") as sf:
                    json.dump(partial, sf, indent=2)

    final_summary = _analyze(by_cell, cell_meta, K)
    final_summary["progress"] = {"cells_done": len(cells), "cells_total": len(cells),
                                 "calls_done": n_calls, "errors": n_errors,
                                 "elapsed_s": round(time.monotonic() - t0, 1), "done": True}
    with open(summary_path, "w", encoding="utf-8") as sf:
        json.dump(final_summary, sf, indent=2)

    print(json.dumps(final_summary, indent=2))
    print(f"\n-> {out_path}\n-> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
