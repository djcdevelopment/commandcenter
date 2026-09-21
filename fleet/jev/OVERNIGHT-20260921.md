# Overnight JEV / Hermes work log

Authority: Derek permitted 4–8 hours of iteration to improve useful local-work
throughput and quality. The goal remains active. Do not confuse a completed
cycle or a healthy endpoint with completion of that larger objective.

Previous goal turn classification (cycle 12): **progress**. Active-work polling
and safe wait reasons were deployed, and a live JEV dispatch used curated
quality feedback. This does not imply reliable unassisted code delivery.

## Latest: cycle 13, missing worker artifact; useful Codex HTML fallback

The [updated human/agent HTML snapshot](reports/fleet-operations-20260921-cycle13.html)
now exposes verified work quality alongside the fleet controls and capacity.
It was captured at **10:27:02 UTC**, not live. Its quality table and embedded
JSON agree: eight selected tasks, six reached the worker, one unchanged delivery,
four assisted deliveries, one incomplete, and two that did not run there.
Those labels are operator-curated; this is not a benchmark or automatic training.

This lap's actual experiment **failed**. Window 10:17:50–10:37:50 UTC, first
rendered artifact target10:22:50, latest dispatch10:21:30, generation ceiling
10:32:50, final five minutes reserved for delivery/restoration. JEV dispatched at
10:20:14; worker result was terminal by10:22:30, but no source artifact existed.
The direct Codex fallback was first saved at **10:23:06.5188096**, missing the
first-artifact target by **16.518 seconds**. No generation retry followed.

Parent `br-20260921-101826-9c4871a9`; child `br-20260921-102014-e72849c0`;
task `jev-35f435a106d15feb1147bb14`; worker commit
`950125e55db5c077740d89df02d2c204d76b7a10`. JEV fit3.0/confidence1.0/ambiguity0.13,
957 input tokens,0.249s, estimated USD0.000040194. Its request used history SHA
`d6a91fa05674982f12434c9002e149f5d0bfea85627453637aa989bb6c9b4922`.
High task-fit confidence again did not predict useful output.

The resident worker reports four steps /126 seconds, completion reason `budget`.
Its commit contains only `retro.md`; neither root nor nested `operations_page.py`
exists. The farm's generic success/162-test assay is not deliverable success.
The capture failure left the queue review_pending and FX99 held with ExceptionGroup.
No candidate existed and **no Hermes inference started**. The actual reason the
four-step run wrote no source is not established by these artifacts; do not
invent a GPU, context or tool diagnosis. Before another full-file rewrite,
inspect this run's tool-call outcomes, not private model reasoning.

Codex had temporarily deployed an appended 130-word findings-only review contract
with a90-second automatic budget. Since no candidate reached it, that change is
**unqualified and rolled back**, both on FX99 and in source. The old review hash
`0a50aba9f7e77ca60829886df28cd887a3187c8f2c45a739fb9a2cdf4f6cc0c5` was restored
from its exact backup. The failed task was marked operator_held, retaining its
document/decision/result. The reviewed HOLD was archived as
`HOLD.review-capture-cycle13-20260921`; CPU service resumed while idle. AM4 owner
was null/model absent. No gateway restart, model rotation, KV reuse or root push.

The actual HTML addition is **entirely Codex work**, not repaired model output.
It reuses the validated quality loader and existing escaped table renderer,
retains unknown states and exposes the same quality record/hash to agents.
Independent checks used the actual rendered snapshot: seven displayed metrics,
13 registry rows, embedded JSON, absent/malformed/unavailable quality, exact-true
availability, escaped hostile hash text and existing-output refusal. No new test
files. A CPU-only headless Chrome render was visually checked; the new table is
readable and matches the JSON. The failed worker outcome was also hot-deployed
to FX99's curated history for subsequent JEV decisions.

Evidence: [raw worker result](evidence/20260921-operations-quality-worker.json),
[actual execution and restoration](evidence/20260921-operations-quality-execution.json),
[deployed quality feedback](evidence/20260921-operations-quality-feedback.json).
Private first/final HTML and screenshot are under
`C:/Users/derek/.fleet-scheduler/advisory-cycle13/`. The original09:30 snapshot
is preserved. Cumulative JEV ledger:12 attempts, USD0.000377160 confirmed usage
estimates, USD0.005505024 uncertain reservations, USD0.005882184 booked. Codex
and local hardware/power costs are not captured by that ledger.

## Cycle 12, deployed active polling and safe wait reasons

Window **09:57:43–10:17:43 UTC**, generation ceiling 10:12:43. Useful artifact:
deployed adaptive polling, a real worker-authored status-view starting point,
and a live JEV decision using curated quality feedback. First working output
was **10:02:49**, six seconds after the 10:02:43 target; do not call it on time.
Parent `br-20260921-095909-41874797`; child `br-20260921-100117-3f5abe25`;
task `jev-aacd8778e90c5ef5a61aa434`. No generation retry or new test files.

Codex changed the scheduler to sleep five seconds while an active build is in
progress, 30 seconds when idle/held, and one second after a completed review.
Status records the requested delay without falsely refreshing observation time.
The actual FX99 active observation gaps were **5.759, 5.658 and 5.667 seconds**.
The sampler captured the configured one-second post-review delay, not the next
observation. This is a cadence result, not a jobs/hour benchmark. The previous
13-second worker / 31-second observed completion gap also includes farm assay
and transport. Conductor clock was about 1.02–1.32 seconds ahead of OMEN, so
cross-host file timestamps cannot support precise finish-to-review claims.

