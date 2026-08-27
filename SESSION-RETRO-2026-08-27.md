# Session retro — 2026-08-27

**We set out to benchmark a model and spent most of the session discovering that the
instrument, the evidence, and my own fixes each needed proving before the measurement
meant anything — and the campaign's headline number turned out to describe the one
operating point where the answer comes out backwards.**

## What this session was

A resumption, then a review, then a recovery — and only incidentally a benchmark run.

Derek pointed at a campaign spec and suggested running the new Qwen models through the
wind tunnel. The campaign was not greenfield: a prior agent session had already built the
whole instrument — a 2,270-line measurement engine, 19 stage scripts, a 72-task assay, 30
unit tests — and had stopped on a failure that never happened. Windows returned a null
exit code for a watchdog process that had exited cleanly, and the parent read that as an
abort. The first real work was proving the instrument, not running it.

It then met two physical walls, both thermal, both real. The replica-per-card topology —
the placement the spec had *predicted would win* — tripped the 95 °C line at 96 °C on a
light cell, with no spill and no system events. Later the 128K context tier did the same
and killed the campaign outright. The second abort named its own cause precisely: VRAM on
one specific card at 96 °C while its sibling sat at 86 °C and both GPU cores idled in the
high seventies. The ceiling on this rig is cooling on one card, not capacity.

The largest single category of work was verification catching errors. An adversarial
review of thermal-resume machinery written by another agent mid-session produced 42
findings, of which 24 survived independent attempts to refute them — including one that
would have let a crashed leg be skipped forever while the stage reported green. Five more
defects were my own, and every one of them surfaced the same way: by running the change
against real state instead of trusting that it was correct.

The verdict came back `do_not_promote` on 12 of 15 gates, which is the right mechanical
answer. The more useful answer is that the failing gate measures 512-token prompts — the
single regime where a dense 27B loses to a 3B-active mixture-of-experts — and that along
the prompt-length axis the result inverts completely.

## What shipped

**commandcenter** — `9bf8f24..08b322a`, 13 commits, 52 files, +14387/−12.
One commit in the range (`ca8eb27`) belongs to **another agent's session**, not this one.

| Commit | What |
|---|---|
| `6030254` | The qwen38 campaign harness, 27B-only run prep, arc maintenance sentinel |
| `334a140` | Preflight feature assertion via `git grep`, not a host `rg` |
| `d1ba4c3` | TODO: chase slot-cache thrash on the HEARTH ledger |
| `5d9d381` | Dual thinking regimes — no-think gated, think-on side cell |
| `f6b1bc7` | `qwen38-summary` corpus adapter — campaign aggregates into `bench-row.v1` |
| `ca8eb27` | *(other session)* resume safely after replica thermal abort |
| `7529b4a` | Land the adapter layer's missing half — schema widening + first adapter |
| `5e9366c` | Close the resume-integrity holes an adversarial review found |
| `169f47f` | Leg completeness is coverage, not success; parse abort stamps as instants |
| `817cc30` | Merge the duplicate cooldown function I introduced |
| `9ebf3b3` | A verdict may not attribute quality it measured elsewhere |
| `75f0d91` | Land the full qwen38 campaign as `bench-row.v1` (1227 rows) |
| `08b322a` | Slot-cache-thrash mechanism measured; frequency needs instrumentation |

**Durable artifacts:** a completed campaign with `promotion-verdict.json` and
`promotion-scorecard.json`; 1227 schema-validated `bench-row.v1` rows covering three
models; a second corpus adapter with tests; [ADR-0038](docs/adr/0038-a-verdict-cites-only-evidence-from-the-configuration-it-promotes.md).

## The team retro — our collaboration across the seats

### Architect
The load-bearing design call was Derek's: **measure both thinking regimes** rather than
picking one. That call is what made the think-on side cell exist, and the side cell is
what let us say "thinking buys nothing measurable here" with a number instead of a guess.
It cost one extra assay leg and answered a question that would otherwise have stayed open.

What I would decide differently: the promotion gate's operating point was inherited
without challenge. A gate that fixes prompt length at 512 tokens encodes an assumption
about the workload, and nobody — including me, for most of the session — treated that as a
design decision. It is the single most consequential unexamined constant in the campaign.

### Implementer
Rework was high and it was mostly mine. Five defects in my own changes, four of them in
code written specifically to *fix* other defects. The pattern is consistent: each one
looked correct when written and was wrong about how the environment actually behaves —
NTFS's lag on open files, PowerShell's last-definition-wins, `tasklist` printing window
titles, ISO offsets versus `Z`. None was a logic error in the usual sense.

What went well: every fix was landed with a test that would have caught the defect, and
the mid-run edit to `lib.ps1` was timed into the quietest window available rather than
deferred indefinitely or done carelessly.

### Reviewer / QA
This seat did the session's best work, and it was not me reading my own code. The
adversarial review — findings generated independently, then each one attacked by a
verifier told to refute it — kept 24 of 42. The refutation step mattered: 18 plausible
findings died there, which is 18 investigations not spent.

