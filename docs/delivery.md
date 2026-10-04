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

## How the door chooses the procedure

A delivery runs one of three procedures: `one_call` (one constrained call writes the whole answer), `carry` or `items` (both below). A brief that declares `items` takes the items procedure; for any other brief the choice is between the first two. A caller that passes a brief and no `procedure` lets the door choose; `procedure="one_call"` or `procedure="carry"` pins one, and a pinned submit is exactly what that procedure was before the door could choose (same prompt, budgets and refusals).

The door reads a table, `delivery-procedures.v1`, from the file named by `HEARTH_DELIVERY_PROCEDURES`, at every submit (a new table needs no restart). The table counts accepted and rejected verdicts from the lab-rnd registry per backend, per task family and over all families. The rule: a procedure qualifies at a level when it has at least `rule.min_accepted_briefs` distinct accepted briefs (2 today). The door looks first at the task family on the selected backend, then at the backend's `all`; among qualifying procedures it takes the higher accepted share, then more accepted verdicts, then `one_call`. No table, no entry for the backend, or nothing qualifying gives `one_call`, level `none`. A table file that exists but is not a valid table refuses the submit.

What the door does not leave to the table: a submit with `max_tokens` or `revise=True` is `one_call` (level `caller_argument`); an idempotent retry reuses the procedure recorded for that work. A chosen `carry` that cannot run here falls back to `one_call` and records why: the lane is not `deep`, the backend declares no `deliberate_max_tokens` of at least 24,576, `form.quote_mode` is not `text`, or the carried prompt does not fit the context. A fallback is not an error.

The lane follows the same table. For a delivery with `lane="auto"` and nothing pinned (no procedure, no `items`, no
`max_tokens`, no `revise`), the lane is first picked by task family and source size as for any work; when that lane's
backend has no qualifying procedure and the deep lane's backend has one, the delivery takes the deep lane and
`route.lane_choice` records from, to, the level, the counts and the table's hash. A retry keeps its stored lane. When
the deep seat is missing or retired the picked lane keeps the work and `lane_choice.declined` says why.

Every delivery's work manifest records `route.procedure` (`carry` or `one_call`) and `route.procedure_choice`: `by` (`door`, `caller` or `retry`), `level` (`family`, `backend`, `none`, `caller_argument`, `pin` or `recorded`), the `rule` and the `counts` of the level used, `table_sha256`, and `fallback` when there was one.

The table is generated, never edited by hand: in lab-rnd, `python3 research/cli.py export-procedures --out <file>` (byte-identical for the same registry). The tracked copy is `host/omen-linux/hearth-production/delivery-procedures-linux.json`, deployed to `~/hearth-production/` as a `tools/ops/host_config.py` pair; the drop-in `hearth-production.service.d/delivery-procedures.conf` sets the variable.

## The carried procedure

`submit_local_work(..., brief=..., procedure="carry")` pins it; the door also chooses it from the table (above). It runs only for a delivery with `quote_mode` text on the deep lane, on a backend that declares `deliberate_max_tokens` of at least 24,576 (today `omen-dense-27b`); pinned, `revise=True` and an explicit `max_tokens` are refused at submit with a named reason. The driver takes it as `run_delivery_brief.py --procedure carry`. Origin and grades: `~/work/delivery-plan/evidence/multistep/RESULT.md`, "Carry the draft".

Three stages, one `inference.deliberate` job at a time, each recorded in the work manifest's `carry` field (`stage`, `batch`, `retried`, `jobs`, `quotes`) before it is dispatched:

