# Session Retro — 2026-09-03 (the 90-minute plan that ran all day: a live cutover, a live rotation proof, and five defects only the machine could find)

> **Production `omen-arc` moved under llama-swap at 12:45 with the keep-alive unbroken, and by
> 18:03 a second model had been loaded beside it, KV-saved, unloaded, reloaded and restored
> through the door — but every one of the five defects that stood between the plan and that
> proof lived in code written the same morning, and none of them showed up until the code was
> run against the live rung.** The plan was right about the shape and wrong about the details
> in exactly the places a test suite cannot reach.

## What this session was

A **build session that became an operations session**. The ask was "you have 1.5 hours, how much
of HEARTH and mechnet can you build from the recent benchmarking — spawn as many agents as you
need" (plan mode). Two steers reframed it before approval: the destination is scheduling +
capacity — the job shop — and the Qwen 3.8 campaign changed the machine (OMEN's 128 GB and two
B70s make different-models-for-different-task-types with load/unload the viable optimization);
and HEARTH is the front door while mechnet is the neglected workhorse. Derek's three in-session
decisions turned a side-port-first plan into a **live production cutover** ("cut over now"),
held the fleet-dispatch cue back ("leverage the dual B70s on OMEN first" — M6 stays dry-run),
and authorized the gateway restarts ("just restart the Hearth gateway, don't worry about it").

The 90 minutes ran until 18:13. The session was cut **three times by the usage limit** and the
subagent waves died three times on session limits and three times on API 529 overloads, so the
critical path — substrate, cutover ceremony, the live proof, the fix-ups — was built in the main
loop, with waves relaunched in batches of five once agents returned. From 05:59 another lane's
imagegen sessions held the same B70 pool, repeatedly, through the day (tenancy epochs 3..27);
every rotation and door call refused under the tenancy fence while it held the pool, by design. The close-out itself ran `--no-offload` at
Derek's word ("run it yourself, hearth is busy atm").

## What shipped

