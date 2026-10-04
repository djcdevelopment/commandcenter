> Start here: `START-HERE.md` — the Local Compute Operator control plane (entry contract, D-103).
# commandcenter — agent instructions

## Local-first offload (HEARTH on omen-linux)

HEARTH is the MCP gateway at `http://127.0.0.1:8710/mcp` (registered for Claude Code at user
scope; for Codex in `~/.codex/config.toml`). Delegate suitable self-contained work through
`mcp__hearth__local_generate`: summaries of files/logs/diffs, structured extraction,
classification, boilerplate, and prose drafts. Keep architecture, multi-file logic, ambiguous
judgment, integration, and final validation with yourself. Do not split tightly coupled work
merely to force an offload. Offload when the expected benefit exceeds briefing, latency, and
review costs; handle trivial work directly. Batch related small items into one call.

**Lanes on this host (from `~/hearth-production/backends-linux.toml`):**

- `omen-vllm` (default; tags `default, code, research, reasoning`): Qwen3-30B-A3B MoE behind
  HAProxy `:18090`, 40,960 ctx, 8 slots. Sunk local compute, resident: spend it freely on grunt work.
  (historical Windows note: formerly served by omen-arc on :8082).
- `omen-dense-27b` (tags `dense, quality, agent`): Qwen3.8-27B behind `:18095`, 65,536 ctx, 2 slots
  (its KV pool holds ~1.5 full-window requests), output reserve 16,384.
  The only local model that completed the fix task with receipts (continuity output/07). Pin it
  with `backend="omen-dense-27b"` for code candidates, careful review, or long inputs; it is
  slower per token, so do not send it grunt work.
- `am4-vllm` (tags `dense, reasoning`): AM4 27B over the direct cable, 16,384 ctx, 1 slot. Live only under
  the `memsplice` lab configuration, for MemSplice research; the default `day` has AM4 on `tool-pair`.
  Depth specialist for needle retrieval; never route it through the tailnet.
- `fx99-vllm` (tag `utility`): Qwen2.5-Coder-7B on FX99, 4,096 ctx. Text-only summaries; it cannot
  produce reliable tool calls.
- `am4-tool-4070ti` / `am4-tool-5070` (tag `tool-use`): Qwen3-8B-AWQ, one seat per AM4 card, 24,576 /
  16,384 ctx, the Ti admitting 4 concurrent requests (3 HEARTH leases), the 5070 2, live by default
  (configuration `day`, AM4 on `tool-pair`; check `ssh 10.44.0.2 ~/bin/am4-profile status`).
  `submit_local_work` takes `lane="tool"` (`am4-tool-4070ti`) and a `brief` for deliveries. Small
  OS-local chores on one file (read, grep, summarize with citations) go here through
  `task_family="tool_execution"` or a `deepagents` brief with `backend: am4-tool-4070ti`: 6/6 chores at a
  10 s median where the 27B took 172 s. When the profile is not live the tag resolves to the door default.
- There is no cloud rung in this pool. A refused local lane is terminal; never substitute cloud.
- `am4-tool-5070` also carries `tool-long` (ADR-0050): when `HEARTH_SIZER` is on, a `tool_execution`
  call whose answer the sizer bins l/xl (>= 1,024 expected output tokens: a rewrite, an 800-word report)
  is refined to `tool_long_output` and lands on the 5070, the faster decoder; the Ti keeps the short,
  prefill-heavy volume. A `deepagents` brief may say `backend: am4-tool` to have the sizer pick the seat.
  The gate is off by default; the sizer only fills an absent `max_tokens` for admission and never sets a
  generation budget or a family for a call that named none.

**Routing and context:**

- Task-family routing is live: `task_family="summarization" | "extraction" | "classification" |
  "drafting" | "reasoning_planning" | "quote_retrieval"`. Explicit `backend=`/`model=` pins override
  the family; omit them when family routing is intended. `plan_execution(operation="inference.generate",
  task_family=..., prompt_bytes=...)` inspects a route without dispatching.
- Don't paste file contents: pass `files=[...]` and the door packs them scope-guarded. Relative paths
  resolve against the gateway's primary repository (`~/work/commandcenter-linux-flash`); absolute paths
  under `~/work` reach other repositories. Never include tokens, keys, or credential-file contents.
- The offloaded model cannot run tools or see the conversation. Supply a standalone brief with the
  task, constraints, output format, and acceptance criteria.

