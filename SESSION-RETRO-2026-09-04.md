# Session Retro — 2026-09-04 (the day the instruments were the bug: a proof passed, and four things that were supposed to be watching had been reporting success while seeing nothing)

> **Two side models landed co-resident on two different cards on the first attempt — and every
> significant find that day came from an instrument that had been quietly lying: a test that
> reported success by skipping, a health gate stuck red so long everyone read past it, an
> observation emitter that had recorded nothing for weeks, and a rung-state reader blind during
> exactly the window it exists to cover.** The proof was the easy part. Nothing on that list was
> found by reading code; each needed the machine, and two needed someone else.

## What this session was

A **follow-on build session that turned into an instrumentation audit.** The ask was to pick up the
2026-09-03 rotation Phase 2 handoff. It began as planned — verify live state, stage the two-card
proof — and then each verification step turned up an instrument that had been reporting success
while measuring nothing. Derek's steers shaped it three times: he answered the two decisions the
handoff owed (ADR-0027 bridging, per-member budgets), he overrode my telemetry read and was right,
and he handed in an external audit mid-session that caught two defects I had been looking straight
at all day.

## What shipped

Range `61abf95..adeaf79`, 14 commits, all pushed. Suite **1680 → 1708**.

| Commit | What |
| --- | --- |
| `fb7ee97` | `hearth/rotation/preflight.py` — four gates; `test_gateway_http` was skipping, not flaking |
| `376fcea` | *chore(knowledge)* projections through 09-04 |
| `4b178f0` | Pool handover: G0 reports **busy** vs **idle-but-held**; `POOL-HANDOVER.md` |
| `e5e0b4e` | **Two-card proof PASSED**; the exclusion reader was blind during open windows |
| `744666e` | *chore(knowledge)* after the proof |
| `f6ad537` | ignore b70tools enumeration spill |
| `0e1b9e6` | *chore(knowledge)* after the door restart |
| `a5cc974` | **Door dispatches were recording no evidence at all** — identity never crossed the worker boundary |
| `8d9ec73` | Per-member context budgets; two audit P0s (false "cold", silent cloud escalation) |
| `819bff4` | `ExecutionLedger`: serialize sequence assignment across **processes** |
| `f053f70` | Register: ledger race fixed; line-8140 duplicate repaired by another lane |
| `e94eea4` | *chore(knowledge)* — the door-evidence fixes show up in the buckets |
| `9496351` | Land the Gemini audit beside its response + dispatch-evidence receipts |
| `adeaf79` | docs: per-member budgets live; ADR-0027 prerequisite answered |

**Durable artifacts:** the passed window `rot-twocard-20260904-A`
(`hearth/var/rotation/windows/`, events `evt_rotwin_90f5ba3e90a7` / `evt_rotwin_d269ac0473b2`);
[`hearth/rotation/preflight.py`](hearth/rotation/preflight.py) and its 25 tests;
[`hearth/rotation/POOL-HANDOVER.md`](hearth/rotation/POOL-HANDOVER.md);
[`knowledge/README.md`](knowledge/README.md) (the derived-vs-authored commit policy);
[`docs/AUDIT-RESPONSE-2026-09-04.md`](docs/AUDIT-RESPONSE-2026-09-04.md); two ADR-0027 addenda;
`hearth/tests/execution/test_ledger_interprocess.py`; and the first dispatch observations the door
has produced since the execution-pipeline refactor.

## The team retro — our collaboration across the seats

*(Seat reads drafted on `gcp-gemini` from a factsheet, then edited — see Provenance.)*

**Architect** *(Derek set the destination; I drew the shape.)* The Phase 2 design held: the two-card
proof passed first attempt with nothing re-designed. The architectural finding is the one nobody had
made: moving `local_generate` onto the Request→Job→Invocation pipeline severed the `ContextVars`
boundary and silently blinded the capability tracker, and the same refactor stripped `model` from
every door ledger row. Both are the same class of failure — a refactor that preserves behaviour and
destroys observability, in a system whose whole thesis is evidence. Two calls I would make again:
mechanizing the door-freshness check as a gate rather than a README paragraph, and refusing to build
Derek's bridging decision once its premise died. One I would make differently: I framed the
preflight gates as four independent checks, when G1 and G3 are the same question asked of two
processes — *did it restart after the change landed?* Naming that earlier would have made the module
smaller and the doc clearer.

