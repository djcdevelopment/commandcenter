# Delivery reports: budgets, evidence and review

A caller supplies a `brief.v2`; the model returns `summary` and `sections` with paragraph `text` and `quotes`. The renderer stores a `delivery.v1` manifest and a report. Local-work candidates stop at `awaiting_review`. Read the complete report against the pinned source before recording an acceptance or rejection: deterministic success establishes form and reference resolution, not substance.

## Brief options

This fragment selects numbered-source references and a 6,000-token output budget; retain the complete substance criteria and pinned sources in the surrounding brief:

```json
{
  "form": {"quote_mode": "line_reference", "citations": "quote"},
  "generation": {"max_tokens": 6000}
}
```

For local-work, output-budget precedence is explicit submission `max_tokens`, then `brief.generation.max_tokens`, then the existing word-derived default (1.75 tokens per capped word plus 1,536 overhead, floor 2,048; uncapped default 8,192; bounded by the operation ceiling). The explicit or brief budget must still fit the route context and execution policy. Impossible budgets are refused; raising the budget is not permission to truncate a response and call it complete. Quote-heavy inventories need room for JSON and evidence as well as prose.

`form.quote_mode` defaults to `text`. Text quotes resolve exactly or through supported normalization; a fuzzy or elided match is unsupported even when its similarity score is 1.00. A candidate location and source excerpt can assist review but do not populate a resolved location. Literal ellipses actually present in the source can still match exactly. Review the recorded match, candidate location, `unsupported`, repairs and deterministic state separately.

With `line_reference`, each `quotes[]` string must be one canonical repository-relative `path:N`, for example `perception/service.py:123`. No ranges, `/input/` aliases or invented source paths. The model sees numbered source; the renderer checks the declared pinned file and line bounds, echoes the exact original line into the report/manifest, and retains the input as `quote_reference`. Invalid references stay unsupported. To cite several lines, emit several entries. The verification/judge input contains the echoed source text.

An exact reference to a closing brace, unrelated function, or “Exposes:” heading can still accompany a false or incomplete paragraph. These are observed failures, not hypothetical ones. Judge each paragraph's attribution and the full brief's coverage; do not substitute quote counts for a source read.

## Revision adoption

A revised answer is mechanically eligible only when unsupported quotes strictly decrease and no new rung-0 failure kind or finding appears. An exact comparison proves quote-only repairs preserve summary, headings, paragraph text and order. Rewritten prose requires a separate non-author 27B coverage assessment to account for the original summary and claims, with evidence for retained/corrected claims or an explicit withdrawal reason. Both answers and the assessment remain recorded. Missing, invalid, refused or incomplete coverage keeps the original with a named reason; no weaker judge silently replaces the 27B.

The coverage assessment guards revision adoption, not final acceptance. A retained answer still requires a frontier or human substance verdict under the applicable environment policy. Local assessments, deterministic checks and recorded verdicts are distinct evidence.

## DeepAgents schema reports

Run from the DeepAgents checkout using its environment and a fresh destination outside immutable historical runs:

```bash
python run_linux_delivery.py --source /absolute/pinned-checkout/perception/service.py \
  --destination /absolute/new-experiment-directory --task-file /absolute/full-task.txt \
  --backend am4-tool-5070 --report --report-delivery schema \
  --brief /absolute/perception.brief.v2.json --quote-mode line_reference \
  --max-output-tokens 6000
```

The complete task remains in both model stages, even if an old brief's criterion was truncated. The supplied brief preserves all criteria, form and generation settings. It must declare exactly the canonical source path; its commit must resolve to bytes identical to the frozen input. A word-cap override conflicting with the supplied brief is refused. Explicit `--max-output-tokens` overrides the brief generation budget; otherwise existing runner defaults apply. Both the tool loop and schema completion request temperature 0, recorded in the run manifest.

