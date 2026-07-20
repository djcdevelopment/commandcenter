# Regret-trend readout — 2026-07 (S5)

**Written:** 2026-07-11 · Fable session `cdcabbd4` · scheduler-lane step S5 of
[SCHEDULER-STRATEGY.html](../SCHEDULER-STRATEGY.html) · evidence for the H1 actuation decision (ADR-0016).
**Closed out:** 2026-07-20 · Fable session `72209076` — the countdown re-check is the
[close-out section](#2026-07-20-close-out--the-countdown-re-checked) directly below the gate quote;
everything from [VERDICT (2026-07-11)](#verdict-2026-07-11) down is the original readout, preserved.

## The gate being evaluated

[ADR-0008](adr/0008-scheduler-advisory-first.md), promotion condition verbatim:

> **The scheduler is advisory until a ledgered regret trend — accrued automatically, on real runs —
> proves it should order the CCMETA list.**

and:

> JS5 […] is specified in the plan but **not built** until the trend shows sustained,
> non-trivial savings. Synthetic assays justify the formulation, not the actuation.

## 2026-07-20 close-out — the countdown re-checked

**Written:** 2026-07-20 · Fable session `72209076`. Countdown item 3's ≥7-day clock started
2026-07-11 and expired 2026-07-18; this section closes S5 with what actually accrued.

### VERDICT

**`GATE: insufficient — the trend series now exists (662 ledgered replays over 8.7 days) and it
moved correctly on a live frontier-mixed dispatch, but every token figure in it is still an
estimate: no real tokens_out exists anywhere in the pipeline, and three structural holes mean
more waiting cannot produce one.`**

The evidence-side reading for H1 is unchanged — **WAIT** — but the remaining condition has
changed shape: it is no longer time. The "real `tokens_out`" clause is unsatisfiable by accrual
alone (requirement B below); satisfying it is a small build (close holes 2→3: record token
spend in the conductor's result.json, then collect it in the hindsight gather). Alternatively
Derek may rule that estimate-grade token evidence is acceptable for the gate — that is the
judgment call this document exists to inform, and it is his alone.

### Requirement A — ≥ 7 days of ledgered replays: **MET**

Series: `Ledger.query(tool="mechnet_watchdog.hindsight")` over `hearth/var/ledger/events.ndjson`
(read at event count 9,286, last event `2026-07-20T08:10Z`):

- **662 events**, first `2026-07-11T14:54:48Z` (the first tick after `3bcaae5` landed — the
  clock start item 3 named), last `2026-07-20T08:06:48Z` → **8 d 17 h elapsed**, events on all
  10 calendar days.
- 656 ok / **6 ok=false** — all six are SSH `ConnectTimeout` to cc-conductor, ledgered as
  visible holes in the series exactly as designed.
- Cadence: ~79 % of the ~837 expected 15-min ticks landed; 22 gaps > 30 min (largest 8.7 h,
  `2026-07-17T16:08Z → 2026-07-18T00:50Z`) — OMEN downtime windows, not accrual bugs.

The series has exactly two regimes:

| window (UTC) | ok events | n_runs | actual tokens | proposed tokens | tokens_saved | span_delta_s |
|---|---|---|---|---|---|---|
| 07-11 14:54 → 07-18 03:05 | 518 (all identical) | 20 | 0 | 0 | 0 | −3838 |
| 07-18 03:20 → 07-20 08:06 | 138 (settling → 121 identical since 07-18 07:19) | 20 | 2000 | 0 | 2000 | −3673 |

Regime 1 is flat because the replay window was static: **zero** conductor runs completed between
2026-07-11 and 2026-07-18. Regime 2 begins at the first tick after the window's one
frontier-mixed dispatch (below). The trend instrument works — it moved when reality moved, in
the right direction, at the right tick — but a 2-point trend over a mostly-static backlog is
shape, not magnitude. "Sustained, non-trivial savings" (ADR-0008) cannot be read off this yet.

### Requirement B — ≥ 1 frontier-mixed dispatch with real `tokens_out`: **HALF-MET**

The dispatch happened, and the locality chain proved itself live end-to-end:

1. `submit_task` `2026-07-18T03:06:42Z` → plan `hearth-g3-queue-and-forget-91adaa10`,
   builders `cc-builder-1` + `cc-builder-2`.
2. `cc-builder-1` — the claude runner, the inventory's only `runner_class = "frontier"`
   builder — **won** at `03:14:38Z` (conductor `runs/…/result.json`, verified over SSH).
3. The very next hindsight tick (`03:20:39Z`) flipped `actual.metered_tokens` 0 → 2000, where
   it has held since. Dispatch → conductor record → gather → `runner_class` classification →
   ledgered trend: the `5245a31` locality fix, working on live data.

But the "carries a real `tokens_out`" clause fails, and not by chance — a recursive key-scan of
all seven result.json files completed since 07-11 finds **no token field anywhere**. The 2000 is
exactly 1 × `DEFAULT_EST_TOKENS`. Three holes, each independently fatal to the clause:

1. **Call sites still don't stamp.** Both 07-18 `submit_task` events carry `est_tokens: null`,
   and `task_class` is null in all seven result.json files (so every replayed duration is the
   600 s default, too). U6 shipped the stamping mechanism 2026-07-04; nothing uses it.
2. **The conductor records no token counts.** result.json carries rich build/assay detail but
   no spend field for any builder — the claude runner's metered usage is never captured at all.
3. **The gather drops the field anyway.** `_GATHER_SRC_TEMPLATE`
   (`hearth/toolsurface/scheduler.py`) collects status/winner/task_class/duration only, so
   `_estimate_tokens_for_record`'s preferred `tokens_out` key can never arrive via the live path.

Until at least holes 2→3 are closed (record the spend, then collect it), token regret is
estimate-grade **by construction**, and the countdown as written cannot complete.

### Data-quality caveats for whoever reads the trend

- **5 of the 7 new completed runs are null-action wins.** The five
  `hearth-drain-known-bad-retest-…` runs (05:05–07:05 on 07-18) report
  `"agent produced nothing"` (rc 3, empty laps) on every builder, yet each completes with a
  winner (`cc-builder-2`) and enters the hindsight window as 19–20 s of "real work" — the
  ADR-0001 exploit shape, now visible inside the regret evidence itself. The S4 acceptance gate
  landed 2026-07-11 but is not wired into this lane.
  **Tagged 2026-07-20 (`babc172`, Derek's call):** drain runs are now labeled
  `task_class="proofing"` at both ends — the drain dispatch stamps it via
  `submit_task(task_class=)`, and the hindsight gather derives it from the drain lane's
  `hearth-drain-` plan-id prefix, which retroactively tags these five historical records in
  every future replay (live-verified 5/5 in the current window; an explicit `task_class` in
  result.json wins). The tag *labels* proofing data; it does not exclude it — whether proofing
  runs should count toward the regret trend at all is part of the H1 read.
- **Span regret still measures placeholders** (Finding 2 below stands): all 20 replayed jobs
  carry `est_s = 600.0`, proposed span is a constant 6000.0, so `span_delta_s = −3673` says
  nothing about real packing quality.
- On the window's one real frontier job the solver behaved as designed, directionally: it
  proposes keeping `g3-queue-and-forget` local (`am4-worker-1`), which is where the entire
  `tokens_saved = 2000` comes from. Direction credible; magnitude an estimate.

### Reproduction

```
# the trend series (ledger side), from repo root:
python -c "import json,collections; ev=[json.loads(l) for l in open(r'hearth/var/ledger/events.ndjson',encoding='utf-8')]; hs=[e for e in ev if e['tool']=='mechnet_watchdog.hindsight']; print(len(hs), hs[0]['ts'], hs[-1]['ts']); print(collections.Counter(json.loads(e['args_preview'])['regret']['tokens_saved'] for e in hs if e['ok']))"
# -> 662 2026-07-11T14:54:48.644286Z 2026-07-20T08:06:48.039668Z   Counter({0: 518, 2000: 138})

# the fresh replay (matches the 121-event stable signature; watchdog leg uses limit=20):
./fleet-worker-node/.venv-omen/Scripts/python.exe -c "from hearth.toolsurface.scheduler import schedule_hindsight; import json; print(json.dumps(schedule_hindsight(limit=20)['report']['regret']))"
# -> {"tokens_saved": 2000, "span_delta_s": -3673.0}
```

Fresh replay 2026-07-20 reproduced the stable signature exactly: `n_runs=20`, actual
`2327.0 s / 2000`, proposed `6000.0 s / 0`, `FEASIBLE`. (The door form
`mcp__hearth__schedule_hindsight(limit=20)` timed out once at the MCP client this session —
SSH gather + solve exceeded the client budget; the CLI form above is the recorded reproduction
path.) The replay numbers slide when the next conductor run completes — reproduce against the
ledger series if the live window has moved.

---

## VERDICT (2026-07-11)

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
   **Checked 2026-07-20 (close-out above): elapsed ✓ · frontier-mixed dispatch ✓ · real
   `tokens_out` ✗ — structurally unreachable without a build; see requirement B up top.**

Until item 3 completes: **WAIT** remains the self-evident H1 answer — recorded evidence, named
condition, no judgment required.

## Session receipts

- Ledger inspected at event count 4,526 (last event `2026-07-11T13:54Z`); hindsight-event count: 1 (failed).
- Patrol snapshot buffer (`hearth/var/mechnet_watchdog_patrol_snapshots.json`): 12-entry rolling
  window, entries carry `{ts, ok, scanned, considered, gaps, gap_keys, error}` — no regret key.
- Fresh replay output preserved above; full per-run table available by re-running the invocation.
