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

**Backend rungs** (hearth/etc/backends.toml; pass `backend="name"` to pin):
`omen-ollama` qwen (sunk — the default), `am4-oxen` (banked fire, big context),
`gcp-gemini` (Vertex `gemini-3.5-flash` on **GCP trial credits** — near-free
frontier-class while they last: prefer it over spending metered Sonnet/frontier
tokens for self-contained reasoning, drafting, and integration proofs).

For **auditable infra builds** (checkable acceptance criteria, receipt wanted),
use the door's **build-request lane**: `create/get/list/update/execute/
close_build_request`; receipts + ledger at
`C:\work\comfy\fieldlab\runs\build-requests`. `close(status="done")` is rejected
unless every criterion has a `passed` row with evidence — write criteria you can
prove. See hearth/BUILD-REQUESTS.md.

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
