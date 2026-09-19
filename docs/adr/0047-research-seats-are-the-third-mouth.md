# 0047 — Research seats are the third mouth: a bespoke llama-server on a port the door never names is ledgered from its own timing log, by harvest, never by driver self-report

**Status:** Accepted (2026-09-19) — Derek's decisions in the planning window that opened with
*"I wonder if that shows up in my local usage at all"*. Execution state per decision below
(**LANDED** / **DECIDED, NOT YET EXECUTED** / **OPEN**). Phase A (private) landed in this record;
Phase B (public) waits for two nightly harvests that reproduce the dry-run table.

**Companion to:** `docs/adr#0018` §3 (*"nothing may bypass the door to `:8082` for real work"* —
amended narrowly for a second mouth by `#0046`, and here for a third, on the same condition),
`docs/adr#0044` (a rate is not a scalar; the epoch anchor is the log's elapsed stamp subtracted from
its mtime, trusted to about a second), `docs/adr#0045` (every number comes from a receipt),
`docs/adr#0046` (the friend gate: an exception to `#0018` is granted only when every request is
ledgered with tokens in and out — the precedent this record follows), `hearth/PUBLIC-PORTFOLIO.md`
(the one-way boundary the public page is built behind).

## Context

The public usage page counts what the HEARTH door brokered. Its "Local inference" family is
`local_generate` calls whose backend is not a cloud rung
(`hearth/projection/call_mix_dashboard.py` `classify_event`), and its one direct-inference cohort,
"instrumented direct local inference", is the DeepAgents `AccountedTransport` receipts from the
2026-09-12 smoke window — nineteen attempts, imported once, unused since.

Meanwhile the B70s have spent their busiest days on research that the door never sees. The
MemSplice laps and the SYCL-vs-Vulkan ladder (`C:\work\memsplice\tests\benchmarks\*.py`,
`E:\work\llamacpp-knee\sycl-seat.cmd`) launch a bespoke `llama-server` on `127.0.0.1:8095` or
`:8096` and drive it over raw HTTP; production is stopped for the seat
(`hearth/var/arc-maintenance.stop`, guard rows in `arc-maintenance-guard.ndjson`). The 09-14 week
shows nineteen inference calls on the page while the machine ran its largest-context work of the
campaign. `hearth/analysis/workload.py` already says so in writing: the ledger records
door-mediated calls and *"cannot answer how busy were the cards"*. The inventory on 2026-09-19:
memsplice (seven drivers, 55 result files, 39 of them on 09-18), the ff-probes (production
`:8082` directly, their own `ff-receipts.jsonl`), the DeepAgents experiments, the shot director,
and the llama-bench campaigns — none of it on either ledger, nothing on a schedule.

What does exist, per seat, is the server's own `-lv 5 --log-file` under
`hearth/var/swap-logs/`: one epoch per file (truncated at launch, one `build` line, stamps
`MMMM.SS.mmm.uuu` with unbounded minutes), and per task a `launch_slot_ … processing task` line,
a `print_timing` block with `prompt eval time = X ms / P tokens` and `eval time = Y ms / G tokens`,
and a `release` line. That block is the receipt: it is what the server processed, not what a
driver claims it sent.

Not every log in that directory is a research seat. Fingerprinting the load reports on 2026-09-19
split them cleanly:

| class | fingerprint | logs | reachable by |
|---|---|---|---|
| bespoke research seat | no `api_keys:` line; `listening on http://127.0.0.1:8095` or `:8096`, ports `hearth/etc/backends.toml` never names | 23 | the driver only |
| llama-swap-managed side seat | `api_keys: ****963f` (the production key, injected by `serve-arc*.cmd`); ephemeral ports 18304–18402; entries in `fleet/arcserve/llama-swap/omen*.yaml` | 9 | the door (`omen-swap`), the friend gate, and direct callers |

The nine managed logs hold 389 timed tasks, mostly the 27B MTP night campaign. The server log
carries no client identity at any verbosity, so a task in those files cannot be shown disjoint
from a row the page already counts. They are excluded, not harvested. All five `(1,1)`
probe-shaped tasks on disk are in those files (`hearth/rotation/swapclient.py` `wait_ready`,
`hearth/rotation/kv.py` pre/post probes); no bespoke seat has produced one.

## Decisions