One real JEV dispatch: fit3.0/confidence1.0/ambiguity0.14, 950 input tokens,
0.316 seconds, estimated USD0.0000399. Its recorded quality-history SHA is
`19d2b3f90fe8db1f3d61bd75c8c78e3ac82f9f70140235de0a41a2f05f8b9212`.
Gates and spending limits are unchanged. Confidence is task fit, not code proof.
The OMEN worker produced candidate `2d7c1652c199128a61dce8d13bdea7c48f8ef42c`
in four recorded steps / 18 seconds. It incorrectly accepted boolean numeric
values and rendered absent wait reasons as unknown. Codex corrected those
conditions, extracted the CLI wrapper, and preserved the seven original lines.
Six focused execution cases pass after correction; four fail on the raw draft.
The raw draft's real newline joining was already correct; no newline failure
should be inferred from JSON escaping.

Hermes returned NEEDS_WORK in 55.971 seconds and released AM4. It caught numeric
bool defects, but incorrectly flagged the original uppercase reviewer label and
misread the two-stage wait guard. The new-file packet lacked the separate CLI
baseline. Executed checks were performed afterward, not supplied to that review;
the saved required-evidence gate remains unloaded. This is **assisted delivery**,
not unchanged model delivery or independent reviewer correctness.

Adaptive CLI deployed at 09:59:11; corrected status module/wrapper were deployed
by 10:12:36 with hash checks and distinct recoverable backups. The actual FX99
command reports eight lines, daemon running, zero pending reviews and no active
build. AM4 helper verified owner null/model absent; OMEN verified at 10:13:04
with its unchanged 16k/eight-slot resident and all slots free. No gateway restart,
production model change, KV reuse, branch promotion or root push occurred.

Curated quality history now has seven selected tasks: five reached the worker,
one unchanged and four assisted deliveries, two not run. Evidence:
[complete captured observations](evidence/20260921-polling-status.json),
[unedited local candidate](evidence/20260921-status-wait-candidate.json), and
[independent execution](evidence/20260921-status-wait-execution.json).
Cumulative JEV ledger: 11 attempts, USD0.000336966 confirmed usage estimates,
USD0.005505024 uncertain reservations, USD0.005841990 booked against USD0.10.
Codex effort and local hardware/power remain unmetered in that ledger.

## Cycle 11, deployed work-quality feedback; unchanged local helper

Window 09:40:11–10:00:11 UTC; first scoreboard target 09:45:11, generation ceiling
09:55:11. Parent `br-20260921-094148-9d008ba6`; child
`br-20260921-094156-8813ad33`; task `jev-f3b1d7857a35c922cbbe0cd5`.
The first real five-task scoreboard was saved **09:43:51**, inside the target.
It was then expanded to include this task's independently checked delivery.

JEV selected the task: fit2.97/confidence0.97/ambiguity0.20, 0.304s, 871 input
tokens, estimated USD0.000036582. The actual MechNet task completed and produced
`quality_counts.py`, candidate `3ae4e60c281e96fd121e18f401d5e511ea224bbf`.
The worker's recorded run reports 2 steps / 13s; submission-to-observed completion
was about 31s. The existing 162-test farm assay is not the acceptance proof.
The implementation was retained **unchanged**, apart from a final newline.
Codex independently checked real curated records, the required mixed-invalid
example, non-list/empty input and immutability. No new test files.

Automatic Hermes review finished in 35.342s and released AM4. Its static PASS is
consistent with the source; it incorrectly says nine counters once, then eight
later. It attempted read-only tool lookups and explicitly did not claim a
successful syntax/example execution. Acceptance comes from the independent
source/output checks, not that PASS. The cached gateway still lacks the saved
required-evidence gate; this review did not receive executed evidence first.

Deployed FX99 `quality.py`, the local-model counter, CLI integration and private
curated history at **09:48:04**. The existing CLI was hash-checked against HEAD,
with `.jev-quality-20260921.backup` retained; new targets were required absent.
CPU service was stopped only while idle, then restarted. The scoreboard has six
selected tasks: four reached the worker (one unchanged, three assisted), two did
not run there. Direct Hearth fallback work is not counted as MechNet authorship.
All labels are explicit operator curation with source evidence hashes, not a
new autonomous scoring system or a model benchmark.

The installed request builder prepends only a fixed numeric summary to this
profile's existing `recent_outcomes`; private descriptions/task IDs/evidence
never enter the request. Other profiles are unchanged. Missing/invalid history
is unavailable, not zero. Live installed request construction and CLI output were
verified; **a subsequent live JEV decision using the new quality feedback is not
yet sampled**. No extra paid re-evaluation of completed work was launched.
The gates remain unchanged: fit floor2.0, confidence floor0.6, ambiguity
ceiling0.25; no automatic promotion.

Local enqueue now reuses `_delegation_brief` before persistence/spend. The exact
cycle-10 Windows-path brief was refused with the queue count unchanged; the real
new task passed preflight and reached the worker. No bypass, path rewriting or
gateway restart. Integration/history/preflight/wrapper code is Codex-authored;
only the counter helper is credited to OMEN. [Initial scoreboard](evidence/20260921-quality-initial-scoreboard.json)
and [live wiring / raw review / attribution](evidence/20260921-quality-live.json)
preserve the evidence. The original candidate is retained separately.

