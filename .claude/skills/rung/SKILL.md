---
name: rung
description: Pick the rung and model for a task from the authored family evidence, then emit the ready-to-run local_generate call. Use for "which rung / what should run this / rung for this task / /rung", and before any offload where the rung matters.
---

# rung — which rung should run this, and on what evidence?

The door already routes itself. This skill exists for the step before that: turning
"what should run this task?" into a **cited recommendation** you can defend, and then
into one `local_generate(...)` call.

Two rules hold the whole thing up:

- **The recommendation is the only source of a model name.** The authored family
  evidence lives in `hearth/etc/routing-families.toml` and is read for you by
  `recommend_rung(...)`. A model name you remember is not evidence.
- **This skill recommends; it never schedules (ADR-0008).** It reads state and emits
  a call. It does not load or unload a rung, open a rotation window, or dispatch
  fleet work. Step 7 says so in tool names.

Everything below is a bounded instruction with the exact call to make. Do them in
order; do not skip step 3 because the rung "was fine earlier" — a rate verdict is
scoped to its baseline epoch (ADR-0044), not to your memory of it.

## Steps

### 1. Classify the task into a family and size the payload

Pick exactly one family. These are the eleven names the declaration knows; anything
that is not one of them is `default`, and `default` is a real answer, not a failure.

<!-- families:begin -->

| family | use it for |
|---|---|
| `quote_retrieval` | verbatim recall of a line or fragment out of a long document |
| `summarization` | condensing a file, log, diff, or thread you already have |
| `extraction` | pulling fields, lists, tables, or JSON out of unstructured text |
| `classification` | labelling, triage, yes/no judgements over a chunk of text |
| `drafting` | prose you will edit — retro notes, a PR body, a commit message |
| `reasoning_planning` | multi-step reasoning or planning over supplied context |
| `tool_execution` | producing a tool/function call or structured action from a spec |
| `document_ocr` | reading text off a document image or scan |
| `chart_diagram` | reading a chart, plot, or diagram image |
| `screenshot_grounded` | answering about a screenshot or UI capture |
| `default` | anything the list above does not name |

<!-- families:end -->

Then estimate `prompt_bytes`: the bytes of the prompt **plus the bytes of every file
you will pass in `files=`**. The door judges the family at the depth of the payload
that actually ships, so size the packed call, not the sentence you typed.

The bytes-to-tokens estimate is integer division by four, so the two depth floors in
the declaration land at roughly 16,384 bytes (the `quote_retrieval` evidence floor,
4,096 prompt tokens) and roughly 32,768 bytes (the depth override, 8,192 prompt
tokens). Straddling a floor changes the answer, so if your estimate is near one, say
which side you assumed.

### 2. Ask for the recommendation

```
recommend_rung(task_family=<name>, prompt_bytes=<n>)
```

Read `recommendation.model_id`, `backend_hint`, `pin_required`, `evidence`, `reason`,
and `depth_rule_applied`. Report `depth_rule_applied` out loud when it is true — it
means a depth rule, not the family's primary preference, chose the model, and the
caller deserves to know their payload size moved the answer.

If `ok` is false the preferences did not load. Do not guess a rung: say the
recommendation is unavailable and why, then let the door route itself with
`task_family=` alone.

### 3. Check the rung's state before you send

```
query_rung_state(rung=<backend_hint, or "omen-arc" when the hint is absent>)
```

This is a passive read of a rate baseline and a keep-alive tail — it sends nothing to
the rung. Only local rungs carry a rate baseline; asking about a cloud rung is
expected to come back `no_baseline`, which is information, not a fault.

| verdict | what to do |
|---|---|
| `at_rate` | proceed |
| `warn` | proceed — inside the epoch's envelope, near its warn line |
| `degraded` | proceed **only** if the caller accepts the slowdown; otherwise prefer `gcp-gemini` and say why: it is the near-free trial rung, so the cost of stepping off a degraded local rung is credit, not money |
| `stalled` | do not send to that rung — use `gcp-gemini`, or wait for it to clear |
| `unreachable` | do not send to that rung — use `gcp-gemini`, or wait for it to clear |
| `stale` | proceed, and say the verdict carries no recent deep sample: a keep-alive ping proves liveness, never rate |
| `no_baseline` | proceed, and say the same — this rung has no epoch on record, so nothing measured it |
| `unknown` | proceed, and say the read itself failed; treat it as weaker than `stale`, not as health |