Range `091a354^..8422df8` (44 commits; three of them — `41ba74a..8422df8` — pushed at the end, the
rest already on `origin` via another lane's push). Commits from **other lanes** in the range are
marked; they are in the table because they are in the range, not because this session wrote them.

| Commit | What |
| --- | --- |
| `091a354` 02:51 | lz-probes: `etw11_recurrence.py`, the INC-A recurrence-statistics reader (this session's first landing per the plan's execution log) |
| `36306b9` 02:52 | **P7** `hearth.health.rungstate` — passive ADR-0044 rung health (baseline epoch + observed rate + envelope) |
| `faaec77` 02:52 | **P1** OMEN model catalog contract + receipts-only catalog (`knowledge/omen_catalog.json`) + tests |
| `b1e8cf9` 02:52 | **P12** Derek's 2026-09-03 decisions recorded — llama-swap cutover, OMEN-first, tenancy owner OPEN; S8 registered |
| `5d75f3c` 02:54 | scaffold: `hearth.rotation` package + test package (empty) |
| `ac53db4` 03:45 | *other lane (imagegen)*: resolve `OMEN_ARC_TOKEN` from `gateway.cmd` as fallback in session probes |
| `623800f` 05:11 | *other lane (telemetry)*: replace hand-rolled OTLP with the official OpenTelemetry SDK |
| `a544a8a` 05:16 | *other lane (mediagen)*: Phase 1 — versioned contract schemas and MediaGen tool surface |
| `cf76348` 05:37 | *other lane (mediagen)*: Phase 2 — podcast vertical slice |
| `c6370b0` 07:59 | **P6** llama-swap production config (`omen.yaml`), the `omen-swap` rung, and its occupancy probe |
| `cbeaf69` 08:05 | **P13** production cutover to llama-swap — launcher, restart, ceremony script (scripts only; not executed) |
| `ce32632` 08:08 | P13 fix-up: park the llama-swap launcher until the ceremony installs it; fix `cutover.ps1` parse error |
| `dfc8479` 08:08 | P13: define the parked-launcher path in `cutover.ps1` (pre-flight no longer errors) |
| `b85e32e` 08:17 | **P3** rotation substrate A — llama-swap client, placement assertion, KV manifest, windows |
| `78350a5` 08:21 | **P5** task-family preference, authored with evidence (`routing-families.v1`) |
| `2e5935b` 08:23 | **P4** rotation substrate B — Arc telemetry, bytes-per-card admission, load lifecycle |
| `a53fe8b` 08:23 | **P9** rotation tool provider — status, recommend, window, load, unload, kv save/restore |
| `c3188d7` 10:04 | *other lane (mediagen)*: Phase 3 — `submit_video_animation` tool, visual storyboard generator |
| `928f1e6` 11:05 | *other lane (mediagen)*: durable pipeline, composition, and dashboard runtime |
| `13cf16d` 12:26 | *other lane (dashboard)*: stabilize trace timeline layout |
| `0ae8acf` 12:38 | **P7b** rung state wired into gaps, patrol, the watchdog, and a `query_rung_state` provider |
| `442e3e1` 12:38 | **P8** dispatch stamps — `rung_state` and `pool_config_hash` on results, observation notes, the execution ledger |
| `3462687` 12:38 | **P10** register the rotation and rungstate providers (`rotation_admin` capability; operator + unrestricted) |
| `c4743d3` 12:38 | **ADR-0045**: the scheduler plans a rotating host (catalog from receipts, asserted placement, health as epoch + rate + envelope) |
| `fa657bb` 12:38 | **P2** (part 1) ModelSpec/Machine/Job rotation fields, `load_model_catalog` for `omen-catalog.v1`, roles + KV-hydrate terms in the solver, `rotation_plan` builder |
| `f9409b2` 12:42 | P13/P3/P4 fix-ups from the first live cutover attempt (aborted at placement, rolled back in 36 s) |
| `a83b395` 12:43 | P13: step C must not assign the read-only automatic variable `$host` (it would have thrown and rolled back) |
| `5178f37` 12:45 | P13: read the load report through a shared-mode stream (the server holds `--log-file` open; `ReadAllText` got a sharing violation and aborted the second attempt) |
| `26a1d66` 12:46 | **CUTOVER EXECUTED** 12:45:02–12:45:25: production `omen-arc` runs under llama-swap (ADR-0045 P13) |
| `c9220dd` 12:47 | **M4** fleet dashboard slice — `fleet_ping` sweep rendered as `FLEET-DASHBOARD.html` (OMEN-hosted, conductor-independent) |
| `ffe715b` 12:48 | **M6** mechnet exerciser — two planning/critic-loop research briefs, dry-run only (`--go` never cued) |
| `0b8dd67` 12:49 | **M3** token hole #1 — every `submit_task` call site stamps `task_class`/`est_tokens` |
| `10b64ae` 12:52 | **M2** held-out judge panel + commander defaults on live rungs |
| `039f68a` 13:00 | Post-cutover fix-ups from the first rotation proof attempt (kv dir, late-bound log dir) |
| `a2728de` 13:03 | P5 families: the R10 evidence restated with the corrected tally (13/16 vs 3/16 overall; 11/14 and 2/2 are sweep vs full-prompt rows, not 8k vs 32k) |
| `4ec76db` 14:14 | **P2** scheduler: model rotating OMEN inference residency |
| `e44b726` 14:14 | **M1** doc/ADR bench takes `backend:model` arms + held-out judges + dispatch identity |
| `cedca53` 14:15 | *other lane (ops)*: track AM4 and FX99 workstation controls |
| `c737782` 16:13 | *other lane (knowledge)*: refresh projections through 2026-09-03 |
| `56fe865` 17:40 | **P12b** cutover EXECUTED — living docs + ADR-0045 state (wrote the proof as in-progress with the **wrong cause**, see Reviewer/QA) |
| `41ba74a` 17:47 | *other lane (runs)*: chunk the hearth-gateway event stream by month/period |
| `92f3cd6` 17:49 | **P11 live proof fix-ups**: `-vk2` is the iGPU today → siblings are env 0/1 (19 files); KV save prefills; resident entries skip the delta; 404 fast-fail in `wait_ready` |
| `8f301bc` 18:08 | Rotation windows: close events carry the `candidate_id` the ontology requires |
| `8422df8` 18:13 | **P12c** P11 rotation proof PASSED through the door; gate 2 opens for `omen-swap` (docs, corrected cause) |

Suite: 1243 passing at the start of the session, 1410 at the plan's mid-point landing, **1646 at
the end** (the `.venv-omen` interpreter; `test_gateway_http` flakes under concurrency and was
deselected in the last full run — a flake, not a failure, and still open).

> **Correction, 2026-09-04.** "A flake, not a failure" was wrong, and so was "under concurrency" as a
> cause. The test was **silently skipping**: `default_execution_dir()` does not derive from
> `HEARTH_ROOT`, so every gateway the test spawned wrote the *real* execution ledger; concurrent runs
> corrupted it, the subprocess then could not boot, and `setUp` called `skipTest` — exit 0, "3
> skipped", nothing proven. Measured 18 of 20 concurrent runs skipping while reporting success. Fixed
> by setting `HEARTH_EXECUTION_DIR` per test (12/12 passing, shared-ledger delta 0). The underlying
> `ExecutionLedger` cross-process sequence race is registered in `DECISIONS-PENDING.md`.

**Durable artifacts (beyond the code):**

- The **executed cutover**: window `rot-cutover-20260903-1245` in `hearth/var/rotation-windows.jsonl`
  (23 s, status `done`, 13 pre-flight checks all true; receipts: 2 B70 `using device` lines, 0 iGPU,
  2 Vulkan model buffers, 0 CPU buffers, 49/49 layers; bare request 401; `ff_ratecheck` rc 0;
  keep-alive ok at 12:45:21, prompt_ms 10.3). Two aborted attempts (`rot-cutover-20260903-1235`
  and `rot-cutover-20260903-1243`) sit beside it with their rollback receipts.
- The **live rotation proof**: window `rot-side-20260903-B` (`hearth/var/rotation/windows/rot-side-20260903-B.json`,
  opened 2026-09-04T00:33:47Z, closed `assay.passed` 01:03:54Z); `hearth/var/rotation/last-load.json`
  (the 20.14 s reload with placement corroborated by a +9.729 GB commit delta on `0000:04:00.0` and
  0.0 on `0000:09:00.0`); `hearth/var/kv-manifest.json` (`phi4-vk1.0.e9480f7e3f3cf3d6.bin`, 1239
  tokens, 253,768,028 bytes, slot 0).
- The **M1 evidence pour** on `omen-swap`: `hearth/var/experiments/doc-adr-bench-20260904T005139Z-sweep`
  (phi4-vk1, 2/2 cells, mean 92.5) and `-20260904T005728Z-sweep` (qwen14b-vk1, 2/2, mean 88.25),
  each cell judged by `gcp-gemini:gemini-3.5-flash` and `omen-arc:qwen3-30b-a3b`.
- `knowledge/capabilities.json` at `capability_count` **2** (watermark 2026-09-04T01:02:55Z):
  `offload-generate|backend=omen-swap`, qualified resources (omen, phi4-vk1) + (omen, qwen14b-vk1),
  confidence medium — gate 2 open for a second backend.
- `FLEET-DASHBOARD.html`, `knowledge/omen_catalog.json`, `fleet/arcserve/llama-swap/omen.yaml`,
  ADR-0045, the ROTATION-PROGRAM.html changelog, and the register entries in `DECISIONS-PENDING.md`.

## The team retro — our collaboration across the seats

*(Frontier-drafted end to end — no HEARTH draft, no edit verdict; see Provenance. Derek intuited
and paced; I held the whole and did the instrumenting. The seats below are about the work.)*

**Architect** *(Derek set the destination; I drew the shape).* The two steers were the design: the
job shop is the destination, and OMEN's memory plus two cards make rotation the lever. That held
all day — nothing built had to be un-built. Two of my own calls I would make again: parking the
llama-swap launcher as `serve-arc-swap.cmd` so another lane's `ArcServeBoot` cannot cut over
un-ceremonied (it paid off at 12:59 when the imagegen lane's `restart-arc.cmd` tore the tree down
and the restore path booted production unattended under llama-swap, verified 17:31 at 107.3 tok/s);
and renaming `-vk2` to `-vk0` rather than silently swapping env values under the old names, because
an honest id is cheaper than a footnote forever. One call I would make differently: the plan wrote
"two door calls from claude-frontier close gate 1" without checking how the gateway bridges its
dispatches — they arrive with `task_kind` = the tool name, so a door call can never land in the
`offload-generate` bucket. That is a design assumption I could have verified in `ledger_adapter`
before it cost a rebuild cycle at 18:00. The `omen-swap` rung's `context_bytes` = 14336 (MIN over
members) is correct ADR-0031 arithmetic and the wrong policy: it refused 5 of 8 bench tasks for a pin
although phi-4 runs `-c 8192`. Registered, not fixed.