At 09:50:38 AM4 was unloaded with no owner. API ledger: 10 attempts, 7,073 known
input tokens, USD0.000297066 usage estimate, USD0.005505024 uncertain reservations,
USD0.005802090 booked. Codex effort/local power are not included. This is a
concrete local artifact and deployed feedback improvement, not proof of higher
general acceptance rates or an optimized multi-route fleet. Goal remains active.

## Cycle 10, human/agent operating page; pre-dispatch refusal

Window 09:25:55–09:45:55 UTC; first-page target 09:30:55, generation ceiling
09:40:55. Parent receipt `br-20260921-092652-380e5888`. The actual
[operations HTML](reports/fleet-operations-20260921.html) was saved at **09:30:19**,
inside the first-quarter target. It combines the three execution layers, the
qualified pilot's limits, all 13 declared loop/harness records, dated evidence,
source hashes, and a fresh capacity capture. The complete source data is embedded
as JSON. `python -m fleet.jev.operations_page [--out NEW.html]` regenerates it.

JEV selected `jev-c06b692f1dc0c488af2a17cf`: fit2.99/confidence0.99/ambiguity0.14;
0.281s, 900 input tokens, estimated USD0.0000378. The CPU scheduler then held on
`ExceptionGroup`. Child receipt `br-20260921-092700-14f7d1eb` remained open with
no execution record. Read-only conductor checks found no matching inbox/run.
The pure `_delegation_brief` check reproduced the refusal: an illustrative TOML
record carried a Windows absolute path. This guard runs before submit. No
inference or duplicate dispatch was attempted; no path guard was weakened.

The dispatch edge ended local-builder/Hermes qualification. **Codex wrote the
normalizer and page**, not the local model. This is artifact delivery with a
failed delegated arm. The exact brief/decision remains private. Queue state was
changed from `dispatching` to `operator_held`, the child receipt closed blocked,
and the known FX99 HOLD archived as `HOLD.windows-brief-cycle10-20260921` before
restarting the existing CPU service. The gateway/production services were not
restarted. At 09:35:36 the daemon was running and idle with a fresh observation.
AM4 was never loaded this lap; the capture showed both NVIDIA cards idle.

Verification used the real page and parsed registries: all 13 rows exactly
matched source, two complete JSON blocks round-tripped, unknown B70 free memory
remained null, and an embedded-markup variant created no active tags/scripts.
One malformed-field/empty-string check and syntax compilation were performed;
no test files or broad matrix. Chrome rendered the actual local page with a
separate profile and GPU disabled; the screenshot was visually inspected.
Long table text was allowed to wrap. [Evidence](evidence/20260921-operations-page.json)
records the final file hash, source checks, browser capture and dispatch failure.

API ledger: 9 attempts, 6,202 known input tokens, USD0.000260484 usage estimate,
USD0.005505024 uncertain reservations, USD0.005765508 booked. Reservations are
not confirmed charges; Codex/power costs remain unmetered here. No default model,
prompt, routing, admission gate, registry status or GPU topology was changed.
The broader throughput/quality goal remains active, not satisfied by this page.

## Cycle 9, complete short reviews but false-PASS qualification failure

Window 09:17:36–09:37:36 UTC; first review target 09:22:36, generation ceiling
09:32:36. Receipt `br-20260921-091819-09a48f31`. The first complete review was
saved **09:19:28**, inside the first-quarter target. One paired check of the
actual rejected draft followed; its false PASS ended the lap at 09:21.

Delivered [review calibration and operating consequence](REVIEW-CALIBRATION-20260921.md),
with both raw model responses. Same Hermes/AM4 model, context, reasoning `none`
and lifecycle; exact source excerpts, reduced evidence pack and verdict-first
short output request. The corrected report got a 59-word PASS in 14.065s. The
rejected draft got a 93-word PASS in 17.576s **despite identifying real defects**.
The negative review also treated a source record omitted from the compact pack
as unsupported; the identifier exists in the full registry. Compact evidence
and verdict-first wording are not a proven general quality improvement.

No default prompt change was deployed. These are advisory findings, not a
qualified automated acceptance gate. No additional retry, test files, code,
model/topology change, or JEV/API spend. The two owned review seats were released;
the CPU scheduler and OMEN worker remain available. Larger goal stays active.

## Cycle 8, route capability report; dispatch/review qualification failed

Window 09:01:01–09:21:01 UTC; generation ceiling 09:16:01. The first useful report
target **09:06:01 was missed**. The corrected 579-word
[capability report](ROUTE-CAPABILITIES-20260921.md) was saved at **09:12:39**.
Receipt `br-20260921-090324-fc28f750`. This delivered a source-checked report,
not a successful autonomous JEV → MechNet → Hermes cycle.

JEV held task `jev-0609302367da3133217c5db1`: fit 2.54/3, confidence 0.54
(below unchanged 0.60), ambiguity 0.19. One call, 0.368s, 910 input tokens,
estimated USD0.00003822. The live profile summary describes writing a single
Python file; that is a possible explanation for a Markdown-task mismatch, not
an established cause of JEV's score. No clearer-summary retry or gate change.
The untouched ready job had no receipt/dispatch; it was explicitly moved to
`operator_held` to prevent later implicit execution. Original decision retained.