In line mode, filesystem tools can read the same frozen bytes at `/input/<basename>` and `/<canonical/repo/path>`. The canonical alias is readonly, and citations still use repository-relative `path:N`. The run and delivery record the `source_path_alias` aid. This addresses the observed failure where a tool loop searched the citation path instead of its `/input/` copy.

For an intentionally smaller final schema source pack, add `--schema-source-ranges '35-49,55-72'`. This is valid only for line-reference schema reports. Ranges must be positive, ordered, nonoverlapping and within the pinned file. Numbers retain original positions. The tool loop still has the whole frozen source; only the final schema SOURCE block is excerpted. The prompt explicitly labels excerpts, retains the full task/findings, and cannot use absence from an excerpt as evidence of absence from the file. The manifest records scope, ranges, path, pin and source hash, and delivery records `source_excerpt`.

Excerpts are an explicit aid, not a fallback after refusal. The full task, loop findings, schema and output reserve still consume context; a reduced SOURCE block can still exceed the seat limit. Record that refusal. Recheck completeness against the full source, especially when omitted lines were unavailable to the final pass. Inspect both `final-message.txt` and the rendered report: schema conversion can discard correct loop findings. Missing support never becomes a successful run merely because all selected references exist.

## Capability records

Only the main lab-rnd checkout writes its hash-chain registry. A worktree may inspect or use a temporary registry, but must not import into shared state. From the main checkout, after reviewing the evidence:

```bash
python3 research/cli.py import-delivery --run-notes /home/derek/work/delivery-plan/evidence --dry-run
python3 research/cli.py import-delivery --run-notes /home/derek/work/delivery-plan/evidence
python3 research/cli.py show --delivery
```

DeepAgents history is opt-in: add `--deepagents-runs /absolute/exact-run-directory` to import that run, or a parent to select its immediate run directories. No DA history is selected by default. A frontier `substance-review.json` sidecar must bind the run ID, delivery brief hash, raw output hash, grade-file hash and every original criterion statement/status/evidence. Old runs without `brief.json` use the manifest's full task as their sole recoverable criterion.

The display keeps resolved/unsupported counts, deterministic state, per-criterion local assessments and frontier decisions distinct. Missing assessment means unjudged. Multiple judge results and disagreements remain visible. DA frontier assessments are imported as assessments; they do not invent an ADR-0048 local-work verdict. Preserve failed generations and unread reports in the surrounding experiment evidence even when no delivery manifest exists to import.

## Changes on 2026-10-03 after the line-reference lap

Seven items follow. They cover changes to the door and its evidence scripts made on 2026-10-03, under `hearth-env dev`, during the itemized-delivery lap and the laps after it (`docs/rnd-log.md`, the top four rows). Each door change was merged and the gateway restarted without asking Derek; each is listed with what was not verified in `~/work/CLAUDE-APPROVED.md`, rows 98 to 101 and 104. "Rung 0" is the first rung of the verification ladder in ADR-0054: checks that code can make exactly. A "revision" is the second answer the door asks the author for when the first has unsupported quotes. "Unsupported quote" means a quote the renderer could not resolve to a range in the pinned source.

### (a) Rung 0 flags a stated total that disagrees with its list (`stated_total`)

Commits `2ed632a` (added the check) and `610d125` (cold review of it; narrowed it). Code: `_totals` in `hearth/delivery/verify.py`.

What it does. When a paragraph states a count and then introduces a list with a colon (or "namely", or "which are"), and the list has three or more items ending in ", and" or ", or", and the count is not the number of items, rung 0 records a finding of kind `stated_total` with severity `fail`. The finding's detail names the sentence, the stated number and the listed number, and tells the author to state the number that matches or to state no total. The rung fails on any finding of severity `fail` (the module docstring).