**Bounded code work → `local-work` (skill `/local-work`, ADR-0048):**

`submit_local_work(intent, acceptance_criteria, repo, base_commit, paths, lane="auto")` freezes the
named files at a commit, produces an immutable candidate, and stops at `awaiting_review`. `auto`
sends `task_family` `code_fix`/`code_review` to `deep` (`omen-dense-27b`) at any size; otherwise
evidence of 8,192 tokens or more goes to `deep` and less to `fast` (`omen-vllm`). The exact check is
`input + output reserve <= context_tokens`, so on the deep lane input may reach 65,536 − reserve.
Watch with `watch_local_work`, fetch with `get_local_work_artifact`, validate in an isolated worktree,
then `record_local_work_verdict(accepted|rejected|superseded)` with evidence per criterion. Never apply
a candidate before the verdict.

Artifact kinds, measured 2026-09-27: prefer `whole_file` (with `target_path`) for any target under
~6K output tokens; the 27B writes correct edits but unreliable `unified_diff` hunks (wrong counts are
repaired by the door's `git apply --recount` step and noted in the manifest as `mechanical.git_apply`;
hallucinated hunk context is not repairable). `max_tokens` is capped at 16,384 per candidate
(`work.produce` ceiling; the dense rung's reserve is 16,384; the default `deadline_s` 1,800 and the ceiling 2,400
were set when that seat decoded at ~10 tok/s and are bounds now: since ADR-0060 it decodes at ~55 tok/s at depth
and reads a prompt at ~1,500 tok/s). Every size on this host is mapped and checked in
`docs/sizing-map.md` (`python tools/ops/sizing_map.py --check`).

**Codex:** `codex exec --approve-for-me ... < /dev/null` is required for unattended HEARTH tool calls;
interactive Codex prompts per call. Skills are installed under `~/.codex/skills/` from
`docs/agents/codex-skills/` (the tracked source).

**Memory tie-in (continuity):** for substantial offloads open or reuse a `ct` work item
(`ct work open --repo <id> --objective ...`) and pass its id as `task_id` on `local_generate` /
`submit_local_work`; run validation under `ct receipt --work <id> -- <cmd>`. `ct recall` then finds the
candidate manifest and HEARTH's `query_offload(task_id=...)` finds the spend, correlated by native ids.

**Validation, reporting, recovery:**

- Trust the result metadata, not the model's self-report: check `ok`, then read `text`; `backend`
  and `routed_by` prove where the work ran.
- `ok:false` or unusable output → one retry at most, then do the work directly and record the
  limitation. If the door is down, run `/checkmcp` once (or `~/.venvs/hearth-private/bin/python -m hearth.callers.doorcheck --revive`
  from the flash repo root with `PYTHONPATH` set) before that retry.
- Every prompt carries `<hearth-task-id>` from the user-level hook; use that id when no `ct` work
  item exists.
- The gateway runs under systemd user service `hearth-production.service`. After landing code changes
  that the door mounts, restart with `systemctl --user restart hearth-production.service` (historical: formerly invoked via Windows schtasks /Run /TN HearthGatewayRestart), then doorcheck.
- In-process callers of `hearth.toolsurface.inference` run in the environment defined by systemd drop-ins; on Linux no launcher wrapper (historical: formerly with-gateway-env.cmd) is needed.

## Reading the decision record

The decision record here is **two-tier and event-sourced**, so reading it in the obvious
order gives the wrong answer. Sources before views, always:

1. `docs/adr/` in this repository — architectural decision records.
2. `~/work/continuity/HANDOFF.md` — current cross-session continuity ledger.
3. Historical decision logs exist read-only at `/mnt/omen-c-read/Users/derek/` (historical Windows path: `C:\work\handoffs\decision-architecture\`).

**Never edit a file whose header or footer says "Generated deterministically from … do not
hand-edit this file."** Run its renderer instead (e.g. `python tools/ops/sizing_map.py --render`).

**ADR citations are ambiguous — cite `<register>#<number>`, never a bare number.**
Resolve any citation against `docs/adr/`.

**Never infer who authored something from filesystem or git metadata.** Agents commit as the
user; `mtime` cannot distinguish a human from an agent.

**Never report "no decisions found" from a file search.** Every observation channel here has
a different blind spot. State which channels you checked and what each cannot see.
