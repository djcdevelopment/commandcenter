# Use HEARTH for Codex offloads and work receipts

## Summary

Your connection is working. HEARTH identified me as **`codex-cli`**, and a real offload completed on **`omen-arc` / `qwen3-30b-a3b`** in about 18 seconds. I verified its caller identity, task ID, and token usage in the gateway ledger.

The YAML declares the skill's MCP dependency and permits automatic invocation. Codex's `config.toml` supplies the actual connection and authentication. Both are configured here. [Skill metadata](https://learn.chatgpt.com/docs/build-skills), [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

My recommendation matches your preference: **Codex coordinates and validates; HEARTH handles suitable delegated work and preserves meaningful work receipts.**

Keep overhead proportional to the work: offload when the expected benefit exceeds briefing,
latency, and review costs; handle trivial tasks directly and batch related small items.
Isolated offloads rely on existing gateway records. Substantial tasks get a work receipt,
grouped evidence updates, and one aggregate-report refresh at completion; reporting for
isolated calls can be refreshed on request.

## Everyday workflow

| Work | How to handle it |
|---|---|
| Summaries, extraction, classification, boilerplate, prose drafts | `local_generate`, supplying `files`, an appropriate `task_family`, and a stable `task_id`. |
| Architecture, debugging across files, integration, final review | Codex performs the work and records its results in a HEARTH receipt. |
| Independent research or a small isolated build | Submit through the asynchronous fleet lane and follow the returned `plan_id`. |
| Evidence of completed engineering work | Use HEARTH's existing build-request lifecycle. |

For each substantial engineering task:

1. Create a receipt with the request and acceptance criteria.
2. Call `execute_build_request(mode="agent")` to record that Codex is doing the work.
3. Use the receipt ID as `task_id` on related `local_generate` calls. Attach returned job IDs and fleet plan IDs to receipt updates.
4. Close the receipt with validation evidence, changed files, and any commits. Supply changed files explicitly when modifying files that were already dirty.

HEARTH already requires evidence for every acceptance criterion before accepting `status="done"`. [Build-request documentation](hearth/BUILD-REQUESTS.md).

A useful prompt is:

> `$hearth Fix this issue. Create a work receipt, delegate suitable self-contained parts, validate the result, and close the receipt with evidence.`

## Changes worth making

- **Refresh the instructions.** Update the HEARTH skill and [canonical offload guidance](docs/agents/hearth-offload-block.md) together. Task-family routing is live, despite the guidance saying to wait for C-05. The actual default is `omen-arc`; remove the conflicting default descriptions.
- **Add the receipt workflow to those instructions.** Make it automatic for substantial engineering tasks. Keep receipts focused on outcomes, decisions, and validation.
- **Use supported correlation fields.** `local_generate` accepts `task_id`; `submit_task` currently does not. Correlate fleet work through the receipt's recorded `plan_id`.
- **Refresh reporting at completion of substantial tasks with offloads, or on request.** Run `project_offload_knowledge` against `hearth/var/ledger/events.ndjson`, then query the relevant caller or task. The saved summary at the September 8 review stopped at September 4. An in-memory rebuild correctly included that day's Codex call, so refreshing the existing projection addresses this gap.

These changes can use the existing MCP interfaces.

## Validation and limits

- Confirm a fresh Codex session connects as `codex-cli`.
- Verify an offload produces matching caller, task, backend, usage, and job records.
- Verify a receipt closes with complete evidence and rejects incomplete validation.
- Confirm refreshed reporting includes the task and a current evidence watermark.

This setup records delegated inference and reported engineering outcomes. Codex's own OpenAI model traffic and native tool activity are not automatically intercepted by HEARTH's MCP connection. Treat the scorecard's dollar savings as estimates; assess usefulness through successful outputs, validation, latency, and rework.
