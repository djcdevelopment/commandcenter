# Session retro — 2026-08-29

**A day that produced four real findings and three of my own retractions, and ended by
discovering that the mechanism behind most of the confusion was already written in our own
cards.**

## What this session was

A review that became a campaign that became a hardware investigation. It started as a critique
of a routing-harvest cheatsheet, grew into designing the Factory Frontier layer (FF1–FF10) whose
unit is *completed R&D work per occupied machine-hour*, and then spent most of its hours running
laps against live hardware — during which production was found to be quietly serving at half its
hardware and, later, at a quarter of its rate.

It was a **build-and-measure** session with an unusually high correction rate. Three of my own
published findings were retracted within the same day, each by evidence I went looking for.

## What shipped

| Commit | What |
|---|---|
| `533359c` | Routing-harvest cheatsheet v2 — topology-planner framing, null-degradation contract, frozen promotion gate |
| `2db086d` | FF campaign cards FF1–FF10 + campaign-layer table on the board |
| `a13d0a7` | FF 5b — the `-ub 2048` "win" was a single-card artifact; recommendation retracted |
| `ea5e802` | FF 6c — crossover surface; ub1024 optimum; FF9 descheduled on a routing-entropy bound |
| `8933e82` | **FF Phase 0 — production was running on ONE B70; visibility filter removed** |
| `f4a480f` | FF T1b/T2 — four-venue lap; GPU residency costs host commit 1:1 |
| `d4a8ed3` | RETRACT `-ub 1024` |
| `789af75` | Sustained-rate decay found *(itself later retracted)* |
| `280e85d` | **ROOT CAUSE — co-residency poisoning; supersedes the decay finding** |

New durable artifacts: `campaign/ff-probes/ff_census.py` (pre-run device/consumer census),
`campaign/ff-probes/ff_ratecheck.py` + `rate-baselines.json` (known-good rate assertion),
`docs/FACTORY-FRONTIER-CARDS.md` (the campaign), and a 152-row receipts ledger at
`E:\work\battlemage\ff-probes\ff-receipts.jsonl`.

## The team retro — our collaboration across the seats

### Architect

The strongest design call was reframing the routing harvest around a boundary rather than a
feature list: *commodity routers answer where a request executes; we answer what topology the box
runs next epoch*. That survived contact and became the spine of the FF cards. The second-best was
insisting FF not re-run a throughput sweep — `bench-row.v1` already normalized the configuration
axes, so the campaign needed a numerator, not another grid. That saved days.

What I'd decide differently: I designed FF1 (the work harness) as the campaign gate and then never
built it, running tier probes on throughput proxies for the whole session. That was the *agreed*
sequencing, but it meant every lap produced numbers whose units the campaign explicitly says are
not the ones that matter. The gate exists to stop exactly that.

### Implementer

The build itself was small — two probe tools and a lot of measurement scripting — and the tools
are the session's most reusable output. Both encode a specific failure they were built to prevent,
which is the right shape for a lab instrument.

My defect list is long and mostly one class: **acting on evidence whose reach I had already
described in writing.** I passed `-ts 1,1` to llama-bench (which parses it as two single-card
runs), mislabeling an entire lap as dual-split. I promoted `-ub 1024` to production after writing
*"the one remaining gap is `-np 2`, still untested because llama-bench has no `-np`"* — then
shipped on the prefill gain anyway. I generalized a KV measurement from one model to another one
message after flagging that they might differ, putting a card at 92.7% of capacity. I set a rate
baseline from data with a **74.87% repeat spread**. And I gated readiness on `/health` after
having written *"port-open ≠ model-ready"* into the tool's own docstring.

What went well: every one of those was caught by me, in-session, and retracted in the repo with
the reasoning recorded. The ledger is append-only throughout; nothing was rewritten to look better.

### Reviewer / QA

The best review came from instruments, not from me. **b70tools argued with itself** — its
`impossible_heap_budget` disagreement report (confidence `Disagreed`) stopped me twice from
concluding on conflated LOCAL/NON_LOCAL evidence, and its README pre-empted three caveats I had
written worse. Derek pointing me at it mid-session was the highest-leverage correction of the day:
I was hand-rolling perf counters to answer a question his tool was built to answer.

What slipped: I published a "sustained-rate decay that recovers after idle" finding assembled from
**scattered log lines across a session with restarts and two configs interleaved** — a
pattern-match, not a measurement. It took a controlled reproduction (40 back-to-back requests on a
fresh server, flat at 102–107) to falsify my own headline. I should have run that before
committing, not after.

