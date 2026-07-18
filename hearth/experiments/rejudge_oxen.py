"""rejudge_oxen — re-score the 192 finals under the NEUTRAL rubric on AM4 oxen-planner,
WITH the reliability the first panel sweep lacked.

Lesson from panel_rejudge.jsonl: the oxen-planner leg died mid-sweep (card-0 outage —
~25 finals scored clean, then 167 straight None) with NO retry and NO error captured,
so the failure looked like a model/format problem when it was an availability blip.
This re-run adds, per the retro:
  - up to RETRIES retries per cell with linear backoff,
  - the error text stored on every row (never a silent None),
  - a longer cool-down after a run of consecutive failures (let an evicted model reload),
  - checkpointing every 12 cells.

Writes to panel_oxen_rerun.jsonl (does NOT touch the original panel_rejudge.jsonl).
Judge budget bumped 300 -> 512 (headroom; oxen-planner scores in ~9 chars at 300, so
this is slack, not a fix — see the reasoning-judge open thread).

    python -m hearth.experiments.rejudge_oxen
"""
from __future__ import annotations

import json
import os
import pathlib
import time

STUDY = pathlib.Path("hearth/var/experiments/study-20260707T073708Z")
OUT = STUDY / "panel_oxen_rerun.jsonl"
PROG = STUDY / "panel_oxen_rerun.progress.json"
RETRIES = 4
BACKOFF_S = 3
COOLDOWN_S = 30          # after this many consecutive fails, pause to let the model reload
COOLDOWN_AFTER = 3
MODEL, BACKEND = "oxen-planner", "am4-oxen"

# load the AM4 token WITHOUT printing it
for _line in pathlib.Path("hearth/var/gateway.cmd").read_text(encoding="utf-8").splitlines():
    if "AM4_OXEN_TOKEN" in _line and "=" in _line:
        os.environ["AM4_OXEN_TOKEN"] = _line.split("=", 1)[1].strip().strip('"')

from hearth.experiments.matrix import PROMPTS, NEUTRAL_JUDGE, _SCORE_PROMPT, _SCORE_RE  # noqa: E402
from hearth.toolsurface.inference import local_generate  # noqa: E402


def score_one(task: str, final: str):
    sp = _SCORE_PROMPT.format(prompt=task, response=final)
    last_err = None
    for attempt in range(1, RETRIES + 1):
        r = local_generate(sp, model=MODEL, backend=BACKEND, system=NEUTRAL_JUDGE,
                           max_tokens=512, timeout_s=120)
        if r.get("ok"):
            m = _SCORE_RE.findall(r.get("text") or "")
            if m:
                return max(0, min(100, int(m[-1]))), None, attempt
            last_err = "unparseable:" + repr((r.get("text") or "")[:80])
        else:
            last_err = r.get("error") or "not_ok"
        time.sleep(BACKOFF_S * attempt)
    return None, last_err, RETRIES


def main() -> None:
    rows = [json.loads(l) for l in open(STUDY / "rows.jsonl", encoding="utf-8") if l.strip()]
    rows = [r for r in rows if r.get("ok") and r.get("final")]
    ok = fails = consec = 0
    with open(OUT, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            task = PROMPTS.get(r["prompt_id"], "")
            score, err, att = score_one(task, r["final"])
            f.write(json.dumps({
                "cell_id": r["cell_id"], "variant": r.get("variant"), "laps": r.get("laps"),
                "judge_model": "am4-oxen-planner", "score": score, "error": err, "attempts": att,
            }) + "\n")
            f.flush()
            if score is not None:
                ok += 1
                consec = 0
            else:
                fails += 1
                consec += 1
                if consec >= COOLDOWN_AFTER:
                    print(f"  {consec} consecutive fails — cooling {COOLDOWN_S}s (last_err={err})",
                          flush=True)
                    time.sleep(COOLDOWN_S)
            if i % 12 == 0 or i == len(rows):
                json.dump({"done": i, "ok": ok, "fails": fails, "total": len(rows),
                           "consec_fails": consec}, open(PROG, "w"))
                print(f"[{i}/{len(rows)}] ok={ok} fails={fails} consec={consec}", flush=True)
    print(f"DONE ok={ok}/{len(rows)} fails={fails} -> {OUT}")


if __name__ == "__main__":
    main()
