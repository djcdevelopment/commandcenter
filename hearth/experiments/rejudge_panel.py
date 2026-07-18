"""rejudge_panel — Experiment 2: diversify judge MODELS, not just rubrics.

Every "multi-lens" result so far (rejudge.py) varied the RUBRIC on a single judge
model (OMEN qwen3-coder:30b). This re-scores the same 192 finals under the SAME
(NEUTRAL) rubric with THREE distinct judge MODELS, so cross-model disagreement can
be measured and compared to the existing cross-rubric spread (mean ~= 6.5, from
rejudge_rows.jsonl). Regenerates nothing — reads ``final`` text straight out of
rows.jsonl, same pattern as rejudge.py.

Panel (see PANEL below for the final composition + why): OMEN qwen3-coder:30b,
AM4 oxen-planner (Qwen3-30B-A3B, card0), OMEN qwen2.5:14b (fallback for the third
seat — AM4 oxen-critic/card1 was down, 502 Bad Gateway on two attempts, and
wake_am4 only manages the planner slot per hearth/toolsurface/summon.py so it
would not have helped).

    python -m hearth.experiments.rejudge_panel <study_dir>

Runs one full pass per panel model, checkpointing the raw rows continuously and a
progress snapshot every ~24 cells per model. Panel models run in parallel threads
(the AM4 leg is a separate card so it overlaps the two OMEN legs; the two OMEN
legs share the Ollama queue and simply serialize there, which is correctness-
neutral, only slower).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections import defaultdict

from hearth.experiments.matrix import PROMPTS, score_proposal, NEUTRAL_JUDGE
from hearth.toolsurface.inference import local_generate

# ---- panel composition (label, backend, model) ----
PANEL = [
    ("omen-qwen3-coder:30b", None, "qwen3-coder:30b"),
    ("am4-oxen-planner", "am4-oxen", "oxen-planner"),
    ("omen-qwen2.5:14b", None, "qwen2.5:14b"),
]
PANEL_NOTE = (
    "3rd seat is a fallback: AM4 oxen-critic (card1, :8081) answered 502 Bad "
    "Gateway on both the initial ping and one retry (cold-backend policy: one "
    "retry max). wake_am4 only starts/health-checks the b70-planner unit "
    "(:8080) per hearth/toolsurface/summon.py -- it never touches the critic "
    "slot, so it would not have fixed this. Substituted OMEN's second "
    "installed Ollama model (qwen2.5:14b, confirmed via /api/tags) as the "
    "third distinct-model judge, per the task's documented fallback path."
)

CHECKPOINT_EVERY = 24


def _ensure_am4_token() -> None:
    if os.environ.get("AM4_OXEN_TOKEN"):
        return
    cmd_path = os.path.join("hearth", "var", "gateway.cmd")
    if not os.path.exists(cmd_path):
        return
    with open(cmd_path, encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"set AM4_OXEN_TOKEN=(\S+)", content)
    if m:
        os.environ["AM4_OXEN_TOKEN"] = m.group(1)


def _mean(v):
    v = [x for x in v if x is not None]
    return round(sum(v) / len(v), 1) if v else None


def _run_one_model(label, backend, model, ok_rows, out_path, lock, progress, progress_path):
    """One full pass over ok_rows for a single judge model. Thread target."""
    done = 0
    for r in ok_rows:
        prompt = PROMPTS.get(r["prompt_id"], "")
        res = score_proposal(r["final"], prompt, [(backend, model)],
                             local_generate, judge_system=NEUTRAL_JUDGE, timeout_s=300)
        row = {
            "cell_id": r["cell_id"], "variant": r.get("variant") or "baseline",
            "laps": r.get("laps"), "judge_model": label, "score": res.get("mean"),
        }
        with lock:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        done += 1
        if done % CHECKPOINT_EVERY == 0 or done == len(ok_rows):
            with lock:
                progress[label] = {"done": done, "total": len(ok_rows)}
                with open(progress_path, "w", encoding="utf-8") as pf:
                    json.dump(progress, pf, indent=2)
            print(f"  [{label}] {done}/{len(ok_rows)}", flush=True)


def _spread_stats(spreads: list[float]) -> dict:
    if not spreads:
        return {"mean": None, "median": None, "max": None, "n": 0}
    ss = sorted(spreads)
    n = len(ss)
    median = ss[n // 2] if n % 2 else round((ss[n // 2 - 1] + ss[n // 2]) / 2, 2)
    return {"mean": round(sum(ss) / n, 2), "median": median, "max": max(ss), "n": n}


def _rank(arm_means: dict) -> list[str]:
    """Arms sorted best-first by mean score (None sorts last)."""
    return sorted(arm_means, key=lambda a: (arm_means[a] is None, -(arm_means[a] or 0)))


def _spearman(rank_a: list[str], rank_b: list[str]) -> float | None:
    """Spearman rank correlation between two rankings of the same arm set."""
    arms = list(rank_a)
    if set(rank_a) != set(rank_b) or len(arms) < 2:
        return None
    pos_a = {a: i for i, a in enumerate(rank_a)}
    pos_b = {a: i for i, a in enumerate(rank_b)}
    n = len(arms)
    d2 = sum((pos_a[a] - pos_b[a]) ** 2 for a in arms)
    return round(1 - (6 * d2) / (n * (n ** 2 - 1)), 3)


def analyze(study_dir: str) -> dict:
    """Compute the cross-model spread / cross-rubric comparison / arm-ranking
    agreement and write panel_summary.json next to the raw file. Read-only on
    rows.jsonl and rejudge_rows.jsonl -- never modifies either."""
    panel_path = os.path.join(study_dir, "panel_rejudge.jsonl")
    panel_rows = [json.loads(l) for l in open(panel_path, encoding="utf-8") if l.strip()]

    by_cell: dict = defaultdict(dict)      # cell_id -> {judge_model: score}
    variant_of: dict = {}
    for r in panel_rows:
        by_cell[r["cell_id"]][r["judge_model"]] = r["score"]
        variant_of[r["cell_id"]] = r["variant"]

    panel_labels = [label for label, _, _ in PANEL]

    # ---- cross-model per-cell spread (overall + per arm) ----
    overall_spreads = []
    spreads_by_variant: dict = defaultdict(list)
    for cell_id, scores_by_model in by_cell.items():
        vals = [scores_by_model.get(m) for m in panel_labels]
        vals = [v for v in vals if v is not None]
        if len(vals) < 2:
            continue
        spread = max(vals) - min(vals)
        overall_spreads.append(spread)
        spreads_by_variant[variant_of[cell_id]].append(spread)

    cross_model = {
        "overall": _spread_stats(overall_spreads),
        "by_arm": {v: _spread_stats(s) for v, s in sorted(spreads_by_variant.items())},
    }

    # ---- cross-rubric spread, recomputed from the existing rejudge_rows.jsonl ----
    cross_rubric = None
    rubric_path = os.path.join(study_dir, "rejudge_rows.jsonl")
    if os.path.exists(rubric_path):
        rubric_rows = [json.loads(l) for l in open(rubric_path, encoding="utf-8") if l.strip()]
        rubric_overall = []
        rubric_by_variant: dict = defaultdict(list)
        for r in rubric_rows:
            vals = [v for v in r["scores"].values() if v is not None]
            if len(vals) < 2:
                continue
            spread = max(vals) - min(vals)
            rubric_overall.append(spread)
            rubric_by_variant[r["variant"]].append(spread)
        cross_rubric = {
            "overall": _spread_stats(rubric_overall),
            "by_arm": {v: _spread_stats(s) for v, s in sorted(rubric_by_variant.items())},
            "source": "rejudge_rows.jsonl (3 rubrics x 1 model, qwen3-coder:30b)",
        }

    comparison = None
    if cross_rubric and cross_rubric["overall"]["mean"] is not None and cross_model["overall"]["mean"] is not None:
        cm, cr = cross_model["overall"]["mean"], cross_rubric["overall"]["mean"]
        comparison = {
            "cross_model_mean": cm, "cross_rubric_mean": cr,
            "cross_model_minus_cross_rubric": round(cm - cr, 2),
            "cross_model_is_bigger": cm > cr,
            "ratio_cross_model_over_cross_rubric": round(cm / cr, 2) if cr else None,
        }

    # ---- per-model arm means + rankings + agreement ----
    arm_means_by_model: dict = {}
    for label in panel_labels:
        per_variant: dict = defaultdict(list)
        for r in panel_rows:
            if r["judge_model"] == label and r["score"] is not None:
                per_variant[r["variant"]].append(r["score"])
        arm_means_by_model[label] = {v: _mean(s) for v, s in per_variant.items()}

    rankings = {label: _rank(means) for label, means in arm_means_by_model.items()}
    top_arm = {label: (r[0] if r else None) for label, r in rankings.items()}
    concise_wins_under = [label for label, top in top_arm.items() if top == "concise-author"]

    pairwise_spearman = {}
    for i in range(len(panel_labels)):
        for j in range(i + 1, len(panel_labels)):
            a, b = panel_labels[i], panel_labels[j]
            pairwise_spearman[f"{a} vs {b}"] = _spearman(rankings[a], rankings[b])

    verdict_bits = []
    if comparison:
        bigger = "MODELS" if comparison["cross_model_is_bigger"] else "RUBRICS"
        verdict_bits.append(
            f"cross-model spread ({comparison['cross_model_mean']}) is "
            f"{'bigger' if comparison['cross_model_is_bigger'] else 'smaller'} than "
            f"cross-rubric spread ({comparison['cross_rubric_mean']}) -> diversifying "
            f"{bigger} is the {'bigger' if comparison['cross_model_is_bigger'] else 'smaller'} lever."
        )
    verdict_bits.append(
        f"concise-author ranks #1 under {len(concise_wins_under)}/{len(panel_labels)} panel models: "
        f"{concise_wins_under}."
    )
    verdict = " ".join(verdict_bits)

    summary = {
        "study_dir": study_dir,
        "panel": [{"label": l, "backend": b, "model": m} for l, b, m in PANEL],
        "panel_note": PANEL_NOTE,
        "n_cells_scored": len(overall_spreads),
        "cross_model_spread": cross_model,
        "cross_rubric_spread": cross_rubric,
        "cross_model_vs_cross_rubric": comparison,
        "arm_means_by_model": arm_means_by_model,
        "arm_rankings_by_model": rankings,
        "top_arm_by_model": top_arm,
        "pairwise_spearman_rank_correlation": pairwise_spearman,
        "verdict": verdict,
    }
    out_path = os.path.join(study_dir, "panel_summary.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"-> {out_path}")
    return summary


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: rejudge_panel <study_dir> | rejudge_panel --analyze-only <study_dir>")
        return 2
    if argv[0] == "--analyze-only":
        analyze(argv[1])
        return 0
    study_dir = argv[0]
    _ensure_am4_token()

    rows_path = os.path.join(study_dir, "rows.jsonl")
    rows = [json.loads(l) for l in open(rows_path, encoding="utf-8") if l.strip()]
    ok_rows = [r for r in rows if r.get("ok") and r.get("final")]
    print(f"panel re-judge: {len(ok_rows)} finals x {len(PANEL)} judge models "
          f"(neutral rubric)...", flush=True)
    for label, backend, model in PANEL:
        print(f"  panel seat: {label}  backend={backend}  model={model}", flush=True)
    print(f"  {PANEL_NOTE}", flush=True)

    out_path = os.path.join(study_dir, "panel_rejudge.jsonl")
    progress_path = os.path.join(study_dir, "panel_rejudge.progress.json")
    # fresh raw file for this run (idempotent re-launch = clean slate, not append-forever)
    open(out_path, "w", encoding="utf-8").close()

    lock = threading.Lock()
    progress: dict = {}
    threads = []
    started = time.monotonic()
    for label, backend, model in PANEL:
        t = threading.Thread(target=_run_one_model,
                             args=(label, backend, model, ok_rows, out_path, lock,
                                   progress, progress_path),
                             name=f"judge-{label}")
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    elapsed_s = round(time.monotonic() - started, 1)
    print(f"\nall panel passes done in {elapsed_s}s -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