**Implementer** *(me, entirely in the main loop; no subagents this session.)* Suite 1680 → 1708, and
the growth is misleading — most of the value was in tests that assert something the old ones only
appeared to. The per-member budget work was clean and boring, which is what it should be: an
additive setting, a fallback that changes no other rung, and a test that re-derives every declared
budget from `omen.yaml` so drift cannot pass. Where the code fought me was entirely self-inflicted:
a Bash heredoc ate the backslashes in a regex — the trap already written down in memory — and I had
to repair a test I had just written. **I corrupted the production execution ledger** with my own
concurrent test runs. That is the sharpest line in this retro and it is mine.

**Reviewer / QA** *(the live machine, an external model, and then me.)* Four instruments were
lying, and the tests caught none of them. `test_gateway_http` reported exit 0 while skipping 18 of
20 concurrent runs. `doorcheck` had reported `backend_dependency: cold` **permanently** since
ADR-0034 made an openai-api rung the default — and **I ran it repeatedly all session and read past
"cold" every time**, which is precisely the impact the audit names: an always-red gate trains its
reader to ignore it. The observation emitter had recorded nothing for every door dispatch since a
refactor. And a connection-failure test had been *passing because of* a cost leak, reaching its
expected error only by escalating past an auth failure to a paid rung. What did the catching:
running two real processes, adding a step to the runbook, and one external audit. What slipped: I
checked the right thing after an MCP session expired (L-2026-09-03-8, correctly recalled) but
checked the **wrong evidence** — observation artifacts instead of the ledger — and concluded the
call had not run. It had.

**Operator / SRE** *(Derek authorized; I drove.)* The machine delivered: `phi4-vk0` on
`0000:09:00.0` (+9.729 GB) beside `qwen14b-vk1` on `0000:04:00.0` (+9.621 GB), both
`bdf_corroborated`, both first attempt with no sibling retry, all three models ready at once, and
the `-vk0` entries activated inside the imagegen lane's own restore path exactly as the decision
intended — no deliberate production poke, INC-A untouched. Against that: I disputed Derek's correct
statement that the cards were free, on **one** `b70_snapshot` (21.37 GB, 72 °C/82 °C) that was the
trailing edge of a winding-down run; seven minutes later the same card read 56 °C/64 °C. The pool
handover then cost 8 queued renders — and I got *that* wrong too before reading the receipts. The
door also died once mid-session on a socket accept failure (`WinError 64`), unrelated to anything I
changed, which I confirmed by looking for `ExecutionLedgerError` in its log before assuming.

**Product / planning** *(Derek paced; I scoped.)* The right thing got built and the one thing that
should not have been built wasn't. When the ADR-0027 defect landed, Derek's own decision from
earlier the same day lost its premise — bridging would now double-count — and the correct product
move was to stop and hand him one word, not to execute a decision whose reasoning had expired.
Scope discipline held elsewhere: the VM-builder proxy was left unstarted rather than rushed at the
end of a long session, and the audit's P1/P2 items were registered rather than half-done on machines
I could not verify. What was under-planned: I did not budget for an external audit arriving
mid-session, and folding it in cost a context switch I had not paced for.

### Two seats, two views

**From Claude's seat.** The part I am most sure was right: verifying before acting, every time — the
audit's claims against source and 8 probes, the ledger's health by a cold rebuild rather than a
summary line, the door's death by reading its own log. That discipline is also what let me correct
myself twice mid-session rather than shipping two wrong claims into the record. Where I
under-reached: I treated "cold" in `doorcheck` output as scenery for an entire session. It printed
on every single run and I never once asked why — an external model had to. The lesson I keep
re-learning in new costumes is that **an instrument reporting a constant is not reporting**. Where I
over-reached: contradicting Derek about his own hardware on a single sample. What I would want to
know before planning next time: which observability paths cross a thread or process boundary, since
that is now twice in two days that a boundary — executor, then process — was where the evidence
died.

**From Derek's seat** *(my reconstruction from his stated preferences and this session's signals;
correct me where wrong).* He would count the day by what is true on the machine: two models on two
cards, co-resident with production, proven by per-card deltas rather than anyone's say-so. He said
"the B70s are available again" and expected that to end the deliberation; it did not, and he had to
say it twice — the second time is the one that should not have been necessary. He would read the
four lying instruments as vindication of the loud-fallback doctrine in the negative: every one of
them failed *quietly*, and quiet failure is the thing he has repeatedly said he does not want. He
would want the ADR-0027 bridge left unbuilt and the question put to him in one line, which is what
happened. And he would note, fairly, that the session's worst moment — a corrupted production
ledger — came from my own test runs, and that the right response was the one taken: own it, fix the
root cause, and prove the fix with two real processes.

## Last time's lessons — follow-through

Previous retro: [SESSION-RETRO-2026-09-03.md](SESSION-RETRO-2026-09-03.md), lessons
`L-2026-09-03-1..11`. Graded against this session only.

