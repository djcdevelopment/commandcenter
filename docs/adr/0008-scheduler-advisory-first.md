# ADR-0008 — The scheduler advises until the ledgered regret trend earns it dispatch

**Status:** Accepted (2026-07-04) — JS1–JS7 built + live; JS5 (actuation) deliberately unbuilt.
**Context sources:** SESSION-RETRO-2026-07-04.md (addendum 4), JOB-SHOP-SCHEDULER-PLAN.html,
Derek's 2019 article "Solving the Job Shop Problem" (OR-Tools), `hearth/scheduler/`,
`contracts/scheduler-decision.v1.schema.json`, the imagegen-250 assay
(`hearth/scheduler/experiments/`).

## Context

Derek's 2019 job-shop article is the system's declared north star: the lab's ledger, assay, and
capacity instrumentation are — and retroactively always were — the *inputs* a CP-SAT scheduler
needs (task durations, machine capabilities, constraints). In one session the solver was built:
two-economies objective (metered frontier tokens dominate makespan), model-residency state,
sequence-dependent setup times from measured `warmup.wall_ms`, a single DDR4 staging slot on AM4,
per-card VRAM budgets. A 250-job synthetic assay beat FIFO 19→0 on deadline misses with 88% less
setup time — structurally, by packing residency across both cards.

But the hindsight replay of 50 *real* conductor runs showed zero token regret — the history is
all-local, so the solver has not yet demonstrated value on real dispatch decisions. And the
"one scheduler" doctrine (Banked Fire #1) stands: the conductor owns dispatch; HEARTH only names
eligible builders via CCMETA.

## Decision

**The scheduler is advisory until a ledgered regret trend — accrued automatically, on real runs —
proves it should order the CCMETA list.**

- `propose_schedule` / `schedule_hindsight` are read-only tools; nothing dispatches from them.
- Watchfire's 15-minute patrol refreshes the solver's inputs (`capacity.json`,
  `am4_catalog.json`) and runs a 20-run hindsight replay every tick; the regret summary rides
  the patrol's own ledger event. The evidence accrues unattended.
- JS5 (scheduler-informed CCMETA ordering, behind `HEARTH_SCHEDULER_ADVISE=1`, fan-out minimum
  preserved) is specified in the plan but **not built** until the trend shows sustained,
  non-trivial savings. Synthetic assays justify the formulation, not the actuation.
- The conductor remains the one scheduler regardless: even actuated, HEARTH orders eligibility,
  nothing more.

## Consequences

- The gate is now *data*, not a meeting: when the ledgered trend turns positive, JS5 is a small
  gated diff with regression tests proving flag-off behavior is byte-identical.
- Known gaps that must close for real regret signal: dispatches must carry real `task_class` /
  `est_tokens` (U6 shipped the mechanism; call sites must use it), and frontier-mixed history
  must exist before token regret can be nonzero.
- Cold-load vs first-inference remains unmeasured on AM4 (open item in PICKUP-battlemage.md);
  `warmup_ms_p50` is the accepted proxy until then.
