# Session retro — 2026-08-28

**One-line:** Asked for a strategy, we **measured our way past our own plan twice** — a
framework bake-off deleted most of the controller before it was written, four maintenance
windows turned the rotation thesis into receipts, and the public article was rebuilt twice
in one day because the second build finally carried the story Derek actually asked for.

## What this session was

Strategy → probe → build → publish, with a mid-course correction on each leg. It began as
a planning session (mechnet revival + self-learning multi-model scheduling), became a
measurement campaign (windows W0–W3 on the B70s), spawned an independent research session
that fed corrections back mid-flight, and ended as a publishing session (the article's
chart gallery, shipped and then re-shipped with its narrative restored).

## What shipped

| Commit | What |
|---|---|
| `74c6cda` | Rotation program ratified: ADR-0039 (27B + fx99 rungs), ADR-0040 pending probes, ROTATION-PROGRAM.html created, Track M conductor lift, registers/hygiene |
| `fdb2405` | `-sps` verified 0.10 (already active) — recon disagreement settled by `--help` |
| `737ac04` | W1: mmap-warm refuted (8.2s dio vs 12.7–13.3s warm), 4-model critic quad works, R7 sidecar landscape mapped |
| `28ef511` | Regret-gate hole #3 closed (gather lifts `tokens_out`, 56 tests green, live-verified); Flash MTP sidecar pinned + acquired |
| `73ce76c` | W2: R10 slice 1 confirms the 27B rung (verbatim recall 8/8 & 5/8 vs 2/8 & 1/8) |
| `ddcb690` | W3: sidecar refused loudly (cross-fork tensor mismatch), R9 redesigned around the era-mismatch lesson, TP measured (loses), experts-on-host operating point mapped |
| `a0d79e3` | Flash-lite proven **on tap beside live production**, zero pagefile tax; pagefile posture revised to staged-unapplied |

Site repo: `9a0a255` (the 16-slide chart gallery, 8 new machine-checked claims, registry
25, full ci 30/30) and `3c78a22` (the narrative retrofit: character-bound colors,
who-is-pulling chips, the workbench Interlude, folklore woven into captions with every
claim assertion surviving byte-exact). Both deploys confirmed live by polling.

Concurrent lanes, credited not claimed: the spawned Level-Zero session landed `ac9af0e`
(the leverage brief) and `5794f2b` (LZ lap 0 — prefill cliff refuted co-resident, wire
frozen ~13 GB/s); another agent landed `188e8c3`/`4b3b80b` (BF6 Hatchet, Clippy stub).