What it skips, on purpose, because a false alarm sends a correct answer into a revision it did not need: lists of fewer than three items; lists without a final ", and" or ", or"; items that themselves contain "and" or "or", a number, or a group word; counts qualified by words such as "of", "at least", "first", "other"; unit nouns such as "tokens" or "minutes"; and counts that are the tail of a number such as 3.8 or 4,096. A cross-sentence form ("six endpoints. The GET endpoints a, b, and c ... The POST endpoints d, e, and f ...") was written first and removed in `610d125`, because it was tuned to one stored report and fires on a correct report that lists only part of a stated total.

What it does not catch. A total given as the sum of groups. The lap row for `work_139ce21f` records one: "six HTTP endpoints: two GET routes … and four POST routes", where the source has eight paths in five branches; the check did not fire on that shape. `CLAUDE-APPROVED.md` row 99 records that the check missed a sum-of-groups total again on the next run.

Measured, from the `610d125` commit message: under the first version, 20 of 91 correct sentences fired (examples in the commit: "Six seats in two hosts: omen and am4"; "Eight slots: the fast lane and the deep lane share them"). Under `610d125`, 0 of 95 correct sentences fire, and a replay of 146 stored answers gives 3 hits (5 listed against 7 stated, 6 against 7, 6 against 7), all true mismatches. Neither commit changes a test file; the evidence is the replay recorded in the commit message.

How to see it working: a delivery manifest whose rung-0 verification carries a finding with `kind: stated_total`; the replay over stored answers described above is the check that was run.

### (b) When a revision is adopted

Commit `7207c2f`. Code: `LocalWorkService._better` in `hearth/localwork/service.py`; test `hearth/tests/localwork/test_service.py`.

The first two gates are unchanged: a revision is refused if it introduces a rung-0 failure kind or a rung-0 finding that the original did not have. After those, the revision is mechanically eligible when its unsupported-quote count is strictly lower than the original's, or when the two counts are equal and the revision has strictly fewer distinct rung-0 findings (the recorded reason reads "unsupported quotes equal (N) and rung 0 findings A -> B"). Equal counts with equal findings are refused. Before this commit a revision that only fixed a stated total was thrown away (row 99). Eligibility is still only the mechanical test: the coverage assessment described in "Revision adoption" above must also pass before the revised answer is kept. A test that pinned the old rule (equal quotes, refuse) was changed (row 99).

How to see it working: the manifest's `revision` block, `kept` and `reason` fields.

### (c) The coverage check on a 27B revision runs on `am4-vllm`

Code: `_dispatch_coverage` in `hearth/localwork/service.py` chooses `backend = "am4-vllm" if author == "omen-dense-27b" else "omen-dense-27b"`, and raises "coverage judge unavailable" when that backend is not available. ADR-0054 records that no judge shares a backend or model with the arm it scores.

Consequence, recorded in rnd-log (11:58 row) and `CLAUDE-APPROVED.md` row 100: a 27B revision can be adopted only while AM4 serves its 27B, that is, under the `dense-tp2` profile. Today's window was 12:16:49Z to 12:25:32Z; AM4 was then returned to `tool-pair`. Row 110 notes that a second 27B on OMEN (profile staged on branch `wp/two-dense`, not applied) would not change this, because the door's coverage check would still pick `am4-vllm`. Row 100 asks Derek to know this when deciding AM4's role.

### (d) Blank quote strings are dropped and counted

Commit `46d70b8`. Code: `hearth/localwork/service.py` (before `contract.check_output`), `hearth/delivery/contract.py` (comment on the repair keys).

Constrained decoding lets a quotes list end in `""` or whitespace. Before validation the door removes any blank string from every paragraph's `quotes`, and records the number removed as `repairs.empty_quote_dropped` in the `delivery.v1` manifest. Two answers had been lost whole to this (row 101: "for the second time"; the lap row names `work_60fe8cd0`, failed on blank quotes before the repair; `work_139ce21f` padded 8 blank quotes).

