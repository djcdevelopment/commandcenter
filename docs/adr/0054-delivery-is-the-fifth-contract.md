# 0054 — Delivery is the fifth contract: the door owns one renderer between the model's answer and the verdict; the model writes prose and exact quotes, code writes ranges and counts

**Status:** Proposed (2026-10-03, omen-linux; plan `~/work/delivery-plan/`, task 1). The contract documents exist and
self-check (`hearth/delivery/contract.py`, `python -c "from hearth.delivery import contract; contract.selfcheck()"`);
the source map is task 2, the renderer task 3, door integration task 4, the re-run task 11.

**Companion to:** ADR-0048 (candidates stop at `awaiting_review`; verdicts are human or frontier), ADR-0050 (sizing),
ADR-0053 (environment axis; every manifest records it), lab-rnd ADR 0007 (acceptance is recomputed from artifacts).
ADR-0055 is reserved for the cloud judge instrument (task 7) and is not decided here.

## Context

The lab's fixed contracts are door, brief, receipt, verdict. Between the model's answer and the verdict there was a
hole, and each lane filled it alone: `lab-rnd/daily/normalization.py`, `deepagents-linux/run_linux_delivery.py::normalize_citations`,
and string-citation parsing in `hearth/localwork/service.py`. The 2026-10-02 review found every candidate right on substance and
wrong on form: invented prose line numbers, word caps, citation spelling, one arithmetic slip. Briefs mixed substance and form in
prose ("strictly under 320 words. Cite actual line numbers"), so models were graded on jobs that are not model jobs.

## Decision

1. **Delivery is the fifth contract**, owned by the door, so every lane inherits it. Three documents:
   `brief.v2` (what to deliver), `delivery-output.v1` (what the model writes), `delivery.v1` (what the renderer recorded).
2. **Substance and form are separate in the brief.** `substance[]` holds closed statements a judge can answer yes/no;
   `form` holds `words{min,max,enforce}`, `citations`, `sections`, `style`. `sources[]` pins path + commit; `aids[]` names the
   set-up the run is allowed (source map, quote renderer, constrained output). The front block is JSON in the CCMETA comment; an
   absent `schema` is v1 behaviour, so every existing brief stays valid.
3. **The model writes prose and exact quotes. It never writes a line number, a word count or citation syntax.**
   The output schema is closed (`additionalProperties: false`) and handed to the engine as a constrained-decoding schema.
4. **One renderer** resolves each quote to `path:start-end` against the pinned commit, measures words and sections, applies form,
   and records every repair as a count in the manifest. A quote it cannot find is `missing` and `unsupported`; it is never
   silently dropped or fuzzed into a pass.
5. **Repairs are counted, not failed.** `enforce: "measure"` (default) records deviation; `"fail"` only where the destination
   truly enforces the limit. A repair is a data point about the configuration, not a model defect.
6. **Verification is a ladder ordered by who is good at what:** code-checkable facts to code (exact, free); closed-rubric
   checks with evidence present to a calibrated System One judge; open or long-context reasoning to an LLM reviewer (local 27B
   first); consequential calls to Derek. A judge is never the acceptance authority (ADR-0048); its probability routes work.
7. **Form rules (index decisions 1-6, frontier 2026-10-03; Derek may overrule).**
   (1) Words are whitespace tokens of the rendered body (summary + paragraph text), excluding headings and the citation
   strings the renderer emits; `measures.words` records that number and `form.words` is compared against it.
   (2) `form.sections` is advisory: presence and order are measured and deviation recorded; no `enforce` of its own in v2.
   (3) `form.citations`: `range` renders `path:start-end`; `quote` renders the quote text followed by `(path:start-end)`;
   `none` renders no citation while the manifest still carries every claim and range.
   (4) No per-claim substance link in `delivery-output.v1`: a paragraph's quotes support that paragraph; the manifest has
   one claim row per (paragraph, quote). Task 9 may add an optional `substance_id`.
   (5) `sources[]` is authoritative for the renderer and `locate`; the prose header's `repo:`/`commit:`/file lines stay for
   the drain, and task 4 generates `sources[]` from the header so the two cannot drift.
   (6) A quote found more than once is rendered at one hit (the claim's symbol, else the first) with `ambiguous: N` on the
   claim and counts as an `ambiguous_quote` repair; a quote under 24 characters (whitespace collapsed, HTML entities decoded)
   never matches line-window fuzzy and, found more than once by any pass, is `missing` with `ambiguous: N`, counted as a
   `short_ambiguous_quote` repair (found once, it resolves). The claim's symbol is one named in the paragraph by a dotted
   prefix or part (`configuration.day`, `day`); the name naming the fewest hits wins (one every hit shares
   counts for nothing), then the smallest symbol; a tie keeps the first hit.
8. **Every run leaves a capability record** (substance verdict, repairs, aids used, configuration, environment): the
   per-configuration record the router is meant to read.

## Consequences

Each is a lesson from the lever ledger (`~/work/lever-ledger/dist/COMMANDCENTER-LEVER-LEDGER.md`, judges section) and binds the
tasks that follow.

- **The rubric dominates the judge.** One rubric spread arms by 9.9 points, a neutral one by 2.9; cross-model disagreement was
  no larger than cross-rubric. So substance is closed, explicit, per-claim statements, never "is this good?".
- **No judge shares a backend or model with the arm it scores** (held-out panel rule since 2026-09-03). The 27B does not judge
  its own deliveries; the local twin is a different model or a verifier class.
- **One vote per claim.** Repeat votes bought nothing (within-cell std 0.40 over 6 repeats, 171/192 identical).
- **Degrade loudly.** A judge that is unavailable yields `unverified` with a banner, never `pass`; a silent drop to one
  perspective hid a 503. The manifest has no state in which an unrun rung can read as passed.
- **Prove switches on the wire.** `enable_thinking=false` never reached the wire (9,691 of 11,733 output tokens were reasoning)
  and capped thinking models truncated JSON mid-string while reporting success. The schema and the thinking switch are proven by
  usage counts and the returned bytes, not by the request that was sent (task 4).
- **One bounded objection round.** The open-ended critic never converged (0/139 cells); the adversarial reviewer is a single
  objection round against closed criteria (task 9).
- **Fit is not correctness; truncation is a sizing bug.** The 2026-09-21 Jev experiment closed because fit scores never
  measured correctness, and its 2,048-token JSON writes truncated until the ceiling rose to 4,096. Constrained decoding with
  bounded arrays and strings (`output_json_schema()`) plus ADR-0050 sizing is the general fix.
- Three lane-local fixers collapse into one renderer; their string-citation patches become dead code once task 4 lands.
- The candidate schema `local-work-candidate.v1` is untouched here; task 4 changes it, citing this ADR.
- Scope (index decision 3 for Derek): prose deliverables first; code candidates keep receipts and ADR-0048 verdicts.
- First-hit ambiguity is a known weak spot: in the example, `os.replace(tmp, p)` occurs twice in
  `fleet/bankedfire_linux.py`; only the claim's symbol (`add_skip`) picks the right hit, the first hit is the slots writer.
