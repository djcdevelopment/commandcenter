# 0058 — Work is routed by grain: one stated value per item goes to the fast seats, code behaviour and gating go to the 27B, code lists the items and writes the counts, and a fact handed to a writer has passed a gate

**Status:** Proposed (2026-10-03, omen-linux; evidence `~/work/delivery-plan/evidence/itemized/`, `docs/rnd-log.md` top four rows). Nothing in this decision is built at the door; the item runs were caller-side scripts.

**Companion to:** ADR-0048 (candidates stop at `awaiting_review`; verdicts are human or frontier), ADR-0050 (sizing), ADR-0054 (delivery contract), ADR-0059 (the 27B for multi-step work).

## Context

Grain means how much a single model call is asked to decide: a whole report, one item (one function, one endpoint), or one stated value (one default, one required flag). Fast seats are the 30B (`omen-vllm`) and the 8B (`am4-tool-4070ti`, `am4-tool-5070`).

One-shot reports from the 30B and the 8B fail on completeness and association over a whole file (`RESULT.md`, "The question"): an endpoint's parameters are given to another endpoint. On 2026-10-03 the lab split tasks into items: code or a planner lists them, a seat answers one item from that item's lines, code assembles and counts, a judge gates. What each grain measured (Opus-graded; `RESULT.md`, `RESULT-tables.md`, `grades/GRADE-*.md`):

- Whole-item grain, typed fields, one item a call (77 item ids): 27B 84% correct and 4% wrong; 30B 68% and 16%; 8B 60% and 18%. Coverage was exact in every arm and the one-shot mix-up did not recur. Stated values (14 backend entries): 14 of 14 on every seat. Code behaviour: 36 to 75% on the 30B and 8B; the 27B 79 to 100% except environment reads (63%). Typed fields lifted the 30B (47% to 68%) and made the 8B invent more (wrong 10% to 18%).
- Value grain (one request parameter or response field per item, 52 items; truth list audited by Opus, 52 of 52 right): parameter defaults 13 of 13 on every seat; required flags 13 of 13 on the 27B, the 30B and the 8B at four items a call (11 at one a call); response-field presence 36 to 38 of 39. The 30B and the 8B (four a call) agreed on 51 of 52 facts and 50 were right.
- Rule grain (one invariant rule per item, 38 items): 27B 28, 30B 14, 8B 9. 33 of the misses state the failing condition as the rule.
- Repository scale (205 environment reads, truth from the syntax tree, no grader): defaults right 182 of 205 on the 30B and 151 and 148 on the two 8B seats. Where the 30B and the 8B give the same default they are right in 140 of 146; where they differ, the 30B is right in 42 of 59. The 30B took about five minutes for the inventory plus 757 more index entries, at about 14,000 items an hour; the 27B gates about 2,300 an hour (rnd-log 12:51 row).
- Gates: of 45 false answers the 27B judge let through 2; the 30B and 8B judges 1 to 4 per arm plus about a dozen incomplete ones. Agreement of two fast seats is not a gate in general (7 of 27 agreed answers were not correct) and is one for stated values (11 of 11 at whole-item grain; 50 of 51 right at value grain).
- Listing the items: code enumerators 100% where items are syntactic; the 27B as planner 53 of 53 definitions with exact ranges; the 30B and 8B could not list items (names right, ranges adrift, or they list until the token limit).
- Writing from facts: an ungated fact sheet put its one wrong fact (`region` required) into the 27B's report; gated by the AM4 27B the error went. The 27B stated its own totals wrongly in new shapes, and a prompt rule against stated totals did not stop it (rows 94, 95). The perception report built from value-grain facts that four readings agreed on, with a code-counted first sentence, was accepted: `work_0916fe9c`, with `work_3f4f1cf1` its byte-identical repeat (row 107).

## Decision (proposed)

