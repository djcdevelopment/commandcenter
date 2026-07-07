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
    agg: dict = defaultdict(list)
    # original (directness-default) scores come straight from the rows
    for r in rows:
        m = (r.get("score") or {}).get("mean")
        if r.get("ok") and m is not None:
            agg[(r.get("variant") or "baseline", "directness-default")].append(m)

    ok_rows = [r for r in rows if r.get("ok") and r.get("final")]
    out_path = os.path.join(study_dir, "rejudge.json")
    print(f"re-judging {len(ok_rows)} finals x {len(ALT_JUDGES)} rubrics...", flush=True)
    for i, r in enumerate(ok_rows, 1):
        prompt = PROMPTS.get(r["prompt_id"], "")
        for jname, jsys in ALT_JUDGES:
            res = score_proposal(r["final"], prompt, JUDGE, local_generate, judge_system=jsys)
            if res.get("mean") is not None:
                agg[(r.get("variant") or "baseline", jname)].append(res["mean"])
        if i % 12 == 0 or i == len(ok_rows):
            table = defaultdict(dict)
            for (variant, jname), scores in agg.items():
                table[variant][jname] = {"mean": _mean(scores), "n": len(scores)}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"done": i, "total": len(ok_rows), "by_variant_judge": table}, f, indent=2)
            print(f"  [{i}/{len(ok_rows)}]", flush=True)

    print("\n=== ARM MEAN under each judge rubric ===")
    judges = ["directness-default"] + [j for j, _ in ALT_JUDGES]
    print(f'{"arm":20s}  ' + "  ".join(f"{j:18s}" for j in judges))
    for variant in sorted({v for v, _ in agg}):
        cells = [_mean(agg.get((variant, j), [])) for j in judges]
        print(f'{variant:20s}  ' + "  ".join(f"{str(c):18s}" for c in cells))
    print(f"\n-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
