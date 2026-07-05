#!/usr/bin/env python3
"""campaign/status — poll every in-flight mechnet pour and summarize.

Reads campaign/speculation_manifest.json plus the standalone pours (the two
build slices + the wordcount re-run) and reports done/running per plan_id.
Reusable as the reap loop: run it, harvest the done ones.

    ./fleet-worker-node/.venv-omen/Scripts/python.exe -m campaign.status
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hearth.toolsurface.task_lane import task_status  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Standalone pours launched alongside the speculation campaign.
EXTRA = [
    ("js5-actuation (slice)", "hearth-js5-actuation-bc96bebb"),
    ("assay-stream-acceptance (slice)", "hearth-assay-stream-acceptance-8d04d66a"),
    ("rerun-wordcount-local", "hearth-rerun-wordcount-local-dbe8bb4b"),
]


def _rows():
    rows = list(EXTRA)
    mpath = os.path.join(HERE, "speculation_manifest.json")
    if os.path.exists(mpath):
        m = json.load(open(mpath, encoding="utf-8"))
        for idea in m.get("ideas", []):
            if idea.get("plan_id"):
                rows.append((f"spec:{idea['slug']}", idea["plan_id"]))
    return rows


def main() -> int:
    rows = _rows()
    done = running = errored = 0
    print(f"polling {len(rows)} pours...\n")
    done_ids = []
    for label, pid in rows:
        st = task_status(pid)
        if not st.get("ok"):
            errored += 1
            print(f"  ERR   {label:44s} {st.get('error','?')}")
        elif st.get("done"):
            done += 1
            done_ids.append((label, pid))
            print(f"  DONE  {label:44s} {pid}")
        else:
            running += 1
            print(f"  ...   {label:44s} running/queued")
    print(f"\n  {done} done | {running} running/queued | {errored} err  "
          f"(of {len(rows)})")
    if done_ids:
        print("\ndone plan_ids (harvest runs/<id>/result.json on cc-conductor):")
        for label, pid in done_ids:
            print(f"    {pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