A direct Hearth fallback on the same resident OMEN model produced a draft in
51.907s (12,243 input / 1,028 output tokens), job
`job_e9757e4f1f7ccf62fae3113163a469bb`. The draft conflated catalog status with
runtime readiness, repeated historical AM4 settings as current, invented a source
path/test-mode recommendation, and contradicted existing review automation.
It was **not accepted**. Codex corrected the final report against actual source;
the [raw draft](evidence/20260921-route-local-draft.json) remains unedited.

One manual Hermes review used the unchanged AM4 model/topology, reasoning `none`,
60s application budget, owner `jev-86afa060609140988d301900`. Its wrapper reached
`hermes_review_deadline`; the owned seat was stopped and released. A delivery-only
inspection found one saved 6,381-character, tool-free assistant entry, but no
NEEDS_WORK/PASS/INCONCLUSIVE verdict and no completed `result.json`. It is partial
analysis, not a completed independent review; it remains private. No inference
retry. Why this run exceeded its budget is not established.

The final report distinguishes gateway/harness/execution layers; declared
DeepAgents status versus the named undeployed harness; the fixed pilot route;
planning validity versus field freshness; unknown VRAM; and inference, receipt
and accepted-work feedback. No claim of automatic hardware optimization or
weight training. Markdown links and named source entry points were checked.
No new test files, code changes, benchmarks, deployment or topology changes.
See [execution evidence](evidence/20260921-route-execution.json) and
[recovery metadata](evidence/20260921-route-hermes-recovery.json).

Total API ledger now: 8 attempts, known-usage estimate USD0.000222684;
uncertain reservations USD0.005505024; booked USD0.005727708 of USD0.10.
Reservations are not confirmed charges. Codex effort and local power are not
included. CPU scheduler remains active; production is untouched; AM4 is released.
The larger goal remains active. The next useful change must improve accepted
work, not turn this failure into more apparatus or a looser confidence threshold.

## Cycle 7, human/agent capacity page and failed authoring probe

Window 08:44:13–09:04:13 UTC. The first-page target at **08:49:13 was missed**;
the actual page was saved at **08:52:13**. Do not count the wrapper or settings
patch as meeting that artifact deadline. Receipt `br-20260921-084635-8a66fa73`.

Delivered [capacity snapshot HTML](reports/capacity-20260921.html), plus
`python -m fleet.jev.capacity_page [--out NEW_PATH]` for fresh pages. It uses the
existing observer, shows native slots/B70 adapter-vs-process memory/AM4 driver
memory, and embeds the complete source JSON. Timestamps, TTLs and a prominent
"Snapshot, not live" warning prevent an archived page from masquerading as a
reservation. B70 free memory remains unknown. Existing output files are refused.

Hermes/Dense was asked to author a small pure renderer using requested reasoning
`low`, a setting already documented by `E:/work/hermes/local_cli.py` for the custom
provider. Same model, hardware and context; no topology change. Normal review
default stays `none`. The optional setting was deployed to FX99 with the guarded
`review.py.jev-reasoning-option-20260921.backup` retained; the CLI flag and provider
override both receive it. Metadata calls it requested_reasoning, not proof of the
model's internal behavior.

The authoring run hit `hermes_review_deadline` with a 90-second application budget
(the existing wrapper allows up to another 20 seconds before its interrupt/grace
handling). No final result exists; its database has one user message and no saved
assistant answer. At 08:50:24 the owned seat was confirmed stopped/released; no
extension or second generation was launched. Root-cause attribution is unknown:
this was a different task from the earlier helper, so it does not isolate a
reasoning-mode effect. Do not call it proof that low reasoning is broken or slow.

Codex wrote the fallback renderer and CLI; **no Hermes-authored renderer shipped**.
The failed prompt/state remain privately under `html-cycle7` and FX99 review owner
`jev-9a450e9b7bf742e444aef4d3`. This was a late artifact rescue, not successful local
authoring. Setup/dispatch overhead and the broad output request still consumed too
much of the first-quarter reserve.

Actual page: 7062 bytes, captured 08:52:13.528206 UTC. HTMLParser verification on
the real schema and a markup/unknown-source variant proved one application/json
script, exact JSON round-trip, no injected tags/event handlers, and explicit
snapshot/allocation warnings. No browser screenshot or visual render is claimed.
No test files were added. [Execution evidence](evidence/20260921-capacity-html-execution.json)
records page hash, preserved unknown B70 fields and the failed model attempt.

The larger goal remains active. JEV API spend is unchanged. This is a useful
reporting artifact plus negative authoring evidence, not a demonstrated
throughput/quality improvement from reasoning `low`.

## Cycle 6, deployed failure-first review packets

Window 08:31:15–08:51:15 UTC. Corrected helper working against the real packet at
**08:35:40** (within five minutes), deployment verified at **08:36:19**, and
live review/release finished at 08:37:33. Receipt `br-20260921-083206-da4f68bb`.

Changed the source-generation assignment: Hermes on the existing AM4 Dense seat,
not another OMEN synthesis retry. No model/topology change or extra permissions.
Hearth's receipt backend is routing context only; it is not the author here.

- Hermes/Dense source generation: 69.815 seconds, 90-second ceiling, owner
  `jev-09fd57e8c3f827fde058737b`. It emitted an incorrect first code block, then
  explicitly corrected itself with a second final block in the same response.
  Both are preserved. The final block was selected, not silently described as a
  clean one-block answer.
