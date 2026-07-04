# ADR-0001 — The assay is a regression gate, not an acceptance oracle

**Status:** Accepted (2026-07-04)
**Context sources:** `ASSAY-ACCEPTANCE-GAP-2026-07-03.html`, `POUR-STATUS.md`,
`runs/regression-probe-ccb1/PROVENANCE.md`, wave-2 curation (2026-07-04).

## Context

The fleet's behavior assay (`assay_compare_branches`) grades each competing build branch
by running the test suite and scoring pass rate (typically B/70), then a debiased AM4
critic breaks score ties. The winner is (optionally) promoted.

This selects the *least-broken* branch. It does **not** verify the winner is *correct* or
*complete*. Across five separate laps it picked an unlandable "winner":

- **pour-c2**: a timed-out, collector-only lap (missing its required tests) tied a complete
  lap at B/70; the tiebreak hit position bias, fell back to list order, and crowned the
  incomplete one.
- **pour-f1, pour-ga**: winners shipped the module but not the DoD-required test files;
  graded B/70 regardless. (Curation later found a latent `KeyError` crash in ga's code
  that the missing tests would have caught.)
- **pour-gc**: *neither* lap was landable — the assay "winner" (70) was a bare module +
  retro; the runner-up (64) called a function it never defined (`NameError`, broke 10
  existing tests). The assay graded a crashing projector 64.
- **wave-2 (all five)**: cc-builder-1 showed `agent_timed_out` yet produced the winning,
  complete code every time — so a timeout flag does **not** imply an incomplete lap.

The assay's score was often *right about "does it pass tests"* and *wrong about "is it the
deliverable we asked for."* No single scalar (timeout flag, rc, test count) separates
"timed-out + incomplete" (pour-c2) from "timed-out + complete" (wave-2): both scored 70
with passing baseline tests.

## Decision

1. **Treat the assay as a regression gate, not an acceptance oracle.** A "winner" is a
   *candidate*, never automatically landable. Landing to `master` goes through a **curation
   step**: verify the winner produced the stream's required deliverables, run its tests,
   and only then merge. This is how wave-1 and wave-2 actually landed.
2. **The real fix is stream-scoped deliverable acceptance**, implemented in the *assay
   layer* (not `conductor_maf.py`): each stream declares its required deliverables (e.g. a
   CCMETA `requires:` list or a structured `## DOD:` block), and `assay_compare_branches`
   marks a lap `acceptance_failed` (excluded from ranking) when a required deliverable is
   absent — *before* ranking by behavior score.
3. **Reject the naive timeout grade-cap.** An earlier proposal ("cap any `agent_timed_out`
   lap's score to 0") is **rejected**: wave-2 proved it would demote the builder that
   actually delivered. `agent_timed_out` is recorded as metadata (real signal for the
   belief/economics layers) but must never gate the winner.

## Consequences

- Pours produce candidate branches + evidence; a human/curator (or, once built, the
  stream-scoped acceptance assay) decides what lands. `promote: false` is the safe default
  for campaigns until acceptance is automated.
- Until (2) ships, expect curation on most laps — and expect it to catch real defects (it
  caught ga's `KeyError` and gc's `NameError`).
- The tiebreak and promote health-gate remain, but they are downstream of acceptance, not a
  substitute for it.
- Follow-up work: implement (2). Design in `ASSAY-ACCEPTANCE-GAP-2026-07-03.html`; the
  retraction of the timeout-cap is recorded in `CONDUCTOR-FOLLOWUPS-2026-07-04.md`.
