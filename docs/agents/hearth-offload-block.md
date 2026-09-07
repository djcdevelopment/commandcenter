## Local-first offload (HEARTH)

HEARTH is an always-on MCP door on loopback at `http://127.0.0.1:8710/mcp`. Before spending
metered frontier tokens on a self-contained sub-task, delegate it with
`mcp__hearth__local_generate`. Keep frontier reasoning for architecture, multi-file logic,
judgment, and anything needing whole-repo or whole-conversation context.

**Reach for `local_generate` — don't reason inline — when the sub-task is:**
- summarizing / condensing a file, log, or diff you have already read
- extracting structured data (fields, lists, JSON) from unstructured text
- generating boilerplate (config, test scaffold, docstring, commit-message draft)
- classifying / labeling / yes-no triage over a chunk of text
- drafting prose you will then edit (retro notes, PR-body first pass)

**The door routes itself — pin a rung only with cause** (`backend="name"`), preferred order:
- `gcp-gemini` — near-free frontier-class flash; the default target for self-contained work.
- `gcp-gemini-pro` — **pin-only**; the large-context reach flash cannot carry. Omit `max_tokens`
  and let the rung apply its own default.
- `omen-arc` — **the door default** and the sunk-cost local rung: resident, no cold-start tax,
  so spend freely on grunt work. Consumers keep using port 8082 unchanged.
- `omen-arc-oss` — banked fire, **pin-only**; it costs a model swap, so pin it with cause.
- `omen-swap` — the rotation rung, **pin-only** and always with `model=`. Port 8081 owns the
  model lifecycle: load and unload only through the door's rotation-window tools, **never** the
  bare llama-swap unload endpoint, which unloads production too.

**Rules:**
- Don't paste file contents — pass `files=[...]` and the door packs them scope-guarded.
  Repo-relative paths resolve against the primary root, and absolute paths anywhere under the
  HEARTH scope root reach other repos; only context from outside that root travels in the body.
- The offloaded model cannot run tools and cannot see your conversation — briefs stand alone.
- Trust the result metadata, not the model's self-report: check `ok` first, then read `text`;
  `backend` and `routed_by` are the proof of where the work actually ran.
- `ok:false` or unusable output → one retry at most, then do the task yourself; never loop on a
  cold worker. If the door itself is down, run `/checkmcp` once.
- For async, minutes-scale work (research briefs, simple builds) use `submit_task` (returns a
  `plan_id`; poll `task_status`). The brief must be self-contained.
- Never paste secrets — tokens, keys, credential file contents — into a prompt or `files=` pack.
- A `task_family=` label is expected after C-05 lands; until then, do not pass it.

<!-- synced by tools/ops/sync-offload-block.mjs from docs/agents/hearth-offload-block.md; edit the source, not this copy -->