- The selected helper preserved all failing cases and candidate source on the
  real packet. One defect remained: comparing the replacement JSON size with the
  entire old packet could enlarge small evidence sections. A direct check grew
  2115 to 2358 bytes while claiming compaction. Codex changed that guard to
  compare complete packets. The rest of the selected helper is Hermes-authored;
  integration/deployment/checks are Codex. This is assisted delivery, not a
  wholly accepted unmodified model patch.
- The real packet fell from **27,284 to 16,070 bytes** (about 41%). Every failed
  row, non-case evidence field and candidate prefix/suffix matched exactly.
  Failure summary correctly says four passed/four failed. Missing/duplicate
  markers, malformed/nonboolean evidence and no size benefit leave input intact.
  No test files or broad assay were added.
- Live deployed Hermes review: 36.793 seconds, one assistant/no tool messages,
  NEEDS_WORK. Prior uncompressed run: 46.676 seconds, five assistant/four tool
  messages (four assistant tool-call messages). Counts were read from actual
  state databases. This single pair does not prove a general speedup or isolate
  the effect of payload size from changed model behavior.
- The new review named all four failed checks correctly, but claimed a list was
  hashable and invented a justification for dropping an empty-string rule. Those
  statements are false. The compaction is a payload/traceability improvement,
  **not evidence of reliable semantic review**. Acceptance stays grounded in
  executed evidence; no automatic promotion.

Deployed on FX99: `fleet/jev/review.py` and `review_evidence.py`; verified imports
and exact hashes. Existing review file backed up as
`review.py.jev-evidence-review-20260921.backup`; no overwriting old backups.
The idle CPU scheduler was stopped only for installation and restarted. Fresh
installs now include `review_evidence.py` and the previously omitted `budget_view.py`.
No gateway/conductor restart or capability expansion. The baseline fix and
required-execution-evidence gate remain pending gateway maintenance.

Every live review now retains original/sent packets and packing metadata as
mode-0600 files. The audit hashes matched their actual bytes during the live run.
Review owner `jev-c087547d4ec649048eb0d1d0` was released; AM4 returned to unloaded.
JEV API ledger is unchanged: no API call was needed for this review-path change.

Evidence: [Hermes source](evidence/20260921-review-packing-hermes-candidate.json),
[executed preservation checks](evidence/20260921-review-packing-execution.json),
[live review and audit](evidence/20260921-review-packing-live.json).

## Cycle 5, commit-bound review baselines

Window 08:19:27–08:39:27 UTC. A real reconstructed packet was working and checked
at **08:22:53**, before the five-minute cutoff. Hermes finished at 08:24:39,
AM4 was released, and exit checks at 08:26:10 show a fresh/running CPU scheduler
with no active build or review. Receipt `br-20260921-082020-bce9dd11`.

`fleet/jev/baseline.py:read_baseline()` now reads bounded immutable Git blobs,
validates the full commit and exact path, distinguishes absent file from missing
commit, and returns provenance with the content. `artifacts.review_packet()` uses
that helper, writes `baseline.json`, labels new files, and warns that the current
worktree may contain later edits. This is **manually verified source**, not code
loaded into the running gateway. No restart or substitute service was attempted.

The real cycle-4 task was reconstructed from its original envelope and actual VM
candidate. At base `57f0aca07ab2de3b629958f77ccb5415f89c87e6`,
`fleet/jev/b70_capacity.py` is absent, while the current worktree contains the
corrected implementation. The packet now says so. Existing `artifacts.py` baseline
content is byte-identical to `git show` and differs from today's edited file.
Missing commit, branch-name and parent-path checks fail rather than silently
substituting empty/current source. No test files were added.

- Direct Hearth / OMEN attempt: 14.031 s, 2432 input/816 output, job
  `job_b1f12fdea5f7c8b96e015e1c101bde68`. Unusable: split-on-NUL parsing then
  required the removed NUL, misparsed ls-tree header fields and never returned
  absence. Executing existing/absent real paths failed both. No retry.
- Codex wrote the replacement helper and packet integration. The failed local
  source is retained, not credited as a successful patch.
- Hermes / AM4 Dense 27B used the reconstructed packet with a 60-second budget;
  elapsed 46.676 s, verdict NEEDS_WORK for the original parser. It correctly
  separated later working-tree fixes from the candidate and identified its bool
  and non-string-rule faults. It still falsely listed `sorted_unique_string_rules`
  as passing (the supplied evidence says false). It also mentioned today's
  working-tree header despite the packet-only instruction. The verdict is useful,
  but the model is not a reliable substitute for executed checks. No speedup or
  controlled quality-rate claim is made from this one re-review.

Evidence: [failed local attempt](evidence/20260921-baseline-local-attempt.json),
[actual Git/packet checks](evidence/20260921-baseline-execution.json),
[unedited Hermes review](evidence/20260921-baseline-hermes-review.json).
Private rebuilt packet/baseline are retained at
`C:/Users/derek/.fleet-scheduler/baseline-cycle5/`; the packet hash and byte count
are committed in the execution evidence. Original cycle-4 review is unchanged.
Manual review owner was `jev-531e8784ffe56af3387a6d31`, subsequently released.

No JEV request this lap: the problem was evidence provenance, not task selection.
Ledger unchanged at seven attempts / 4392 input tokens / USD 0.005689488 booked,
including USD 0.005505024 uncertain reserves. The wider optimization goal remains
active. Repeated unusable OMEN synthesis is now substantial evidence to change
which work it receives, not justification for more identical helper retries.

