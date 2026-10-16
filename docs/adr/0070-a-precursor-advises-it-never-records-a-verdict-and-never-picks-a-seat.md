# 0070 — A precursor advises: it never records a verdict and never picks a seat

**Status:** Proposed (Track D1, 2026-10-16, filed by Claude Fable 5.1 under cc-c657d41c on the plan
`tender-gathering-fox` rev 2 §5–§6 and `~/work/lab-rnd/research/precursor/CONTRACT.md`; nothing here is
live until Derek accepts it and Gate 1 of that plan reads "proceed"). Goal tag G-harness under G-lab.

## Context

The lab's work ledger holds 303 local-work runs under `runs/operator/work_*` (2026-10-16), 289 of them
with an `outcome.final`. Each new submission is reviewed blind: the reviewer does not see that six prior
runs with the same intent on the same seat and family were accepted five times, or that a sibling brief
was rejected twice for the same bucket. The precursor plan asks two questions (hypothesis A: are nearest
prior runs useful evidence to a reviewer; hypothesis B: does a conditional acceptance estimate beat the
base rate on held-out groups) and answers them with a frozen replay first (Tracks A–C). This ADR fixes
what the door-side component may and may not do once the replay says "proceed".

Standing decisions it builds on:

- **ADR-0008** (scheduler advisory first): a new signal enters as advice beside the existing decision,
  recorded and compared before it is allowed to act. The precursor is such a signal.
- **ADR-0038** (a verdict cites only evidence from the configuration it promotes): any promotion of
  the precursor to a route recommendation cites only evidence from the configuration being promoted;
  shadow rows from one corpus version and config id do not promote another.
- **ADR-0048** (local-work candidates require frontier verdicts): a candidate stops at
  `awaiting_review`; only a frontier or human caller records the verdict. The precursor's `p_accept` is
  never a verdict and never an input the door acts on.
- **ADR-0049** (learning recommends a route; only a recorded decision changes the drafter): a
  materialized recommendation changes nothing by itself. The precursor is held one step further back:
  it does not even recommend a route; it reports associations under the recorded configuration.
- **ADR-0050** (request sizer informs admission): the env-gate pattern. `HEARTH_SIZER=off` is the
  default and every route is byte-identical with the gate off; an advisor fault is a signal, not a
  failure. `HEARTH_PRECURSOR` copies that shape exactly, with a stricter `off`: no import, no file.

## Decision

1. **A materializer, not a door component.** `tools/workflow/project_precursor.py` (with its lazily
   imported sibling `tools/workflow/precursor_index.py`) runs off the request path: by hand or from a
   user timer (`host/omen-linux/systemd/hearth-precursor-shadow.{service,timer}.draft`, not installed).
   `hearth/localwork/service.py`, `hearth/delivery/procedures.py`, `hearth/toolsurface/*`,
   `hearth/execution/*` and every routing table are unchanged. The door does not import it.