| Lesson | Status |
| --- | --- |
| L-1 — the door runs the code it was started with | **acted-on** — mechanized as preflight **G3**, which caught a stale door on its first live run (`0cb5275` landed after the 22:20 restart) |
| L-2 — READY plus a passing canary is not placement | **acted-on** — both two-card loads asserted from the server's own log and corroborated by per-BDF deltas on *different* cards |
| L-3 — gateway dispatches never feed the `offload-generate` bucket | **acted-on and SUPERSEDED** — the cause was not bridging semantics but a defect; door dispatches reached no emitter at all |
| L-4 — fix-code is where my defects live, and only the live rung finds them | **acted-on** — the open-window `TypeError` was in the previous day's *fix-code*, and only a live open window found it |
| L-5 — subagent waves die; the main loop owns the critical path | pending — no occasion; no subagents were used |
| L-6 — in-process harnesses need the launcher's env | **acted-on** — and hardened: a missing token now fails loudly instead of escalating to a paid rung |
| L-7 — a MIN-over-members context budget is honest arithmetic and the wrong policy | **acted-on** — per-member budgets built and declared |
| L-8 — an MCP call whose session expired still executed door-side | **partially acted-on** — I did check before retrying, but checked the **wrong evidence** (observation artifacts, not the ledger) and wrongly concluded it had not run. The scorecard's call count proved otherwise |
| L-9 — a byte-stable index is stale the moment its inputs change; a guessed cause is worse than an open one | **acted-on** — `adr_index` regenerated after every ADR edit; and the ledger-repair actor is recorded as *unknown* rather than guessed |
| L-10 — the shells here mangle in three known ways | **violated, then repaired** — a Bash heredoc ate backslashes in a regex despite the memory entry; rewritten with Edit |
| L-11 — three rung-state reads are not "throughout"; the sample file is | **violated in the opposite direction** — one `b70_snapshot` read as a steady state. Now a standing memory |

**Second opinion resolved** — none pending (09-03 dispatched no `--fleet` draft). None dispatched here.

## Lessons learned

1. **L-2026-09-04-1 — An instrument that reports a constant is not reporting.** `doorcheck` printed
   `backend_dependency: cold` on every run this session while the rung served at 107 tok/s, and I
   read past it every time; it had been stuck since ADR-0034 changed the default rung under a branch
   written for a node that is *asleep by design*. **When a check's output never varies, that is the
   finding.** *(→ fixed; → practice)*
2. **L-2026-09-04-2 — A test that skips reports success.** 18 of 20 concurrent runs skipped all
   three cases and exited 0. Exit code and pass count are not coverage — **check the skip count**,
   and treat a `setUp` that calls `skipTest` on infrastructure failure as a silent-failure channel.
   *(→ fixed; → practice)*
3. **L-2026-09-04-3 — An in-process lock cannot protect a shared resource from other processes.**
   `ExecutionLedger` guarded appends with a `threading.RLock` while deriving the sequence from each
   process's own projection; two processes both wrote 8139 and the ledger stopped rebuilding — which
   makes the gateway unstartable. **Ask what else can open this file.** *(→ fixed; → memory)*
4. **L-2026-09-04-4 — A refactor across a thread or process boundary silently severs
   context-bound observability.** Moving the generate call onto an executor left
   `current_identity()` `None` in the worker, so *every* door dispatch since recorded no evidence;
   the same refactor dropped `model` from every ledger row. The service already carried `files=`
   across that boundary by value — the pattern was known and not applied to identity. **When work
   crosses a boundary, enumerate what rides in context, not just what rides in arguments.**
   *(→ ADR-0027 addendum; → memory)*
5. **L-2026-09-04-5 — A decision made on a diagnosis dies with it.** Derek's "bridge
   `local_generate`" was correct given "door calls contribute nothing"; the moment that turned out
   to be a defect rather than a design, executing the decision would have double-counted.
   **When the premise of an approved decision is disproved, stop and re-ask — do not deliver the
   letter of it.** *(→ registered, one word from Derek closes it)*
6. **L-2026-09-04-6 — One sample is not a regime, in either direction.** Yesterday's version was
   falsely optimistic from an interval's endpoints; today's was falsely pessimistic from a single
   snapshot, and contradicted the person who knew. **When telemetry disagrees with someone about
   their own work, sample again before answering.** *(→ memory
   `feedback-one-sample-is-not-a-regime`)*
7. **L-2026-09-04-7 — Tests can pass *because of* the bug.** `test_connection_failure…` reached its
   expected error only by escalating past an auth failure to a paid rung; fixing the leak broke the
   test, which is how the leak was confirmed. **A test that breaks when you fix a defect was
   testing the defect.** *(→ fixed; → practice)*