## Cycle 4, runnable live capacity command

Window 08:08:39–08:28:39 UTC. First working end-to-end command at **08:12:36**,
inside the five-minute target. JEV dispatched at 08:09:45; local generation and
Hermes review finished well before 08:23:39. The larger overnight goal is active;
this is a visibility improvement, not an autonomous hardware-placement policy.

`python -m fleet.jev.capacity [--json]` now produces a real report on OMEN using
native slot admission, existing B70tools and AM4 nvidia-smi. Source windows/TTL,
LUID/BDF or NVIDIA UUID identity, raw recording hashes, disagreement names and
unknown B70 free memory survive into machine-readable output. No inference is
started by this command. Partial source failures remain unknown individually.
Two live captures succeeded; the second completed in about 2.9 seconds, with
unchanged OMEN residency and idle AM4. No timing comparison is claimed.

Parent `br-20260921-080905-510881e7`; task `jev-85c23aa9ba09049946734927`;
worker receipt `br-20260921-080945-5fafd6b7`; plan
`hearth-hermes-br-20260921-080945-5fafd6b7-aa436bf8`; raw candidate
`b2ab223443b2779aff6e129d1fb52693c613ae07` (not promoted).

- JEV: 0.437 s, 892 input tokens, USD 0.000037464 usage estimate; fit 2.97,
  confidence .97, ambiguity .07. Gates unchanged. This fit score is not a
  calibrated probability of delivering correct code.
- MechNet/OMEN `qwen3-30b-a3b`: 84 s, three steps. The parser handled the actual
  B70 capture, source separation and late identity records. It again used
  `isinstance(..., int)` despite explicit exact-int requirements, and accepted
  non-string disagreement rules. Original: four of eight targeted checks pass.
  Existing MechNet 162-test assay is not acceptance evidence for these failures.
- Codex: live-probe orchestration/CLI; corrected parser integer/rule guards,
  combined metric/rule processing into the requested second pass, and guarded
  identity types. Integrated parser passes all eight direct checks. No test
  files or second builder retry. The local model's unedited candidate is retained.
- Hermes / AM4 Dense 27B: 55.561 s, NEEDS_WORK for original candidate. It identified
  both substantive failures from code and execution evidence. Its statement that
  failing cases marked `passed:false` are "internally inconsistent" is wrong;
  the supplied evidence is consistent. Do not repeat that statement as fact.

**New review-packet defect to address next:** `artifacts.review_packet()` reads
the current worktree target as "Original" instead of the envelope's base commit.
Here the integration file already contained corrections, so Hermes described
the original candidate as a regression against those later corrections. The
verdict is still correct, but baseline attribution is not. No second review or
plumbing refactor was squeezed into this lap. Preserve base-commit truth before
using these reviews as learning/quality feedback.

Evidence: [original candidate](evidence/20260921-capacity-parser-candidate.json),
[executed checks](evidence/20260921-capacity-parser-execution.json),
[Hermes review](evidence/20260921-capacity-parser-review.json),
[JEV decision](evidence/20260921-capacity-parser-decision.json),
[actual command output](evidence/20260921-capacity-command-live.json).

CPU scheduler restarted at 08:16:11; AM4 released by the review lifecycle and
observed idle in the 08:15 capture. No gateway restart, policy expansion, model
rotation experiment, automatic promotion, or placement gate. The required-evidence
gate remains source-only; this lap used operator-paced dispatch/evidence/review.
Cumulative API ledger: seven attempts, 4392 confirmed input tokens,
USD 0.000184464 configured-price estimate plus USD 0.005505024 uncertain reserves;
USD 0.094310512 remains. Codex/local power costs are outside this ledger.

## Cycle 3, trustworthy capacity evidence

Window 07:57:34–08:17:34 UTC; report and observations completed by 08:07:15,
ending early after the useful finding. Initial report missed its five-minute
target by 16 seconds; source discovery and context recovery still cost too much.
Receipt `br-20260921-080250-98700cbe`.

[Capacity report](CAPACITY-20260921.md) and three machine-readable evidence files
record actual native slots, AM4 free memory, B70 adapter-vs-process observations,
and independently corrected local-model analysis. No controller was added.

- Native OMEN: 8 idle slots, 16384 tokens per slot, resident `qwen3-30b-a3b`.
- AM4: both NVIDIA GPUs effectively idle; reviewer owner null/resident false.
- Existing b70tools: about 14.58/15.44 GiB adapter-local committed versus 4 KiB
  DXGI observer-process usage. Do not turn the latter into a free-memory claim.
  Historical SYCL undercount and current budget disagreements remain explicit.
- Direct Hearth report: 23.484 s, 6851 input/729 output, job
  `job_7659232a0d7a8546191a15003512b5bf`. Useful but not independently correct:
  invented state path, wrong context-check attribution, and imprecise digest
  claims. Codex corrected from source; no retry or additional model review.
- JEV only ranks approved tasks for one builder profile. General operator
  inspection also leaves live VRAM unknown; changing snapshot entrypoints alone
  is not the missing observation adapter. DeepAgents registry is not readiness.

