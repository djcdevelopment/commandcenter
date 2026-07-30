# Session Retro — 2026-07-29 (one delegated call: what a faithful summary still leaves out)

> **"Do not invent claims" held perfectly — and the summary still dropped the ADR's
> load-bearing distinction.** The failure mode of a good offload isn't fabrication, it's
> omission, and omission is invisible from the output alone.

## What this session was

The **smallest real unit of work in the lab**: Derek pasted a literal `local_generate`
parameter block — prompt, `files`, `backend="am4-moe"`, `max_tokens`, `timeout_s` — and the
session is that one call plus its verification. Not a design session, not a build session; an
*execution* session. Worth a retro anyway, because the thing being exercised was the offload
doctrine itself, and it produced one clean observation about how delegated summarization fails.

## What shipped

**No commits.** No code, no config, no repo changes. One file read
([`docs/adr/0005-one-boundary-three-planes.md`](docs/adr/0005-one-boundary-three-planes.md)),
zero files written during the working phase.

What actually shipped is an **observation and its provenance**, both now on the HEARTH ledger:

| Fact | Value |
| --- | --- |
| Backend / model | `am4-moe` / `gpt-oss-120b` (`http://192.168.12.233:8082`) |
| Routing | `routed_by: pinned:am4-moe`, occupancy `available` |
| Tokens | 858 in / 242 out |
| Latency | 8,657 ms (warm — no model-load tax) |
| File packing | 1 file, 3,062 bytes, door-side (`files=`, nothing pasted into the prompt) |
| Verification verdict | no fabrications; one redundancy, one material omission |

Durable artifacts from the retro phase: this file, a memory refinement
([`feedback-verify-relayed-agent-reports`](../../Users/derek/.claude/projects/C--work-commandcenter/memory/feedback-verify-relayed-agent-reports.md)),
and one escalation into [DECISIONS-PENDING.md](DECISIONS-PENDING.md).

## The team retro — our collaboration across the seats

*(Drafted first pass by `am4-moe` from a factsheet; edited and in places reversed — see
Provenance. Kept short on purpose: a one-call session that gets five paragraphs of seat
analysis is a retro inflating its own subject.)*

