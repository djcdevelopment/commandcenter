# BUILD-NOTES-HC — Stream H-C: Callers + Mind wiring

2026-07-03 · branch `worktree-agent-ac0d9cff39c7c4677` · builds against the frozen
contracts in HEARTH-BUILD-ORCHESTRATION.html; no imports from `hearth.kernel`
or `hearth.toolsurface`.

## What was built

| File | Purpose |
|---|---|
| `hearth/callers/client.py` | `HearthClient` — thin wrapper over `mcp.client.streamable_http.streamablehttp_client` + `ClientSession`. Async `list_tools()` / `call(tool, **args)`, sync `list_tools_sync()` / `call_sync()` (asyncio.run). Async-context-manager form reuses one session; bare calls open one session per op. Requires venv-omen python (mcp 1.28.1). |
| `hearth/callers/local_caller.py` | `run_task()` — Ollama tool-calling agent loop against the gateway. mcp-free at import (HearthClient imported lazily; injectable `client=` for tests); Ollama reached via `_post_json` (urllib, patchable). Endpoint from `HEARTH_OLLAMA` env, default `http://127.0.0.1:11434`. Returns `{ok, result_text, turns, tool_calls_made, tokens_in_total, tokens_out_total, error}` (tokens from `prompt_eval_count`/`eval_count` per turn). |
| `hearth/callers/frontier.md` + `mcp-config-snippet.json` | Frontier connection doc + `.mcp.json` stanza (`type: "http"`, url, `headers: {"X-Hearth-Key": ...}`) and the act-only-through-the-gateway rule. |
| `hearth/projection/ledger_adapter.py` | The mind wiring, mcp-free (system python). Maps hearth-event.v1 → workflow events, appends via `tools.workflow.append_event.append_event` (imported, not shelled; validates every event). Cursor at `hearth/var/projection_cursor.json`. CLI: `python -m hearth.projection.ledger_adapter [--ledger PATH] [--target PATH] [--cursor PATH] [--dry-run]` → prints `{processed, skipped, errors}`. Default target: `runs/hearth-gateway/events.jsonl`. |
| `hearth/projection/economics.py` | `summarize(ledger_path)` — pure read. Per runner_class and per tool: `{calls, ok, ok_rate, total_duration_ms, tokens_in, tokens_out}` + `frontier_vs_local` table. CLI prints JSON. Seed of knowledge-per-local-hour / naadam scoreboard. |
| `hearth/tests/callers/` | 28 unittest tests (see below). |

`hearth/__init__.py` and `hearth/tests/__init__.py` are EMPTY per the merge contract.

## Header mechanism found in the installed SDK (verified, not guessed)

mcp 1.28.1, `mcp/client/streamable_http.py` line 686:
`async def streamablehttp_client(url, headers: dict[str, str] | None = None, ...)` —
the `headers` dict is passed to `create_mcp_http_client(headers=...)` (the httpx
client factory), so `{"X-Hearth-Key": key}` rides every HTTP request of the
session. Note: the `headers` parameter on the lower-level
`StreamableHTTPTransport.__init__` is deprecated in this SDK version; the
top-level `streamablehttp_client(headers=...)` path is the supported one and is
what `HearthClient` uses. `task_id` is carried per-call via
`ClientSession.call_tool(..., meta={"task_id": ...})` (the request `_meta`
field) — the gateway may read it there.

## hearth-event.v1 → workflow-event mapping

| hearth-event.v1 | workflow event (contracts/workflow-event.schema.json) |
|---|---|
| `event_id` | `event_id` = `evt_hearth_<event_id>` |
| `ts` | `timestamp` |
| — | `event_type` = `work.accepted` (constant; see decision below) |
| — | `workflow_id` = `wf-hearth-gateway`, `run_id` = `hearth-gateway` |
| `caller.id` | `actor.id` |
| `caller.runner_class` | `actor.type`: frontier→`builder`, local→`builder`, human→`operator`; raw value kept in `payload.runner_class` |
| `task_id` | `segment_id` (nullable free-form) + `payload.task_id` |
| `ok` | `status` `completed`/`failed`, `outcome` `success`/`failure` |
| `tool` | `payload.tool` |
| `caller.node` | `payload.node` |
| `args_digest`, `args_preview`, `result_digest`, `error` | `payload.*` (same names) |
| `duration_ms` | `payload.duration_ms` (economics) |
| `cost.tokens_in/tokens_out/watt_s` | `payload.cost.*` (economics) |

