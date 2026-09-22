# Short Hermes reviews: delivered findings, unreliable verdicts

Two real report reviews completed on the unchanged AM4 Dense 27B seat through
Hermes on FX99. This is useful delivery evidence, **not qualification of an
automatic quality gate**. No prompt-format change was deployed to the scheduler.

| Input | Saved result | Duration | Independent interpretation |
| --- | --- | --- | --- |
| Corrected capability report at `b48eaae` | PASS, 59 words | 14.065s | Consistent with supplied source evidence; not a live qualification of every registry capability. |
| Actual rejected OMEN draft from cycle 8 | PASS, 93 words, with defect findings | 17.576s | False PASS. The findings themselves identify material unsupported or contradictory claims. |

Raw, unedited model outputs: [corrected-report review](evidence/20260921-route-short-review.json)
and [rejected-draft review](evidence/20260921-route-short-negative-review.json).
Exact private input packets and lifecycle results remain under
`C:/Users/derek/.fleet-scheduler/route-review-cycle9/` and
`C:/Users/derek/.fleet-scheduler/route-review-negative-cycle9/`; packet hashes are
included in the published review metadata. They are not production configuration.

## What changed

The request required a verdict first, at most three findings, no process narrative
or tool lookups, and at most 130 words. Evidence used selected parsed registry
records and exact function excerpts, plus the dated pilot/runtime facts. Both
runs used `reasoning=none`, a 60-second application budget, and the existing
128k/one-slot reviewer. No KV reuse, model swap or hardware/topology change.

The prior failed review packet was 16,822 bytes. These packets were 14,373 and
13,136 bytes. Candidate content, evidence selection and instructions changed;
the pair does not isolate a speed effect from any one lever. The corrected
report's review was saved at **09:19:28 UTC**, inside this lap's 09:22:36
first-artifact target. The second was saved at **09:21:04**. Both seats were
released immediately afterward. The negative result ended the lap.

## What the rejected-draft review got right and wrong

Hermes correctly identified conflated DeepAgents statuses, an unsupported
`mode:test` assignment, and historical AM4 reader settings labeled as current.
Those findings require correction; a leading PASS contradicts them.

It also called `review_second_rung` unsourced. That identifier actually exists in
`hearth/etc/loops.toml`; the compact pack omitted its record. This demonstrates
a limitation of the supplied evidence, not a fabricated identifier in the draft.
The reviewer should have scoped uncertainty accordingly. Its final remark about
the single-route pilot is not a demonstrated defect: the fixed `route()` graph
and constants support the report's limited statement about this pilot.

The negative response used four findings despite the requested maximum of three.
Both responses stayed below the word limit. The earlier commentary's “66-word”
count was incorrect; whitespace-delimited counts of the saved outputs are 59
and 93. No model output was edited to repair its label or explanation.

## Operating consequence

Use these local reviews as **advisory criticism**, not acceptance signals.
Keep candidate execution/source checks and manual promotion separate. A fast
verdict is not useful quality evidence by itself, and compacting sources can
remove the very evidence needed to judge a claim. Preserve raw findings and
omissions; do not repair a model verdict silently or add a keyword heuristic
that pretends to establish correctness.

This lap added no tests, controller code, deployment or cloud calls. JEV gates
and the scheduler's default review behavior remain unchanged. The broader
throughput/quality goal remains incomplete.