**Architect.** No architectural decisions were made or needed — the parameter block *was* the
design, and it was Derek's. The one thing worth naming is what the call demonstrated rather than
decided: `files=` + a pinned rung + `routed_by` on the result is the offload contract working
exactly as ADR-0005 and the offload doctrine describe it, on the ADR that defines the boundary
it crossed. (The local draft tried to fill this seat with "the tool schema had to be fetched
first," which is plumbing, not architecture — cut.)

**Implementer.** The call ran verbatim: no parameter altered, added, or "improved," which is the
correct behavior for a literal invocation and the only real discipline this seat exercised. One
mechanical step preceded it — `local_generate` is a deferred tool here, so its schema had to be
loaded by name before it was callable.

**Reviewer / QA.** This is the seat that earned its keep. Checking the output against the source
found the three-bullet blurb factually clean but **missing the ADR's sharpest point** — that the
planes are *control-surface roles, not machine roles*, so AM4 can host both a sense surface and
act capacity precisely because its acting is only reachable through HEARTH
([ADR-0005](docs/adr/0005-one-boundary-three-planes.md), line 18) — and also missing the
consciously accepted cost (indirection, lines 47–48). It also found a redundancy that is *not*
the model's error: the requested framing asked for "the boundary" and "the three planes" as
separate bullets, but in this ADR the boundary **is** the third plane, so restating it was the
faithful move.

**Operator / SRE.** Clean warm path, `ok:true`, first attempt, correct rung, no revive needed —
nothing to report, which for the door is the good outcome. Background state worth naming and
**not** this session's to fix: `master` is **10 commits ahead of `origin/master`** and the working
tree carries ~23 modified and ~10 untracked files from other, earlier sessions (a demo page,
`hearth/gcp/`, a dashboard module, a draft ADR-0026, projection JSON). None of it authored here.

**Product / planning.** Nothing to plan — the ask was fully specified and fully delivered. The
only judgment call was mine and it was about *scope of reply*: whether to hand back the model's
bullets alone, or to also report what they left out. Reporting the omission is what made a
30-second call worth anything.

### Two seats, two views

**From Claude's seat.** The right call was reading the ADR myself instead of relaying a
clean-looking result — and the interesting part is *why* that was necessary. The output passed
every check that doesn't require the source: no invented facts, no bad numbers, no
mis-attribution. Assertion-checking would have cleared it. Only reading the 51-line source
revealed that the one sentence a "technically curious visitor" most needs — planes are roles,
not machines — wasn't there. Where I'd tighten: I offloaded this retro's prose after a session
whose entire content was *one* offload, and the draft came back too thin to use for two of five
seats. The round-trip was still worth it as an observation, but I should be honest that the edit
cost approached the write cost at this size.

**From Derek's seat** *(my reconstruction — correct me where wrong)*. He handed over an exact
invocation, which reads as him using me as a hand on the lever rather than a collaborator on a
question — the offload-first doctrine executed literally, on a document he chose deliberately
(the ADR that defines the boundary the call crosses). He'd likely care most about the two lines
of editorial feedback, since they're the difference between a blurb he could publish and one he
couldn't, and least about the seat analysis for a call this small. My read of the implied ask: he
was testing the *pipeline*, and the visitor-facing text was the payload.

## Last time's lessons — follow-through

The previous retro is [SESSION-RETRO-2026-07-21.md](SESSION-RETRO-2026-07-21.md) — **eight days
and several un-retro'd sessions ago** (the Buzz/mesh-llm work, the i5 deploy lane, the GCP spend
diagnosis all shipped without a retro). This audit therefore spans a gap, not one session, and
"no occasion" below means no occasion *in this session*, not in the intervening week.

| Lesson | Status |
| --- | --- |
| L-2026-07-21-1 — a "works anywhere" memory is a claim about its write-date | pending — no occasion |
| L-2026-07-21-2 — `python -m pkg` resolves against cwd, not interpreter path | pending — no occasion |
| L-2026-07-21-3 — unpushed/unattached git state is a recurring failure shape | **recurred (4th+)** — `master` is 10 ahead of `origin/master`, from earlier sessions. 07-21 said a fourth occurrence should trigger an actual fix; escalated to [DECISIONS-PENDING.md](DECISIONS-PENDING.md) rather than counted again |
| L-2026-07-21-4 — a benchmark can invalidate the premise of the build it gates | pending — no occasion |
| L-2026-07-21-5 — documented capability ≠ actual UI state; exercise it live | pending — no occasion |
| L-2026-07-21-6 — git-invisible operational state is a real gap | acted-on — the session's only real state change (two ledger rows) is named in Provenance with its metadata, not left implicit |
| L-2026-07-21-7 — refusing to guess a credential/config-shaped unknown | pending — no occasion |
| L-2026-07-21-8 — an external caller earns a safety preamble, not just a grant | pending — no occasion |

Also worth closing: 07-21's Reviewer/QA flagged two `local_generate` calls made **without**
`files=` as "a real footgun — naming a path in prompt text gives the delegated model nothing to
read." This session's call used `files=` correctly and the door packed 3,062 bytes. Honesty
qualifier: that came from Derek's parameter block, not from me remembering the footgun.

**Second opinion resolved** — none pending; 07-21 dispatched no `--fleet` draft, and none was
dispatched here.

## Lessons learned

1. **L-2026-07-29-1 — The failure mode of a *faithful* offloaded summary is omission, and
   "do not invent claims" does nothing to prevent it.** The anti-fabrication instruction worked;
   every bullet traced to the source. What went missing was the source's most load-bearing
   distinction. Omission cannot be detected by reading the output — the text looks complete —
   nor by assertion-checking, which a faithful summary passes trivially. **Only reading the
   source catches it.** This sharpens the existing verify-relayed-reports directive, which is
   written for *claims* (re-run the test, query the ledger) and is silent on *summaries*, where
   there is no claim to re-run. *(→ memory: refine
   [[feedback-verify-relayed-agent-reports]] — done, not a new ADR; see Judgment call below)*
