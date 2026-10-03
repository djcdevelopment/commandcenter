# 0059 — The dense 27B is served for multi-step work: thinking on, several turns in one long context; small-grain work goes to the lighter seats

**Status:** Proposed (2026-10-03, omen-linux). This one records Derek's direction; the measurement behind it is a first sample, and a longer run was in progress when this was written (see Context).

**Companion to:** ADR-0039 (depth specialists earn pin-only rungs), ADR-0054 (delivery), ADR-0058 (work is routed by grain), `am4-role-intent` (MemSplice). Evidence: `~/work/delivery-plan/evidence/multistep/`.

## Context

Derek's two statements, 2026-10-03 about 06:45 PDT, after he was told that the sizing brief still failed on the 27B and more aids were proposed (verbatim from the memory note `feedback-test-a-model-in-its-own-regime.md`):

> "well we're failing to be good researchers then, 27b is a model that does better over time and at longer context windows for multistep tasks"

> "if we're feeding it almost nothing and giving it no room to itterate in it's context... we're not using it for what it's good for, might as well use a cheaper/lighter model"

A few minutes later, while the first multi-step run was in progress, he added: "b27 is dense and prefills slowly on b70's, that's the entire reason memSplice exists, is because 3.8-b27 is designed for a min 64k context and arguably 128k to give it the most time to think and self refine".

The regime every 27B delivery run had been run in, per the memory note: one call, schema-constrained JSON from the first token, thinking off, at most one revision, 15 to 25K tokens of a 65,536 window; the one-item runs were a few hundred tokens each. The door sets `enable_thinking = false` on every backend in `backends-linux.toml` and has no multi-turn operation; the DeepAgents runner also forces thinking off. Conclusions about "the 27B as author" were drawn from that regime.

First multi-step measurement (`run_multistep.py`: turns notes, verify, draft, critique and a final schema turn, in one conversation, thinking on; per-turn lines in `logs/*.multistep.log`). With a turn budget of 8,000 tokens:

| brief | seat | turn | prompt tokens | completion tokens | finish_reason | reasoning chars | content chars | seconds |
|---|---|---|---|---|---|---|---|---|
| perception (2 sources) | 27B | notes | 6,455 | 8,000 | length | 27,496 | 0 | 983.7 |
| sizing | 27B | notes | 24,482 | 8,000 | length | 27,111 | 0 | 978.6 |

On both briefs the 27B spent the whole 8,000 tokens thinking and wrote no answer; the run stopped there (one line in each log). The memory note adds that inside that thinking it had already worked out a point no one-shot run reached: the sizing checks read rung rows from `backends-linux.toml`, not the configurations file. By division the logs give 8,000 tokens in 983.7 s, 8.1 tokens a second; the memory note says about 11 tokens a second per slot; the two were not reconciled.

The 30B (`omen-vllm`, 40,960 window), same driver and briefs, all five turns:

| brief | notes | verify | draft | critique | final |
|---|---|---|---|---|---|
| perception | 12.2 s | 12.7 s | 10.8 s | 10.8 s | 13.1 s |
| sizing | 34.4 s | 25.8 s | 16.8 s | 35.0 s | 69.9 s, finish_reason length at 4,096 tokens |

On perception the final answer was produced, and the harness then failed on its own check: the manifest was rejected because the aids `multistep:notes+verify+draft+critique` and `thinking` are not known to the contract. What the 30B got wrong in these runs is not recorded in the memory note or the logs; the run directories hold its turns, which this record did not read.

Not in this record: a longer multi-step run, one thinking turn of up to 24,000 tokens per brief (`run_multistep.py` step `work`; logs `perception.omen-dense.work24k.log` and `sizing.omen-dense.work24k.log`), was in progress when this was written. Both log files were empty at that time. Its result is not here.

## Decision (proposed)

1. The dense lane (`omen-dense-27b`) is for multi-step work: thinking on, several turns in one conversation with its own notes in context, a large share of a long window. Small-grain work, one stated value or one item, goes to the lighter seats; that is Derek's second statement and ADR-0058 gives its measured side (at one value per item the 30B and 8B match the 27B).
2. The door needs an operation that can run a seat that way, with the same ledger, receipts and verdict rules as today (ADR-0048: candidates stop at `awaiting_review`; verdicts are human or frontier). It needs: thinking set per call (today it is fixed off per backend); several turns in one context under one work item; an output budget sized for thinking. The 8,000-token turn above was not enough for this model on either brief, and a budget that is generous for other models starved it.
3. Until that operation exists, results for the 27B from the one-call, thinking-off regime are recorded as results for that regime, not as what the 27B can do. The regime (turns, thinking, context used against the window, output budget) is written down with each result.

## Open questions (listed, not answered)

- The 128K window. The sizing map (`docs/sizing-map.md`, line 84) states: "seat 0: window 65,536 · KV 76–99K tok" and lines 15 to 16 that seat 0 came up with 99,048 KV tokens and, on the same drop-ins, 76,706 after the next restart. The memory note: a 128K window does not fit one B70 as configured. The sizing map names the seat `:18091`; this record did not check that it is the seat behind `omen-dense-27b`.
- Prefill time on the B70s with a long context, and MemSplice (prefill on the AM4 CUDA cards, splice the KV to the Arc cards, per the memory note). Prefill time was not measured in these runs.
- A second 27B seat. Branch `wp/two-dense` stages an OMEN profile with the 27B on both B70s (commit `2eea959`, runbook `docs/runbooks/two-dense-window.md` in that branch); it is staged, not applied (rows 106, 110). A switch needs the router restarted and takes the 30B lane away for the window. The door's coverage check for a 27B author would still pick `am4-vllm` (row 110).
- What the turn budget and window should be for a thinking turn; the in-progress 24,000-token run is meant to inform this and has no result yet.
- Whether a thinking turn changes substance results on the briefs that fail today (sizing). Not measured: no multi-step run has yet produced an answer from the 27B.

## Consequences

- If accepted, `enable_thinking = false` ceases to be a backend-wide setting for the dense lane, and the manifest and capability record must name the regime (thinking, turns, context used) as aids or configuration; the contract today rejects unknown aid names (see the harness error above).
- The AM4 27B and MemSplice are covered by `am4-role-intent`; this ADR changes nothing there.