1. A task whose answer is one stated value per item (a default, a required flag, a key's presence) is split into items and goes to the fast seats. A value is stated in a deliverable only where independent readings agree; the accepted perception report used four (30B, 8B on each card, 27B) and marked disputed rows NOT VERIFIED for the writer to settle from the source.
2. Code behaviour, and the gate over any item answer a deliverable will rest on, go to the 27B (`omen-dense-27b`). Fast seats do not gate: at whole-item grain only the 27B kept false answers out.
3. Where items are syntactic (definitions, environment reads, endpoints), code lists them. Where code has no enumerator, the 27B lists them, and a fast seat does not.
4. Counts and the first sentence of a report are written by code from the item list. The model writes prose and exact quotes (the ADR-0054 split, one level up). Rung 0 `stated_total` (`docs/delivery.md`) catches a wrong total in a shape it knows and not a sum of groups.
5. A fact handed to a writer must have passed a gate. An ungated fact carries its error into the report.

## Consequences

What must exist at the door for item runs to leave capability records (from the gaps named in the sources; none is built):

- An item operation: a list of items with their lines, a batch size, typed fields per item kind, a reader seat, the agreement rule and the 27B gate, assembly by code. Row 96: itemized caller-side runs are not records until the path is a door operation.
- A way to name the fact sheet in the manifest. It rides in the task text, so `aids_used` does not name it (rows 94, 107; rnd-log 13:05 row).
- Room for aids in the request. The door caps the task text at 4,000 characters (D-115, `hearth/operator/envelope.py`: an intent of 8,171 characters was refused); the sizing rule rows could not be handed over for that reason.
- Seats that can take the calls. FX99 refuses schema calls (`fx99-vllm` does not declare `structured_outputs`, row 87), and whole files do not fit the 5070's 16K (door refusal on a 57,344-byte budget, `RESULT.md`).
- Door capacity: at the time of the item runs the door's per-call cost capped small calls; see ADR-0057.

Open risks named in the sources:

- The aids were tuned on one brief (perception) over four rounds against one grader's gap lists; they are untested on any other brief. The sizing brief did not reach acceptance (`work_160d61d3`; its comparison criterion asks for something the code does not do).
- One grader per report. The deterministic state of the accepted report is `fail`: one short quote of 24 does not resolve; the grader found the statement behind it true.
- The rule against stated totals was tried on one sample at temperature 0 on one brief (row 95). Temperature above 0 for item authors is not sampled. The 8B at four items a call gave the better required-flag result on perception (13 of 13 against 11) and the worse result on the environment inventory (80% against 88% on literal defaults).
- The 30B cannot be kept busy with items: they drain it in minutes (rnd-log 12:51 row), so the card idles.
- Rule-grain truth is the Opus grader's reading, not a file (row 102).
- Not sampled (rnd-log 10:42 row): the 27B at more than one item a call; `summarize-tick` and `backoff` as items (no enumerator). The 30B's symbol index is ungraded by a frontier reader.

## Added 2026-10-04: the item operation is built under dev (acceptance of this record stays Derek's)

`procedure="items"` at the door (`docs/delivery.md`, "The items procedure"; lap `~/work/delivery-plan/laps/20-items-at-the-door.md`): code
lists the items, the `fast` and `tool` lanes' seats read each one, the `deep` seat judges the disputed ones, code writes
the report and the counts, the work stops at `awaiting_review` and leaves a capability record with its readers. First
kind: environment reads.

What the build measured, and where it departs from the decision above:

- **One item a call**, not four: on 189 reads with a truth from the syntax tree the 30B is right on 94.7% at one a call
  and 87.8% at four, and four is not faster (`lab-rnd research/evidence/seat0-short-calls-20261004/RESULT.md`).
- **The settler judges with thinking on.** Decision 2 says the gate goes to the 27B; measured on the 50 rows the two
  readers dispute, a third plain reading got 45 of 46 right, a thinking-off judge 45 of 50 (it never said "neither"),
  and the thinking judge's own stated default was right in 49 of 49 answers (ADR-0059's regime).
- **The first scorer understated every reader** (it compared string spellings, took the first call in a statement as
  the truth, and counted 16 assignments as reads): the 30B is at 94.7%, not 89%.
- **The first live run was rejected** (`work_4b0569a8`, 91 reads in 24 files, 181 s; `~/work/delivery-plan/evidence/wave9/`).
  The 16 judge-settled rows and the 2 NOT VERIFIED rows were right. But 7 of 89 verified defaults were wrong by the
  brief's definition (five `get(X) or <value>` reads where both fast readers answered "none"), and the one-line
  "what it controls" sentence, which no gate reads, was false in 3 rows and vague in 22. That is decisions 2 and 5 of
  this record seen from the other side: a fast seat's sentence about code behaviour reached a deliverable ungated.
  Two fast seats agreeing is a gate for a stated value and for nothing else.
- Changed after that run: the enumerator marks the `or` shape and the prompt says where to look; a row cites the read's
  own line; the ungated sentence is no longer delivered; the summary says what the enumerator cannot see (reads through
  a helper function).

Still open from the consequences above: the task-text cap; a behaviour field per item gated by the 27B; item kinds where
code has no enumerator.
