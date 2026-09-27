#!/usr/bin/env python3
"""candidate_worth_retire — propose, then apply, retirements in knowledge/candidate_worth.json.

The worth file is authored (knowledge/README.md: a rebuild never touches it) and `author`
stays a human. This tool does two things and nothing else:

  --propose           print the table: candidate_id, worth, decoded combo, proposed reason code
  --apply --ratified-by <name>
                      write candidate-worth.v2: every proposed row gains
                      status="retired", retired_reason, retired_on; author is unchanged

Retire rule (omen-linux, 2026-09-27): a priced candidate is retired when its decoded
builder is not a node this host serves (omen, am4, fx99 — backends-linux.toml settings.node),
when its id names a dead backend family (ollama*, vllm-on-WSL, the Windows conductor task
kinds), or when the id no longer exists in the derived knowledge/experiment_candidates.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from hearth.backlog import dispatch  # noqa: E402

WORTH = REPO / "knowledge" / "candidate_worth.json"
CANDIDATES = REPO / "knowledge" / "experiment_candidates.json"
LINUX_NODES = {"omen", "am4", "fx99"}
DEAD_MARKERS = ("ollama", "omen-wsl", "am4-oxen", "am4-moe", "omen-swap", "omen-arc", "cc-builder", "omen-worker",
                "am4-worker", "omen-5070", "openai", "claude", "capacity-probe", "repo-build", "engine-start",
                "|vllm", "ollama+", "+ollama",
                # Windows-era single-workflow ids that name no combo: the WSL vLLM MoE crash class and
                # model-portability probes for Ollama tags / the AWQ variant that no Linux lane serves.
                "moe_offload_crash", "model_portability:model_id=mixtral", "model_portability:model_id=qwen2.5:14b",
                "model_portability:model_id=qwen3-30b-a3b-awq")


def propose(worth_doc: dict, known: set[str]) -> list[dict]:
    rows = []
    for e in worth_doc.get("entries", []):
        cid = e["candidate_id"]
        combo = dispatch.combo_from_candidate_id(cid)
        if cid not in known:
            reason = "stale-id"
        elif combo is not None and combo.builder_id not in LINUX_NODES:
            reason = "dead-builder"
        elif combo is None and any(m in cid for m in DEAD_MARKERS):
            reason = "dead-backend"
        elif combo is not None and any(m in combo.backend for m in ("ollama", "vllm-wsl")):
            reason = "dead-backend"
        else:
            reason = None
        rows.append({"candidate_id": cid, "worth_points": e.get("worth_points"), "status": e.get("status"),
                     "combo": f"{combo.builder_id}|{combo.model_id}|{combo.backend}" if combo else None,
                     "proposed": reason})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ratified-by", default=None)
    ap.add_argument("--worth", default=str(WORTH)); ap.add_argument("--candidates", default=str(CANDIDATES))
    a = ap.parse_args(argv)
    worth_doc = json.loads(Path(a.worth).read_text())
    known = {c.get("candidate_id") for c in json.loads(Path(a.candidates).read_text()).get("candidates", [])}
    rows = propose(worth_doc, known)
    if a.apply:
        if not a.ratified_by:
            print("--apply needs --ratified-by <human>", file=sys.stderr); return 2
        today = date.today().isoformat(); n = 0
        by_id = {r["candidate_id"]: r for r in rows}
        for e in worth_doc["entries"]:
            r = by_id[e["candidate_id"]]
            if r["proposed"] and e.get("status") != "retired":
                e["status"] = "retired"; e["retired_reason"] = r["proposed"]; e["retired_on"] = today
                e["retired_ratified_by"] = a.ratified_by; n += 1
        worth_doc["contract_version"] = "candidate-worth.v2"
        Path(a.worth).write_text(json.dumps(worth_doc, indent=2) + "\n")
        print(f"retired {n} entries; contract_version candidate-worth.v2; ratified by {a.ratified_by}")
        return 0
    keep = [r for r in rows if not r["proposed"]]
    print(f"{'worth':>5}  {'proposed':<13} {'combo':<52} candidate_id")
    for r in sorted(rows, key=lambda r: (-int(r["worth_points"] or 0), r["candidate_id"])):
        print(f"{r['worth_points']!s:>5}  {(r['proposed'] or 'KEEP'):<13} {(r['combo'] or '-'):<52} {r['candidate_id'][:110]}")
    print(f"\n{len(rows)} priced; retire {len(rows)-len(keep)}; keep {len(keep)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