Recommendation: source-packed work on the resident route, task budget at most
8192 tokens within the 16k slot, fresh admission at dispatch; keep AM4 sleeping
between needed reviews. Next implementation lever: per-adapter observations with
source/scope/freshness/disagreement, initially advisory rather than an allocation
gate. No new benchmark, test file, model swap, gateway restart, or API request.
Exit checks at 08:07:14–15 confirm the original hardware state and running CPU
scheduler. Budget remains USD 0.005652024 booked, of which USD 0.005505024 is
uncertain reservations, not confirmed charges. Larger goal remains active.

## Cycle 2, read-only budget command

07:34–07:54 UTC window. Delivered `fleet-scheduler budget` and `budget --json`
on FX99 at 07:50:57. Real reads preserve ledger bytes and `status --json`.
Generation stopped in time; documentation/commit finished at 07:54:14, slightly
past the delivery ceiling. Final receipt accounting followed; do not report the
entire cycle as within budget merely because the command was already deployed.
Output separates USD 0.000147000 usage-based estimates from USD 0.005505024
uncertain reservations; USD 0.094347976 remains against the unchanged USD 0.10
cap. Estimates use the configured price, not provider invoice reconciliation.

Parent `br-20260921-073615-8a1052e5`; task `jev-d2d8cebb2cd971e6ebb5a049`;
worker receipt `br-20260921-073728-1ff691f8`; candidate
`e20776d4d4ddc17e37f84796f79137fdaf2112b5`, not promoted.

- JEV: 0.374 s, 917 input tokens, USD 0.000038514 estimate; gates unchanged.
- OMEN/MechNet: isolated helper in 36 s/two steps versus 177 s/five steps for the
  earlier whole-file task. Different tasks: not a controlled speedup claim.
- Initial code handled the actual ledger but failed overflow/exact-int criteria.
  Evidence-first Hermes review rejected it in 51.499 s, but misread a poorly
  described subclass case and incorrectly treated a soft size target as blocking.
- One guided HEARTH repair on the same model took 9.016 s, 2158 input/541 output
  tokens, job `job_ba37615d838b312a11f16ca591fbf741`. It fixed the targeted faults
  but dropped finite-input checks. No second repair retry was attempted.
- Codex restored those checks and integrated the CLI. Ten direct contract cases
  passed, including the actual ledger. No test files were added. Existing broad
  MechNet assay tests were not treated as acceptance proof.
- Final evidence-first Hermes review passed in 17.928 s, before 07:49. Its overflow
  explanation was imprecise: `except OverflowError`, not a later result check,
  handles conversion failures. Independent execution remains authoritative.

This is one usable **assisted** delivery, zero newly accepted unassisted patches.
Initial source and repair were OMEN-authored; finite-check correction, integration
and a bounded review-budget parameter were Codex work. `run_hermes` accepts
30–180 seconds, retaining its old default; the final review used 45 seconds.
CPU scheduling resumed; AM4 released. Gateway and conductor remain unchanged;
the required-evidence gate is still **not loaded**. Dispatch/evidence/review were
operator-paced with `run-once`, and evidence presence was verified in the packet.

[Initial source](evidence/20260921-budget-initial.py),
[initial review](evidence/20260921-budget-initial-review.json),
[repair feedback](evidence/20260921-budget-repair-feedback.json),
[raw repair](evidence/20260921-budget-repair-result.json),
[final execution](evidence/20260921-budget-final-execution.json),
[final review](evidence/20260921-budget-final-review.json),
[deployed output](evidence/20260921-budget-deployed-budget.json).

Cumulative API ledger: six attempts, 3500 confirmed input tokens. The API figures
exclude Codex effort and local power/hardware costs. Most cycle wall time was
operator integration/accounting, not generation. Next work should apply these
findings to actual hardware-capacity/execution-route artifacts using existing
knowledge/operator tooling, not keep adding status/billing helpers as substitutes
for the larger goal. Respect the unresolved gateway-restart restriction.

## Cycle 1: truthful scheduler status

Useful artifact: `fleet-scheduler status` on FX99 now distinguishes actual
daemon liveness from cached scheduling/reviewer observations. `--json` retains
its existing valid JSON output. Cached ready does not imply a running process;
the output explicitly disclaims live hardware-residency evidence.

Budget: started about 07:10 UTC; latest dispatch 07:15; no generation after
07:29; delivery/state accounting by 07:35. The useful worker candidate was
observed at 07:16:36. The deployed, independently repaired command was verified
at 07:22:42, including a real stopped-daemon check at 07:25:45. No new test files.

Parent receipt: `br-20260921-071234-8b607786`.
Task: `jev-6b6659cbbf258638035da94e`.
Worker receipt: `br-20260921-071259-279d5a84`.
Plan: `hearth-hermes-br-20260921-071259-279d5a84-466437e6`.
Candidate: `afa3331fccb0a9a26e1e2a461c2244c1780eb43f`, unpromoted worker branch.

| Stage | Actual observation | Interpretation |
| --- | --- | --- |
| JEV | 0.373 s; 914 input tokens; fit 2.95, confidence .95, ambiguity .17 | Gates unchanged; one task admitted at 07:12:59 |
| OMEN builder | qwen3-30b-a3b, native 16k, one worker; 177 s, five steps; candidate committed | Completed a real artifact, but not an acceptable patch |
| MechNet assay | B/70; 162 existing tests passed, candidate import failed | Existing broad tests did not cover the requested behavior; not acceptance evidence |
| First Hermes review | 41.919 s, PASS | False PASS; confused baseline with candidate and missed blocking faults |
| Independent inspection/execution | Missing `connect_call`/`lap`; injected build-label suffix accepted; NaN age accepted | Candidate rejected; bool-count rejection did work |
| Evidence-first Hermes review | 32.984 s, NEEDS_WORK | Correctly described all three supplied failures and attributed execution to Codex |
| Integration | Codex repaired/reused the status layout while preserving both original scheduler functions | Real FX99 command passed live/stopped, JSON and malformed-value checks |

