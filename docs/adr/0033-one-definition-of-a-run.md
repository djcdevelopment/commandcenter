# 0033 — One definition of a run: the directory is the run, `result.json` is the only terminal marker

**Status:** Accepted and implemented (2026-08-20)

## Context

Three tools answered "what is a run?" three different ways, and the disagreement was
load-bearing: it opened a class of phantom that could inflate the queue's occupancy
indefinitely with no path to auto-heal.

- `hearth/toolsurface/task_lane.py::queue_status` counted **every** `runs/*/` dir and
  called it *running* when it had no `result.json`. Universe: **187 dirs**.
- `hearth/toolsurface/patrol.py::_GATHER_SRC` emitted a record only for a dir carrying a
  `nodes.json` ("i.e. were dispatched"), then sorted by age and truncated to
  `records[:60]`. Universe: **125 dirs**, of which 60 were examined.
- `hearth/health/gaps.py::scan_runs` raises `phantom_in_flight` for a record with
  `has_result == False` past `PHANTOM_AGE_S`, and `masters_pet` auto-heals exactly that
  kind (ADR-0007) — but only over records `patrol` handed it.

So **62 run dirs were structurally invisible** to the guard dog. A phantom inside that
set held occupancy in `queue_status` forever while `masters_pet` truthfully answered
`healable: []`. Measured on cc-conductor 2026-08-20: `runs/spine-hello/` and
`runs/spine-verify.crashed/` (both 2026-06-30, the conductor's first day, both predating
the `nodes.json` convention) held `running=2` for **51 days**.

The tempting fix — teach `queue_status` to skip dirs with no `nodes.json` — is the wrong
direction, and it is wrong for a live reason, not a historical one.
`conductor_maf.run_workflow` does this:

```python
run_dir=RUNS/plan_id; run_dir.mkdir(parents=True,exist_ok=True)
snap=run_dir/"nodes.json"
...
builders=_select_builders(builders, target_meta)
if not builders: log.error("no build workers available"); return {"error":"no builders"}
snap.write_text(...)
```

The dir is created *before* the builder graph is pinned, and the "no build workers
available" abort returns without writing either file. Filtering on `nodes.json` would
make that abort **silently vanish from the occupancy count** rather than become a
healable gap — hiding the failure instead of fixing it. Absence of `nodes.json` is not
"not a run"; it is a run that died at an earlier stage.

A third, smaller gap sat alongside: `runs/spine-verify.crashed/` had been hand-triaged
via a `.crashed` directory suffix. Nothing reads that suffix — not this repo, not the
conductor (verified by grep across both trees) — so the rename released nothing, and a
rename also breaks `task_status(plan_id)` and the conductor's own checkpoint resume,
both of which address a run by its directory name.

## Decision

**1. One universe: a run is a `runs/<id>/` directory, and its only terminal marker is
`result.json`.** `patrol`'s gather stops filtering on `nodes.json` and emits a record for
every run dir. `nodes.json` becomes an *attribute*, `dispatched: true|false` — reported,
never used to skip. `queue_status` keeps counting the same universe it always did, so
the two now agree by construction, and it additionally reports `running_undispatched`
(the subset of `running` with no `nodes.json`) so any future divergence shows up in the
number itself rather than as a silent gap between tools.

`scan_runs` treats an undispatched aged dir as the same `phantom_in_flight` gap with the
same heal — only the detail wording differs ("never dispatched (no nodes.json)" vs
"reads in-flight but is stalled/errored"). No new gap kind, no new heal path,
`AUTO_HEAL_KINDS` unchanged.

**2. Truncation is bounded on the expensive half only.** A *finished* record carries up
to ~800 chars of error and question text; those stay capped at
`FINISHED_RECORD_CAP = 60`, newest first. An *unfinished* record is four keys wide and
is the only auto-healable class, so unfinished runs are swept **unbounded** — one `stat`
per dir. A phantom can therefore never be truncated away, at a live payload cost of 18 KB
for 187 dirs.

**3. The sweep reports its own coverage.** The gather emits `scanned` (true total),
`truncated`, and `undispatched`; `patrol` surfaces all three, and `masters_pet` — which
previously reported none of them — now reports `scanned`/`considered`/`truncated` plus a
`truncation_note` naming what a truncated sweep can and cannot hide. `healable: []` must
be readable as "nothing to heal", never as "nothing I could see".

**4. The `.crashed` directory-suffix convention is dropped, not honoured.** Adding a
second way to say "terminal" is precisely the defect this ADR closes. The only way to
release occupancy is a `result.json` — written by the conductor, or by `masters_pet` as
a reversible abandoned stub. This is stated in `queue_status`'s docstring where an
operator reaching for the rename will read it. Such a dir is no longer stuck anyway: it
is visible to the sweep now and heals like any other phantom.

## Consequences

- The 62 invisible dirs are visible. Live proof on the conductor, same tree, before and
  after: the old gather reported `scanned=125`; the new one reports `scanned=187,
  undispatched=62, truncated=127` — and finds **the identical four flagged gaps**. Reach
  increased; the flag set did not move.
- `queue_status`'s `running` is unchanged in meaning and value (the occupancy number
  operators already read), so no caller breaks; `running_undispatched` is additive. A
  parse of older conductor output that lacks the new token still yields `0`, not an error.
- Both new payload keys (`truncated`, `undispatched`) default to `0` when absent, so a
  gateway running new code against an older cached gather degrades quietly.
- The `"no build workers available"` abort is now a *healable* gap instead of permanent
  occupancy. It remains a conductor-side defect worth fixing at the source; this ADR only
  guarantees the fleet can see and release it. (Related: `_ensure_fanout_minimum` in
  task_lane.py patches the neighbouring single-builder fan-out crash the same way —
  compensating for concurrently-owned conductor code rather than editing it.)
- `hearth/toolsurface/scheduler.py`'s gather deliberately keeps the `nodes.json` filter:
  it derives `duration_s` from the `nodes.json`→`result.json` mtime delta, so a run
  without one has no measurable duration. That is a scoped requirement, not the same
  question; hindsight regret answers "how long did dispatched work take", not "what is a
  run".
- The two hand-cleared phantoms keep their manual stubs (`_stub_reason:
  "manual-phantom-heal"`); they are reversible in the same way an auto-heal is — delete
  the file. `queue_status` reads `queued=0, running=0, running_undispatched=0, done=187`.
- **Verification honesty:** the new gather source was executed against the live conductor
  read-only (via `git show HEAD:` for the old one, side by side) and both `_GATHER_SRC`
  and the shell snippet are now executed by tests against temp trees rather than
  asserted about in mocks. What is *not* staged: no live phantom existed at
  implementation time to heal end-to-end, so the undispatched→heal path is proven by the
  gather-source test plus `scan_runs`, not by a live `masters_pet(apply=True)`.