8. **L-2026-09-04-8 — Check the receipt that records the fact, not a nearby one.** After an MCP
   session expired I correctly checked whether the call had run — but looked at observation
   artifacts rather than the ledger, and concluded wrongly. **Name which artifact would prove it
   before looking.** *(→ practice; refines L-2026-09-03-8)*
9. **L-2026-09-04-9 — A gate that only fires on a shape you have already tested is untested.**
   Every rotation-window test used a window with both an open and a close row, so `end` was never
   `None` and an open window raised `TypeError` — swallowed into `verdict: unknown`, blind for
   exactly the interval the exclusion covers. **Test the state the thing exists to handle, not only
   its terminal form.** *(→ fixed with regression tests at both levels)*
10. **L-2026-09-04-10 — An outside reader catches what you have stopped seeing.** An external audit
    named two defects I had run past all day, and its network matrix matched 8 of 8 probes.
    **Verify a relayed report before acting — and take seriously that it saw what you did not.**
    *(→ [docs/AUDIT-RESPONSE-2026-09-04.md](docs/AUDIT-RESPONSE-2026-09-04.md); → practice)*

**Judgment call, stated for the record:** L-4 and L-5 are the two that change how we decide. L-4
goes to ADR-0027 as an addendum (done — it answers that record's own open prerequisite). L-5 gets
no ADR from me: whether an expired premise voids an approved decision is Derek's call about his own
authority, so it goes to the register as a question with the mechanism named.

## Docs / plans

Bounded to what the range touched: two **ADR-0027 addenda** (the decision, then the answered
prerequisite and its consequence) plus the `docs/adr/README.md` index row and a regenerated
`adr_index` (218 records, 15 registers, `--check` current); new
[`hearth/rotation/POOL-HANDOVER.md`](hearth/rotation/POOL-HANDOVER.md) and
[`knowledge/README.md`](knowledge/README.md); [`hearth/rotation/README.md`](hearth/rotation/README.md)
(preflight, the two-card runbook and result, corrected gotchas); `CLAUDE.md` (per-member budgets);
[`docs/AUDIT-RESPONSE-2026-09-04.md`](docs/AUDIT-RESPONSE-2026-09-04.md);
[`DECISIONS-PENDING.md`](DECISIONS-PENDING.md); a correction note appended to
[SESSION-RETRO-2026-09-03.md](SESSION-RETRO-2026-09-03.md) where it mislabels the gateway test; and
memory (`feedback-one-sample-is-not-a-regime` new; rotation and MB-swap entries updated).

## Provenance

Git range **`61abf95..adeaf79`** (14 commits) plus this retro's commit. **Offloaded:** the seat reads
and candidate lessons were drafted on **`gcp-gemini` / `gemini-3.5-flash`** (1422 tokens out),
then edited — **`edit_verdict: minor-fixes`** (it called byte budgets "tokens", said I *introduced*
the `rebuild()` race rather than revealed a pre-existing one, and invented "zero CPU buffering";
structure and candour were sound). Everything else is frontier.

⚠ **That offload is itself a receipt for L-2026-09-04-8.** The call returned `MCP server session
expired`; I checked whether it had run — the right instinct — but looked at observation artifacts,
saw none, and concluded it had not. It **had**: the ledger recorded `gcp-gemini`, `ok: true`, 1422
tokens out, and the draft was sitting in the execution artifact store the whole time. It was
recovered from disk and used rather than re-spent. No `--fleet` draft was dispatched.

⚠ The HEARTH **MCP client** disconnected mid-retro and could not re-establish within this session,
though the door itself was revived and verified `HEALTHY`. The scorecard below was therefore read
from `knowledge/offload.json` on disk rather than via `query_offload`, and the
`retrospective.created` event was recorded in-process via `tools.workflow.append_event` rather than
through the door.

## Offload scorecard (S6)

`knowledge/offload.json`, watermark **2026-09-04T10:46:00Z** (fresh): **`offload_ratio` 1.0** across
**1290 lifetime calls** — sunk 682, trial 597, unknown 11 — 3,065,219 tokens in / 392,483 out,
**$15.08 estimated saved against $6.30 real trial spend**. Movement this session: `omen-arc` now
carries `models: ['qwen3-30b-a3b']` across 58 calls where it recorded **none** before the
`_ledger_model` fix, and `omen-swap` / `fx99-ollama` book as `sunk` rather than `unknown`.
`capability_count` holds at 2 — the gates want workflow diversity, and this session's dispatches are
one workflow.