1. **A research seat is a llama-server with no API key on a port outside the door's backend
   pool** (`RESEARCH_SEAT_PORTS = {8095, 8096}` today). Eligibility is read from the server's load
   report, never from the label. llama-swap-managed seats are door-reachable and are never
   harvested; the dry-run table lists them with the reason. Production logs (`arc-serve.log`,
   `arc-swap*.log`) live outside the seat directory and are never offered to the harvester: the
   door already owns those rows. **LANDED** (`hearth/seats/harvest.py` `eligibility`).
2. **The seat's own `print_timing` block is the receipt; drivers never self-report.** A receipt
   carries the tokens the server processed (cached prefixes are not re-counted), the server's own
   milliseconds, the model by `general.name`, build and `n_ctx_seq`, and never a filesystem path
   (rejected at validation). **LANDED** (`hearth/seats/serverlog.py`, `hearth/seats/receipts.py`).
3. **Receipts live in a third, append-only ledger, `hearth/var/seats/receipts.ndjson`, not in the
   execution ledger.** Importing the 86 rows through the direct-inference importer would add 344
   execution events, lift "Inference jobs" from 347 to 433 accepted, turn the 4 unknown-usage
   tasks into failed jobs, and land every row in the "other" agent lane — re-laning research work
   into a family the page defines as door-brokered jobs. A third input with its own prefix hash
   keeps every existing counter untouched: two rows, never one. **LANDED**.
4. **Wall-clock is derived, and every receipt says so.** `epoch_start = mtime(log) − elapsed(last
   stamped line)` (the `#0044` anchor, restated in `hearth/seats/serverlog.py` because `hearth/`
   never imports from `campaign/`), pinned in the cursor on the first tick of an epoch so all of a
   seat's rows share one derivation; `timestamp_derivation` records the method, the anchor and an
   `error_bound_s` of 2. The cohort may contribute to the public watermark day and never to the
   weekly series. **LANDED**.
5. **Identity is `(basename, first-64-stamped-lines sha256, task)`.** A launcher that reuses a
   label opens the log fresh, the prefix hash changes, and the file is a new seat epoch; rows from
   the old epoch stay. Re-harvesting an unchanged directory writes nothing and leaves the cursor
   byte-identical. The `seat_harvest` kernel timer runs every 15 minutes; a relaunch inside one
   tick loses the old epoch's tail — an undercount, never an overcount. **LANDED**
   (`hearth/kernel/timers.py`, `hearth/etc/stage-public-portfolio.ps1`).
6. **Untimed tasks are `unknown`, never `failed`.** The log carries no failure verdict; a task with
   a launch line and no timing block (the lap-10 eviction refusals, `release … n_tokens = 0`) is
   written as `outcome: unknown` with `usage: null` and a reason, and only once it has settled (a
   release line, a later launch on the same slot, or thirty minutes of quiescence). An in-flight
   task is left pending because receipts are immutable. `failed_attempts` is zero by construction.
   **LANDED**.
7. **The public page shows the cohort inside the charts that already exist, drawn hollow.** One
   rule carries it: *solid was observed at the gateway, hollow was harvested from the servers' own
   logs.* The family chart gains one hollow row under Local inference (`seat_inference.attempts`,
   on the same log scale) and one clause in its note; the weekly chart moves to a logarithmic
   height like its neighbour (Derek, 2026-09-19: door checks are truthful but not what matters
   relative to the other activity) and gains a slim hollow bar beside each week
   (`weekly[].seat_attempts`, dashed when fewer than ten, a baseline tick when none). Nothing is
   stacked across the two, no ratio is drawn, and the boundary box does not change. The snapshot
   carries `seat_inference` (six counts plus `seats`), `provenance.seat_prefix_sha256`, a second
   boundary string and one limitation sentence. Four gates changed: `PUBLIC_KEYS`, the schema, the
   no-jsonschema fallback set, and the site's `SYSTEM_KEYS` with a guarded monotonic check (a
   published cohort may grow but never disappear or shrink). A first draft that added a
   two-boundary chart pair, caveat lists and event ticks was rejected as off-register; the
   surviving change is the smallest one that lets the volume be seen. **LANDED** on branches
   (`hearth/projection/seats_cohort.py`; site branch `copy/field-lab-research-runs`) — Derek
   merges and pushes; push deploys.