1. `work`: one thinking-on turn (24,576 output tokens, the work's deadline) writes verified notes under a line `Verified notes` and a report in plain prose under a line `Report`, from the task text and the numbered source. A draft with no `Report` heading fails the work: the notes carry the model's own line numbers in forms no pattern can tell from values, so they are never delivered.
2. `attach`: code splits the draft into blocks (`hearth/delivery/carry.py`; a paragraph over 2,400 characters is split at sentence ends), takes the report part (the last heading that names the report, and everything after it) and cuts each text block into sentences (`carry.units`): a paragraph gives one unit per sentence, `[block 3.1]`, `[block 3.2]` …; a list item, table, code fence or one-sentence paragraph is one unit. Where a cut is doubtful (an abbreviation, a decimal, a file name, inside backticks, brackets or quotation marks) there is no cut. Eight units at a time go to a thinking-off call that only attaches exact source quotes to each sentence. Its budget is `min(4096, 512 x units + 1024)` tokens: a report whose paragraph is one long sentence must still get room to answer (600 tokens for one unit was cut off). The attacher sees the units as written and the notes as guidance (they name source lines); it may not quote from the notes. The work manifest marks this with `carry.units: "sentence"`.
3. `render`: code assembles `delivery-output.v1` from the report's blocks, unchanged except for any line reference the model wrote in them, which is stripped and counted, and the existing renderer resolves the quotes. A paragraph is delivered whole when its sentences' quotes together (duplicates removed) number 8 or fewer. With more, it is delivered split, one paragraph per sentence, each with its own quotes (a sentence without a quote joins its neighbour), and `paragraphs_split_by_sentence` counts the blocks split; a split that would put more than 24 paragraphs in a section is not made, and quotes beyond 8 a paragraph are dropped and counted as before. The notes stay a work artifact (`carry-draft.md` holds the whole draft) and are counted in `repairs` as `notes_blocks_not_carried`.

The work stays `queued` or `running` until `render` ends at `awaiting_review`. The delivery manifest carries `procedure: "carry"`, `aids_used` ending in `thinking` and `carried_draft`, and the procedure's repairs as counts beside the renderer's: `line_reference_stripped`, `quotes_beyond_cap_dropped` (more than 8 quotes on one paragraph), `quote_over_limit_dropped`, `json_unescaped_quote`, `blocks_empty_after_stripping_dropped`, `headings_without_paragraphs_dropped` (a zero count is omitted). Besides the usual files, `get_local_work_artifact` serves `carry-draft.md` (the visible draft), `carry-reasoning.txt` and `line-references.json` (each stripped reference, and whether a quote of the same paragraph resolved to a range that covers it).

What fails loudly: a thinking turn that is cut off or fails fails the work and keeps the partial draft and reasoning; an attach answer that is cut off or that names the wrong units is retried once as two half batches (8 units become 4 and 4), a one-unit batch is not retried, and a second failure fails the work; a work found in stage `attach` without `carry.units` (dispatched by block before a restart) fails by name; a gateway restart during any stage closes the job (`recover_pending` never replays a deliberate turn) and the work fails at the next reconcile. The failure names the stage and batch in `failure` and in `carry.failure`, the draft stays on disk, and nothing falls back to the one-call path or renders a partial report.

Blank quotes: the renderer owns the repair. `render` and `verify` drop a quote that is `""` or whitespace from a copy of the answer before validation and count it once as `empty_quote_dropped`; the stored `delivery-output.json` is the model's raw answer, so re-rendering or verifying it gives the same counts, and the ladder reads answers the same way.

**The check turn.** Between the work turn and the attach pass the door continues the same conversation: the draft goes
back as the model's own answer and an instruction (`hearth/prompts/local_work_delivery_check_v1.txt`) asks it to check
its report against the code and write the corrected report. The turn thinks with a budget computed from what is left
of the window (at most 24,576 tokens; under 12,000 the work fails at `check`). `carry-draft.md` stays the work turn's
answer; `carry-check.md` is the check turn's answer and `carry-checked.md` the draft's notes with the corrected report,
which is what the attach pass and the renderer use. The manifest's `carry.check` records the state (`on`, `off`,
`skipped`), the turn's tokens and how many statements changed; the delivery manifest's `aids_used` gains `self_check`
and its repairs `check_statements_changed`. A check that is cut, fails, returns no report or returns one under a third
of the draft's length fails the work at `check`, named, with the partial answer kept. `HEARTH_CARRY_CHECK=off` (read at
submit) runs the procedure without the turn; a work whose prompt is too large for a second turn runs unchecked and its
manifest says `skipped` and why. Measured through the drain on 17 reports (lap 21 Wave 5, `~/work/delivery-plan/evidence/wave5/RESULT.md`): the
turn takes a median 3.9 minutes (the work turn 3.5); on the twelve held-out briefs the carried rate went from 8 to 9 of
12 and false statements from 1 to 0; it fixed four decisive statements in three open drafts, damaged none a grader
found, and recovered no omission. The instruction is the fourth of four probed (`evidence/check-probe/`): it changes a
statement only where the code contradicts it for some input and does not count words (a word-limit line sent one turn
into counting until its budget ran out).