`queue_status()` is available as a read-only look at the door's own task lane if you
want it; it is not part of this decision and it dispatches nothing.

### 4. Emit the call

```
local_generate(prompt=..., task_family=<name>[, backend=<backend_hint>, model=<model_id>][, files=[...]])
```

Two honest forms, and the difference is worth knowing:

- **`task_family=` alone.** The door routes by the family. When `pin_required` is
  true the door pins the recommended rung for you, and `routed_by` comes back
  `family:<name>:pinned:<rung>`. When the recommended model is opportunistically
  reachable it routes by tag and `routed_by` reads `family:<name>:<inner>`.
- **`task_family=` plus `backend=` and `model=` copied from the recommendation.**
  The pin is visible in your own call, which is the more honest form to show a
  reader — but a caller pin outranks the family, so the family is stamped
  advisory-only and `routed_by` will read `pinned:<rung>` rather than `family:...`.
  The recommendation still rides back on the result either way.

Whichever form you use, **never pass a model name you did not get from step 2.** A
pin over the rung's declared context budget is refused at the door rather than
re-routed (ADR-0031), so an oversized pin fails loudly — but a wrong model name on a
rung that does not serve it is a fault, not a substitution.

### 5. Cite only what the recommendation gave you

Quote `recommendation.evidence` **verbatim**. It is the authored evidence string for
this family, and it is the only campaign claim this skill lets you repeat from the
recommendation.

If you need any other number, take the **corrected form** from `docs/CLAIM-REGISTER.md`
and cite the row — never a figure from memory, and never one lifted out of prose that
was true when it was written. Two worked examples:

- Correct: a 1-token ping every 20 s holds 104.83 tok/s (`docs/CLAIM-REGISTER.md` #13).
  The rung goes cold when idle, and the ping **prevents** that transition rather than
  reversing it — so the figure is a hold, not a recovery.
- Wrong: quoting 121.6 tok/s as a dual-card figure (`docs/CLAIM-REGISTER.md` #6: MISLABELED).
  Its receipt is a single-card bench run, so the number never described two cards.

When you quote a rate, **name the regime it came from** (shallow versus deep prompt,
solo versus co-resident). A rate is not a scalar (ADR-0044), and a figure without its
regime is not citable.

### 6. Trust the result metadata, not the model's self-report

Check `ok` first, then read `backend`, `routed_by`, `task_family`, and
`family_recommendation`. Those are the door's record of where the work actually ran.
The generated text's own claim about which model produced it is not evidence.

If you passed **only** `task_family=` and `routed_by` does not start with `family:`,
say so: a caller signal outranked the family, and the reader should know the authored
evidence did not choose that route. An escalated family route reads
`family:<name>:escalation:<a>-><b>` — that is still a family route, and it means the
first rung failed and the door climbed once.

### 7. Never

The skill recommends; it does not schedule (ADR-0008). None of the following belongs
in a `/rung` answer, whatever the state you just read:

- `rotation_load(...)`, `rotation_unload(...)`, `rotation_window(...)` — loading or
  unloading a model, and the named window that has to contain it, are the operator's
  rotation lane. A recommendation never opens one.
- `submit_task(...)` — dispatching fleet work is the conductor's, not this skill's.
- `masters_pet(apply=True)` — never apply a change from inside a recommendation.
- The bare llama-swap unload endpoint (`POST /api/models/unload`) — it takes
  production down with it. Not through this skill, not in any form.

If the honest answer is "this rung needs a rotation to serve your task", say that and
stop. Naming the operator action is the deliverable; taking it is not.

## Reporting

One line for the verdict — family, model, rung — then the `evidence` string quoted
verbatim, then the emitted call. If step 3 changed the destination, say which verdict
moved it and what it cost.
