# ADR-0012 — The commander issues intent; mechnet carries it, with no frontier model in the run loop

Status: Accepted (2026-07-05)

## Context

The lab's own workflow kept putting a frontier model (Opus, in an interactive session) in the
*dispatcher* seat: hand-crafting each brief, writing the pour driver, polling `task_status`,
harvesting results, synthesizing. That is precisely the orchestration the system exists to
eliminate — capture-first, many-perspectives, laps on electricity are supposed to be *standing
mechnet capabilities*, not something re-improvised per session (see
[[project-centralized-capture-principle]], [[feedback-how-derek-scales]]). Derek named the
bottleneck directly: "mechnet shouldn't need a frontier model to load and retrieve work." He wants
to issue intent in three shapes — *refine & review this a bunch of times* / *build & score this* /
*both* — and have mechnet carry it.

## Decision

**Model commander intent as a first-class lane with three modes, driven entirely by local models;
frontier builds the lane once and then exits the run loop.**

- **REFINE** = a local author↔critic convergence loop (`hearth/commander/refine.py`): a planner
  model drafts, critic model(s) review with an explicit `VERDICT: CONVERGED|REVISE` signal, the
  draft iterates until the critics are satisfied or a round budget is spent. Hybrid engine: local
  sync loop by default, `--fan` spreads reviews across several local models.
- **BUILD** = the existing fleet lane (`submit_task` → builders → assay); **BOTH** = refine to a
  converged spec, then build it. (BUILD/BOTH wrappers deferred; REFINE shipped first.)
- Exposed **two ways**: HEARTH door tools (`refine_idea`/`refine_result`, ledgered like every tool)
  **and** a CLI (`python -m hearth.commander.cli`), so intent can be issued with no Claude session.
- The whole run loop — expand, review, revise, and even the results digest — runs on local models
  (OMEN ollama / the B70s). **Frontier is required only to build or improve the lane itself**, which
  is exactly the tier CLAUDE.md reserves for frontier (architecture, judgment, multi-file coherence).

This is the general principle behind the specific slice: **the highest-leverage use of frontier is
building the tool that removes frontier from the next hundred runs.** The same shape produced the
matrix dataset harness (`hearth/experiments/`), which reuses `run_refine` as its per-cell engine.

## Consequences

- **Good:** Derek drives with a one-liner; capture is preserved (door tools ledger the intent +
  result); the lane composes with the fleet (BUILD) and the wind-tunnel (the matrix harness is a
  swept REFINE). Reuse over rebuild — it sits on `local_generate`, `submit_task`, and the existing
  backend routing.
- **Cost / open:** per-turn author/critic calls are not yet individually ledgered (only intent +
  final are captured) — a gap against the one-boundary principle ([ADR-0005](0005-one-boundary-three-planes.md))
  to close. `--fan` with a big slow MoE (mixtral) trades wall-clock for diversity. Mode
  auto-classification is deferred (explicit mode for now).
- **Boundary preserved:** advisory-first discipline carries over — REFINE proposes, it does not act;
  BUILD/BOTH still route through the conductor as the one scheduler
  ([ADR-0008](0008-scheduler-advisory-first.md)).
