"""Matrix dataset generator — planning quality across models x laps x roles.

Each CELL is one run_refine planner<->critic loop, with the planner and critic
routed to specific HEARTH backends (AM4 B70 via "am4-oxen" + a slot-model, OMEN
via ollama). After the loop, a held-out CRITIC PANEL scores the final proposal
0-100 against a rubric (adapted from b70tools/eval/scoring-rubric.md). One row
per cell -> a comparable dataset across the hardware.

Pure except for the injected ``generate`` (defaults to local_generate), so the
whole sweep is unit-testable offline. Residency (which AM4 model is loaded) is
NOT this module's job — residency.py drives vllama up/down through its gates.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

# ---- The three planning archetypes (from hardware-capability-matrix.md open cells) ----
PROMPTS: dict[str, str] = {
    "choose-next-agent": (
        "You are the controller of a small fleet of build/plan agents (a fast local "
        "planner, a slow careful critic, a frontier reviewer, and a cheap classifier). "
        "A new work item arrives: 'refactor an auth module and add tests, deadline 2h, "
        "medium risk'. Decide which agent(s) to dispatch, in what order, and why. State "
        "the single next action concretely."
    ),
    "escalate-or-not": (
        "A local model produced a plan for a database migration. Its self-reported "
        "confidence is medium; the change is irreversible and touches production data. "
        "Decide whether to escalate to a stronger/more expensive reviewer or proceed as-is. "
        "Give the decision, the threshold logic behind it, and what evidence would flip it."
    ),
    "plan-skeleton": (
        "Produce a plan skeleton for adding observability (metrics + tracing) to an "
        "existing multi-service system with no current instrumentation. Give the phases, "
        "the first shippable slice, the key risks, and the done-criteria for phase 1. "
        "Keep it a skeleton, not an essay."
    ),
    "risk-triage": (
        "A team hands you eight open risks on a system launching in two weeks: a flaky "
        "integration test, an unpatched dependency CVE, a single-owner service, unbounded "
        "retry logic, no runbook, a slow query under load, a hard-coded credential, and "
        "missing backups. Rank the top three to fix before launch and justify the cut line "
        "for the rest. Be decisive."
    ),
    "resource-allocation": (
        "You have 3 engineers and one week before a demo. Four things want doing: fix a "
        "P1 data-loss bug, build the demo's headline feature, pay down test debt blocking "
        "CI, and write docs a customer asked for. Allocate the engineers across the week "
        "and state what you explicitly will NOT do, and why."
    ),
    "rollback-decision": (
        "A deploy 40 minutes ago correlates with a 15% error-rate rise on one endpoint, but "
        "a marketing push started 30 minutes ago and traffic is up 3x. Latency is normal. "
        "Decide: roll back now, hold and watch, or forward-fix. Give the decision, the signal "
        "that would change it, and the time budget before you must act."
    ),
}

# ---- Prompting-study variants (Phase: does the critic/author PROMPT shape the laps curve?) ----
# Hypothesis: the L3-L4 over-refinement collapse is driven by the critic pushing complexity.
# A minimalist critic should flatten it; a thorough critic should steepen it.
MINIMALIST_CRITIC = (
    "You are a decisive editor. Reward brevity, clarity, and commitment. Push the author to "
    "CUT scope, remove hedging and caveats, and make the call. Treat verbosity, over-engineering, "
    "and unnecessary complexity as DEFECTS to flag. A shorter, sharper answer is a better answer. "
    "Do not ask for more content — ask for less, better."
)
THOROUGH_CRITIC = (
    "You are an exhaustive reviewer. Surface EVERY gap, edge case, failure mode, missing "
    "consideration, and unstated assumption. An answer that omits any relevant factor is "
    "incomplete. Push relentlessly for completeness, rigor, and coverage — more thorough is better."
)
CONCISE_AUTHOR = (
    "You are a systems architect who prizes decisiveness. Produce the SHORTEST proposal that is "
    "complete and buildable. Lead with the decision. No preamble, no restating the question, "
    "minimal hedging. Use structure only when it aids clarity."
)

# Each config = one prompt-variant arm. author_system/critic_system None -> run_refine defaults.
STUDY_CONFIGS = [
    {"name": "baseline", "author_system": None, "critic_system": None},
    {"name": "minimalist-critic", "author_system": None, "critic_system": MINIMALIST_CRITIC},
    {"name": "thorough-critic", "author_system": None, "critic_system": THOROUGH_CRITIC},
    {"name": "concise-author", "author_system": CONCISE_AUTHOR, "critic_system": None},
    # follow-up: stack the two winners — does prompt design fully defeat over-refinement?
    {"name": "concise+minimalist", "author_system": CONCISE_AUTHOR, "critic_system": MINIMALIST_CRITIC},
]


def configs_by_name(names: list[str]) -> list[dict]:
    """Select STUDY_CONFIGS arms by name (for a focused follow-up sweep)."""
    by = {c["name"]: c for c in STUDY_CONFIGS}
    return [by[n] for n in names if n in by]

# ---- Critic-panel scorer ----
JUDGE_SYSTEM = (
    "You are a strict evaluator of planning responses. Score on specificity, "
    "feasibility, risk-awareness, completeness, and directness. Reward concrete, "
    "actionable answers; penalize vagueness and hedging."
)
_SCORE_PROMPT = (
    "Planning task:\n{prompt}\n\n---\nResponse to score:\n{response}\n\n---\n"
    "Score the response 0-100. Output ONLY a final line exactly:\nSCORE: <integer 0-100>"
)
_SCORE_RE = re.compile(r"SCORE:\s*(\d{1,3})", re.IGNORECASE)

# Default held-out judge panel: OMEN's resident coder MoE (separate from cell roles).
DEFAULT_JUDGES: list[tuple] = [(None, "qwen3-coder:30b")]


def _parse_score(text: str) -> Optional[int]:
    matches = _SCORE_RE.findall(text or "")
    if not matches:
        return None
    return max(0, min(100, int(matches[-1])))


def score_proposal(final: str, prompt: str, judges: list[tuple],
                   generate: Callable[..., dict], timeout_s: int = 600) -> dict:
    """Score a final proposal with a held-out judge panel; return {mean, judges}."""
    per_judge = []
    for jb, jm in judges:
        r = generate(_SCORE_PROMPT.format(prompt=prompt, response=final), model=jm,
                     backend=jb, system=JUDGE_SYSTEM, max_tokens=300, timeout_s=timeout_s)
        score = _parse_score(r.get("text", "")) if r.get("ok") else None
        per_judge.append({"model": r.get("model", jm), "backend": jb, "score": score,
                          "ok": bool(r.get("ok")) and score is not None})
    scored = [j["score"] for j in per_judge if j["ok"]]
    mean = round(sum(scored) / len(scored), 1) if scored else None
    return {"mean": mean, "n_scored": len(scored), "judges": per_judge}


# ---- Cells ----
@dataclass(frozen=True)
class Role:
    node: str            # "am4" | "omen"
    backend: Optional[str]   # HEARTH backend name (None -> omen-ollama default)
    model: str           # model id / facade alias


@dataclass(frozen=True)
class Cell:
    cell_id: str
    prompt_id: str
    planner: Role
    critic: Role
    laps: int
    ordering: str        # "am4->omen" | "omen->am4" | ... (planner->critic node)
    variant: str = ""    # prompt-variant arm name (e.g. "minimalist-critic"), "" = baseline
    author_system: Optional[str] = None   # per-cell author system-prompt override
    critic_system: Optional[str] = None   # per-cell critic system-prompt override


def _node_of(backend: Optional[str]) -> str:
    return "am4" if (backend or "").startswith("am4") else "omen"


def build_pilot_cells(am4_models: list[tuple], omen_model: tuple,
                      prompt_ids: Optional[list[str]] = None,
                      laps: tuple = (1, 3), repeats: int = 1) -> list[Cell]:
    """The pilot grid: each AM4 model paired with the OMEN model, both orderings,
    every prompt, every lap count. am4_models/omen_model are (backend, model) pairs.
    ``repeats`` > 1 runs each cell N times (distinct cell_ids ``_r<k>``) so the
    stochastic score gradient can be averaged — a confirmation sweep."""
    prompt_ids = prompt_ids or list(PROMPTS.keys())
    omen = Role(_node_of(omen_model[0]), omen_model[0], omen_model[1])
    cells: list[Cell] = []
    for ab, am in am4_models:
        am4 = Role(_node_of(ab), ab, am)
        for pid in prompt_ids:
            for lp in laps:
                for planner, critic in ((am4, omen), (omen, am4)):
                    ordering = f"{planner.node}->{critic.node}"
                    base = f"{am.split(':')[0]}_{pid}_L{lp}_{ordering}".replace("/", "-")
                    for k in range(max(1, repeats)):
                        cid = base if repeats <= 1 else f"{base}_r{k}"
                        cells.append(Cell(cid, pid, planner, critic, lp, ordering))
    return cells


def build_planner_critic_cells(prompt_ids: Optional[list[str]] = None,
                               laps: tuple = (1, 2, 3), repeats: int = 1,
                               planner: tuple = ("am4-oxen", "oxen-planner"),
                               critic: tuple = ("am4-oxen", "oxen-critic")) -> list[Cell]:
    """A DEDICATED planner<->critic loop with FIXED asymmetric roles (not swapped):
    a strong planner drafts, a distinct critic reviews. This is the setup where
    refinement laps are supposed to pay off — the proper test of the "laps hurt"
    finding, which used two general models with a generic critic instead."""
    prompt_ids = prompt_ids or list(PROMPTS.keys())
    p = Role(_node_of(planner[0]), planner[0], planner[1])
    c = Role(_node_of(critic[0]), critic[0], critic[1])
    cells: list[Cell] = []
    for pid in prompt_ids:
        for lp in laps:
            base = f"{planner[1]}x{critic[1]}_{pid}_L{lp}"
            for k in range(max(1, repeats)):
                cid = base if repeats <= 1 else f"{base}_r{k}"
                cells.append(Cell(cid, pid, p, c, lp, f"{p.node}:planner->{c.node}:critic"))
    return cells


def build_variant_cells(configs: list[dict], prompt_ids: Optional[list[str]] = None,
                        laps: tuple = (1, 2, 3, 4), repeats: int = 1,
                        planner: tuple = ("am4-oxen", "oxen-planner"),
                        critic: tuple = (None, "qwen3-coder:30b")) -> list[Cell]:
    """A PROMPTING study: sweep prompt-variant arms (configs) x prompt x laps. Roles are
    FIXED (planner drafts, critic reviews) and memory-safe cross-machine by default (one
    model on AM4). Each config carries author_system/critic_system overrides. Repeats are
    the OUTER loop so a partial run still covers the whole grid at n>=1."""
    prompt_ids = prompt_ids or list(PROMPTS.keys())
    p = Role(_node_of(planner[0]), planner[0], planner[1])
    c = Role(_node_of(critic[0]), critic[0], critic[1])
    cells: list[Cell] = []
    for k in range(max(1, repeats)):
        for cfg in configs:
            for pid in prompt_ids:
                for lp in laps:
                    cid = f"{cfg['name']}_{pid}_L{lp}_r{k}"
                    cells.append(Cell(cid, pid, p, c, lp, f"{p.node}:planner->{c.node}:critic",
                                      variant=cfg["name"],
                                      author_system=cfg.get("author_system"),
                                      critic_system=cfg.get("critic_system")))
    return cells


def run_cell(cell: Cell, generate: Callable[..., dict],
             judges: Optional[list[tuple]] = None,
             on_progress: Optional[Callable[[str], None]] = None) -> dict:
    """Run one matrix cell (a planner<->critic refine loop) + score it. Returns a row."""
    from hearth.commander.refine import run_refine
    judges = judges if judges is not None else DEFAULT_JUDGES
    prompt = PROMPTS[cell.prompt_id]
    if on_progress:
        on_progress(f"cell {cell.cell_id}: planner={cell.planner.model} "
                    f"critic={cell.critic.model} laps={cell.laps}")
    res = run_refine(
        prompt, rounds=cell.laps, generate=generate,
        author_model=cell.planner.model, author_backend=cell.planner.backend,
        critic_specs=[(cell.critic.backend, cell.critic.model)],
        author_system=cell.author_system, critic_system=cell.critic_system,
    )
    score = (score_proposal(res.get("final") or "", prompt, judges, generate)
             if res.get("ok") else None)
    return {
        "cell_id": cell.cell_id, "prompt_id": cell.prompt_id, "laps": cell.laps,
        "ordering": cell.ordering, "variant": cell.variant,
        "planner": asdict(cell.planner), "critic": asdict(cell.critic),
        "ok": res.get("ok"), "converged": res.get("converged"),
        "rounds_run": res.get("rounds_run"), "cost": res.get("cost"),
        "score": score, "error": res.get("error"), "final": res.get("final"),
    }


def run_matrix(cells: list[Cell], generate: Callable[..., dict],
               judges: Optional[list[tuple]] = None,
               on_progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Run every cell in order (residency is arranged by the caller) -> dataset rows."""
    rows = []
    for i, cell in enumerate(cells, 1):
        if on_progress:
            on_progress(f"[{i}/{len(cells)}] {cell.cell_id}")
        rows.append(run_cell(cell, generate, judges=judges, on_progress=on_progress))
    return rows


