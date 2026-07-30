# ADR-0027 — Gateway dispatches are not observations until we say what they observe

**Status:** Proposed (2026-07-30) — **awaiting ratification. Nothing in the belief layer
changes until this is accepted.** The plumbing repair described under "Already fixed" has
landed; the mapping proposed under "Decision needed" has deliberately NOT been built.
**Context sources:** ADR-0010 (two ledgers, two bounded contexts), ADR-0011 (double-write
is intentional), `hearth/projection/ledger_adapter.py`, `hearth/projection/freshness.py`,
`tools/workflow/project_associations.py`, `tools/workflow/project_capacity.py`,
`contracts/capacity-observation.v1.schema.json`, EVIDENCE-HUNT-A3.md,
`C:\work\handoffs\SINCE-BOOT-FORENSIC-PERIODIZATION-2026-07-30.md` §8.1.

## Context

### The freeze

From 2026-07-04 to 2026-07-30, eleven of the thirteen knowledge projections were frozen
while the rebuild reported `ok: True` on every run. `capacity.json` and `offload.json`
stayed current to the hour because they read the kernel ledger
(`hearth/var/ledger/events.ndjson`, `hearth-event.v1`) directly. Everything else replays
the workflow corpus (`runs/**/events.jsonl`), which stopped growing.

Cause: the only sanctioned bridge between the two bounded contexts — `ledger_adapter.py`,
named as the anti-corruption translator in ADR-0010 — is reachable only as a hand-run CLI.
Its cursor sat at **line 3 of a 20,110-line ledger**. Nothing scheduled it. The rebuild was
faithfully replaying a corpus that had not moved in 26 days.

The identical `sha256:6ec4ad2f…` digest across all eleven files was the symptom that looked
like a short-circuit and was not: `_restamp_written_file` stamps the same
`Corpus.enumerate("runs")` digest and count into every event-derived document. Same frozen
corpus, same digest. The files were rewritten on every run, from frozen input.

Worth naming plainly, because it is the reusable lesson: **a from-zero rebuild cannot
detect this class of failure by construction.** Replaying a frozen corpus succeeds, and the
staging directory it validates against starts empty, so `corpus_guard` has no prior file to
compare. Every internal signal said healthy. The staleness was only visible from outside the
replay, against a reference that moves.

### Already fixed (landed, not part of this decision)

1. **The bridge runs on the rebuild path.** `rebuild.main()` advances it before replaying.
   It sits in `main()` rather than inside `rebuild_knowledge()` on purpose:
   `rebuild_knowledge` is a pure read-side replay with a byte-untouched-on-failure
   contract, and appending to the event store is a write-side concern — ADR-0010's
   direction, preserved.
2. **Self-monitoring is filtered.** 19,295 of 20,110 rows (96.1%) were the lab watching
   itself: the watchdog's patrol/watchfire/trend/hindsight/dream loop, `bankedfire_drain`
   ticks, and `kernel_status` probes. Bridging all of it produced an 18 MB git-tracked
   heartbeat file and a `corpus_event_count` of 20,124 that measured nothing. The filter
   keys on `caller.id` and `profile`, not tool names, because caller identity is what those
   loops declare about themselves and tool names churn. Result: 815 bridged, corpus
   33 → **829** events, watermark 2026-07-04T05:50:00Z → **2026-07-30T07:38:54Z**, digest
   `6ec4ad2f…` → `008a2bd1…`, file 18 MB → 876 KB.
3. **A freshness guard** (`hearth/projection/freshness.py`) fails the run when the bridge
   backlog, the corpus watermark, or any document's stamped watermark falls behind the
   **ledger head** — not the wall clock, per the standing rule that "now" is the newest
   fact the lab holds. Documents that stamp no watermark are reported `checked: false`
   rather than counted as fresh.

### What the repair did not fix, and cannot

`capability_count` is still **0**. `evidence_watermark` on `associations.json`,
`capabilities.json`, `coverage.json`, and `policy.json` is still 2026-07-04. This is not a
residual bug — two independent structural facts block it:

1. **Belief projectors do not read events; they read observations.**
   `extract_observations` walks `event["artifact_refs"]` for
   `artifact_type == "capacity_observation"` and loads a separate JSON artifact from disk.
   `map_event` emits no `artifact_refs` at all. The observation count is unchanged at 25,
   so `evidence_watermark` — computed as `max(o["timestamp"] for o in observations)` — cannot
   move no matter how many events are bridged.
2. **Every bridged event belongs to one workflow.** The adapter stamps constant
   `WORKFLOW_ID = "wf-hearth-gateway"` / `RUN_ID = "hearth-gateway"`, while
   `ASSOCIATION_MIN_WORKFLOWS = 2` gates association formation on ≥2 *distinct* workflows.
   Even with observations attached, capability synthesis would still yield zero.

So the honest state is: **the measurement layer and the action layer are now connected, and
the connection carries no capability-grade evidence.** Closing that gap requires deciding
that a gateway dispatch *is* an observation of something — which is an evidence-semantics
decision, not a plumbing one.

### Why this is not a drive-by

Three standing constraints bear directly on it:

