# ADR-0050: A request sizer informs admission and the tool-seat split; it never sets a budget or invents a family

- Status: **accepted for the heuristic; the NPU encoder is not adopted on current evidence** (omen-linux, 2026-09-28; lap N2 below)
- Register: commandcenter `docs/adr` (cite as `commandcenter#0050`)
- Precedents: `commandcenter#0031` (a pin picks the rung, not the physics: admission arithmetic at
  the door), `commandcenter#0039` (depth inversion: prompt depth changes the rung), `commandcenter#0045`
  (task families are authored, cited preferences; the door routes itself)
- Plan: `~/.claude/plans/mossy-stargazing-naur.md` (NPU build-out, laps N0-N4)

## Context

Derek asked for a tiny model on OMEN's Arrow Lake NPU that reads a request (file size, media type,
likely output size) so routing can send long-output work to the RTX 5070 and short, concurrent
work to the RTX 4070 Ti, at the cost of a little DDR5.

What the evidence said before anything was built:

- **Output, not input, is the unmeasured axis.** The door's admission rule (`#0031`, re-derived
  2026-09-27) reserves `max_tokens` beside the prompt, but the reserve is the caller's number or
  the rung's default; nothing ever *predicted* the output. On the Linux ledger (1,039 succeeded text
  jobs, 2026-09-23..28) outputs run p50 77 / p90 279 / p99 604 tokens against inputs of p50 2.9 KB /
  p90 32 KB, so a default reserve of 4,096-8,192 tokens over-reserves by an order of magnitude on
  almost every call, and "the answer is longer than the prompt" is a declared-intent case (a word
  budget on a summary, a rewrite, a code candidate) that is readable off the request text.
- **The family labels cannot train a classifier.** 1,013 of those 1,039 jobs are `extraction` from
  two system prompts; a model fit to them learns campaign style. The quantity with real labels is
  `tokens_out`.
- **The two AM4 cards are mirror images.** Qwen3-8B-AWQ under vLLM: the 4070 Ti prefills 4,379 tok/s
  to the 5070's 3,395; the 5070 decodes 110 tok/s to the Ti's 85. Decode is the cost that scales
  with output, so Derek's split is right, and it needs a signal about output length that nothing
  supplied.
- **The NPU is bound but has no user space.** `intel_vpu` on kernel 7.0, `/dev/accel/accel0`,
  firmware 37xx; no `libze_intel_npu`, no OpenVINO. The corpus verdict stands (`LEVEL-ZERO-LEVERAGE-
  BRIEF.md:375`): no LLM on the NPU, encoders only. Whether a MiniLM pass on the NPU beats the
  285K's 24 cores is unmeasured until lap N1.

## Decision

1. **One contract, two implementations, one gate.** `hearth.sizer.size_request(prompt, system,
   files, payload_bytes, task_family)` returns `{output_class: xs|s|m|l|xl, expected_output_tokens,
   task_family, confidence, source, signals, ms}`. `HEARTH_SIZER=off|heuristic|npu` (host env,
   read per call) selects nothing (default), the pure in-process heuristic, or the loopback encoder
   service with the heuristic as fallback. Off means every route is byte-identical to the unsized
   door; the regression guard `AbsentTaskFamilyIsUnchangedTests` runs unsized.
2. **Bins by upper edge.** xs < 128, s < 384, m < 1,024, l < 2,048, xl >= 2,048 tokens. The edge,
   not a midpoint, is what admission reserves, so a right-bin guess never under-reserves.
3. **The sizer feeds admission only.** Its `expected_output_tokens` is the reserve `select_backend`
   checks against a rung's window *when the caller named no `max_tokens`*. It never sets the
   generation budget: a wrong bin can misroute, it cannot truncate. The one budget change is a
   clamp: when the sizer admitted a call on its small reserve and the rung's default budget no
   longer fits beside the prompt, the budget is clamped to the room left, because the server would
   refuse the request otherwise.
4. **The sizer never invents a family.** A call that named no `task_family` keeps none; the sizer's
   only family effect is to refine a declared `tool_execution` to `tool_long_output` when the bin is
   l or xl. Family-less door calls stay on the door default; the 8B tool seats are not a quality
   upgrade for general text.
5. **`tool-long` lives on the decode seat.** `am4-tool-5070` carries `tags = ["tool-use", "tool-long"]`
   and `max_tokens 6144`; `am4-tool-4070ti` stays `tool-use` only. `[family.tool_long_output]`
   (routing-families-linux.toml) is the sizer's target, cited to the 2026-09-28 decode measurement.
   Nobody declares it by hand.