2. **Gate.** `HEARTH_PRECURSOR ∈ {off, shadow, display}`, default `off`. In `off` the script prints
   `{"state": "off"}` and exits 0 without importing the index code and without opening any file
   (proved in `tests/workflow/test_project_precursor.py` with a patched `builtins.open`, a fresh
   interpreter's `sys.modules`, and `strace -e trace=openat,connect`). `display` is identical to
   `shadow` for the materializer; display is the AMUX side's concern (Track D2) and nothing is
   displayed in this increment.
3. **What it writes.** `knowledge/precursor_index.json` (`precursor-index.v1`: rows from the
   immutable corpus export `precursor-run.v2` plus live work dirs newer than the corpus, the frozen
   config and its `config_id`, `sha256` of the rows; written through `corpus_guard.guard_write`, tmp +
   `os.replace`, fsynced), `runs/operator/work_<id>/precursor/advice.json` (a list of
   `precursor-advice.v1`, one per arm, `mode: shadow`, `view: operational`, `displayed: null`), and
   `knowledge/precursor_accuracy.json` (`precursor-accuracy.v1`: advice joined to each run's first
   `outcome.final` only when that verdict's timestamp is after `advice.generated_at`; per arm per
   period n, Brier, balanced accuracy, coverage; `late_excluded` counted, never silently dropped).
4. **Arms and rules.** `table` and `bm25` per CONTRACT §2: T is the run's `step.dispatched`
   timestamp (else `task.received`); evidence rows need `first_final_at <= T`, `artifact_at <= T`,
   a binary label at T, and are never the query's own run; pool < 3 abstains; k = 5; Laplace
   `(acc+1)/(n+2)`; conditions are only `backend`, `task_family`, `serving_profile_sha`, the fields
   known at T. The JEPA arms stay in the replay.
5. **States.** `off | unavailable | timeout | abstain | stale | ok`; anything but `ok` carries a
   `diagnostic`; `p_accept` is null unless `ok`. A stale index (older than 24 h, or missing the newest
   verdict in the runs tree) still advises and is marked `stale`. Per-run budget 200 ms; over budget is
   recorded as `timeout` and the sweep moves on. Any exception becomes an `unavailable` row with the
   exception class and 200 characters; the sweep never raises. Advice computed after a verdict already
   exists is written with `diagnostic: late:advice_after_verdict` and excluded by the join.

## What it never does

- **Record a verdict.** It writes no `verification.recorded`, no `outcome.final`, no manifest
  `verdict`, and calls no `record_local_work_verdict`. `p_accept` is an association under the recorded
  configuration, not a decision (ADR-0048).
- **Pick a seat.** It never reads a request before dispatch and never writes anything a router reads.
  `choose()`, `lane_code_backend`, the family regimes and every route field in the manifest are
  identical with the gate `off` or `shadow` (the sweep test diffs a work dir byte-for-byte and finds
  only the new `precursor/advice.json`). Callers still never choose seats.
- **Change a routing decision.** `procedure`, `lane_choice`, `backend_choice` and the manifest are
  untouched; the advice file lives beside the run, not in it.
- **Recommend another configuration.** Historical outcomes come from the existing selection policy
  and say nothing about what another lane or procedure would have done (plan §5.5).
- **Fall back to cloud or to any other seat when unavailable.** `unavailable` is a recorded state.

## Exposure stages

- **Stage 1, unexposed shadow** (this ADR, after Gate 1): advice recorded, nothing displayed, no hook
  line, no dashboard line. Runs until ≥ 40 joined rows exist (Gate 2).
- **Stage 2, bounded display pilot** (after Gate 2, its own increment): attributable evidence and
  sample counts only ("k prior runs like this on this seat and family: n accepted of m; nearest:
  work_…"), a probability only if the calibration criterion holds for that arm, ids and counts only
  across repos, visibility randomised by `work_id` parity and recorded as `displayed: true|false` so
  H-1 can be compared (plan §6). Surfaces: `streams/lib/dashboard.py` waits panel and
  `~/.claude/hooks/hearth_handoffs.py`, one line each (Track D2).

## Promotion criteria (plan §5.4)

All of: the held-out primary measure (evaluation period and live shadow) clears the minimum (Brier
lower than `const` by ≥ 0.02, 90% grouped-bootstrap interval excluding 0, balanced accuracy above the
majority class) ; ≥ 100 joined live rows across ≥ 30 groups; usefulness ≥ the BM25 reference on the
blinded sample; calibration slope 0.7–1.3 on covered rows; coverage ≥ 0.6; p95 advice latency ≤ 200 ms
and index build ≤ 60 s; the display pilot shows no increase in active review minutes and no change in
the accept rate attributable to display. Only then a further ADR may move from `display` to any route
recommendation, and that ADR cites only evidence from the configuration it promotes (ADR-0038). If the
pilot cannot resolve H-1 at the available n, H-1 is reported unmeasured, not assumed.

## Consequences

- One new timer draft, two new modules, one test file, one fixture corpus; no door restart, no change to
  the request path, no new seat load (CPU only, `nice -n 10`, under the night-backlog rule of the contract).
- The reviewer's blind spot is measured before it is addressed: Gate 2 needs joined rows that exist
  only if shadow runs for weeks with nothing shown.
- Deviations to resolve before acceptance: the pool requires a binary label at T
  (`pool_requires_binary_label` in the frozen config, so the replay can match or override it);
  live rows newer than the corpus get `group_id` by identical intent text only (lineage is Track A's);
  `task_family` and `serving_profile_sha` for a query are read from the manifest's route, which the
  ledger writes at dispatch; Track A's `field_availability` decides whether that is pre-outcome.