Test posture is the honest gap: this campaign has no regression check. `ff_ratecheck.py` is the
first, and it is still incomplete (it measures a burst, and its baseline is cleared).

### Operator / SRE

Production was touched repeatedly and ended healthy at **104.86 tok/s**, dual-split, door-proofed.
But it spent much of the day degraded, and I caused some of that: a ~3 minute outage from looping
`ArcServeRestart` three times (the task's restart policy gave up; recovery was `ArcServeBoot`
directly), and hours of co-resident benchmarking that poisoned the incumbent without my noticing.

The infrastructure findings are the durable operator value: the **visibility filter had been
costing a whole card**; `lanes.json` LUIDs are stale so the render-lane spill guard has been
**inert since the 08-28 reboot**; and llama-server's per-process CPU counters read 0 under S4U, so
process-level attribution silently fails.

Derek ran the elevated stop when I hit the S4U permission wall — the one thing I could not do —
and called the session's end.

### Product / planning

Scope grew a lot, and mostly correctly: each expansion was Derek redirecting toward the question
that actually mattered (the four-venue lab shape, the NPU opportunity-cost reframe, the
prefill-elsewhere idea). Pacing was his throughout; I'd have kept optimizing ubatch.

What to change: I let a **retracted** finding get committed twice before the real mechanism
surfaced. Two of the nine commits are corrections of the two before them. That churn is visible in
the history forever, and while append-only honesty is right, some of it was avoidable by measuring
before publishing.

## Two seats, two views

### From Claude's seat

The pattern in my own errors is sharper than any single one: **I repeatedly wrote down the exact
limitation that would later bite me, and then acted as if I hadn't.** The `-np` gap, the
port-open-≠-ready rule, the "seat 2 may differ" caveat, the poisoned-load floor quoted in my own
cards that morning. Naming a risk gave me the *feeling* of having managed it. The fix isn't more
caution in prose — it's making the named risk a gate that blocks the action, which is what
`ff_ratecheck`'s spread guard now does mechanically.

I also over-reached on inference from sparse data twice (the ubatch causal claim, the decay curve),
both times assembling a story from log lines rather than running a controlled test that would take
five minutes. Where I under-reached: I never built FF1, so nothing today was measured in the units
the campaign is about.

What I'd want to know next time: whether the poisoning has a threshold (does *any* co-resident
Vulkan work poison, or only sustained work?), because that determines whether the whole
co-residency research programme is measurable at all without a restart between every cell.

### From Derek's seat *(my reconstruction — correct me)*

He'd see a productive day where his instincts kept paying: he built b70tools years-deep into
exactly this frustration, and it caught what my ad-hoc counters couldn't. His "DHCP-style
reshuffle" framing was the correct diagnosis where mine was a weaker session-context guess. He'd
be unbothered by the retractions — he said as much — because he's tuned these cards long enough to
know Windows produces exactly this class of silent misbehaviour, and because the retractions are
the product: *"this is why people hate Windows… but that's why we're here doing the engineering,
for the common AI dev that no business is incentivized to help."*

He'd want the mission framing kept visible: four Windows-specific silent-degradation modes found
today are precisely what would cost an unaided developer a weekend and teach them the wrong
lesson. And he'd want less thrash — fewer published-then-retracted findings, more measure-then-
publish.

## Last time's lessons — follow-through

| Lesson | Status | Note |
|---|---|---|
| L-2026-08-28-1 — mid-build creative direction is a design change | acted-on | Applied to the cheatsheet v2 rewrite: treated the topology reframe as a redesign, not a patch |
| L-2026-08-28-2 — probe the commodity before building the bespoke | acted-on | FF cards' §0.2 "already answered, do not re-run" table; FF9 descheduled on an analytical bound before any window |
| L-2026-08-28-3 — a signal matching a known failure still gets its own diagnosis | **dropped (violated)** | I matched "slow decode" to a decay narrative without diagnosing it; the real cause was poisoning |
| L-2026-08-28-4 — stamp the pool-config hash on every dispatch | pending | Still registered; T3's decision-record work is where it lands |
| L-2026-08-28-5 — mine probes from the artifact, never author from memory | acted-on | FF1's slice design is a *replay of a probe whose true verdict we hold*, for exactly this reason |
| L-2026-08-28-6 — a thinking model's token floor is part of harness design | pending | Not exercised this session |
| L-2026-08-28-7 — a concurrent session writing shared memory is a QA channel | acted-on | Derek's mid-session corrections (b70tools, DHCP framing) played the same role |
| L-2026-08-28-8 — the page cache has no universal sign | acted-on | Reinforced: mmap kept 87 GiB file-backed at +2.2 GB commit while `-dio` commits ~1:1 |
| L-2026-08-28-9 — state files without identity need identity in their names | acted-on (widened) | Flash slot-save confirmed no model identity; now also a *cross-venue* hazard |
| L-2026-08-28-10 — folklore that survives a fact-check is stronger than fiction | pending | No publishing this session |