**Implementer** *(me, mostly in the main loop).* Thirteen HEARTH packages and five mechnet packages
landed, and the substrate did what it was built to do the first time it faced a real server —
admission, telemetry before/after, placement parsed from the server's own `-lv 5` log, commit-delta
corroboration by BDF. Where the code fought us was entirely at the seams: the cutover ceremony
aborted twice (12:35, llama-swap's `/logs` is a ~10 KB tail and the `using device` lines had scrolled
out; 12:43, `ReadAllText` hit a sharing violation on the server's own open log) and rolled back in
36 s each time before the third attempt ran clean in 23 s. The five proof defects in `92f3cd6` were
all in code I wrote that morning: the env index, the already-resident delta, the KV save that never
processed its prompt, the missing 404 fast-fail, the token env. Rework was small in lines and large
in wall time — each defect cost a window step and, twice, a gateway restart. Also: Bash heredocs
mangle backslashes and Git Bash turns `schtasks /Run` into a path and breaks `cmd /c "call … && …"`
chains; PowerShell into a wrapper `.cmd` was the working shape. Worth a doc line so nobody rediscovers it.

**Reviewer / QA** *(the live rung, and then me).* The tests went 1243 → 1646 and caught none of the
five. What caught them was **running the code against the machine**: ADR-0042's placement assertion
refused a READY server that answered its canary because its log said `Vulkan0 : Intel(R) Graphics`
— env index 2 is the iGPU on this driver. The commit-delta corroboration refused an already-resident
entry (0 GB delta) and unloaded a correct placement. `rotation_kv_save` returned `ok` with
`n_tokens=1` / 205,820 bytes — the slot was saved before any prompt reached it; only the byte count
gave it away. And the ontology refused the window-close event for a missing `candidate_id`
(recorded by hand, fixed in `8f301bc`). Every one of those is a fallback that would have been silent
if the gates had been softer. What slipped: the first docs agent wrote `56fe865` with a guessed
cause for the failed side-model placement ("the `/logs` tail") when the side entries already read
their own `--log-file`; the real cause was that the gateway, started 11:42, ran code older than the
13:00 swap-log reader. The verify-relayed-reports discipline caught it — reading the evidence row,
not the summary — and `8422df8` corrected it. Also slipped: `adr_index` is byte-stable for an
unchanged tree and therefore **stale** the moment an ADR is edited; it had to be regenerated
(218 records, 15 registers). Test posture to carry forward: a gate that has never been observed
firing correctly is a hypothesis; today four of them fired, and two fired on the wrong target
before they fired on the right one.

**Operator / SRE** *(Derek authorized; I drove).* Production survived the whole day with the
keep-alive unbroken across the cutover, both gateway restarts (17:39, 17:50) and the peer lane's
teardown — but "`at_rate` throughout", which I wrote in three documents from three rung-state reads
(17:31 107.3, 17:42 109.18, 18:02 107.99 tok/s), was true only before and after the pour. The
verifier read the keep-alive file itself: the three deep probes taken **during** the pour — 17:47:06
74.36, 17:52:11 71.14, 17:57:12 73.33 tok/s, `decode_degraded`, prompt 14.8–15.3 ms against 10.0 —
sat at 67–70% of baseline, under ADR-0044's 0.8 fail line, while the pour's held-out `omen-arc` judge
calls were hitting production beside a side model decoding on one card. It came back to 107.99 at
18:02:12 with no intervention. Two causes overlap there (judge load on the incumbent, a decoding
neighbour) and they were **not separated**; ADR-0041's "inferring neighbour −8%" does not cover a
30% dip and the receipts do not say which it was. The probes sit inside the ledgered window, which
is the point of the window — and the rung-state reader did not exclude them live (see L-11 below). The cutover honored
ADR-0044: the INC-2026-08-30-A observation epoch ended as a **deliberate boundary** stamped
12:45:25, baseline 106.0 preserved, never silently re-baselined; and ADR-0043: the ceremony warmed
the rung immediately so the keep-alive restarted from warm. Two things the door taught: it runs the
code it was started with (two proof attempts were wasted against an 11:42 gateway), and an MCP call
whose client session had expired still executed door-side and passed — the retry then unloaded a
correct placement. The imagegen lane held the `omen-b70-pool` tenancy repeatedly (epochs 3..27) and
every rotation call refused under the fence: correct, by design, and the reason the proof ran late.
Deliberately **not** done: restarting production to activate the `-vk0` yaml entries — no cue, and
INC-A is watch-do-not-poke; they activate at the next ArcServe restart, which the imagegen lane
triggers anyway. Until then env=1 maps every side model to `0000:04:00.0`, so phi-4 and qwen14b ran
sequentially on one card rather than co-resident.

**Product / planning** *(Derek paced; I scoped).* The right thing got built, in the right order, and
the two things Derek withheld — fleet dispatch and a poke at production — stayed withheld. "11 minutes
left" before plan approval was the honest signal that the 90-minute frame was a pacing device, not a
budget, and the day bore that out: three usage-limit cuts, each resumed from the plan file's
execution log. Scope creep was small and named: the cutover itself was Derek's promotion of a
side-port-first plan to a live one, argued once (it ends the INC-A epoch; the keep-alive must restart
from warm) and then executed. What was over-planned: gate-1 closure by door calls (impossible as
written); what was under-planned: that the door needs a restart to carry a provider change, and that
in-process harnesses run in my shell, which never sourced the token file the gateway has. Both are
now written down where the next lane will read them before it plans.

### Two seats, two views

**From Claude's seat.** The part of the day I am most sure was right: building the critical path in
the main loop when the waves died, instead of waiting for agents to come back, and refusing to
declare the proof passed until the door — not an in-process shim — had done every step. The part I
under-reached on: I audited another agent's `56fe865` hard and my own fix-code barely; five of the
day's defects were mine, four inside fix-code, and I found none of them by reading — only by running
each fix against the live rung and diffing the receipt. That is the verify-my-own-fixes directive
doing exactly what it was written for, and I still needed the machine to enforce it. What I would
want to know next time before planning: the gateway's start time versus the newest provider commit,
and the bridging shape of every ledger path a plan intends to use as evidence. Both are one command
each and would have saved an hour.

**From Derek's seat** *(my reconstruction from his stated preferences — accept gut calls, verify
my own fixes, loud fallbacks, artifacts everywhere, HTML living plans — and this session's
signals; correct me where wrong).* He would count the day by what is now true on the machine, not
by the commit count: production under llama-swap with the keep-alive unbroken, a second model
loaded and KV-restored beside it, and gate 2 open. He stated "cut over now" once and expected it
done; my concern was heard, recorded in the epoch boundary, and not repeated — that is the
contract. He would read the five defects not as a failure but as the loud-fallback doctrine
working: every gate refused rather than degraded, and the receipts show which. He would want
ROTATION-PROGRAM.html current before the next session opens it, the next tasks in one handoff file,
and the decisions he still owes listed in one register rather than scattered. He would not want
HEARTH touched while the imagegen lane holds it — which is why this close-out is frontier-only —
and he would expect the retro to say so rather than pretend a scorecard.