6. **Caller precedence is unchanged.** Endpoint pin > backend pin > model > quality/task >
   task_family > default (`#0045`). A caller's `max_tokens` is never replaced.
7. **The verdict is on the record.** `routed_by` gains `sizer:<source>:<bin>:` *inside* the family
   prefix (`family:tool_execution:sizer:heuristic:m:tag:tool-use`), so the kernel ledger's family
   bucket is unchanged and the sizer's answer rides the closed schema's one free-text field. The
   execution lane puts the whole dict in `observed.sizer` on `job.dispatched` and
   `invocation.succeeded`. The result carries `sizer`.
8. **The execution lane's admission now sees the job's budget.** `_select_for_route` takes
   `max_tokens` (the policy's, else the sizer's); before this it passed none and the reserve check
   used the rung default whatever the job asked for.
9. **The drain can ask.** A DeepAgents brief with `backend: am4-tool` has the sizer pick the seat
   (`size_tool_seat`): the 5070 for l/xl, the Ti otherwise; the spec records `backend_requested`
   and `sizer`. Briefs that name a seat are untouched.

## What the heuristic reads (lap N0, measured on the ledger replay)

Precedence: an explicit budget in the text (`at most 250 words`, `max_report_words: 800`,
`N-word`), then a tiny-answer phrase (`one line`, `just the number`), then the verb class of the
instruction head scaled by input depth (rewrite/translate grow with the input; summarize/list
shrink; report/review sit in the middle), then the declared family's ledger median, then `s`. A
verb and a family median that disagree by two bins or more hand the decision to the median: the
median is measured, the verb is a guess. Packed `<file>` bodies are never read for verbs.

`tools/sizer/replay.py` over 1,034 joined jobs since 2026-09-23 (26 capped rows excluded):

| version | exact bin | within one bin | under-reserved | false long (l/xl) | p50 |
| --- | --- | --- | --- | --- | --- |
| first cut | 74.7 % | 84.3 % | 4.8 % | 108 of 1,008 | 0.2 ms |
| after two fixes | **87.7 %** | **98.2 %** | 11.0 % | **0** | 0.2 ms |

The two fixes were both edges the replay found: "candidate" and "proposal" are nouns in retrieval
prompts, not produce-verbs (92 rows), and "a 2,048-token window" is an input description, not a
budget (16 rows). The under-reserve rows are budgets the model overran (an 60-word ask answered in
150); they matter only when a seat is nearly full. The corpus holds no true l/xl output, so the
long class is unevaluated until the N4 pour supplies long briefs.

## Consequences

- With the gate off nothing changes; that is the default until the N4 pour passes (>= 90 % of l/xl
  briefs on the 5070, zero route changes on pinned or family-routed work, < 5 ms added door latency
  for the heuristic, < 40 ms for the encoder).
- The NPU encoder was built (lap N1: OpenVINO 2026.4.0 in `~/.venvs/npu`, MiniLM-L6 exported to IR
  at a static `[1,256]` window, fp16 cosine 1.000 / int8 0.999; `tools/sizer/serve.py` on
  `127.0.0.1:8797` with the heuristic as fallback) and measured on the CPU (int8 3.5 ms p50) and the
  iGPU (5.4 ms); the NPU itself waits on the sudo block in `docs/npu-sizer-bringup.md`. **Lap N2 said
  no:** a class-weighted softmax head over the embedding scored 66.2 % held-out on whole-campaign
  folds against the heuristic's 87.6 %, and no labelled set on this host holds a single l/xl output,
  so the class that decides the seat split cannot be learned from history. The decision rule wrote no
  `head.npz`; the service serves the heuristic's bin with the encoder's latency and is not deployed.
  The `npu` mode stays in the code as the seam for a future labelled long-output set (the N4 pour is
  the first source of one); the NPU bring-up remains a prepared, optional measurement of encoder cost.
- `tools/ops/sizing_map.py` carries the sizer rows and three invariants: the gate is a known mode,
  `tool-long` sits on exactly the decode seat, and the long rung's budget covers the xl edge.
- The 34 frozen research scripts and every pinned route are untouched by construction (pins and
  explicit budgets win; the sizer only fills blanks and refines one tool family).

## Rollback

`HEARTH_SIZER=off` (no restart). Remove the `tool-long` tag and the `tool_long_output` family from the
host TOMLs (live re-read). The code paths are inert without the gate.
