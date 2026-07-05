#!/usr/bin/env python3
"""pour_speculation — toss 20 speculative planning briefs onto mechnet (all-local).

Idle-hours divergent exploration (two-economies doctrine: laps on electricity).
Each idea is poured as a research brief across a rotating pair of LOCAL builders,
so every idea gets two independent model perspectives. Outputs land as
proposals/<slug>.md on each lap branch under runs/<plan_id>/ on the conductor;
harvest later. Writes campaign/speculation_manifest.json with the plan_ids.

Run:
    ./fleet-worker-node/.venv-omen/Scripts/python.exe -m campaign.pour_speculation
    ./fleet-worker-node/.venv-omen/Scripts/python.exe -m campaign.pour_speculation --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hearth.toolsurface.task_lane import submit_task  # noqa: E402

# Rotate local builders so each idea gets two different local models and load
# spreads across the three reachable shells. (mixtral/cc-builder-4 excluded --
# stranded network + 16x slower; it can join later as a comparison lane.)
PAIRS = [
    ["omen-worker-1", "cc-builder-2"],   # qwen3-coder:30b  vs  vllama-planner
    ["omen-worker-1", "am4-worker-1"],   # qwen3-coder:30b  vs  oxen
    ["cc-builder-2", "am4-worker-1"],    # vllama-planner   vs  oxen
]

PREAMBLE = (
    "SPECULATIVE PLANNING BRIEF - divergent design exploration, NOT a build.\n"
    "You have READ-ONLY source at ~/commandcenter-src. Ground every claim in the\n"
    "ACTUAL code and docs (cite real file paths); do NOT invent files, tools, or\n"
    "APIs. Write NO production code. Produce ONE concise design proposal (~1-2\n"
    "pages) at proposals/{slug}.md and commit only that file, with sections:\n"
    "  ## Problem  ## Proposed approach (concrete, cite real files)\n"
    "  ## Risks & tradeoffs  ## Suggested slices & rough effort  ## Open questions\n"
    "Honor the repo's doctrines: two-economies (metered vs sunk compute),\n"
    "visibility/testability/resilience, advisory-first / act-then-document, and\n"
    "'the conductor is the one scheduler'. Bold-but-grounded beats safe-but-vague.\n\n"
)
POSTAMBLE = "\n\nDeliverable: proposals/{slug}.md only. This is idea generation."

IDEAS = [
    ("scheduler-imagegen-inference",
     "How would we expand the CP-SAT job-shop scheduler (hearth/scheduler/) to make "
     "IMAGEGEN and INFERENCE first-class job and machine types alongside builds? "
     "Extend JS7's setup-aware model-residency + DDR4-staging idea to the AM4 imagegen "
     "pipeline and to inference calls; treat per-GPU VRAM as a resource and the "
     "two-economies token objective (metered frontier vs sunk local) as the cost. What "
     "changes in ontology.py / solve.py / capacity buckets?"),
    ("self-learning-loop-improvements",
     "Improvements to the self-learning loop (observation -> finding -> policy -> "
     "candidate -> experiment; tools/workflow/project_*.py + knowledge/). Target: faster "
     "convergence, confidence decay so stale findings age out, closing the "
     "candidate->dispatch loop, and hardening against the null-action / acceptance "
     "exploits documented in docs/adr/0001 and ASSAY-ACCEPTANCE-GAP."),
    ("architecture-alignment",
     "Other architecture-alignment improvements across the three-plane doctrine "
     "(AM4-MCP = sense / mechnet = act / HEARTH = the one boundary). Where do the two "
     "bounded contexts (ADR-0010) and the intentional double-write (ADR-0011) create "
     "friction, and what would tighten the seams without losing capture-first?"),
    ("sensory-dashboard",
     "Design a better overall dashboard that surfaces everything AM4 and the fleet "
     "expose: GPU temp/power/clock, fan, model residency, per-node health, ledger "
     "throughput, in-flight pours, belief deltas. The 'fans, digitized' made visible. "
     "Build on the real physical-telemetry fields (contracts/capacity-observation, "
     "runs/g2-validation) and the existing :8080 conductor dashboard."),
    ("bankedfire-candidate-source",
     "The idle-drain engine (fleet/bankedfire_drain.py) is armed but its "
     "knowledge/candidate_worth.json is EMPTY, so it no-ops. Design a live candidate "
     "source that self-feeds the drain from the belief layer's coverage gaps + "
     "experiment candidates, priced by worth, so idle windows fill themselves."),
    ("acceptance-oracle",
     "Generalize stream-scoped acceptance into a full ACCEPTANCE-ORACLE contract that "
     "works across task classes (build / research / imagegen), not just builds. Fix the "
     "'assay is a regression gate, not an acceptance oracle' gap (docs/adr/0001). What is "
     "the contract, and how does the assay consume it before ranking?"),
    ("builder-elo-tournament",
     "Move beyond pairwise A/B assay to an ELO / tournament rating of "
     "builder x model x backend combos, so routing uses a calibrated skill rating instead "
     "of raw pass/fail. How does this fold into the belief layer (findings.json) and the "
     "scheduler's eligibility ordering without breaking the current evidence discipline?"),
    ("watchfire-npu-classifier",
     "Design Watchfire Slice 1: the deferred NPU coherence-gap classifier (ADR-0007). "
     "Beyond the rules-first spells in hearth/health/gaps.py, a learned detector of "
     "incoherence/gaps. What signals, what model, where does it run, and how does it stay "
     "within the 'act on obvious+reversible, flag ambiguous' policy?"),
    ("knowledge-per-kwh",
     "Real energy/economics accounting: combine ledger duration + token counts + the "
     "physical telemetry (power_w, gpu_temp) into a knowledge-per-kWh metric and a budget "
     "governor tied to knowledge/operating-budget.json. How do we measure 'was this idle "
     "hour worth it' in joules, not just tokens?"),
    ("learned-router",
     "The JS5 actuation brain (H5): a local-vs-frontier routing policy LEARNED from the "
     "regret ledger (hearth/scheduler/hindsight.py). How does regret history train a "
     "router that orders the CCMETA eligibility list, staying advisory-first (ADR-0008) "
     "with the conductor still the one scheduler?"),
    ("imagegen-as-machine",
     "Model the AM4 imagegen pipeline (Tempo.ImageGen: GpuLaunch + Allocator, dual "
     "ComfyUI on Arc, sd3.5_large, am4bot queue) as a first-class scheduler MACHINE with "
     "model-residency setup times and per-Arc VRAM budgets. Its GpuLaunch/Allocator is "
     "already the safe-transition/arbiter seed -- how does the scheduler drive it?"),
    ("end-to-end-lineage",
     "A unified provenance / OTel trace spanning HEARTH ledger event -> conductor dispatch "
     "-> builder lap -> assay -> belief projection, under ONE trace id. End-to-end "
     "lineage for any observation. Build on the existing MAF-middleware/OTel->Jaeger "
     "wiring on AM4 and the ledger's args_digest/result_digest provenance."),
    ("fleet-autoscale",
     "Raise idle throughput. Today ~3 local builder shells, each pour needs >=2, so "
     "throughput is ~1-2 sequential pours. Design on-demand provisioning of more local "
     "builder shells (OLLAMA_NUM_PARALLEL on OMEN ollama, extra logical workers pointed at "
     "OMEN, VM checkpoint/import per project-fleet-vm-provisioning) and a policy for when "
     "to scale up vs down."),
    ("belief-recency-decay",
     "Belief-layer confidence decay / recency weighting: findings.json confidence_score "
     "is static. Design time-decay so stale evidence ages out, quarantines auto-expire on "
     "fresh evidence, and a 'repeated particular' can graduate to an abstraction. Keep the "
     "corpus-guard / fixture-taint protections (tools/workflow/corpus_guard.py) intact."),
    ("mechnet-modes",
     "Formalize 'mechnet modes' (art / local-dev / deep-research / frontier-collab) as "
     "safe-transition states with an arbiter, seeded by AM4's GpuLaunch/Allocator pattern. "
     "How does the fleet decide what to do with idle capacity per mode, and how do modes "
     "compose with the two-economies objective and the operating budget?"),
    ("watchfire-autoheal-envelope",
     "Expand Watchfire AUTO_HEAL_KINDS (currently just phantom_in_flight) in "
     "hearth/health/gaps.py + toolsurface. Design the SAFETY ENVELOPE for more reversible "
     "auto-remediations: what qualifies as obvious+undoable (act) vs ambiguous (flag), and "
     "how to prove reversibility before acting."),
    ("idea-to-candidate-loop",
     "Close the loop so mechnet-generated proposals (like THIS very campaign) auto-file as "
     "scored experiment candidates in the idea-to-plan pipeline (project-idea-to-plan; "
     "conductor_plan.py; speak->board). How does a raw proposal become a priced candidate "
     "the drain can later pull?"),
    ("research-brief-assay",
     "How to grade NON-CODE deliverables (research / design briefs): an assay for prose "
     "and proposals. This campaign exposes the gap -- the behavior assay grades tests, not "
     "ideas. Propose a rubric + LLM-judge ensemble + consensus/debias (reuse the existing "
     "planner/critic ensemble + tiebreak machinery) so proposals get a real, gameable-"
     "resistant score."),
    ("fleet-model-residency-planner",
     "A cross-node model-residency planner: which model lives on which GPU given VRAM + "
     "demand, fleet-wide (OMEN 5070, AM4 4070Ti + 2x Arc, VMs). Extend the AM4 catalog + "
     "JS7 residency idea (hearth/scheduler, gather_am4_catalog) from one box to the whole "
     "mechnet. When to warm/evict a model, and who decides."),
    ("ledger-rebuild-dr",
     "Disaster-recovery / reproducibility: deterministically rebuild the ENTIRE knowledge "
     "store from the append-only event ledger at fleet scale (the CQRS --from-zero + "
     "golden-determinism work, docs/CQRS-ES-STANDARDIZATION). What's missing for a "
     "one-command full rebuild + continuous determinism verification across nodes?"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = []
    for i, (slug, question) in enumerate(IDEAS):
        pair = PAIRS[i % len(PAIRS)]
        body = PREAMBLE.format(slug=slug) + question + POSTAMBLE.format(slug=slug)
        if args.dry_run:
            print(f"[dry] {slug:34s} -> {pair}")
            manifest.append({"slug": slug, "builders": pair, "plan_id": None})
            continue
        res = submit_task(body, builders=pair, plan_id_hint=f"spec-{slug}",
                          task_class="research")
        ok = res.get("ok")
        pid = res.get("plan_id")
        print(f"{'OK ' if ok else 'ERR'} {slug:34s} {pid}  {res.get('builders')}")
        manifest.append({"slug": slug, "builders": res.get("builders"),
                         "plan_id": pid, "ok": ok, "error": res.get("error")})

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "speculation_manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"count": len(manifest), "ideas": manifest}, f, indent=2)
    print(f"\nmanifest -> {out}  ({len(manifest)} ideas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