A line number the model writes into its report is removed by code and counted (`line_reference_stripped`). Besides the
written forms (`L141`, `lines 12-14`, `(198-202)`), a bare number in parentheses right after a code name, such as
`collect_backends (192)`, is removed when the number is 10 or more, the name looks like code, a declared file has that
name on the line or the one beside it, and no line holding the name also holds the number (then it may be the value).
A value stays: a single number in parentheses after a code name is kept when a declared code line (comments and docstrings
excluded) holds that name with that number in a value position (`psm=6`, `.get("psm", 6)`, `range(2)`). What no rule can
tell apart: a name whose value also equals a line it appears on.

## The items procedure

For work whose answer is one stated value per item (ADR-0058). A brief declares `"items": {"kind": "env_reads"}` and
`form.quote_mode` `line_reference`; with no procedure named the door takes `items` (`procedure_choice.level`
`brief_items`), and `procedure="items"` pins it. `max_tokens` and `revise` are refused with it.

1. **Code lists the items** (`hearth/delivery/items.py`): for `env_reads`, every `os.environ.get`, `os.environ[...]` read and
   `os.getenv` in the declared Python files at the pinned commit. A write (`os.environ[X] = ...`) is not an item. At most
   384 items (one delivered paragraph each); the declared files must fit the delivery source cap (1,048,576 bytes).
2. **Stage `read`**: the route profile's `fast` and `tool` seats each answer every item, one item a call, schema-constrained,
   thinking off, from that item's lines only. Twelve jobs are in flight at a time. A reader that fails more than 10% of
   its items fails the work, named.
3. **Stage `settle`**: where the two readings differ (a string prefix and doubled backslashes are spelling, not a
   difference), the `deep` seat judges with thinking on (`inference.deliberate`, 8,000 tokens): it is shown the item's
   lines and both readings and states the default itself. The row is settled when the judge's own default equals one of
   the readings. A judge call that fails or gives no parsable answer leaves the row unverified; more than half failing
   fails the work.
4. **Render**: code writes one paragraph per item with one quote `path:line` (the line of the read itself), the summary
   and every count. A verified row states the variable, its place and its default, nothing else: the readers' one-line
   "what it controls" sentence passes no gate, so it stays in `items-readings.json` and is not delivered. A name that is
   not a string constant is shown as its expression as written with "(a name computed at run time)", never as a string
   taken from inside it. A row no two sources agree on is delivered as `NOT VERIFIED` with
   the readings shown, never as a value. The summary says which reads the enumerator cannot see (a helper function,
   `setdefault`, `pop`, a membership test).

Where a read has no second argument and sits in an `or` chain, the enumerator marks it and both prompts carry a note
saying the value used when the variable is unset is what follows the `or`: code points, the model reads.

A read that code can see is unusual carries a `judge` mark and goes to the thinking judge whatever the two readers say:
a name that cannot be resolved to a string constant (`computed`), a default that is a conditional or boolean expression
(`conditional_default`), a value reassigned on the following lines when unset (`later_fallback`). For such a row the
judge is asked for the default first and shown the readers' answer after; the row is verified only when the judge's
own default equals theirs, and otherwise is delivered NOT VERIFIED with the readings and the judge's value shown. A
loop over a literal tuple of names is listed as one item per name. `judged_by_mark` in the manifest counts these rows.