The automatic JEV → MechNet → Hermes → release lifecycle completed without
identity repair, receipt reconciliation, or delivery recovery. The reviewer
model was actually released: helper owner null, model absent, NVIDIA memory
88 MiB / 15 MiB. This proves that lifecycle on this one task, **not** trustworthy
automatic code acceptance. The original candidate and false PASS remain intact.

The evidence-first follow-up was an **operator-driven review**, not a second
JEV dispatch or an automatic retry. It used the same candidate/model, a fresh
Hermes session, candidate-only context plus executed findings, and the existing
owned start/stop helper. No KV reuse. Its success is one assisted-review sample;
do not claim independent discovery or a statistically established improvement.

## Evidence and attribution

- [JEV decision](evidence/20260921-status-decision.json).
- [Unedited worker candidate](evidence/20260921-status-candidate.json).
- [Unedited initial review](evidence/20260921-status-review.json).
- [Independent failure evidence](evidence/20260921-status-execution-evidence.json).
- [Unedited evidence-first review](evidence/20260921-status-evidence-first-review.json).
- [Deployed command outputs](evidence/20260921-status-integrated-live-status.json).

Codex fixed daemon-state classification, exact label matching, finite timestamps,
malformed-value handling, and JSON read errors, and kept `connect_call`/`lap`
unchanged. AST comparison against the pre-cycle source proved those two
functions unchanged; the restarted real scheduler also ran its idle cycle.
The worker candidate is **not** the deployed file. Do not attribute Codex's
corrections or the supplied execution evidence to OMEN or Hermes.

This cycle produced one usable deployed status improvement with operator
correction, **zero accepted unassisted worker patches**, one false-PASS review,
and one correct evidence-assisted review. Report those outcomes alongside
latency instead of treating files written or test totals as useful throughput.

## Current live state, 07:29 UTC

- FX99 CPU scheduler active, observed systemd MainPID `1514424`; no HOLD,
  no active build, no pending automatic review, no admissible ready task.
  These are timestamped observations, not durable PID identities.
- Corrected `fleet/jev/cli.py` is deployed on FX99. Previous file retained as
  `.jev-status-cli-20260921.backup`. It was restarted after the live stopped-state
  check and the new code completed an idle scheduling cycle.
- AM4 deliberately remains **unloaded between reviews** for the ongoing
  overnight work, rather than immediately restoring the resident Dense baseline.
  The exact private baseline remains available through `restore-baseline`.
- OMEN resident worker configuration unchanged; no model or KV/topology changes.
- Pilot conductor policy remains installed for subsequent approved jobs;
  baseline backups/manifest are retained. Production 8710/8712 are untouched.
- Pilot gateway/tunnel remain the instances started 07:12:36 UTC: gateway parent
  36036, listener child 39548, tunnel 39600 (verify identities before any action).

The tool policy **rejected the attempted pilot-gateway restart before execution**.
No process was stopped by that rejected command; do not retry it through another
shell or encoding. CPU scheduler restoration was a separate allowed action.
Consequently the new `review_requires_execution_evidence` gate is **saved in
source but not loaded in the current gateway process**. Do not rely on it live.
The earlier optional evidence-attachment implementation is loaded, but the first
automatic review started before independent evidence existed; its packet did not
contain those results. This timing is why a required pre-review gate was added.

## Cost

No additional JEV call for the evidence-first review. New confirmed API estimate:
USD **0.000038388**. Cumulative ledger: five attempts, 2583 confirmed input tokens,
USD **0.005613510** booked. This consists of USD **0.000108486** confirmed estimates
and USD **0.005505024** uncertain reservations from the earlier failed requests.
Actual failed-request billing remains unknown. The existing USD 0.10 ceiling
and 100-attempt cap remain unchanged. Local-model wall time is not metered API
spend; power/hardware costs were not measured.

## Next experiments that would produce useful work

1. Shape the next small implementation as a short helper or targeted patch,
   not a complete rewrite containing large unchanged scheduler functions. A
   useful next task is a bounded cost-accounting view separating confirmed spend
   from uncertain reservations, using the actual existing ledger contract.
2. Require actual execution evidence before the next review. The source now
   supports `review_requires_execution_evidence: true`; its live activation is
   pending the policy-restricted gateway restart. Until that is legitimately
   resolved, use an explicitly operator-driven evidence-first review and do not
   claim the gate is active. Do not repeat the completed status task as a smoke.
3. Preserve first-pass acceptance, false positives, correction work, first-artifact
   latency, and GPU release observations. JEV task/profile confidence is not a
   patch-correctness probability. These failure outcomes should inform future
   task shaping; current queue counts alone do not encode code quality.

Do not expand into new model downloads, a broad benchmark matrix, or the
three-route optimizer before another useful artifact. DeepAgents, multi-profile
placement, and saved-KV waking remain unqualified. Existing shared-main code and
the worker branch were not automatically promoted or pushed by this cycle.