Durable artifacts new this session: `ROTATION-PROGRAM.html`, ADR-0039, ADR-0040,
`E:\work\battlemage\rotation-phase1\` receipts (r2/w1/w2/w3 + r10 results), the
article's gallery + claims, two memory files (rotation program updated; article cast
conventions new).

## The team retro — our collaboration across the seats

### Architect
The load-bearing call was Derek's Phase −1 order: *prove the commodity layer exists
before building it*. One afternoon of probes (P1–P7) deleted 60–70% of a controller that
a ratified plan had already specified — the cheapest large decision of the session,
made cheap precisely because it was made before any code existed. The two-problem
decomposition that came out of it ("where should this request run" is commodity; "what
should this box become next epoch" has no OSS peer) is now the program's spine.

What I'd decide differently: I shipped the 27B rung on the inversion evidence *before*
any quality-at-depth measurement existed — Derek's call, and R10 confirmed it, but the
honest sequencing was R10-first; we got away with it. And the article's first build
treated a mid-conversation creative direction as garnish rather than a design change —
an architecture failure, not a taste failure.

### Implementer
Rework was moderate and instructive. My own defects this session: the llama-swap unload
call used a query param the unload-all endpoint rightly ignores (documented; my usage
error — I initially recorded it as the tool's quirk and had to correct the record); two
stale constants in my own Playwright spec (a table count from the plan draft, a
chapter-label assertion that ignored the slide I'd just inserted); seven
caption-before-tbody violations from writing tables in the visually-intuitive order; and
a PS 5.1 commit message with embedded quotes that exploded into git pathspecs. R10's
first harness was designed wrong end-first — handwritten questions whose answers weren't
in the packed text (3/8 and 6/8 present); the rebuilt version mines probes from the pack
and is correct by construction. R9's first run gave a thinking model a 16-token budget
and measured only `<think>`.

What went well: every probe and window script wrote receipts as it ran; the fix loop on
CI failures was one-shot each; and nothing shipped un-eyeballed.

### Reviewer / QA
The best review this session came from outside this session: the spawned Level-Zero
researcher cross-annotated my rotation memory mid-flight, flagging that the 11.7 tok/s
prefill figure was a 22-token-prompt artifact below the batch-32 op-offload trigger —
caught *between* my measurement and my publication, so the article shipped the honest
"unmeasured" caveat instead of a wrong number. Its LZ lap 0 then refuted the cliff
outright (32.6–52.5 tok/s co-resident). A concurrent session writing to shared memory
functioned as a genuine QA channel.

What our own review caught: the era-mismatch in R9 (all 60 routed events came from
July's dead pool — the headline "0/60 agreement" would have been a fake finding, and
the autopsy turned it into a design requirement instead); the grader-fairness check on
R10 before claiming the 30B's failure; P4's negative control proving the cross-model
restore guard fails loudly. What slipped: my first R9 and R10 harness designs should not
have needed live runs to expose their flaws — both were reviewable on paper.

### Operator / SRE
Four production outages, four proven restores — the maintenance protocol (sentinel,
elevated stop, serviceability probe as a real one-token generation) held every time,
including twice recovering from its own preflight refusals (control-file drift, lock
drift) by the documented ceremony rather than force. The one diagnosis reversal:
"watchdog SSH dead" pattern-matched to "conductor VM off," and the VM was *running* —
only the gateway timers were held. Five minutes of live verification replaced what could
have been a pointless VM restart. The pagefile change hit my shell's privilege ceiling;
it is staged for Derek's elevated run and — after the experts-on-host discovery — its
posture was revised to *unapplied unless full-fat co-resident Flash is ever needed*,
which is the tax Derek wanted to avoid. Renders drained mid-session and were treated as
a canary confound while they ran, not ignored.

### Product / planning
Scope evolved four times and each pivot was Derek's, explicit, and worth it: strategy →
bake-off → measurement → publication → narrative retrofit. The pacing artifact worth
naming: the article's first ship was *correct by every checker and wrong by the brief*.
Derek's character direction arrived while the chart build was already moving, and I
folded it in as content (a cast slide, some names) instead of re-centering the design
(color identity, recurring presence, the journey). The retrofit cost one extra pass and
produced the better artifact — including the fact-check that turned the best story beat
into a measured claim (the Oxen really did 14.29 GB/s card-to-card on the Linux
passage). The lesson is priced and recorded.

## Two seats, two views

### From Claude's seat
I held the whole board well — three repos, four windows, a spawned session, two deploys —
and the receipts discipline meant nothing rested on my say-so. Where I over-reached:
declaring llama-swap's unload API quirky before re-reading its documentation, and
shipping a "prefill cliff" framing that a sibling session had to soften. Where I
under-reached: treating "make it an experience" as a feature request instead of a design
brief; Derek had to say "something was lost in translation" before I rebuilt the design
around the story. Next time a creative direction lands mid-build, the right move is to
stop and re-plan, not to graft.

### From Derek's seat *(my reconstruction — correct me)*
The socratic thread worked: Level-Zero, the NPU, tensor parallelism, experts-in-RAM —
each nudge got measured within the hour, and two of them became operating points on the
public record. The bake-off order paid for itself before lunch. The article is now the
thing I described — machines with names, a trail paid for in weekends, charts that
belong to their characters — and the claims machinery didn't have to bend to allow it.
The miss was real but the recovery was same-day, and the fact that my folklore survived
a fact-check *better* than fiction is exactly the register this lab writes in. The
pagefile staying unapplied is the small win I'll remember: the physics found a way to
not pay the tax.

## Last time's lessons — follow-through

| Lesson (2026-08-27) | Status | Note |
|---|---|---|
| L-1 operating point is a workload claim | **acted-on** | The inversion is now a public chart (C1); the 27B rung exists *because* the gate's point wasn't the workload's |
| L-2 guard-receipts read as run-receipts | pending | No direct occasion; claims registry work is adjacent, not the same |
| L-3 name integrity claims for what they compare | **acted-on** | R9's era autopsy + R10's "agreement, never accuracy" framing |
| L-4 completeness is coverage, not success | **acted-on** | R7 resolved-*negative* and the TP loss are first-class receipts |
| L-5 absence of progress ≠ no progress | **acted-on** | R9's empty results file correctly read as writes-at-end, not a stall |
| L-6 false terminal monitors | **acted-on** | Deploy polls emit both terminal states (DEPLOYED / NOT-LIVE) |
| L-7 test equivalence, not proxies | **acted-on** | P4 cross-model 400 control; MTP plan diffs outputs, not rates |
| L-8 evidence belongs to its configuration | **acted-on** | Era panels never share an axis; era-mismatch killed R9 slice 1's fake number |
| L-9 defaults in others' templates are your decisions | **acted-on** | llama-swap configs set ttl/swap/exclusive explicitly |
| L-10 thermal state persists between measurements | pending | W1–W3 probes were short; no inter-trial thermal instrumentation (render co-load noted instead) |
| L-11 read a shared namespace before adding to it | **acted-on** | `data-chart-*` chosen after reading the checker's seven-slide pin |

## Second opinion resolved

None pending — the 2026-08-27 retro did not use `--fleet`.

## Lessons learned

1. **L-2026-08-28-1 — A mid-build creative direction is a design change, not a
   garnish.** Grafting the character brief onto a chart-first design shipped the nouns
   without the mechanics and cost a full second pass. Stop and re-plan when the brief
   changes register. → practice (+ the cast-conventions memory).
2. **L-2026-08-28-2 — Probe the commodity before building the bespoke.** One afternoon
   of probes deleted 60–70% of a specified controller; the bake-off pattern converted an
   architecture argument into receipts. → embodied in ADR-0040.
3. **L-2026-08-28-3 — A signal that pattern-matches a known failure still gets its own
   diagnosis.** "SSH dead" ≠ "VM off": the conductor was running; only timers were held.
   → practice.
4. **L-2026-08-28-4 — Routing evidence expires with its pool: stamp the pool-config hash
   on every dispatch.** Sixty routed events from a dead era almost scored a live router
   0/60. → doc (Phase 3 shadow-log design, registered in DECISIONS-PENDING).
5. **L-2026-08-28-5 — Mine probes from the artifact; never author them from memory.**
   Handwritten R10 questions missed 5/8 answers in the actual pack; pack-mined probes
   are correct by construction. → practice.
6. **L-2026-08-28-6 — A thinking model's token floor is part of harness design.** A
   16-token budget measured `<think>`, not routing. → practice.
7. **L-2026-08-28-7 — A concurrent session writing to shared memory is a QA channel.**
   The spawned researcher corrected a confounded number between measurement and
   publication. Keep spawning research chips with memory write-back. → memory (this
   retro) + practice.
8. **L-2026-08-28-8 — The page cache has no universal sign: it lost on weight loads
   (−55%) and won as the mechanism behind zero-commit experts.** Measure the loader
   path per workload; the flag names lie (`--direct-io` is file-layer inert on
   Windows). → doc (published as article A4/A3).
9. **L-2026-08-28-9 — State files without identity need identity in their names.**
   Slot-saves carry no model fingerprint; the 400 on mismatch is structural luck. The
   `{model}.{slot}.{hash}` manifest is a Phase 2 controller requirement. → doc
   (registered).
10. **L-2026-08-28-10 — Folklore that survives a fact-check is stronger than fiction.**
    The Oxen-spoke-on-Linux beat became load-bearing because 14.29 GB/s was already in
    the tables. Check the myth against the measurements before publishing either. →
    practice (cast-conventions memory).

## Provenance

Git range: commandcenter `5ecd3bc..HEAD` (mine: `74c6cda`,`fdb2405`,`737ac04`,`28ef511`,
`73ce76c`,`ddcb690`,`a0d79e3`; spawned session: `ac9af0e`,`5794f2b`; concurrent agent:
`188e8c3`,`4b3b80b`) + steppeintegrations-site `d466eb5..3c78a22`. Offload: one batched
`local_generate` draft (omen-arc, 1,345 tokens out) for timeline/roles/lessons —
**edit_verdict: hallucinated** (invented defect counts, a CI catch that never happened,
and "Derek ran the pagefile script"; skeleton reused, facts rewritten frontier-side).
`--fleet`: not requested. Derek's-seat section is my reconstruction, marked as such.

## Offload scorecard (S6)

Offload ratio **1.0** (1,240 lifetime calls, zero frontier fallbacks): sunk 634 calls /
380k in / 159k out · trial 595 calls / 2.67M in / 227k out · est. **$14.92 saved** vs a
metered reference, against **$6.27 real trial spend**. New this session: the first-ever
`fx99-ollama` bucket (the door-verification call) and fresh `omen-arc` drafting traffic
(watermark 2026-08-28T11:46Z; the retro draft itself lands in the next projection).
