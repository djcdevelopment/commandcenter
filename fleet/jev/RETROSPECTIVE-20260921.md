# JEV scheduler and on-demand Hermes: final retrospective

Date closed: 2026-09-21 UTC

Scope: the committed Hermes foundation and the JEV scheduler experiment through cycle21

Disposition: **land the evidence and tooling; end the experiment; do not enable unattended acceptance**

This document closes the experiment. It records historical results, not current
service state or permission to load a model, restart a service, or reclaim a GPU.
The detailed chronology and raw references remain in
[the overnight log](OVERNIGHT-20260921.md).

## Outcome

The experiment produced a working local control loop: JEV scored work, the FX99
scheduler admitted selected tasks, an OMEN worker attempted them, and Hermes on
AM4 reviewed candidates before a human-controlled release step. That loop made
useful artifacts, exposed real failure modes, and remained manually governed.

It did **not** establish autonomous code acceptance. Hermes sometimes returned a
false `PASS`; builders sometimes produced incomplete, malformed, or superficially
plausible work; and the final AM4 builder comparison stopped at authentication
before inference. Independent tests and source inspection remained the acceptance
authority throughout.

## What landed

- Scheduler commands for status, budget, capacity, operations, and decision
  explanation, with human and JSON output where appropriate.
- Adaptive polling: observed active-build polling around 5.66–5.76 seconds,
  30-second idle polling, and a one-second post-review follow-up.
- Saved fit judgments and curated quality-history feedback, without presenting
  either as proof that a patch is correct.
- Better worker action decoding after valid JSON responses had been discarded,
  a larger response allowance within the existing gateway limit, and documented
  redaction-fidelity boundaries.
- An opt-in packet-only Hermes reviewer. It is useful evidence, but not the
  default: its measured review exceeded the requested word limit and included
  unsupported claims even though it used no tool-call/result messages.
- Canonical operator capacity evidence and a generated capability catalog, with
  measured catalog values tied to receipts.

## Scorecard and accounting

| Measure | Result |
| --- | --- |
| Selected outcomes | 12 |
| Unchanged | 1 |
| Assisted deliveries | 8 |
| Incomplete worker result | 1 |
| Did not reach this worker | 2 |
| JEV calls through cycle20 | 16 |
| Estimated usage | USD 0.000538692 |
| Uncertain reservations | USD 0.005505024 |
| Total booked | USD 0.006043716 |
| Cycle21 additions | 0 JEV calls; no model completion |

The dollar figures are accounting estimates, not invoices. The larger cost was
operator and review time: candidates repeatedly needed diagnosis, correction,
or independent reconstruction before they were safe to keep.

## Attribution

The components did different jobs and their contributions should not be blurred:

- **JEV** scored task/profile fit, enforced budget/admission rules, and selected
  work. Its score was never a probability of correctness.
- **OMEN/local builders** generated candidates and, in several cycles, complete
  source artifacts. Their raw outputs are preserved even when rejected.
- **Hermes** reviewed packets and supplied useful criticism, but its verdict was
  evidence rather than authority. The packet-only variation was explicitly
  limited and remained opt-in.
- **Codex** designed and integrated the scheduler changes, inspected source and
  raw evidence, corrected assisted deliveries, supplied work when builders did
  not, ran validation, and made the final acceptance decisions.
- **Derek** set the experiment boundaries, authorized consequential extensions,
  and retained the manual promotion decision.

An “assisted” result therefore means local model work materially contributed; it
does not mean that the model independently delivered an accepted change.

## The failures that mattered

### A reviewer verdict was not an acceptance gate

Hermes could approve work that source inspection or tests later disproved. The
lesson is structural: critique can focus attention, but only executable checks
and direct inspection establish acceptance.

### Formatting and transport failures looked like reasoning failures

The worker sometimes returned valid JSON that the action parser discarded, and
some responses were truncated at 2,048 tokens before a complete file action.
Fixing the parser and allowing 4,096 tokens recovered useful work. This was not
evidence that the model had improved; the surrounding apparatus stopped losing
what it had produced.

### Complete-looking output still required correction

The next full-file builder response completed in 63 seconds, yet independent
review still found defects. The experiment repeatedly demonstrated that artifact
presence, reviewer `PASS`, and behavioral correctness are three different facts.

### The final comparison stopped before the useful artifact

Cycle21 had a fresh 22:30–22:50 UTC window for one Dense builder comparison. At
22:33:14 the request returned HTTP 401, “missing or invalid bearer token,” before
inference. No candidate or quality comparison existed. The owned model was
unloaded by 22:34:11, shared runner configuration was unchanged, and there was no
second attempt. Stopping there was the right outcome: changing credentials or
topology would have exceeded the authorized experiment instead of rescuing it.

### Saved source was not necessarily loaded runtime

The required-execution-evidence gate was saved, but a policy-denied gateway
restart meant it was not loaded. Documentation must distinguish source state,
deployed state, and observed runtime state.

## What we would keep

Keep the manual promotion boundary, short feedback loops, explicit budget and
capacity views, raw-output preservation, exact attribution, and the decision
explanation command. Keep using the real task as the smoke test. Keep the rule
that tests and source evidence outrank model self-report.

For a future lap, require a useful artifact early and reserve the final quarter
for review, accounting, cleanup, and restoration. When setup consumes that
reserve—or authentication fails before inference—stop and ask instead of turning
apparatus work into the deliverable.

## Why the experiment ends here

The branch contains enough working machinery and evidence to retain, but the
remaining edges require a new decision rather than another automatic retry. The
scheduler is historical/manual, the reviewer is advisory, and no production
activation follows from landing this work.

Reopen only in a newly authorized window with all of the following named up
front:

1. a concrete task whose candidate is the useful artifact;
2. an authenticated, read-only builder facade check completed before GPU load;
3. a wall-clock and spend ceiling with an explicit delivery reserve;
4. independent tests or source assertions that decide acceptance;
5. a restoration owner and known final service/model state.

The immediate technical prerequisite for repeating the AM4 comparison is to
reconcile the builder-specific credential with the AM4 facade and prove it using
a read-only authentication check. That prerequisite is not authorization to do
so now.

## Final state on record

At the last recorded cleanup, the FX99 scheduler was ready/idle and AM4 was
unloaded. That statement is historical and was not re-verified while preparing
this retrospective. No deployment, restart, credential change, GPU load, or
remote publication is part of this closeout.