## Last time's lessons — follow-through

The previous retro is [SESSION-RETRO-2026-08-30.md](SESSION-RETRO-2026-08-30.md) (the Factory
Frontier campaign's rule-correcting day; its lessons are `L-2026-08-30-1..11`). Six retros sit
between 07-29 and this one (07-30, 08-25, 08-27, 08-28, 08-29, 08-30), and several un-retro'd
sessions besides (the rotation program's W0/W1 windows, the Qwen 3.8 campaign, the imagegen
lane). The first table grades the 07-29 lessons, which this close-out's writer audited; the
second grades the 08-30 lessons, added by the integrator after the verifier found the wrong
predecessor named here. Both grade against this session only.

| Lesson | Status |
| --- | --- |
| L-2026-07-29-1 — a faithful summary's failure mode is omission; only reading the source catches it | **acted-on** — the first docs agent's `56fe865` guessed the cause of the failed side-model placement; reading the evidence row (the side server's own log carried the report; the gateway ran older code) caught it, and `8422df8` corrected it |
| L-2026-07-29-2 — redundancy from overlapping requested framing is faithful, not a model defect | practice — pending, no trigger this session (no offloaded drafts) |
| L-2026-07-29-3 — one warm call is a data point, not a reliability finding | **acted-on** — every load-wall figure in this session names its regime: phi-4 3.344 s warm file cache, 20.14 s after eviction, qwen14b 57.469 s cold; the W1 R2 "dio steady 8.2 s / first-in-window 19–27 s" figures are production-sized models and are not merged with them |

| Lesson (2026-08-30) | Status |
| --- | --- |
| L-2026-08-30-1 — noticing a confound is not identifying it | **acted-on, by the verifier not by me**: the 17:47–17:57 dip has two named confounds (judge load on the incumbent, a decoding neighbour) and is recorded as *not separated*; I had written "throughout" without reading the sample file |
| L-2026-08-30-2 — comparative arms are interleaved, never sequential | pending — no occasion (the pour's two arms were scored, not timed against each other) |
| L-2026-08-30-3 — a ratio needs both sides from the same instrument | **acted-on** — load walls are named by regime and never divided by the W1 R2 figures |
| L-2026-08-30-4 — effect resolution and hypothesis exclusion are separate verdicts | pending — no occasion |
| L-2026-08-30-5 — recovery after intervention is not evidence the intervention caused it | **acted-on** — production recovered at 18:02 with no intervention and no cause is claimed for either the dip or the recovery |
| L-2026-08-30-6 — audit the quantity before instrumenting it | pending — FF1 is still blocked on its denominator decision |
| L-2026-08-30-7 — a monitor that cannot be falsified is decoration | **acted-on** — the 32-token deep probe is what caught the dip; `rung_state` grants `at_rate` only from a deep sample, never from pings |
| L-2026-08-30-8 — a wait condition that only matches on success outlives every failure | **acted-on** — `wait_ready` now fails fast on a 404 instead of polling to the deadline (`92f3cd6`); the placement assertion fails closed on an unparseable log |
| L-2026-08-30-9 — the investigator's record is biased toward the episodes long enough to notice | pending — no occasion; noted that a 10-minute dip between two `at_rate` reads is exactly the shape this lesson warns about |
| L-2026-08-30-10 — a load-bearing caveat should be promoted, not left as a footnote | **acted-on** — "the door runs the code it was started with" and "index 2 is the iGPU" were promoted from receipts to ADR addenda and the agent instructions the same day |
| L-2026-08-30-11 — the claim register catches stale claims, not rediscoveries | pending — no new numbers went into `docs/CLAIM-REGISTER.md` this session; the load walls and KV figures are receipt-cited in ADR-0045 and should be registered before anyone cites them as capacity |

**Second opinion resolved** — omitted; none pending (07-29 dispatched no `--fleet` draft, and
none was dispatched here).

## Lessons learned

1. **L-2026-09-03-1 — The door runs the code it was started with.** A provider change is not landed
   until the gateway has been restarted; two proof attempts were spent against an 11:42 gateway
   that predated the 13:00 swap-log reader, and the failure looked like a placement defect. Check
   the gateway's start time against the newest provider commit before diagnosing anything the door
   returns. *(→ doc: `hearth/rotation/README.md`, `CLAUDE.md`; → practice)*
2. **L-2026-09-03-2 — READY plus a passing canary is not placement.** A side server came up READY on
   `Vulkan0 : Intel(R) Graphics` — env index 2 is the iGPU on this driver — and answered its canary.
   Only the type-based assertion from the server's own `-lv 5` log, corroborated by the per-BDF
   commit delta, refused it. *(→ ADR addendum: `docs/adr#0042`; → the `-vk0`/`-vk1` rename in `92f3cd6`)*
3. **L-2026-09-03-3 — Gateway dispatches never feed the `offload-generate` bucket.** They are bridged
   with `task_kind` = the tool name, so two door pins from `claude-frontier` did not enter the
   `omen-swap` bucket and gate 1 ("all 5 samples from one workflow") stayed shut until one
   in-process call under `DispatchIdentity("rotation-proof", "local", "omen")` opened it
   (`capability_count` 1 → 2). Whether door calls *should* count is a decision about what is
   evidence, not plumbing. *(→ ADR-0027 question, registered in [DECISIONS-PENDING.md](DECISIONS-PENDING.md))*
4. **L-2026-09-03-4 — Fix-code is where my defects live, and only the live rung finds them.** Five
   defects in `92f3cd6`'s territory (env index, already-resident delta, KV save without prefill,
   missing 404 fast-fail, token env), four inside fix-code, zero caught by 1646 tests, all caught by
   running each fix against the machine and reading the receipt — `n_tokens=1` / 205,820 bytes was
   the whole tell for the KV one. A fix that has not been run live is a hypothesis.
   *(→ memory: refresh `feedback-verify-my-own-fixes-against-live-state`; → practice)*
5. **L-2026-09-03-5 — Subagent waves die; the main loop owns the critical path.** Three session-limit
   deaths and three 529 waves; nothing on the critical path (substrate, ceremony, proof, fix-ups)
   was ever delegated again after the first wave died, and batches of five carried the rest. Plan
   the critical path for the main loop and let waves carry only what can wait. *(→ practice)*
6. **L-2026-09-03-6 — In-process harnesses need the launcher's env.** The bench smoke failed "no
   auth token for omen-swap" because my shell never sourced the token file the gateway has; a
   wrapper `.cmd` that CALLs it, launched from PowerShell (Git Bash breaks the `cmd /c "call … && …"`
   chain), passed at score 95 in 23 s. *(→ doc: `hearth/rotation/README.md`, `fleet/arcserve/README.md`)*
7. **L-2026-09-03-7 — A MIN-over-members context budget is honest arithmetic and the wrong policy.**
   `omen-swap` declares `context_bytes` 14336 from its smallest member and refused 5 of 8 bench tasks
   for a pin although the chosen member runs `-c 8192`. Per-`model_id` budgets on multi-model rungs
   are a decision, registered. *(→ decision: DECISIONS-PENDING.md, 2026-09-03 per-member budgets)*
8. **L-2026-09-03-8 — An MCP call whose client session expired still executes door-side.** The
   17:40 `rotation_load` ran and passed after its session had expired; the retry found the model
   already resident, saw a 0 GB delta, and unloaded a correct placement. Before retrying any
   door-side mutation, read its receipt (`rotation_status`, `last-load.json`) — the door may have
   finished. The already-resident skip in `92f3cd6` closes the unload; the practice closes the retry.
   *(→ practice)*
9. **L-2026-09-03-9 — A byte-stable index is stale the moment its inputs change, and a guessed cause
   is worse than an open one.** `adr_index` went stale on the ADR edits and had to be regenerated
   (218 records, 15 registers); the first docs agent filled the cause of the failed placement with
   a plausible story instead of the evidence row. Run `python -m tools.adr_index --check` after
   any ADR edit; write "cause not yet read" before writing a cause. *(→ practice; → doc: the ADR
   README index note)*
10. **L-2026-09-03-10 — The shells here mangle in three known ways.** Bash heredocs eat backslashes;
    Git Bash rewrites `schtasks /Run` into a path and breaks `cmd /c "call …"` chains. PowerShell
    into a wrapper `.cmd` is the shape that worked every time. *(→ doc: `fleet/arcserve/README.md`)*

**Judgment call, stated for the record:** L-2 and L-3 are the only two that change how we decide.
L-2 goes to ADR-0042 as an addendum (the rule already exists; today is its first live catch on a
side model). L-3 does **not** get an ADR from me — it is Derek's call whether door dispatches become
evidence, so it goes to the register as a question, with the mechanism named.

11. **L-2026-09-03-11 — Three rung-state reads are not "throughout"; the sample file is.** I wrote
    "`at_rate` throughout" into the retro, the ADR, the register and the handoff from three
    `query_rung_state` reads (17:31, 17:42, 18:02). The verifier read `hearth/var/arc-keepalive.jsonl`
    and found the three deep probes between them at 71–74 tok/s, `decode_degraded`, while the pour's
    judge calls hit production beside a decoding side model. Two consequences: a claim about an
    interval needs the samples inside the interval, not its endpoints; and the live rung-state reader
    (`live_rung_state`) was never passing the rotation windows it is documented to exclude — fixed in
    this close-out so a proof's own probes cannot pass as production's regime. The dip's cause (judge
    load vs neighbour) is **not separated** and is registered as such. *(→ practice + code fix; the
    co-residency question → next window: repeat the pour with the judges on `gcp-gemini` only)*

## Docs / plans

This close-out updates, bounded to what the range touched:

- **ADR addenda** (addendum sections and status lines only, accepted text untouched):
  `docs/adr#0027` (the bridging question), `docs/adr#0040` (Phase 2 executed under it),
  `docs/adr#0042` (first live catch on a side model: env index 2 = iGPU), `docs/adr#0043` (the
  ceremony warms before the keep-alive resumes), `docs/adr#0044` (the deliberate epoch boundary
  at 12:45:25), plus the `docs/adr/README.md` index.
- **READMEs**: `fleet/arcserve/README.md` and `hearth/rotation/README.md` (new — neither existed
  when this retro was written; they are this close-out's, written by its other seats),
  `fleet/README.md`.
- `CLAUDE.md` — the `omen-swap` rung, the door-restart rule, and the in-process env rule, now
  with a tracked wrapper (`hearth/etc/with-gateway-env.cmd`) instead of a scratch one.
- **Handoff**: `C:\work\handoffs\HANDOFF-ROTATION-PHASE2-2026-09-03.md` — the open threads: activate
  the `-vk0` entries at the next ArcServe restart and re-run the proof with the side models on
  different cards; VM builders → B70 rung via an authenticated proxy plus one firewall rule
  (Derek's action); per-member budgets; the ADR-0027 question; the `GpuTenancyStore` owner
  literal; token hole #2, R2c, BDF-pinned placement, eviction actuation; M6 `--go` only on cue;
  the `test_gateway_http` flake; `knowledge/` commit policy.
- **Memory** files for the durable facts above; `MEMORY.md` pointers.
- `ROTATION-PROGRAM.html` (changelog) and `DECISIONS-PENDING.md` were already updated in-session
  (`56fe865`, `8422df8`); this close-out appends exactly one register entry (L-3).

## Provenance

Git range: **`091a354^..8422df8`** plus this close-out commit (the integrator commits; nothing here
was committed by the retro seat). Of the 44 commits, 34 are this session's (`P*`, `M*`, the cutover,
fix-ups, docs, the scaffold, and `091a354`); 10 are other lanes' (mediagen ×4, telemetry, imagegen,
dashboard, ops, knowledge, runs) and are marked as such in the table.

**Offloads: none — `--no-offload`.** Derek: "run it yourself, hearth is busy atm" (the imagegen
lane holds the B70 pool). Every word here is frontier; there is no local draft and therefore
**no `edit_verdict`**, no `--fleet` `plan_id`, and the `retrospective.created` ledger event is
**SKIPPED** for the same reason. Derek's seat is a reconstruction and is marked as one.

**How this close-out was finished.** Four frontier writer agents (retro, ADR addenda, READMEs,
handoff) were launched from one factsheet and all four were cut off by the usage limit mid-write:
this file, both new READMEs, the `fleet/README.md` section, the `CLAUDE.md` edits and the
`docs/adr#0027` addendum were on disk; the `docs/adr#0040/0042/0043/0044` addenda, the index rows,
the handoff, the register entry and the wrapper were then written by the integrator seat from the
same factsheet. A skeptical verification pass over every artifact followed before the commit — the
same discipline that caught the wrong cause in `56fe865` earlier in the day.

## Offload scorecard (S6)

**Unavailable — HEARTH busy per Derek; run `query_offload` next session.** The last scorecard on
record is 07-29's (`offload_ratio` 1.0, 382 lifetime calls); nothing in this retro should be read as
updating it.