- **The A3 evidence-hunt verdict (2026-07-03).** The capability destroyed in the 2026-07-02
  corpus overwrite was largely fiction: records matched fixtures to the second, and
  `omen-worker-2` never existed. The ruling was *materialize fresh real lineages, never
  restore fixture records.* `capability_count: 0` is a deliberate, documented state — the
  first capability is to be **re-earned**, and the roadmap prose is its only record.
- **The constitutional principle.** "No organizational truth may be authored if it can be
  derived." Beliefs are derived; intent and accepted risk are authored. Synthesizing
  observations from ledger rows sits exactly on that line: the rows are real measured facts,
  but *which* rows count and *what* they are observations *of* is authored.
- **`corpus_guard` monotonicity.** Once observations with a given watermark and count are
  written, a later correction that reduces either is refused as a regression absent an
  authored override. A wrong mapping is not cheap to walk back — which is precisely why the
  guard exists.

The heartbeat filter above stayed on the safe side of that line: choosing what to *observe*
is the authored instrumentation residue the constitution explicitly reserves for the
operator. Deciding what an observation *means* is not, and is why this ADR exists.

## Decision needed

**Do gateway dispatches become `capacity_observation` artifacts, and under what mapping?**

### What the evidence would actually be

Of 20,110 ledger rows, the dispatch-bearing subset is small. `local_generate` accounts for
**409** rows; non-null coverage of the fields a `capacity_observation` would need:

| Field | Non-null rows | Note |
|---|---|---|
| `duration_ms` | 20,110 | universal, but meaningless without a workload |
| `task_class` | 581 | candidate for `workload_shape.task_kind` |
| `outcome` | 535 | `outcome` is required by the schema |
| `model` | 380 | → `model_id` |
| `backend` | 230 | → `backend` |
| `routed_by` | 204 | routing provenance, not capability evidence |

Schema requires `contract_version, observation_id, workflow_id, run_id, timestamp,
builder_id, outcome`. So the realistic yield is **~230 observations** (the rows carrying
backend *and* model), not 20,000. Note also that `outcome` is null on recent
`local_generate` rows even where `ok: true` — the ledger's own success signal and its
`outcome` field are not the same thing, and that discrepancy needs resolving before either
is treated as an outcome of record.

### Options

**A. Author an observation mapping (an ADR amendment, then code).** Define: eligible rows =
`local_generate` with non-null `backend` and `model`; `builder_id` = `caller.id`;
`model_id` = `model`; `backend` = `backend`; `workload_shape.task_kind` = `task_class`;
`observed.tokens_per_s` = `cost.tokens_out / (duration_ms/1000)`; `outcome` derived from
`ok` where `outcome` is null. Segment workflows so the ≥2-distinct-workflow gate is
satisfiable on real grounds — plausibly one workflow per backend, or per task_class — which
is itself the load-bearing sub-decision and should not be chosen for convenience.
*Gets capability-based routing working. Permanently seeds the belief layer under a
monotonicity guard, from a derived mapping, on ~230 samples.*

**B. Instrument dispatches to emit observations at the source.** Have the offload path write
a real `capacity_observation` artifact and an `artifact_refs` entry when it dispatches,
rather than reconstructing one after the fact from audit telemetry. Slower to yield
evidence — it only measures dispatches made from now on — but it is the one option that
satisfies the A3 verdict literally ("fresh real lineages") and keeps the audit ledger out of
the business of birthing domain facts, which ADR-0010 explicitly forbids the adapter from
doing.
*Recommended.* It also makes the ≥2-workflow gate honest instead of engineered, because real
dispatches carry real workflow identity.

**C. Accept `capability_count: 0` as correct and stop.** The loop is connected; the freshness
guard now makes any future freeze loud. Capability stays empty until real instrumented
dispatches exist. *Costs nothing, defers the payoff indefinitely, and leaves
`capability_count: 0` looking like a bug to the next reader — mitigated only if this ADR is
what they find.*

### Recommendation

**B, with C as the interim state.** The ledger is audit telemetry; ADR-0010 is explicit that
the adapter must never become "a second birthplace for domain facts," and option A is that
in all but name. Instrumenting the dispatch path produces the same evidence one layer
earlier, where workflow identity and workload shape are known rather than inferred, and it
is the reading of "re-earned by pouring real dispatches" that the A3 verdict actually
supports. The cost is time-to-first-capability, which C already concedes.

If A is chosen anyway, the ~230-sample basis and the `outcome`/`ok` discrepancy should be
recorded in the override audit trail at the moment of first write, not reconstructed later.

## Consequences

- Until this is decided, the rebuild exits non-zero with four documents reported stale. That
  is a **true positive**, not guard noise: those documents carry a 26-day-old
  `evidence_watermark` and should not be read as current belief. It should stay loud, and
  `--max-lag-hours` should not be widened to silence it.
- `capability_count: 0` now has a written explanation. Anyone auditing the belief layer
  should land here rather than re-diagnosing the freeze.
- The heartbeat filter is a live instrumentation decision with a consequence: widening it
  later does not retroactively bridge rows already skipped past. Re-bridging needs a cursor
  reset *and* a target-stream reset, since `append_event` does not deduplicate.
- ADR-0010 is reinforced, not amended. This ADR records that the sanctioned bridge existing
  is not the same as the sanctioned bridge running, and that "connected" is not "learning".
