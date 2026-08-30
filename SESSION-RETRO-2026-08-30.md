# Session retro — 2026-08-30

**The through-line:** the campaign **stopped correcting numbers and started correcting the rules
that produce them** — and then the rules caught their own author within a day.

## What this session was

A recovery-and-repair session that turned into a methodology session. It opened as the ADR-0041/0042
follow-up (re-measure five suspect claims) and closed with an experimental constitution, a claim
register, two shipped production changes, and one deliberately unbuilt instrument.

Sixteen commits, `280e85d..4688781`. Two of them (`a47abae`, `e1c2f89`) are the concurrent agent's;
the rest are this session's.

## What shipped

| Commit | What |
|---|---|
| `e08c8b5` | **W-A solo controls** — the venue matrix re-measured with no incumbent. The "121.6 dual-split" headline exposed as a single-card llama-bench run |
| `26829d0` | `restart-arc.cmd` waits for real teardown instead of a fixed 3 s — it had been racing itself and leaving production down while every task reported success |
| `604f925` | **ADR-0043** — the rung goes cold when idle; it was never co-residency |
| `c0bed08` | **C2 resolved** (door overhead is a flat 175–264 ms) + the **keep-alive shipped to fx99** |
| `f2b4cfc` | **`-ub 1024` promoted** on a valid A/B; the "4× regression" refuted |
| `40df3ce` | **B3** — the topology crossover located, 512→1024 |
| `d37277d` | **B4** — Flash's −42% tax refuted *and reassigned to the incumbent* |
| `faf1a7b` | **B5** — dense-vs-MoE corrected to 4.5–5.0× |
| `d1f0b77` | **Campaign closed** — `docs/CLAIM-REGISTER.md`, R8 as a gate, R9 |
| `960bc7a` | Boundary frozen; **FF1 audited instead of built** |
| `02359f7`…`603cf14` | **ADR-0044** + R10; FF1 blocked on a semantic decision |
| `4688781` | Board brought current |

Outside this repo: `steppeintegrations-site@f798a04` — the public article corrected and extended.
**Committed, not pushed.**

New durable artifacts: `docs/CLAIM-REGISTER.md`, `docs/FF1-DENOMINATOR-AUDIT.md`,
`docs/adr/0043`, `docs/adr/0044`, `fleet/fx99-keepalive/`, `fleet/arcserve/warm-arc.ps1`, six new
probes under `campaign/ff-probes/`, and `C:\work\writing\ARTICLE-AUDIT-consumer-hardware-2026-08-30.md`.

## The team retro

**Architect.** The strongest call was refusing to build FF1 and auditing the denominator instead —
it turned out never to have been recorded at all, so precision work would have been aimed at
nothing. The weakest was letting "idle collapse" become a universal explanation for every low-rate
state; it took a restart-surviving incident to force classification, and ADR-0044 should have been
written the moment a second signature appeared rather than the moment one refused to fit.

**Implementer.** The probes got steadily better because each failure was fed back into the next one:
`Stop-Probe` learned to wait for real exit, the ready-gate learned about stale markers, the ladder
learned to pre-flight its own context arithmetic. The recurring defect was the same shape every
time — **assuming a fixed delay was long enough** — and it appeared in four separate places before
being generalised into R7.

