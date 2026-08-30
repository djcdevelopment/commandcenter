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