def dataset_summary(rows: list[dict]) -> dict:
    """Aggregate: mean score per (planner.model, laps) and per prompt."""
    def _mean(vals):
        v = [x for x in vals if x is not None]
        return round(sum(v) / len(v), 1) if v else None
    by_planner: dict[str, list] = {}
    by_prompt: dict[str, list] = {}
    by_laps: dict[str, list] = {}
    by_prompt_laps: dict[str, list] = {}
    by_variant_laps: dict[str, list] = {}
    by_variant: dict[str, list] = {}
    for r in rows:
        s = (r.get("score") or {}).get("mean")
        by_planner.setdefault(f"{r['planner']['model']}|L{r['laps']}", []).append(s)
        by_prompt.setdefault(r["prompt_id"], []).append(s)
        by_laps.setdefault(f"L{r['laps']}", []).append(s)
        by_prompt_laps.setdefault(f"{r['prompt_id']}|L{r['laps']}", []).append(s)
        v = r.get("variant") or "baseline"
        by_variant_laps.setdefault(f"{v}|L{r['laps']}", []).append(s)
        by_variant.setdefault(v, []).append(s)

    def _agg(d):
        return {k: {"mean": _mean(v), "n": len([x for x in v if x is not None])}
                for k, v in sorted(d.items())}
    return {
        "cells": len(rows),
        "ok_cells": sum(1 for r in rows if r.get("ok")),
        "mean_score_by_laps": _agg(by_laps),               # the headline: does refining help?
        "mean_score_by_planner_laps": {k: _mean(v) for k, v in sorted(by_planner.items())},
        "mean_score_by_prompt": {k: _mean(v) for k, v in sorted(by_prompt.items())},
        "mean_score_by_prompt_laps": _agg(by_prompt_laps),  # is the effect task-dependent?
        "mean_score_by_variant": {k: _mean(v) for k, v in sorted(by_variant.items())},
        "mean_score_by_variant_laps": _agg(by_variant_laps),  # does the PROMPT shape the laps curve?
    }