**Why `event_type: work.accepted`:** the ontology enum is closed
(`tools/workflow/ontology.py`) and the schema is `additionalProperties: false`,
so no new fields/types were invented. `work.accepted` is the only type that is
semantically neutral to tool-call granularity and carries no extra required
fields (builder/candidate/assay ids). Each gateway call is a unit of work the
lab accepted and executed; the evidence for S1–S8 lives in `payload` (the
schema's free-form object — this is where economics rides). Events land in
their own run stream (`runs/hearth-gateway/events.jsonl`), so the constant type
cannot distort any real build-run's state/board projections.

**Idempotency:** cursor stores `{last_event_id, line}`. Ledger is append-only →
line positions are stable; on resume the adapter verifies the event_id at the
recorded line, falls back to scanning for it, then to reprocessing from zero
(rotation case). Bad lines are reported in `errors` and skipped; good lines
still land. `--dry-run` validates the mapping (via
`tools.workflow.validate_events.validate_event`) and writes nothing — no
target, no cursor.

## Test results

- `python -m unittest discover -s hearth/tests -t .` (system python): **23 tests OK, 1 skipped** (the skip is `test_client.py`, which needs the mcp SDK).
- `& fleet-worker-node\.venv-omen\Scripts\python.exe -m unittest discover -s hearth/tests -t .` (venv): **28 tests OK** (client tests included).
- Regression `python -m unittest discover -s tests`: **177/177 OK**.
- CLI smoke (scratchpad ledger): project → `{processed: 2, skipped: 0}`; re-run → `{processed: 0, skipped: 2}`; output validates with `python -m tools.workflow.validate_events` (`ok`) and projects with `project_state`; economics CLI produces the frontier-vs-local table.

Coverage: mapping validity (against existing validate machinery), economics
passthrough, failed-call mapping, runner_class→actor.type, cursor idempotency
(incl. append-only growth), bad-line tolerance, dry-run purity, economics
buckets/tolerances, local-caller loop with fully mocked Ollama + fully mocked
HearthClient (tool loop, string-JSON args, malformed args → error fed back to
model, Ollama down, gateway down, gateway 500 mid-loop, max_turns exhaustion),
SDK header contract (`headers` in `streamablehttp_client` signature),
CallToolResult/Tool flattening.

## Deviations / notes

1. **`task_id` transport to the gateway** — the frozen contract defines only the
   `X-Hearth-Key` header. `HearthClient` sends `task_id` as MCP request `_meta`
   (`call_tool(meta={"task_id": ...})`); H-A can read it or ignore it. No
   invented headers.
2. **`--target` / `--cursor` CLI flags** — beyond the specified `--ledger` /
   `--dry-run`, needed so tests and operators never touch the real
   `runs/hearth-gateway` stream by accident.
3. **Session-per-call in the bare sync path** — the sync convenience wrappers
   open a fresh streamable-http session per operation (simple, stateless-caller
   shaped); the async context-manager form exists for callers who want one
   session.
4. **Economics summary carries `ok` and `events`/`parse_errors` counters** in
   addition to the specified fields (needed to compute ok_rate transparently);
   nothing specified was omitted.
5. Local model is trusted to emit `arguments` as a dict (Ollama default) or a
   JSON string; anything else is answered with a structured tool-error message
   so the model can self-correct, per spec.
6. **Mid-build design update from Derek (applied):** gateway placement is
   decided — it lives on OMEN (localhost for local callers, tailnet address
   for remote boxes), and frontier callers are MCP-only. `frontier.md`
   reflects both.
