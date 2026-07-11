# Regret-trend readout — 2026-07 (S5)

**Written:** 2026-07-11 · Fable session `cdcabbd4` · scheduler-lane step S5 of
[SCHEDULER-STRATEGY.html](../SCHEDULER-STRATEGY.html) · evidence for the H1 actuation decision (ADR-0016).

## The gate being evaluated

[ADR-0008](adr/0008-scheduler-advisory-first.md), promotion condition verbatim:

> **The scheduler is advisory until a ledgered regret trend — accrued automatically, on real runs —
> proves it should order the CCMETA list.**

and:

> JS5 […] is specified in the plan but **not built** until the trend shows sustained,
> non-trivial savings. Synthetic assays justify the formulation, not the actuation.

## VERDICT

**`GATE: insufficient — no trend series exists (the designed accrual was never wired), and the
single fresh replay's token signal is a classification artifact, not evidence.`**

Per the strategy doc's own rule: thin data is not padded into a story. What follows is why the
data is thin, and the exact countdown that converts H1 from a judgment call into a checklist.

## Finding 1 — the "week of patrol-tick regret records" does not exist

The strategy doc (and session memory) claimed the U3 patrol tick has been ledgering a
`schedule_hindsight` regret record every 15 minutes since 2026-07-04. **Checked: false.**

- The ledger (`hearth/var/ledger/events.ndjson`, 4,526 events at read time) contains exactly
  **one** `schedule_hindsight` event, dated `2026-07-05T09:48:55Z`, and it **failed**:
  `guard: tool 'schedule_hindsight' references knowledge store path … but is not a registered
  knowledge tool`. (That guard bug was later fixed — `hearth/tests/kernel/test_guards.py`
  now pins `schedule_hindsight` passing on the capacity path — but nobody re-ran the accrual.)
- ADR-0008 designed the accrual as "the regret summary rides the patrol's own ledger event."
  It was never implemented on the path that actually runs: the watchdog's snapshot tick calls
  `patrol(refresh=False)` (`fleet/mechnet_watchdog.py:109`), which skips the hindsight leg
  entirely. None of the three ADR-0015 in-gateway timers (`hearth/kernel/timers.py`) invokes
  the `refresh=True` path that would run `schedule_hindsight(limit=20)`.
- The 15-minute events that DO exist (`mechnet_watchdog.patrol_snapshot`, 1,792 of them) carry
  liveness gaps only — no regret payload, and ledger events store result *digests*, not results.

**Consequence:** the trend axis of the gate has zero observations. Not "thin" — absent.

## Finding 2 — the fresh replay's regret numbers are artifacts

Exact invocation (2026-07-11, via the HEARTH door):

```
mcp__hearth__schedule_hindsight(limit=50)          # capacity_path=knowledge/capacity.json (default)
# CLI equivalent, from repo root:
./fleet-worker-node/.venv-omen/Scripts/python.exe -c "from hearth.toolsurface.scheduler import schedule_hindsight; import json; print(json.dumps(schedule_hindsight(limit=50)['report']['regret']))"
```

Result (n_runs=50, solver FEASIBLE):

| | span_s | metered_tokens |
|---|---|---|
| actual | 8,811 | 4,000 |
| proposed | 10,200 | 0 |
| **regret** | **−1,389 (proposal slower)** | **4,000 "saved"** |

Why these numbers cannot ratify the gate:

1. **`tokens_saved=4000` is a locality-classification artifact.** It is exactly
   2 × `DEFAULT_EST_TOKENS` (2000) for the two `omen-worker-1` wins in the window.
   `_LOCAL_BUILDER_NAMES` (`hearth/scheduler/hindsight.py:34`) omits `omen-worker-1` — a
   **local** qwen3-coder builder — so its runs are phantom-charged as metered. Meanwhile the
   set *includes* `cc-builder-1`, the **frontier** claude/sonnet runner, so its eight wins in
   the window count as zero metered spend. The classification is wrong in both directions;
   the real token regret of this window is unknown, and plausibly ≈ 0 in both actual and
   proposed (ADR-0008's known gap stands: no dispatches carry real `task_class` /
   `est_tokens` / `tokens_out` yet — U6 shipped the mechanism, call sites don't use it).
2. **The span delta measures placeholders, not predictions.** Every one of the 50 jobs
   replayed with the identical default duration estimate (`est_s=600.0`); actual durations
   ranged 10–512s. A +16% proposed-makespan delta computed from uniform placeholder inputs
   says nothing about the solver's real packing quality.
3. **No trend is derivable from one observation** (see Finding 1). "Sustained" requires a
   series; n=1.

Reproducibility caveat: the replay window is "most recent 50 completed runs" — the numbers
reproduce exactly until a new run completes on the conductor, then the window slides.
Verified: a second invocation during this session returned identical aggregates.

## The countdown — what reopens H1

ADR-0016 can be ratified as **GO** only after, in order:

1. ✅ **DONE 2026-07-11 (`5245a31`) — locality classification fixed.** Locality now derives from
   the machine/runner registry: `fleet/inventory.toml` stamps a structured `runner_class` on
   every builder node (verified against each node's live `~/fleet-worker-node/runner.json`;
   cc-builder-1 carries none → the worker defaults to the metered claude runner = frontier,
   every other builder is an openai runner on a local backend = local),
   `ontology.load_runner_classes()` reads it (corrected declared fallback when absent), and both
   the solver pool's machine kinds and `hindsight._is_local_winner` consume it. Pinned by the
   two named tests: an omen-worker-1 win charges 0; a cc-builder-1 claude-runner win charges > 0.
   Re-run on this report's exact 50-run window: `actual.metered_tokens` 4,000 → 16,000
   (8 cc-builder-1 wins × `DEFAULT_EST_TOKENS`), omen-worker-1's phantom charge gone — the
   inversion this report predicted.
2. ✅ **DONE 2026-07-11 (`3bcaae5`) — the accrual is wired.** The 15-minute watchdog pass (what
   the ADR-0015 in-gateway `watchdog` timer runs) gained a fourth best-effort leg:
   `schedule_hindsight(limit=20)`, ledgered as one `mechnet_watchdog.hindsight` event per pass.
   The compact regret summary rides the event's **args** — deliberately, because ledger events
   store result *digests* only, while `args_preview` keeps 400 chars of canonical JSON; the
   summary is sized to always fit un-truncated, so the trend series is queryable
   (`Ledger.query(tool="mechnet_watchdog.hindsight")` → `json.loads(event["args_preview"])`).
   Failures ledger as ok=False — a visible hole in the series, not a silent gap. No timer-spec
   change was needed: the timer spawns a fresh subprocess per tick, so the leg armed on the
   first 15-min tick after landing.
3. **Accrue ≥ 7 days of ledgered replays** including ≥ 1 frontier-mixed dispatch that carries a
   real `tokens_out`, so token regret can be nonzero for a true reason. (Clock starts at the
   first `mechnet_watchdog.hindsight` event after `3bcaae5` landed, 2026-07-11.)

Until item 3 completes: **WAIT** remains the self-evident H1 answer — recorded evidence, named
condition, no judgment required.

## Session receipts

- Ledger inspected at event count 4,526 (last event `2026-07-11T13:54Z`); hindsight-event count: 1 (failed).
- Patrol snapshot buffer (`hearth/var/mechnet_watchdog_patrol_snapshots.json`): 12-entry rolling
  window, entries carry `{ts, ok, scanned, considered, gaps, gap_keys, error}` — no regret key.
- Fresh replay output preserved above; full per-run table available by re-running the invocation.