8. **Probe-shaped tasks (`prompt_n == 1 and predicted_n == 1`) are counted and tallied, not
   dropped.** A `--n-predict 1` restore-then-decode research task has the same shape, so a rule
   follows only if a bespoke seat ever produces one; the harvester reports the tally. **OPEN**
   (today: 0).
9. **The August campaign seats are harvested too; what stays outside is named.** The 2026-09-19
   inventory found the same timing-line receipts at `-lv 3` under six roots on the model drive
   (the Vulkan-cliff burn-in, the Qwen3.8 replacement trial, the ff-probes, rotation and Level-Zero
   seats), so the harvester reads those roots beside the seat directory, admits a seat by the
   absence of an API key and a port outside the gateway-reachable set (door pool, friend gate,
   retired Ollama port, llama-swap's ephemeral range) instead of a two-port allowlist, fingerprints
   the epoch by the load report up to the listening line, and records build and model as
   `unrecorded` when the verbosity did not print them. **AMENDED and LANDED** (2026-09-19). Still
   outside, named: seats that do not log (`llama-bench`, `llama-perplexity`, ad-hoc runs without
   `--log-file`); probes that ran against the live production server with the gateway's own
   credential (SAT-L1 and the rate checks), whose only trace is driver-side; the driver-side receipt
   files (`ff-receipts.jsonl`, `lz-receipts.jsonl`, `memsplice/results`), which are self-reports;
   production's `--metrics` counters, which nothing samples; and the Ollama era, which logged
   nothing per request. **OPEN** for a phase-2 importer with its own reconciliation.

## Consequences

- The page's "Local inference" family does not change. The research cohort is a separate figure
  with a separate hash, and the two are never added.
- The first honest public number will be small on the output side and large on the input side:
  this is prefill-heavy research (long-context restore and split-prefill laps answering in 32–96
  tokens), and the figure says so rather than looking like production traffic.
- A seat launched without `--log-file`, or logging outside `hearth/var/swap-logs/`, is invisible
  here, and the boundary says so. The fix is the launcher, not the harvester.
- Pre-existing state surfaced while landing this, not addressed here: the direct-inference receipt
  importer (`hearth/execution/external_inference.py`) and the projection's `external_inference`
  scan are uncommitted on `master`. Phase B builds on them once they land; the seats package
  inlines the two three-line helpers it would otherwise import.

## Verification (2026-09-19, Phase B)

The ledger was rebuilt from scratch on the load-report fingerprint over the seat directory and
the six campaign roots: 197 eligible logs, **177 seats, 10,387 attempts** (10,367 timed, 20
unknown, 6 probe-shaped), **7,694,808 prompt tokens processed, 1,896,187 generated**; 9 logs skipped
as door-reachable, 249 skipped because they never listened; a second run appended nothing. The
projection over the real ledgers staged a candidate watermarked 2026-09-19 with the cohort, weekly
seat cells 08-17 **6,123**, 08-24 **4,161**, 09-07 suppressed, 09-14 102, and gateway and
execution blocks that the tests prove byte-identical with and without the cohort (44 projection
and seat tests). The site validator accepted the candidate, the monotonic rules passed
(282,964 → 303,910 boundary events), and the studio spec still passes with the weekly chart on a
log height. `pytest hearth/tests`: 2,119 passed; the one failure is the pre-existing
declared-budgets test that fails on the base commit.

## Verification (2026-09-19, Phase A)

`python -m hearth.seats.harvest --dry-run` over `hearth/var/swap-logs` (32 logs; the L4c lap was
adding seats while this ran):

| class | logs | launched | timed | unknown | probe-shaped | tokens in | tokens out | seats |
|---|---|---|---|---|---|---|---|---|
| eligible research seats | 23 | 86 | 82 | 4 | 0 | 2,132,209 | 5,670 | 22 |
| skipped: door-reachable (llama-swap api key) | 9 | — | — | — | — | — | — | — |

Nothing written; `hearth/var/seats/` not created. A real harvest into a scratch directory wrote 86
receipts; a second run appended 0 and left the ledger byte-identical (the cursor changed only
because a live seat's log grew between the runs). Every row passes `validate_receipt`; the ledger
contains no drive-letter path and no IPv4-shaped string; 86 distinct `attempt_id`s across 22
seats; outcomes 82 succeeded / 4 unknown. `python -m pytest hearth/tests/seats
hearth/tests/kernel/test_timers.py -q`: 27 passed.