What slipped: the campaign's own MTP compatibility gate compared *validity rates* between
speculation-on and speculation-off and passed them as equal at 0.875. It never compared
the outputs. Equal pass rates on a rubric are not equivalence, and the divergence sat
undetected until I compared response text directly.

### Operator / SRE
The safety machinery worked exactly as designed, twice. Both thermal aborts stopped the
leg, restored production, and proved the restore with a real one-token generation. The
outage discipline held: production came back serviceable every time, the sentinel was
always cleaned up, and no measurement was taken with the door in an unknown state.

The operator lesson is about *my* monitoring, not the campaign's. I wrote a monitor that
reported the campaign dead while it was healthy. Had I acted on it, I would have started
recovery against a running campaign with production still stopped. I also nearly fell into
the previous session's polling loop before retuning the monitor to stop waking me on
per-cell noise.

### Product / planning
We built the right thing and then found out the gates were asking a narrower question than
the one Derek actually has. The campaign answers "should this replace the default rung?"
Derek's real question is closer to "is this worth serving at all, and for what?" — and the
data answers that far more interestingly than the verdict does.

Scope discipline was decent: Flash-Next stayed excluded as agreed, the corpus adapter got
built during dead time rather than competing with the run, and the slot-cache analysis
waited until the outage closed.

## Two seats, two views

### From Claude's seat
I was right to distrust the handoff and wrong to trust myself at the same rate. The
adversarial review of another agent's work was thorough; my own changes went in with far
less scrutiny, and that asymmetry is exactly backwards from what the defect counts justify
— 24 findings in 529 lines of theirs, five in a smaller volume of mine.

Where I over-reached: the first completeness gate. I wrote a rule that was stricter than
the science required, and it would have cost hours of re-measurement to re-observe
timeouts we had already measured. I caught it only because I checked the rule against live
data before relying on it, which I nearly did not do.

What I would want to know next time: what the production call mix actually looks like by
prompt size. I ended the session recommending a long-context rung on structural reasoning
rather than measurement, because the ledger cannot answer it.

### From Derek's seat *(my reconstruction — correct me)*
He asked a short question and got a long campaign, which is the normal shape here. He made
three decisions quickly and cleanly, and each one held up. The "both regimes" call in
particular paid for itself.

What he would likely flag: I reported a stall and killed an 84 GB download on evidence
that turned out to mean nothing, and he had already sent me a screenshot showing the disk
working. The picture was in front of me. He would also, I suspect, care more about the
2.63×/5.49× long-prompt inversion than about the verdict — the gates answer a question he
did not ask, and the interesting number is the one that says *where* this model earns its
place.

## Last time's lessons — follow-through

| Lesson (2026-08-25) | Status | Evidence |
|---|---|---|
| L-2026-08-25-1 — a premise with no number attached is treated as a constant until it breaks | **acted-on** | The 512-token gate is exactly this shape; named it explicitly rather than letting the verdict stand alone |
| L-2026-08-25-2 — every fallback that makes failure survivable makes it invisible; be loud | **acted-on** | The watchdog "passed" receipt was precisely a silent survivable failure; the leg gate now demands measurements |
| L-2026-08-25-3 — cheap checks certify containers, not content | **acted-on, twice** | The MTP gate certified validity rates, not outputs; `tasklist` certified a process list that could not contain the answer |
| L-2026-08-25-4 — a guard needs a test that proves it FIRES | **acted-on** | Every new gate landed with a test that fails without it, including two for the quality-attribution gate |
| L-2026-08-25-5 — when two machines run the same command, the output is the contract | **acted-on** | Applied to one machine, two code paths: MTP-on and MTP-off were compared by output, not by configuration |
| L-2026-08-25-6 — cancel on proof, never on absence of proof | **dropped this session, then relearned** | I killed the download on absence of visible bytes. The lesson existed and I did not apply it; it recurs below as L-2026-08-27-5 |
| L-2026-08-25-7 — config-as-code never re-run drifts from the machine | pending | Not exercised |
| L-2026-08-25-8 — moving work to the right machine is half the fix if the data flow stays | pending | Not exercised |
| L-2026-08-25-9 — subsystems sharing hardware need the separation stated | **acted-on** | The thermal ceiling is a shared-resource fact now stated per-card, with the hot BDF named |
| L-2026-08-25-10 — an unexercised rollback path is not a rollback path | pending | Not exercised |
| L-2026-07-30-6 — local-model seat reads invent self-criticism | **acted-on** | Seat reads written frontier again; offloaded only timeline + lesson extraction |
| L-2026-07-30-7 — in-process `local_generate` calls are invisible to offload economics | **acted-on** | Both retro offloads went through the door and are on the ledger |

## Second opinion resolved

None pending — the 2026-08-25 retro did not use `--fleet`.

## Lessons learned

1. **L-2026-08-27-1 — A benchmark's operating point is a claim about the workload, and
   choosing it wrong inverts the answer.** The promotion gate fixes prompts at 512 tokens
   and returns −31%; the same candidate on the same hardware returns +163% at 8K and +449%
   at 32K. Nothing about the model changed between those numbers. *(→ ADR-0038 context, and
   an open decision)*
