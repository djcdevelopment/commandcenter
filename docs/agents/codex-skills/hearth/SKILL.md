---
name: hearth
description: Offload bounded work through the local HEARTH MCP gateway and record substantial engineering work with validated receipts. Use for suitable subtasks during larger work, or when explicitly invoked as $hearth.
---

# HEARTH

HEARTH is the MCP gateway at `http://127.0.0.1:8710/mcp`. Delegate suitable
self-contained work through `mcp__hearth__local_generate`: summaries of files/logs/diffs,
structured extraction, classification, boilerplate, and prose drafts. Keep architecture,
multi-file logic, ambiguous judgment, integration, and final validation with yourself.
Do not split tightly coupled work merely to force an offload.
Offload when the expected benefit exceeds briefing, latency, and review costs; handle
trivial work directly. Batch related small items into one call when practical.

**Routing and context:**

- Lanes on omen-linux (`~/hearth-production/backends-linux.toml`): `omen-vllm` is the default
  (Qwen3-30B-A3B MoE, `:18090`, 40,960 ctx, 8 slots) — sunk local compute, spend it freely;
  `omen-dense-27b` (Qwen3.8-27B, `:18095`, 65,536 ctx, 2 slots, reserve 16,384) for code
  candidates, careful review and long inputs — pin with `backend="omen-dense-27b"`; `am4-vllm`
  (27B over the direct cable, 16,384 ctx, 1 slot); `fx99-vllm` (7B utility, 4,096 ctx, text
  only); `am4-tool-4070ti` / `am4-tool-5070` (tag `tool-use`, Qwen3-8B per AM4 card, live only under
  AM4's `tool-pair` profile) for small one-file chores via `task_family="tool_execution"`; the 5070
  also carries `tool-long` (ADR-0050: long l/xl answers land there when the `HEARTH_SIZER` gate is on).
  There is no cloud rung in this pool: a refused local lane is terminal.
- Task-family routing is live. Use `task_family="summarization"`, `"extraction"`,
  `"classification"`, `"drafting"`, `"reasoning_planning"`, `"quote_retrieval"`,
  `"code_fix"`, `"code_review"`, `"long_review"`, `"utility_text"` as appropriate. Explicit
  backend/model pins override the family; omit them when family routing is intended.
  Use `plan_execution(operation="inference.generate", task_family=..., prompt_bytes=...)`
  to inspect a route without dispatching.
- The door admits `input + output reserve <= context` per rung and refuses otherwise (a
  `routing_refusal`); output ceilings are 16,384 tokens. Sizes are mapped in
  `docs/sizing-map.md`.
- Don't paste file contents — pass `files=[...]` and the door packs them scope-guarded.
  Relative paths resolve against the gateway's primary repository, not your current
  directory; absolute paths under `~/work` reach other repositories. Never include tokens,
  keys, or credential-file contents in a prompt or a `files=` pack.
- The offloaded model cannot run tools or see the conversation. Supply a standalone brief
  with the task, constraints, output format, and acceptance criteria.

**Work receipts (substantial engineering work):**

For substantial authorized engineering work, reuse the current task's receipt or create
one with `create_build_request`, an explicit `repo`, the request, and acceptance criteria.
Ordinary questions and small offloads do not require a build request.

1. Record yourself doing the work with `execute_build_request(mode="agent")`.
   This records execution; it does not dispatch inference. A receipt's backend selection
   is routing context, not evidence that the selected model performed the work.
2. Pass the receipt ID as `task_id` on related `local_generate` calls. Without a receipt,
   use a stable identifier for the task. Group returned execution job IDs and relevant
   results into milestone or completion updates with
   `update_build_request(evidence=..., tool_call=...)`. The gateway already logs each call;
   avoid duplicate audit events for the same offload.
3. For independent minutes-scale work, use `submit_task` and follow `task_status(plan_id=...)`.
   Supply a standalone brief; fleet source is read-only at `~/commandcenter-src`.
   `submit_task` does not accept `task_id`: record its `plan_id` on the receipt.
   For a whole isolated build, a separate receipt with deliverables can use
   `execute_build_request(mode="delegate")`; follow it with
   `update_build_request(sync_delegation=True)`.
4. Close with `close_build_request`: validation evidence for every acceptance criterion,
   actual commits when present, and explicitly listed changed files, including edits to
   files that were already dirty. Use `done` only when every criterion passes.
   Record failed, blocked, or cancelled outcomes honestly.

**Validation, reporting, and recovery:**

- Trust the result metadata, not the model's self-report: check `ok` first, then read
  `text`; `backend` and `routed_by` are the proof of where the work actually ran. Review
  outputs independently and run validation appropriate to the change.
- To see what a task or caller saved, read — never rebuild — the offload document: use
  `query_offload(task_id=...)` and `query_offload(caller_id=...)` to inspect attribution and
  the evidence watermark. The gateway's own `knowledge_rebuild` timer refreshes that document;
  do not call the `project_*` writer tools from a task. Caller and task filters are separate
  dimensions, not an intersection. Aggregates are bounded; a missing row alone is not proof
  that a call was unrecorded.
- For isolated small offloads, the existing gateway and execution records suffice.
- Dollar savings are estimates. HEARTH's MCP connection does not capture your own
  frontier-model traffic or native shell/edit activity; receipts record meaningful outcomes
  and supplied evidence.
- `ok:false` or unusable output → one retry at most, then do the work directly and record
  the limitation; never loop on a cold or failing worker. If the door itself is down, run
  `/checkmcp` once (Claude Code) or `python -m hearth.callers.doorcheck --revive` once from
  the commandcenter repository root before that retry.
- Receipt writes, projection refreshes, and service recovery follow the task's existing
  authorization and the active execution mode. These instructions do not override a
  restriction on mutations.

<!-- body mirrored from docs/agents/hearth-offload-block.md (the canonical offload block); edit the block, then regenerate this file: the tracked copy here is the source of the installed ~/.codex/skills/hearth -->