2. **L-2026-07-29-2 — When the requested framing overlaps the source's own structure, redundancy
   in the output is the faithful answer, not a model defect.** Asking for "the boundary" and "the
   three planes" as separate bullets guarantees repetition when the boundary *is* the third
   plane. Diagnose prompt-shape before diagnosing the model — and specify granularity up front
   when a source's concepts nest. *(→ practice)*
3. **L-2026-07-29-3 — One warm call is a data point, not a reliability finding.** The draft
   offered "sub-10 s latency, reinforcing its reliability" from a single sample; that's the
   over-claim to resist. What this session actually adds is *one* unblinded observation
   consistent with the 07-21 benchmark (am4-moe scored 93.8, highest of three backends, on
   documentation-consistency tasks) — and the ledger now shows this rung at **84 lifetime calls,
   `ok_rate` 1.0, $0**. The ledger is where reliability claims come from; a retro anecdote isn't.
   *(→ practice)*

**Judgment call, stated for the record:** L-2026-07-29-1 is decision-shaping and would normally
argue for an ADR. I deliberately did **not** mint one from a single call — the honest evidence
base is n=1 plus an aligned prior benchmark, and the existing directive memory is the right home
for a refinement of this size. If a second session hits omission-in-a-clean-summary, that's the
trigger to promote it to an ADR. (Note also that an uncommitted **ADR-0026** draft from another
session already sits in `docs/adr/` — untouched here.)

## Docs / plans

**None updated, deliberately.** The git range is empty, so the diff-derived doc sweep has nothing
to derive from, and no living plan's state changed. Per the skill's bounded-sweep guardrail, a
retro is not license to touch docs this session didn't affect.

## Provenance

Git range: **none — zero commits**, and unusually for this repo, zero working-tree changes from
the working phase either (one file read). All uncommitted state in `git status` predates this
session (see Operator/SRE).

**Offloads: two `local_generate` calls, both pinned `am4-moe` / `gpt-oss-120b`, both `ok:true`.**
(1) The session's actual subject — the ADR-0005 visitor blurb (858 in / 242 out, 8.7 s, 3,062
bytes packed). **Edit verdict: `faithful`** — zero corrections needed to the text; the two
findings I reported were about what it *omitted*, not what it got wrong. (2) The first pass at
this retro's seat reads and lessons (1,498 in / 689 out, 34.2 s). **Edit verdict: `minor-fixes`**
— factually clean and no fabrication, but two of five seats were filled with mechanics instead of
reads (the Architect seat treated a tool-schema fetch as an architectural decision — a category
error, rewritten), and one lesson over-claimed reliability from a single sample (corrected in
L-3 above). Frontier: the verification against the ADR, the two editorial findings, the
follow-through audit, the ADR-vs-memory disposition call, and this file's judgment and structure.

## Offload scorecard (S6)

`offload_ratio` **1.0** · **382** lifetime calls — **sunk** 119 (327,172 in / 139,807 out),
**trial** 257 (2,084,047 in / 150,767 out), unknown 6 · `est_usd_saved` **$11.59** vs
claude-sonnet reference · `real_usd_spent` **$5.77** (105 priced / 152 unpriced calls, Vertex
pricing verified 2026-07-23). This rung specifically: **`am4-moe` 84 calls, `ok_rate` 1.0,
`real_usd` $0.00**, last seen `2026-07-29T23:29:09Z` — this session's first call.
*Caveat:* the projection was refreshed **before** the second (retro-draft) offload landed, so
that call is not yet in these numbers; it appears on the next `project_offload_knowledge` run.
The legacy zero-token `model:<name>`-shaped buckets (167 of 382 lifetime calls) remain the
known, already-tracked undercount.