The stored answer is the model's raw one, not the repaired one (row 104). So a re-render of a stored answer must repeat the repair; the evidence script `~/work/delivery-plan/evidence/rerender.py` was patched to do so (row 104: after the patch the negative control passed and exit was 0). Row 104 also says the repair should move into one shared place so that a stored answer and a live one take the same path; that has not been done. Not verified (row 101): the raw failing answer was not stored, so the repair was checked on a hand-made copy of the shape; no separate review was made of the ten lines.

### (e) The task text is capped at 4,000 characters

The frozen envelope keeps a user-visible intent capped at 4,000 characters (D-115, `hearth/operator/envelope.py`); `submit_local_work` refuses a longer one ("intent is 8171 characters; D-115 caps the frozen envelope's user-visible intent at 4000"). The cap limits what a caller can hand to the author as an aid in the intent. In that lap the sizing brief's rule rows could not be handed over for that reason. A fact sheet, the code-written lead sentence and the quote rule all share the same text.

### (f) The execution projection and the door's per-call latency

Commits `47fce27` and `356007c`; decision record `docs/adr/0057-the-execution-projection-is-a-derived-index.md`. The execution projection (`projection.sqlite`) now runs in WAL mode with `synchronous=NORMAL`, with an idle keeper connection; the canonical event stream `events.ndjson` is still fsynced on every append. Measured by the commit author on a scratch door with a stub engine (41 ms): p50 latency at 1, 8 and 16 concurrent calls 273, 1553 and 2679 ms before and 133, 430 and 458 ms after; throughput at 8 and 16 concurrent 4.5 and 4.5 calls/s before, 12.3 and 11.6 after. Measured on the production door (row 98, gateway restarted at 11:49:04Z): a tiny call 2.1 s before and 0.20 s after; three fast seats at once 1.6 calls/s before and 6.2 after. How to see it: `RESULT.md` item 9 in `~/work/delivery-plan/evidence/itemized/`.

### (g) Every backend runs with thinking off, and the door has no multi-turn operation

`enable_thinking = false` is set on every backend in `backends-linux.toml`, and the door's operations are single calls; the docstring of `~/work/delivery-plan/evidence/multistep/run_multistep.py` states "the door sets enable_thinking = false on every backend and has no multi-turn operation (2026-10-03)". The DeepAgents runner also forces thinking off (Derek's memory note, `feedback-test-a-model-in-its-own-regime.md`). So, per the memory note, nothing in the lab's delivery path through the door had run a model with thinking on or with more than one call per answer (plus at most one revision). That is a property of the configuration measured, not of the models; see `docs/adr/0059-the-dense-27b-is-served-for-multi-step-work.md`.

## Item-grain work and fact sheets (caller-side, not yet a door operation)

Between 10:42Z and 13:35Z on 2026-10-03 the lab tested splitting a task into items: code (or a planner model) lists the items, a seat answers one item from that item's source lines, code assembles the answers and counts them, and a judge gates them. All of it ran from scripts under `~/work/delivery-plan/evidence/itemized/` that call the door per item; none of it is a door operation, and the item runs are not capability records (row 96: "itemized caller-side runs are not records until the path is a door operation"). Decision proposal: `docs/adr/0058-work-is-routed-by-grain.md`.

What was measured (`RESULT.md`, `RESULT-tables.md` and the grader files `grades/GRADE-*.md` in that directory; Opus graders read the answers):

