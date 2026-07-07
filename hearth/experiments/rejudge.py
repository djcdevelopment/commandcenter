"""rejudge — confound probe: is "concise wins" a judge artifact?

Re-scores a completed study's finals under alternative judge rubrics (completeness-
favoring and length-neutral) using the SAME judge model, so we can compare the arm
ranking across judge biases WITHOUT regenerating anything. If concise-author still
wins under a completeness-favoring judge (which should penalize brevity), the finding
is genuinely about planning quality, not the default judge preferring short answers.

    AM4_OXEN_TOKEN=... python -m hearth.experiments.rejudge <study_dir>
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

from hearth.experiments.matrix import (
    PROMPTS, score_proposal, COMPLETENESS_JUDGE, NEUTRAL_JUDGE,
)
from hearth.toolsurface.inference import local_generate

JUDGE = [(None, "qwen3-coder:30b")]                    # same model, varied rubric
ALT_JUDGES = [("completeness", COMPLETENESS_JUDGE), ("neutral", NEUTRAL_JUDGE)]


def _mean(v):
    v = [x for x in v if x is not None]
    return round(sum(v) / len(v), 1) if v else None


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: rejudge <study_dir>")
        return 2
    study_dir = argv[0]
    rows = [json.loads(l) for l in open(os.path.join(study_dir, "rows.jsonl"),
                                        encoding="utf-8") if l.strip()]
    judges = ["directness-default"] + [j for j, _ in ALT_JUDGES]
    by_vj: dict = defaultdict(list)         # (variant, judge) -> scores
    by_vlj: dict = defaultdict(list)        # (variant, lap, judge) -> scores

    ok_rows = [r for r in rows if r.get("ok") and r.get("final")]
    cell_path = os.path.join(study_dir, "rejudge_rows.jsonl")
    out_path = os.path.join(study_dir, "rejudge.json")
    print(f"re-judging {len(ok_rows)} finals x {len(ALT_JUDGES)} rubrics (per-lap)...", flush=True)

    def _record(variant, lap, judge, score):
        if score is not None:
            by_vj[(variant, judge)].append(score)
            by_vlj[(variant, lap, judge)].append(score)

    with open(cell_path, "w", encoding="utf-8") as cf:
        for i, r in enumerate(ok_rows, 1):
            variant, lap = r.get("variant") or "baseline", r.get("laps")
            prompt = PROMPTS.get(r["prompt_id"], "")
            scores = {"directness-default": (r.get("score") or {}).get("mean")}
            _record(variant, lap, "directness-default", scores["directness-default"])
            for jname, jsys in ALT_JUDGES:
                res = score_proposal(r["final"], prompt, JUDGE, local_generate, judge_system=jsys)
                scores[jname] = res.get("mean")
                _record(variant, lap, jname, res.get("mean"))
            cf.write(json.dumps({"cell_id": r["cell_id"], "variant": variant,
                                 "prompt_id": r["prompt_id"], "laps": lap, "scores": scores}) + "\n")
            cf.flush()
            if i % 12 == 0 or i == len(ok_rows):
                vj = {f"{v}|{j}": {"mean": _mean(s), "n": len(s)} for (v, j), s in by_vj.items()}
                vlj = {f"{v}|L{lp}|{j}": {"mean": _mean(s), "n": len(s)}
                       for (v, lp, j), s in by_vlj.items()}
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump({"done": i, "total": len(ok_rows),
                               "by_variant_judge": vj, "by_variant_lap_judge": vlj}, f, indent=2)
                print(f"  [{i}/{len(ok_rows)}]", flush=True)

    print("\n=== ARM MEAN under each judge ===")
    print(f'{"arm":20s}  ' + "  ".join(f"{j:18s}" for j in judges))
    for v in sorted({x for x, _ in by_vj}):
        print(f'{v:20s}  ' + "  ".join(f"{str(_mean(by_vj.get((v, j), []))):18s}" for j in judges))
    print("\n=== NEUTRAL judge, score by lap (does the collapse survive?) ===")
    laps = sorted({lp for _, lp, _ in by_vlj})
    print(f'{"arm":20s}  ' + "  ".join(f"L{lp}" for lp in laps))
    for v in sorted({x for x, _, _ in by_vlj}):
        print(f'{v:20s}  ' + "  ".join(f"{_mean(by_vlj.get((v, lp, 'neutral'), []))}" for lp in laps))
    print(f"\n-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
