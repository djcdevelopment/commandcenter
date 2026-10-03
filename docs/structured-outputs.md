# Structured outputs at the door (delivery-plan task 4a)

Date 2026-10-02. vLLM 0.30.0 XPU on both OMEN lanes (`/version`). Schema: `hearth.delivery.contract.output_json_schema()`,
sha256 `208b4462299e8ec92235375c12e4236fb2628449fc706fc6125cb879a9b82353` (canonical JSON: sorted keys, no whitespace;
`hearth.toolsurface.inference.response_schema_digest`).

## The switch

`local_generate(..., response_schema=<dict>)` sends `response_format={"type":"json_schema","json_schema":{"name","schema","strict":true}}`
and only to a backend whose `[backend.settings]` has `structured_outputs = true` (set on `omen-vllm` and `omen-dense-27b`
after the probe below; AM4 tool seats, fx99 and the rest are unflagged until probed). Any other backend is refused:

    {"ok": false, "error_code": "structured_outputs_unsupported",
     "error": "StructuredOutputsUnsupported: backend fx99-vllm does not declare structured_outputs = true in [backend.settings]; response_schema was not sent and nothing was generated"}

A refusal never escalates to another rung. The result carries `response_schema_sha256`, and on the wire `finish_reason` and
`tokens_reasoning` (from `usage.completion_tokens_details`). A non-`stop` finish is `ok: false`,
`error_code: structured_output_truncated` (measured: `max_tokens=40` gives `finish_reason='length'` and the refusal; no truncated
JSON is ever reported as success). A `stop` whose content does not parse as JSON is `ok: false`,
`error_code: structured_output_invalid` (a server that dropped `response_format` would otherwise pass prose as success).
The digest is stamped on every exit of the primitive, refusals included.

## Wire probe (direct to the seats, not through the gateway)

Request: 7-line snippet from `prep/so_probe.py`, `temperature 0`, `max_tokens 600`, `chat_template_kwargs {enable_thinking: false}`,
`strict` json_schema. `check_output` = `contract.check_output(json.loads(content))`. Reasoning tokens: the server reports
`completion_tokens_details.reasoning_tokens` = 0 and returns no reasoning text on any call.

| lane (port) | call | finish_reason | completion_tokens | reasoning_tokens | check_output | wall |
|---|---|---|---|---|---|---|
| omen-vllm qwen3-30b-a3b (:18090) | 1 | stop | 168 | 0 | valid | 2.3 s |
| | 2 | stop | 156 | 0 | valid | 1.2 s |
| | 3 | stop | 156 | 0 | valid | 1.2 s |
| | 4 | stop | 156 | 0 | valid | 1.2 s |
| | 5 | stop | 156 | 0 | valid | 1.2 s |
| omen-dense-27b qwen3.8-27b (:18095) | 1 | stop | 391 | 0 | valid | 9.6 s |

5/5 fast, 1/1 deep: shape guaranteed. In-process through the new primitive (one call per lane, `HEARTH_BACKENDS` = the live TOML):
omen-vllm stop/174 tok/0 reasoning/valid 1.6 s; omen-dense-27b stop/310 tok/0 reasoning/valid 6.8 s; fx99-vllm refused as above.

## Finding: shape is guaranteed, quote exactness is not

Every call returned schema-valid JSON, but on this probe 2 of 4 quotes per call were **not exact substrings of the source**:
the 30B elided (`rows.append({...})`, `tmp.write_text(...)`); the 27B swapped straight quotes for typographic ones
(`{“source”: “candidate”...`). A schema cannot constrain a string to the source text, so exactness is a prompt matter plus a
counted repair at `locate`, never a schema guarantee.

The prompt wording that got **6/6 exact** quotes in the earlier probe (`~/work/delivery-plan/prep/so_probe.py`, both lanes):

> For each claim give a short EXACT quote copied from the source that supports it. Never give line numbers.

Task 4b's local-work prompt template uses that sentence verbatim, and adds "no ellipsis, no changed quote marks". This
probe's prompt asked for more and longer quotes and drifted.

Repair path (task 2's `hearth/delivery/sourcemap.py`, 2026-10-03): `normalized` now also folds U+201C/U+201D to `"` and
U+2018/U+2019 to `'` (a counted repair, not fuzzy); a quote with `...` or U+2026 that is not an exact hit is matched as one
elided window inside a single symbol and is reported `fuzzy:<score>` (score over the non-elided characters), never `exact`.
Against `fleet/bankedfire_linux.py@de666ef`: the curly-quoted skips write resolves `normalized` (437), with a trailing `…`
`fuzzy:1.00` (437); `rows.append({...})` resolves `fuzzy:1.00` at 433-435; `SKIP_DAYS = 14` stays `missing`.

## Task 4b: execution-service pass-through (not done here)

Through the gateway, `response_schema` is refused loudly today as an unknown argument
(`ExecutionServiceError: unknown inference.generate arguments: response_schema`). The edits, all in
`hearth/execution/service.py` (line numbers as of `f174a28`):

1. `_validate_arguments` (:331, `allowed` set :362-374): add `"response_schema"`; validate it is a non-empty dict and
   JSON-serializable (reuse `hearth.toolsurface.inference.response_schema_digest`).
2. `submit` (:578; route at :646-655): keep the provider `_select_for_route` returns and, when `response_schema` is set and
   `provider.settings.get("structured_outputs") is not True`, raise `StructuredOutputsUnsupported` before a Job exists
   (today the primitive refuses only after `_run_job` holds a lease; no request is sent either way).
3. `_run_job` (:889; forward loop :1005): add `"response_schema"` to the optional keys forwarded to the primitive.
4. `_result_observed` (:1087, `allowed` :1099-1113): add `"response_schema_sha256"`, `"finish_reason"`,
   `"tokens_reasoning"`, `"error_code"` (the execution-ledger field).
5. `execute_sync` (:1179): failure projection (:1216-1220) returns the failed invocation's `error_code` and
   `response_schema_sha256` (today only `error: reason`, so `structured_output_truncated`/`_invalid`/`_unsupported` are lost);
   success key loop (:1244-1263) adds `response_schema_sha256`, `finish_reason`, `tokens_reasoning`.
6. `plan` (:740) and `hearth/toolsurface/execution_control.py::plan_execution` (:79): optional `response_schema`; return
   `response_schema_sha256` and whether the chosen provider declares `structured_outputs`.

Then one gateway restart (Derek gate, 0 active jobs) and doorcheck HEALTHY.

## Not covered here

AM4 tool seats are unprobed (absent under lab configuration `day`) and stay unflagged. Thinking-on behavior with a schema is
untested.
