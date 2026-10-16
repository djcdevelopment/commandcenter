## HEARTH method: using the local seats well

The local models on this hardware do real work when they are set up to succeed. Your job is to find how
to use them, not reasons to fail them. Lead every review with what the model got right and what it is
usable for.

**1. Delegate by naming the task family.** Write a standalone brief and name the family; the door
picks the seat, thinking, budget and recovery from the family's authored regime
(`plan_execution(operation=..., task_family=...)` shows it as `family_recommendation.regime`).

| Work | Call | What the door does |
|---|---|---|
| Judgment: review against a rule, trace control flow, recommend a bounded fix | `submit_execution(operation="inference.deliberate", arguments={"task_family": "code_review", "messages": [...]})`, then `watch_execution` / `get_execution_artifact` | 27B, thinking on, 24,576 tokens, think-then-emit; about 5 minutes |
| Code from a design you already wrote | `submit_local_work(..., task_family="code_fix", artifact_kind="whole_file")` | 27B, thinking off, one call; stops at `awaiting_review` |
| Summaries, extraction, classification | `local_generate(task_family=...)` | the family's seat |

Judgment is in scope for delegation: with thinking on, the 27B traced a cloud fallback and recommended
the right fix where a thinking-off call had missed it (amux receipt LOCAL-REVIEW-THINKING-RERUN-20261014).
Do not pass `thinking`, `backend` or a budget (`policy.max_tokens` included) unless you are testing a
lever on purpose; a pin overrides the family and is recorded. The division that works: you design and verify (compile, tests, a
live check); the 27B writes the code.

**2. Brief.** The model sees nothing else. Substance criteria first, then form limits. State the house
rules that bear on the answer (for example "no cloud fallback"). Give the exact output form. Supply the
code it must read, line-numbered. An output cap in words implies a budget of about 1.75 x the words.

**3. Review protocol.** Put every miss in one bucket, and name the bucket:
- **form** (wrong date format, citation off by a line, extra prose): a sidecar or you repair it; count
  the repair; it is not a verdict.
- **setup** (thinking off for judgment, budget too small, rule missing from the brief, excerpt missing
  context, wrong seat): change one lever and rerun.
- **substance** (a wrong answer under a fair setup): only this counts against the model. Write it as
  "capable of X, not yet Y".
Never write "failed" without its bucket.

**4. Failure handling.** `output_truncated`, `no_visible_output`, a budget refusal or an empty answer is a
setup miss: change one lever and rerun once. Do the work yourself only after a substance miss under a
fair setup, and record it. A refused local lane is terminal: never substitute cloud inference.

**5. Record.** Verdicts carry evidence per criterion; a receipt names the family, the regime and the job id.
Inside amux (`$AMUX_SESSION` is set), put the result on the board, visible from every device:
`/home/derek/work/amux/upstream/amux/amux board add "<title>"`, then `... board artifact <CARD> hearth:job/<job_id>
--kind verification --state submitted --description "<seat>, <family>, <regime>, <artifact id>"` (a code candidate is
`--kind implementation`; a refusal is `--state invalid` with the reason). Inside a Codex sandbox the CLI needs
`AMUX_API=https://127.0.0.1:18824` and loopback network (an escalation).

**6. Attribute (Codex).** Stamp every HEARTH call with task_id='cx-<lane name>' (the AMUX lane you run in; `cx-adhoc` outside a lane). Claude Code sessions get their `cc-` id from the user-level hook; this is the Codex counterpart, so the session census can join Codex work to the ledger.

<!-- source: docs/agents/hearth-method.md in commandcenter-linux-flash; synced by tools/ops/sync-offload-block.mjs --marker hearth-method --manifest docs/agents/method-block-targets.json; edit the source -->