**Reviewer / QA.** The verdict logic needed correcting twice, and both corrections were substantive:
once for conflating *unresolvable* with *unexcluded* (which would have discarded B4's refutation),
once for verdict bands that called −83% "near −42%". Writing the interpretation *before* the run is
what made those catchable at all. What slipped: a claim assertion added without its matching
`data-claim-id` anchor, caught only by the site's e2e suite.

**Operator / SRE.** Production came back healthy from every window, and the stop/restore lever was
exercised roughly a dozen times without an unrecoverable state. Two operational defects were found
and fixed in passing — `restart-arc.cmd`'s teardown race, and a BOM that made the monitor's own log
unparseable. One orphaned background waiter ran for 3h22m before Derek spotted it; its exit
condition became unsatisfiable the moment the run it watched crashed.

**Product / planning.** Derek's sequencing was the load-bearing decision: B3 → B4 → B5, explicitly
so that topology and warmth could not masquerade as an architectural effect. B5 proved that ordering
correct — its clean result depended on B3 and B4 already being settled. The counterfactual is
uncomfortable: run in any other order, the dense-vs-MoE number would have moved for reasons nobody
could have attributed.

## Two seats, two views

**From Claude's seat.** The session's most useful moment was being wrong in public. Diagnosing the
incident I reverted my own recent config change, read 65 → 97, and was one step from reporting that
my promotion had caused a 33% regression — the identical error B1 made, on the same flag, one day
after I wrote the rule against it. I under-reached on classification (letting one explanation absorb
every observation) and over-reached on attribution (treating "the most recent change was mine" as a
diagnosis). What I would want next time: to write the rival hypotheses before the run, not just the
interpretation bands.

**From Derek's seat** *(my reconstruction, to be corrected).* The measurement work was table stakes;
the durable output was the constitution. He pushed consistently toward rules over results — R8 from
guidance to a gate, R9 as its own verdict, R10 as a distinct trap from R1 — and toward *not* acting:
leave the incident alone, block FF1, freeze the boundary. His sharpest correction was that
epoch-scoped is not epoch-homogeneous, which closed a reading I had left open. He also caught the
orphaned waiter I had not noticed.

## Last time's lessons — follow-through

| id | lesson | status |
|---|---|---|
| L-2026-08-29-1 | Co-resident work degrades the incumbent until restart | ⚠ **SUPERSEDED** — ADR-0043: the trigger is *idle*, not co-residency |
| L-2026-08-29-2 | We rediscovered our own finding | **acted-on** — `docs/CLAIM-REGISTER.md` exists to make prior art reachable at decision time |
| L-2026-08-29-3 | A named risk that does not block is not managed | **acted-on** — R8 promoted from guidance to an admissibility gate |
| L-2026-08-29-4 | Index-based device selection is unsafe | **acted-on** — every probe selects by type; `placement.ps1` hardened three times |
| L-2026-08-29-5 | A flag validated only where the harness reaches is not validated | **acted-on** — the `-ub` A/B redone on the live server at `-np 2` |
| L-2026-08-29-6 | GPU residency costs host commit ~1:1 | **acted-on** — re-confirmed by the 84.7 → 54.0 GB drop when production stopped |
| L-2026-08-29-7 | Never derive KV; take `memory_breakdown` | **pending** — not exercised this session |
| L-2026-08-29-8 | Correct-but-degraded is the characteristic failure | **acted-on** — INC-2026-08-30-A is another instance; the deep probe exists because of it |
| L-2026-08-29-9 | Assemble findings from controlled runs, not log archaeology | **acted-on** — every B-series result is interleaved and controlled |

**Second opinion:** none pending. The 2026-08-29 retro recorded no `--fleet` plan_ids outstanding.

## Lessons learned

1. **L-2026-08-30-1 — Noticing a confound is not identifying it.** A withdrawal is not a diagnosis;
   the confound you happened to name can stand in for the one you have not found. → **rule R1**
2. **L-2026-08-30-2 — Comparative arms are interleaved, never sequential.** A-then-B cannot separate
   "B is faster" from "the machine got faster", and this machine changes state on its own. → **R3**
3. **L-2026-08-30-3 — A ratio is inadmissible unless both sides came from the same instrument.**
   One habit distorted three findings, each internally plausible. → **R8, a gate**
4. **L-2026-08-30-4 — Effect resolution and hypothesis exclusion are separate verdicts.** A probe
   that cannot say *"I don't know the value, and I know it isn't that"* is under-reporting. → **R9**
5. **L-2026-08-30-5 — Recovery after intervention is not evidence the intervention caused it.**
   The `change → degradation → revert → recovery` sequence reads as an observation rather than an
   inference, which is exactly what makes it dangerous. → **R10**
6. **L-2026-08-30-6 — Audit the quantity before instrumenting it.** FF1's denominator was never
   recorded at all and its axes have no observable here; precision work would have priced the wrong
   economic quantity. → **doc: `docs/FF1-DENOMINATOR-AUDIT.md`**
7. **L-2026-08-30-7 — A monitor that cannot be falsified is decoration.** The 32-token deep probe
   was added solely so the keep-alive could be checked; it caught the first real incident while the
   1-token pings reported healthy throughout. → **practice**
8. **L-2026-08-30-8 — A wait condition that only matches on success outlives every failure it
   watches.** The orphaned waiter polled for a string a crashed run never printed. → **practice**

## Provenance

Git range `280e85d..4688781` (16 commits, 2 the concurrent agent's). Offload: one
`local_generate` on `omen-arc` (qwen3-30b-a3b, 1,036 tokens out, 20.6 s) for the timeline /
role-reads / lessons skeleton — **edit_verdict: `minor-fixes`**. The agency-swapping that made the
last two drafts `hallucinated` did **not** recur. It did invent two criticisms that are factually
wrong and were cut: that the FF1 block was not communicated as deliberate (it is documented as such,
with reasons), and that the incident was misattributed "due to lack of monitoring" (the monitor
detected it within one interval; the misattribution was a methodology error). Skeleton kept, facts
rewritten frontier-side. Derek's-seat section is my reconstruction. No `--fleet` dispatch.

## Offload scorecard (S6)

`query_offload`, evidence watermark `2026-08-30T02:40:25Z` (predates this retro's own call by a few
hours — reported as-is rather than re-projected).

| | calls | tokens out |
|---|---|---|
| **sunk** (local hardware) | 646 | 161,710 |
| **trial** (GCP credits) | 596 | 226,778 |
| unknown | 11 | 2 |
| **total** | **1,253** | **388,490** |

`offload_ratio` **1.0** · `est_usd_saved` **$14.98** against a claude-sonnet reference ·
`real_usd_spent` **$6.27** (Vertex pricing, 404 priced calls).

⚠ This session contributed exactly **one** offloaded call — the retro skeleton. Everything else was
frontier work: the campaign was judgment, multi-file coherence and live-machine control, which is
what the doctrine says to keep. The honest read of a 1.0 ratio on a 1,253-call corpus is that it
measures the *door's* history, not this session's discipline.

---

# Addendum — closing the day *(2026-08-30, later)*

Written after the first retro because three things happened afterwards: the public article was
audited and edited, the incident recurred five more times and produced a **correction to my own
characterisation of it**, and a concurrent session localised its mechanism.

## What shipped after the first retro

| Commit | What |
|---|---|
| `4688781` | Board brought current (B3/B4/B5, campaign close, ADR-0044) |
| `8d7344f` | The first retro |
| `4dc470e` | INC-2026-08-30-A recurrence — the pre-committed negative result fires |
| *(this)* | Clustering claim withdrawn; episode table; addendum |
| `steppeintegrations-site@f798a04` | The public article — **committed, not pushed** |

## The article

Corrected one number and added one finding. The number is the interesting part: **`130` does not
exist in the corpus.** Searching every bench-row for 126–136 tok/s returns 121.58 (llama-bench, MoE,
one card) and 127.49 — which is an **aggregate at concurrency 24**. The `23` it was paired against is
single-stream. So the published `5.7×` most likely compared aggregate-under-load with single-stream:
the exact class R8 was written to make inadmissible, found in our own published work.

Corrected to **5.0× (112 vs 22)** from B5's controlled measurement. ⚠ **The dense side was always
right** (23.75 published vs 23.52/23.62 measured) — the error was entirely on the sparse side, and
finding 2's conclusion is untouched.

Added **finding 8** on machine state. ⚠ And found that the article had already documented the
llama-bench `-ts` comma trap and the bench-vs-server gap — **the campaign rediscovered both.** That
is the second rediscovery this week, after ADR-0041 re-derived OMEN-LIMIT F3. The claim register
exists to stop the first kind; nothing yet stops the second.

## I mischaracterised the incident, and impartial sampling corrected me

I recorded INC-2026-08-30-A as *"a stable ~61% state"* and noted its levels *"cluster at 64–69"*,
with the caveat that four samples is not a distribution. **The caveat was right and the claim was
wrong.** Six episodes across 12.4 h:

| onset | span | rates |
|---|---|---|
| 00:32 | ≤5 min | 65.17 · 68.37 |
| 00:54 | ≤5 min | 69.22 |
| 02:20 | 5 min | 66.56 · 66.64 |
| 03:00 | ≤5 min | 67.01 |
| 03:20 | ≤5 min | **46.13** |
| 04:30 | ≤5 min | **1.24** |

8 degraded samples of 145 — **5.5% of sampled time** — then **6.0 h clean**. Depth spans two orders
of magnitude, so there is no preferred level. And most episodes are **single-sample**, not stable
states.

⚠ **Why I got it wrong is the useful part.** The first episode was the one long enough for me to
stand over, restart, and re-measure by hand — so it is the one I characterised. **An operator
investigating an intermittent fault samples the long episodes preferentially**, because the short
ones end before the investigation starts. The impartial record and the investigator's record are
different documents, and the investigator's is biased by construction.

## The concurrent session localised the mechanism

While this was running, another session ran an ETW campaign on the same incident and got further
than the behavioural work could. Recorded as claim #24: in the degraded state **GPU busy/token rises
only +5.5% (8.811 → 9.297 ms) while wall/token rises +97.4% (9.272 → 18.298 ms)**; host gap/token
goes 0.461 → 9.001 ms and GPU busy fraction falls **95.0% → 50.8%**. On the 1-token ping, **91% of
the extra latency is host-side.**

So the loss is **non-busy wall time, not extra GPU work** — a family (host scheduling, driver
submission latency, synchronisation waits) rather than a mechanism, but a far tighter family than
"unattributed". They also refuted an energy-based inference of their own along the way (claim #23):
a stalled GPU here stays pinned at 2800 MHz and burns near-load power, so J/token is a symptom of
prolonged wall residence, not evidence of extra work.

That is consistent with the brief-and-bursty picture above and sharper than it. **Credit theirs, not
mine** — and worth noting that two sessions converged on the same phenomenon from opposite ends,
behavioural and instrumented, without contradicting each other.

## Additional lessons

9. **L-2026-08-30-9 — The investigator's record is biased toward the episodes long enough to
   investigate.** An intermittent fault sampled by hand looks more persistent than it is; only
   impartial sampling gives the duration distribution. → **practice**
10. **L-2026-08-30-10 — A caveat that turns out to be load-bearing should be promoted to a
    correction, not left as a hedge.** "Four samples is not a distribution" was written as a
    footnote and was falsified within hours; the hedge protected the reader but the claim still
    needed withdrawing. → **practice**
11. **L-2026-08-30-11 — The claim register catches stale claims, not rediscoveries.** Both
    rediscoveries this week (`-ts` comma trap, OMEN-LIMIT F3) were of things *correctly recorded*
    and simply not reached at decision time. A register of what is *true* does not help; what would
    is a habit of searching the record before measuring. → **open**

## Provenance (addendum)

No offload — the correction and the audit were judgment work. Commits `4688781`, `8d7344f`,
`4dc470e`, this one, and `steppeintegrations-site@f798a04` (unpushed). The ETW findings are the
concurrent session's; they are cited here and credited there.