2. **L-2026-08-27-2 — A receipt that certifies the guard will eventually be read as
   certifying the work.** The watchdog's `passed` receipt means "no safety line tripped" and
   is written even when the request runner dies; a later change made it sufficient to skip a
   leg, which would have left silent holes in the matrix. *(→ ADR-0038, fixed in `5e9366c`)*
3. **L-2026-08-27-3 — Name an integrity claim for exactly what it compares, or it will be
   read as covering everything.** `unchanged_inputs_proved` compared only model and engine
   bytes while the file holding every safety threshold and promotion constant drifted
   underneath it — and did drift, benignly, on the live run. *(→ practice; renamed to three
   fields that each say what they prove)*
4. **L-2026-08-27-4 — Completeness is coverage, not success: an outcome the experiment
   exists to observe is data, not a gap.** My first gate demanded every request succeed, and
   would have re-run legs whose 8-of-24 timeout rate at 64K *was* the finding. *(→ practice)*
5. **L-2026-08-27-5 — Absence of visible progress is not evidence of no progress; verify
   through the channel that measures the work.** Three independent size-based probes agreed a
   download was dead; the handle held 84 GB that materialized the moment it closed. Process
   I/O counters would have shown it. *(→ memory, written)*
6. **L-2026-08-27-6 — A monitor that reports a terminal state falsely is worse than no
   monitor.** Mine announced the campaign dead because `tasklist` prints window titles, not
   command lines; acting on it meant recovery against a healthy run with production down.
   *(→ practice: a liveness check must be validated against the live positive case)*
7. **L-2026-08-27-7 — When two code paths must be equivalent, test equivalence, not a proxy
   for it.** The MTP gate compared pass rates (0.875 vs 0.875, passed) where it should have
   compared text; the paths produce different output at temperature 0. *(→ ADR-0038)*
8. **L-2026-08-27-8 — Evidence belongs to the configuration it was measured on.** The
   scorecard was about to stamp an MTP-on winner with an MTP-off pass rate. *(→ ADR-0038,
   enforced in `9ebf3b3`)*
9. **L-2026-08-27-9 — A default inside someone else's template is a decision you have made
   without noticing.** Qwen3.8 enables thinking by default; that single unexamined default
   consumed entire output budgets and failed a correctness gate that had nothing wrong with
   it. *(→ memory)*
10. **L-2026-08-27-10 — Thermal state persists between measurements, so an experiment that
    does not reset it measures the schedule as much as the configuration.** Every deep cell
    began from wherever the previous one left the cards; the config already carried
    cooldown thresholds that nothing consumed between legs. *(→ practice, fixed)*
11. **L-2026-08-27-11 — Adding to a shared namespace without reading it first silently
    replaces someone's better work.** My cooldown function shadowed an existing one that
    wrote telemetry and a receipt; PowerShell keeps the last definition and says nothing.
    *(→ practice)*

## Provenance

**Git range:** `9bf8f24..08b322a`, 13 commits, not yet pushed. `ca8eb27` within the range is
**another session's** commit, not this one's. The working tree carries pre-existing dirty
files (`CLAUDE.md`, two HTML dashboards, `hearth/media/*`, eleven `knowledge/*.json`) that
predate this session and were deliberately left untouched.

**Offloaded:** timeline condense + lessons extraction. First attempt `gcp-gemini`
(`gemini-3.5-flash`) returned **only its own reasoning and no content** — 117 output tokens,
the known strict-format failure mode for that rung. Escalated once per the skill's retry
budget to `gcp-gemini-pro` (`gemini-3.1-pro-preview`), 1761 in / 1071 out,
`routed_by: pinned:gcp-gemini-pro`. **Edit verdict: `minor-fixes`** — the narrative
miscategorised the download misread as one of the two "physical walls" (it was an
operational error, not a hardware limit) and overstated the completeness gate as having
failed the run; several drafted lessons were event restatements and were rewritten. The
draft's framing of the thermal and verification sections survived largely intact.

**Frontier:** all five seat reads, both two-seat views, the follow-through audit, every
lesson's final wording, ADR-0038, and all repo-coherent writes.

**`--fleet`:** not requested.

## Offload scorecard (S6)

`knowledge/offload.json` — watermark `2026-08-27T16:44:26Z`, 1234 calls across 11 buckets.

| Class | Calls | Tokens out |
|---|---|---|
| sunk | 631 | 159,099 |
| trial | 593 | 225,474 |
| unknown | 10 | 0 |

`offload_ratio` **1.0**; `est_usd_saved` **$14.89** against a claude-sonnet reference;
`real_usd_spent` **$6.27** on the trial rungs. Note `omen-arc` shows only **11 calls** —
that rung became the door default on 2026-08-21 and has barely been exercised through the
door, which is the same data gap that makes the slot-cache frequency question
unanswerable (see `todo.txt`).