**One lesson was actively violated** (L-…-3), and it is the same failure that produced the decay
retraction. That escalates.

## Lessons learned

1. **L-2026-08-29-1 — Co-resident GPU work persistently degrades the incumbent until restart.**
   105 → 28.39 tok/s *with the co-tenant already stopped*; restart restored 104.86. Restart the
   incumbent after any co-resident experiment, before measuring it. → **ADR** + memory.
2. **L-2026-08-29-2 — We rediscovered our own finding.** OMEN-LIMIT F3's *"0.08× permanent
   poisoned-load floor; co-tenant eviction −5×"* was quoted in the FF cards that morning and not
   applied for the rest of the day. Prior art in your own document is only useful if the workflow
   forces you to read it at decision time. → **ADR** (same one) + practice.
3. **L-2026-08-29-3 — A named risk that does not block the action is not managed.** I wrote the
   `-np` gap, the port-open rule, and the seat-2 caveat, then acted against all three. Turn the
   named risk into a mechanical gate. → practice (embodied in `ff_ratecheck`'s spread guard).
4. **L-2026-08-29-4 — Index-based device selection is unsafe on this box, and no flag fixes it.**
   Vulkan enumeration reshuffles between runs; `-dev`/`--device` is positional too. The fix was
   *removing* the filter so selection happens by device type. → **ADR** + memory.
5. **L-2026-08-29-5 — A flag validated only where the harness can reach is not validated.**
   `llama-bench` has no `-np`; a batching flag that benched clean regressed the server. → practice
   + upstream issue.
6. **L-2026-08-29-6 — GPU residency costs host commit ~1:1; mmap host residency is nearly free.**
   The commit ceiling couples GPU and host capacity, so "how many projects fit" is a commit
   question. → memory + doc.
7. **L-2026-08-29-7 — Never derive KV from metadata; take the server's `memory_breakdown`.**
   Rates span ~10× across models and the GQA formula is 4× wrong on one of them. → doc.
8. **L-2026-08-29-8 — Correct-but-degraded is this lab's characteristic failure.** Four instances
   in one day — one card, ~10 GB spill, poisoned rate, and the inert spill guard — every one
   serving correct output with all health checks green. Liveness checks cannot see it; only a
   known-good *rate* assertion can. → **ADR** + the `ff_ratecheck` tool.
9. **L-2026-08-29-9 — Assemble findings from controlled runs, not from log archaeology.** The
   decay curve came from scattered lines across a poisoned session; five minutes of controlled
   reproduction falsified it. → practice.

## Provenance

Git range: `4f64385..280e85d` (9 commits; `2fb2aa5` and `4f64385` are the concurrent agent's NPU
history landing mid-session). Offload: one batched `local_generate` (omen-arc, qwen3-30b-a3b,
1,038 tokens out) for timeline / role-reads / lessons — **edit_verdict: hallucinated**. It swapped
agency in four places (credited me with Derek's elevated command, session call, lap-config choice,
and the KV-prefill idea; credited Derek with finding the one-card misconfiguration). Skeleton
reused, facts rewritten frontier-side — the same failure mode as the 2026-08-28 draft.
`--fleet`: not requested; none pending from last retro. Derek's-seat section is my reconstruction,
marked as such. Working tree carries unrelated concurrent-agent changes, deliberately not committed
here.

## Offload scorecard (S6)

`knowledge/offload.json` (watermark 2026-08-29T12:50:28Z): **offload_ratio 1.0**, 1,250 lifetime
calls, 3,049,158 tokens in / 387,439 out. Per class — **sunk** 643 calls (382,455 in / 160,659
out), **trial** 596 calls (2,666,667 in / 226,778 out). Est. saved **$14.96** against
**$6.27** real spend on trial credits.
