# The Scheduler and the Critic

## How a local fleet learned that choosing work is easier than accepting it

This is a podcast-ready narrative source for the Hermes and JEV experiment. It
is written to be read aloud, adapted, or used as research notes. It contains no
invented dialogue. Dates, measurements, and claims come from the linked project
record; current machine state must be checked separately.

## The cast

- **Derek**, the operator, sets the boundary and keeps promotion a human choice.
- **JEV**, the scorer, decides whether a task fits a worker and a budget.
- **The FX99 scheduler**, the stage manager, polls, admits, dispatches, and waits.
- **OMEN**, the builder, turns bounded requests into candidate artifacts.
- **Hermes on AM4**, the critic, reviews candidates without owning acceptance.
- **Codex**, the integrator, checks source and evidence, fixes gaps, and lands work.
- **The evidence**, tests, logs, receipts, hashes, and raw candidates, gets the
  final word.

## Cold open: a deceptively simple loop

The diagram was appealingly small:

`task → JEV fit decision → local builder → Hermes review → manual release`

Every arrow concealed a different question. Does this task fit the worker? Did
the worker actually write the requested artifact? Is the artifact correct? Did
the reviewer inspect the right thing? Is the runtime using the source we saved?
Can the system be restored when the window closes?

The experiment became valuable when it stopped treating those as one question.

## Act one: give the critic a seat

Hermes came first. A restricted controller on FX99 could use a resident Dense
model on AM4 and send bounded work through HEARTH to OMEN. The path produced real
artifacts, but only after corrections. It was useful as a governed reviewer and
orchestrator, not qualified as an autonomous optimizer.

That distinction shaped everything that followed. A local model could propose.
Hermes could criticize. Neither could silently promote.

Source: [Hermes retrospective](../../fleet/hermes/RETROSPECTIVE-20260920.md).

## Act two: teach the scheduler to choose

JEV added admission. It scored task/profile fit, tracked a tiny explicit budget,
and let the scheduler explain why work was—or was not—selected. The first loop
showed why concrete metadata mattered: one request was held at fit 2.27,
confidence 0.27, ambiguity 0.37. A clarified metadata-only request reached fit
3.00, confidence 1.00, ambiguity 0.10 and dispatched one real worker.

The two JEV calls cost an estimated USD 0.000070098. The score described fit,
not patch correctness. That sentence became one of the experiment’s guardrails.

Source: [first-loop handoff](../../fleet/jev/HANDOFF-20260921.md).

## Act three: the first false comfort

The loop could run end to end, but a reviewer verdict could still be wrong.
Hermes produced false `PASS` outcomes. Builders returned work that looked
complete and still failed direct inspection. The word “reviewed” was not the
same as “accepted.”

The operating rule hardened: model criticism is evidence; tests and source
inspection are authority. Promotion remained manual.

## Act four: an overnight system learns where it breaks

Across the recorded cycles, the scheduler gained status, budget, capacity,
operations, and decision views. It polled active builds in roughly 5.66–5.76
seconds including RPC, idled for 30 seconds, and scheduled a one-second follow-up
after review. Curated quality history informed later fit decisions without being
mistaken for correctness.

The selected-outcome ledger eventually read:

| Outcome | Count |
| --- | ---: |
| Unchanged | 1 |
| Assisted delivery | 8 |
| Incomplete worker result | 1 |
| Did not reach this worker | 2 |

Through cycle20, 16 JEV calls accumulated USD 0.000538692 in estimated usage and
USD 0.005505024 in uncertain reservations: USD 0.006043716 booked in total.
Those numbers were tiny. Human attention was not.

One builder run produced no source because three JSON actions were cut off at a
2,048-token boundary. Raising the existing allowance to 4,096 let the next real
build return a complete file in 63 seconds. Another failure turned out to be the
parser discarding an exact, valid JSON response. The apparatus had been throwing
away model work and reporting the absence as a model outcome.

Fixing transport did not solve acceptance. The complete candidate still needed
Codex corrections. A clean envelope can carry a flawed letter.

Sources: [overnight chronology](../../fleet/jev/OVERNIGHT-20260921.md),
[worker-action diagnosis](../../fleet/jev/WORKER-ACTION-DIAGNOSIS-20260921.md),
and [action-decoding correction](../../fleet/jev/ACTION-DECODING-20260921.md).

## Act five: compress the critic, expose the limits

The team tried a packet-only Hermes review: no tool-call or tool-result messages,
just the prepared evidence packet. One measured review took 32.433 seconds and
used 5,659 input tokens and 595 output tokens. It still wrote 386 actual words
against a requested maximum of 250 and made unsupported claims.

The format was useful enough to preserve and weak enough to keep opt-in. Smaller
context did not create stronger epistemics.

Source: [packet-only review qualification](../../fleet/jev/PACKET-ONLY-REVIEW-20260921.md).

## Act six: the last comparison never begins

The final authorized lap was deliberately narrow: 22:30–22:50 UTC on September
21, one Dense builder comparison on AM4. At 22:33:14 the facade returned HTTP
401—missing or invalid bearer token—before inference. There was no candidate,
completion, behavioral check, or Hermes review to compare.

The owned model was unloaded by 22:34:11. Shared runner configuration stayed
unchanged. The team did not change credentials, retry, call JEV again, restart a
gateway, or make the route permanent.

This is not the dramatic ending where one more attempt saves the demo. It is the
more useful ending: the experiment stops at the boundary it was given.

Source: [AM4 builder qualification](../../fleet/jev/AM4-BUILDER-QUALIFICATION-20260921.md).

## Epilogue: who gets the final word?

JEV was good at choosing. OMEN was capable of producing. Hermes was useful at
criticizing. Codex could integrate and correct. Derek kept authority over the
consequential choices. The evidence decided what survived.

That is the architecture worth carrying forward—not an autonomous pipeline, but
a set of distinct roles with explicit handoffs and a manual release boundary.
The most important product of the experiment was not a perfect scheduler. It was
a clearer vocabulary for saying what each part had actually proved.

## Production notes for an adaptation

A spoken version can follow six short acts and use the 401 as the cold-open
flash-forward before returning to the simple loop. Keep the tone curious rather
than triumphant. The tension is not “will the AI code?” It is “which claim has
enough evidence to survive the next handoff?”

Pronunciation notes: say “J-E-V,” “HERM-eez,” “O-men,” and “F-X ninety-nine.”
Read monetary values as estimates. Do not imply that the historical final state
is live today.

For fact checking and a complete closeout, use the
[final retrospective](../../fleet/jev/RETROSPECTIVE-20260921.md). Separate Bonsai,
cold-storage, and later fleet work are outside this story.
