# commandcenter — agent instructions

## Local-first offload (HEARTH)

The HEARTH gateway (always-on MCP door) exposes a local model via
`mcp__hearth__local_generate` backed by boot-started Ollama on OMEN
(`qwen3-coder:30b` by default). Before spending your own frontier tokens on a
self-contained sub-task, delegate it to the local model. Every such call also
lands on the HEARTH ledger and feeds the learning loop — so offloading is both a
token saving and an assay observation.

**Reach for `local_generate` — don't reason inline — when the sub-task is:**
- summarizing / condensing a file, log, or diff you have already read
- extracting structured data (fields, lists, JSON) from unstructured text
- generating boilerplate (config, test scaffold, docstring, commit-message draft)
- classifying / labeling / yes-no triage over a chunk of text
- drafting prose you will then edit (retro notes, PR body first pass)

For **async, minutes-scale** work (research briefs, simple builds), the door also
has a task lane: `submit_task` dispatches to the fleet via the conductor's inbox
(returns a `plan_id`; poll `task_status`). The brief must be self-contained, but
fleet workers do have read-only source at `~/commandcenter-src`, so a git range
they can inspect themselves is fair game.

**Rules:**
- The local model has **no repo access** — pass every bit of context it needs in
  the prompt. It cannot read files, run tools, or see this conversation.
- If it returns `ok:false` (cold/unreachable) or the output is unusable, do the
  task yourself. One retry max — never loop on a cold worker.
- If the door itself is down, run the `/checkmcp` skill (doorcheck `--revive`) once.
- First call after a boot pays a ~12s model-load tax; calls after that run at
  ~54 tok/s. Don't treat the cold-start latency as a failure.
- Keep frontier reasoning for what needs it: architecture, multi-file logic,
  judgment, and anything requiring repo-wide context. Offload the grunt work,
  not the thinking.