The delivery manifest carries `procedure: "items"` and an `items` object (`items`, `agreed`, `settled`, `unverified`,
`reader_failures`, `judge_failures`, `files`, and `readers` with each seat's role and call count); the work keeps
`items.json` and `items-readings.json` (every reading and judgment per item). The capability record carries the readers
and the counts.

Measured on 189 reads with a truth from the syntax tree (2026-10-04, `lab-rnd research/evidence/seat0-short-calls-20261004/RESULT.md`):
the 30B alone is right on 94.7%; rows the 30B and the 8B agree on are right 97.4% of the time; on the rows they dispute
the thinking judge's default was right in 49 of 49 answers, where a third plain reading got 1 of 46 wrong and a
thinking-off judge 5 of 50. Rows both readers agree on wrongly are seen by no settler: in the first live run
(`work_4b0569a8`, rejected) that was 7 of 89 verified rows, five of them `or` reads both readers gave as "none".

Limits: a restart of the gateway fails the judge calls in flight (a thinking call is never replayed); a paused dispatch or
a full execution queue fails the work at its next refill; the `lane` argument is ignored (recorded as requested).

**Kinds.** `env_reads` (above) and `param_defaults`: every function parameter that has a default, in every `def` and
`async def` of the declared files (lambdas excluded); the row states the default exactly as written in the signature and
cites its line. Defaults are compared as Python tokens, so `'x'` and `"x"` are one default and `None`, `0`, `False`,
`()`, `x` and `'x'` are all different. A default that is an expression, or a number written in a form a reader might
rewrite (`0o644`, `200_000`), carries a `judge` mark. An answer that does not parse as one Python expression (a reader
that copied the annotation, `dict | None = None`) is never an agreement: the row goes to the judge, and is verified only
when the judge's own default equals the readers'. Held out (lap 21): 118 of 119 verified rows right before that rule,
118 of 118 after it. Each kind is one entry in the registry in
`hearth/delivery/items.py` (enumerator, fields, compared field, prompts' wording, row text, what it cannot see).

## Code candidates: what changed, and a validator

A `whole_file` candidate is compared with its base file when it is validated (line endings and the final newline
normalised) and the work manifest records `changes`: the hunks' line ranges on both sides and the lines added and
removed. A candidate identical to its base fails the work. The morning report prints the hunks (`whole_file, 3 hunks:
53, 84-85, 326 (+3/-4)`), so an unrequested change is visible before anything is opened. An ordinary work's waiter now
follows its job to the end (it used to return at once, leaving the manifest `queued` until someone asked).

`tools/local-work-validate WORK_ID --test "<command>"` fetches the candidate through the door, applies it in a shared
clone at the base commit (never a live checkout), runs the command on the base and on the candidate with every
`HEARTH_*` variable removed, and writes the JSON evidence a verdict needs: the diff stat, the hunks, both exit codes. `--candidate-file` validates content taken
from elsewhere (a failed work's result artifact).

Held out (lap 21 Wave 5, four fixes through the drain): two accepted, each a two- or three-line change; two missed by a
change nobody asked for (a function rewritten, 52 lines for one field; an unrelated line broken in a whole-file rewrite).
A citation whose path is not declared fails the work without a repair attempt (only a citation's shape is repaired);
one fix failed that way when the model cited the intent's `[goal:...]` tag.

## Night briefs that deliver

A Banked Fire local-work brief submits a delivery when its front block names a `brief.v2` file and pins its bytes:

```
repo: /home/derek/work/commandcenter-linux-flash
paths: [host/lab-configurations.toml, tools/ops/sizing_map.py]
task_family: code_review
delivery_brief: /home/derek/work/delivery-plan/evidence/briefs/sizing.brief.v2.json
delivery_brief_sha256: 8dee295d932b7df0839f3f7f40b6521b5844f8871671883ff0850e9f5cca418a
```

`delivery_brief` is absolute or relative to `repo`; `delivery_brief_sha256` is the sha256 of the file's bytes, and a file that no longer matches refuses the dispatch. `task_family` is required and `criteria` must be absent (the criteria are the brief's `substance` statements); `paths` are the sources. The drain submits `artifact_kind` markdown with the brief, `lane` (default `auto`), `task_family`, `deadline_s` and `max_tokens` only when the block gives it; it never names a procedure, so the door chooses. A brief with no `lane:` line counts against the deep cap, except one
whose brief.v2 declares `items`: the door seats every items run on the fast lane, so the drain counts it there. The idempotency key ends with the first 12 hex digits of the brief's sha256.

The morning report shows a delivery's procedure, deterministic state, quotes resolved and the path of its `candidate.md`. It never reconciles a staged work (a manifest with a `carry` or `items` block): it shows the stored file, with the stage and batch of one in flight, because reconciling there could dispatch the next stage from the wrong process.

A night brief for a code fix that names one path and no `artifact_kind` asks for the whole file when the file is under
24,000 bytes at the commit (`max_tokens` 12,000), and for a diff otherwise; a named kind is never overridden.

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

## Item-grain work and fact sheets (the caller-side measurements of 2026-10-03; the door procedure is "The items procedure" above)

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