- Seven tasks, 110 items at pinned commits; authors the 30B, the 8B on both AM4 cards, and the 27B. With typed fields and one item a call, graded correct and wrong on 77 item ids: 27B 84% and 4%, 30B 68% and 16%, 8B 60% and 18%.
- Coverage and association were exact by construction; the one-shot error of giving one endpoint's parameters to another did not recur on any seat.
- Stated values (14 backend entries): 14 of 14 on every seat. Code behaviour: 36 to 75% on the 30B and 8B, 79 to 100% on the 27B except environment reads (63%).
- One grain finer (rnd-log 11:58 row; `score_v3.py`, `grades/GRADE-truth-v3.md`): one request parameter or response field per item (52 items, truth list audited by Opus, 52 of 52 right): parameter defaults 13 of 13 on every seat; required flags 13 of 13 on the 27B, the 30B and the 8B at four items a call (11 at one a call); response-field presence 36 to 38 of 39. One invariant rule per item (38 items): 27B 28, 30B 14, 8B 9.
- The agreement gate: where the 30B and the 8B give the same answer on a discrete fact, they agreed on 51 of 52 and 50 were right. At repository scale (205 environment reads, truth from the syntax tree, rnd-log 12:51 row): defaults right 182 of 205 on the 30B, 151 and 148 on the 8B seats; where the 30B and 8B agree they are right in 140 of 146. For other discrete fields, `RESULT.md` item 5 records that agreement is not a gate (7 of 27 agreed answers were not correct) except for stated values (11 of 11).
- The 27B gate: of 45 false answers across four arms the 27B judge let 2 through; the 30B and 8B judges let through 1 to 4 per arm plus about a dozen incomplete ones. The cost is that a reader still reads 38 to 65% of items. The AM4 27B as gate on the 27B's rule items caught 7 of 9 wrong ones. The 27B's gate over the 30B's symbol index finished: 2,039 accepted, 180 rejected, 13 without a verdict, in 75 minutes.

The accepted perception report is `work_0916fe9c` (rnd-log 13:05 row; `CLAUDE-APPROVED.md` row 107): written by the 27B (`omen-dense-27b`, lane deep, temperature 0, revision on), graded by one Opus grader (`factsheet/GRADE-factsheet-v7.md`) as meeting all three criteria with no false statement in 16 sentences; a repeat, `work_3f4f1cf1`, was byte-identical. It had these aids, from `run_factsheet_v7.sh` and the grade file: (1) a second source file, `perception/score.py`, on a copy of the brief (`evidence/briefs/perception-2src.brief.v2.json`, whose hash differs from the 2026-10-02 brief; row 108); (2) a fact sheet of value-grain rows, each stated only where four independent readings agreed identically (30B, 8B on each card, 27B), disputed rows marked NOT VERIFIED for the writer to check; (3) a first sentence counted and written by code (the model was told to state no other total); (4) the quote rule restated after the aid text (whole source lines, double quotes written as single quotes, no empty quote); (5) one report rule: a key whose value can be null is still present; (6) nested response fields itemized (for example `data[].id`) and readers asked about the key, not the value. The fact sheet rides in the task text, so the manifest's `aids_used` does not name it (row 94).

Caveats stated in the sources: the aids were tuned on this one brief over four rounds against one grader's gap lists and are untested on any other brief; one grader per report; the deterministic state of the accepted report is `fail` (one short quote of 24 does not resolve; the grader found the statement behind it true); the sizing brief did not reach acceptance (`work_160d61d3` stayed at accept-with-gaps; the comparison criterion asks for something the sizing code does not do, rnd-log 11:58 row); G-delivery stands at 2 of 3 briefs accepted.

Scripts, all under `~/work/delivery-plan/evidence/itemized/` unless noted: `make_items.py`, `plan_items.py` (item lists), `run_itemized.py` (readers), `judge_items.py` (gate), `score_items.py`, `score_v3.py`, `score_envmap.py`, `make_env_corpus.py`, `summarize.py` (tables), `fact_sheet_v3.py` (builds the fact sheet and lead sentence), `run_factsheet_v4.sh` to `run_factsheet_v7.sh` (the four perception deliveries; v7 produced the accepted report), `run_factsheet_sizing.sh`; the driver for door deliveries is `~/work/delivery-plan/evidence/run_delivery_brief.py`. The multi-step driver is `~/work/delivery-plan/evidence/multistep/run_multistep.py` (see ADR-0059).
