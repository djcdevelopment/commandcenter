## Local-first offload (HEARTH on omen-linux)

HEARTH is the MCP gateway at `http://127.0.0.1:8710/mcp` (registered for Claude Code at user
scope; for Codex in `~/.codex/config.toml`). Delegate suitable self-contained work through
`mcp__hearth__local_generate`: summaries of files/logs/diffs, structured extraction,
classification, boilerplate, and prose drafts; judgment (review, tracing, a bounded fix
recommendation) goes to the 27B by `task_family` per the HEARTH method below. Keep architecture,
multi-file logic, integration, and final validation with yourself. Do not split tightly coupled work
merely to force an offload. Offload when the expected benefit exceeds briefing, latency, and
review costs; handle trivial work directly. Batch related small items into one call.

**Lanes on this host (from `~/hearth-production/backends-linux.toml`):**

Seats are profile-bound: `host/lab-configurations.toml` names the live set and `submit_local_work` records it in
`conditions.lab_configuration`. Since 2026-10-05 the configuration is `three-dense` (G-bench27): `omen-dense-27b`,
`omen-dense-27b-b`, `am4-vllm` (dense-tp2) and `fx99-vllm` live; `omen-vllm` and the AM4 tool pair absent;
default-lane calls fall back to the 27B replicas (ADR-0066). Check before pinning a seat another profile serves.

- `omen-vllm` (default; tags `default, code, research, reasoning`; profiles `day` and two-lane only, **absent under
  three-dense**): Qwen3-30B-A3B MoE behind HAProxy `:18090`, 40,960 ctx, 8 slots. Sunk local compute, resident: spend it freely on grunt work.
- `omen-dense-27b` (tags `dense, quality, agent, vision`) and `omen-dense-27b-b` (the second replica on the other
  B70; no routing tags, so it is reached by lane load balancing or an explicit pin): Qwen3.8-27B, 65,536 ctx, 2 slots
  each (a KV pool holds ~1.5 full-window requests), output reserve 16,384.
  The only local model that completed the fix task with receipts (continuity output/07). Name the family
  (`code_fix`, `code_review`, `claim_verification`, `quote_retrieval`, `long_review`) and let the door pick the
  replica; pin a backend only to test a lever or to keep a judge off the author's seat. It is slower per token,
  so do not send it grunt work.
  `local_generate` and local-work run it with thinking off, one call per request (`enable_thinking = false` on every
  backend). Its design regime is multi-step work with thinking on in a long window (Derek 2026-10-03; ADR-0059,
  proposed). For a thinking turn use operation `inference.deliberate` through `submit_execution` / `watch_execution`
  (live 2026-10-03): `messages`, `backend="omen-dense-27b"`, `thinking=true`, policy `max_tokens` up to 24,576 and
  `deadline_s` up to 3,600; streamed; output, reasoning and the exact wire request come back as artifacts. Give a
  thinking turn room: 1,500 tokens cut a retrieval answer off, 8,000 produced no answer to a report brief; use 4,000
  for a lookup and 24,000 for a report. Two-turn driver: `~/work/lab-rnd/research/thinking_workload.py`. A result from
  one thinking-off call is a result for that regime, not its ceiling. One stated value per item (a default, a
  setting, a state) belongs on the lighter seats (ADR-0058, proposed).
  For a report with quotes submit a delivery brief and name no procedure: `submit_local_work(..., brief=<brief.v2>)`.
  The door chooses from the registry's verdicts and records why in `route.procedure_choice` (ADR-0061, live
  2026-10-04; `docs/delivery.md`). On this seat that is the carried procedure: one thinking turn writes notes and a
  report, code carries the report's text, a second pass attaches exact quotes sentence by sentence, and the work stops
  at `awaiting_review`. `procedure="one_call" | "carry" | "items"` pins one (a comparison arm). Carried deliveries stand
  at 9 of 12 accepted on the three 2026-10-02 night briefs and 0 of 3 on briefs written since: read every one before
  relying on it. A brief that declares `"items": {"kind": "env_reads"}` takes the items procedure (code lists the
  items, the 30B and an AM4 8B read each, the 27B judges disputes with thinking on; two runs, both rejected, the second
  on 1 wrong row of 91).
- `am4-vllm` (tags `dense, reasoning`): the AM4 27B over the direct cable, dense-tp2 across both AM4 cards,
  49,152 ctx under three-dense (16,384 under `memsplice`), 1 slot, 4,096 output for a one-call answer (thinking
  turns through `inference.deliberate` at 24,576). In the deep pool: `lane="auto"` code work and deliveries spread
  to it (I1.5: the waits-panel report, accepted). Depth specialist for needle retrieval; never route it through
  the tailnet.
- `fx99-vllm` (tag `utility`): Qwen2.5-Coder-7B on FX99, 4,096 ctx. Text-only summaries; it cannot
  produce reliable tool calls.
- `am4-tool-4070ti` / `am4-tool-5070` (tag `tool-use`): Qwen3-8B-AWQ, one seat per AM4 card, 24,576 /
  16,384 ctx, the Ti admitting 4 concurrent requests (3 HEARTH leases), the 5070 2; live only under the AM4
  `tool-pair` profile (configurations `day`, `tool-night`), **absent under three-dense** (`ssh 10.44.0.2
  ~/bin/am4-profile status` reads `dense-tp2`).
  `submit_local_work` takes `lane="tool"` (`am4-tool-4070ti`) and a `brief` for deliveries. Small
  OS-local chores on one file (read, grep, summarize with citations) go here through
  `task_family="tool_execution"` or a `deepagents` brief with `backend: am4-tool-4070ti`: 6/6 chores at a
  10 s median where the 27B took 172 s. When the profile is not live the tag resolves to the door default.
- There is no cloud rung in this pool: the `gcp-gemini` rungs are retired (the GCP trial ended, 2026-10-15).
  A refused local lane is terminal; never substitute cloud.
- `am4-tool-5070` also carries `tool-long` (ADR-0050): when `HEARTH_SIZER` is on, a `tool_execution`
  call whose answer the sizer bins l/xl (>= 1,024 expected output tokens: a rewrite, an 800-word report)
  is refined to `tool_long_output` and lands on the 5070, the faster decoder; the Ti keeps the short,
  prefill-heavy volume. A `deepagents` brief may say `backend: am4-tool` to have the sizer pick the seat.
  The gate is off by default; the sizer only fills an absent `max_tokens` for admission and never sets a
  generation budget or a family for a call that named none.
- `omen-perception` (tags `perception, ocr`; models `tesseract-ocr`, `clip-vit-l14`): the CPU perception lane,
  2 slots. `task_family="document_ocr"` lands here (plain text extraction; no tables, formulas or layout).
  `chart_diagram` and `screenshot_grounded` go to the 27B with its vision tower, which under three-dense means
  the seat-1 fenced swap of ADR-0068 (accepted 2026-10-15; the `perception` alias and the swap request/release
  are harness-plan I2.6, not yet code): until then those families are refused outside `seat0-27b-vision`.

**Routing and context:**

- Task-family routing is live: `task_family="summarization" | "extraction" | "classification" |
  "drafting" | "reasoning_planning" | "quote_retrieval" | "code_fix" | "code_review" | "claim_verification" |
  "long_review" | "tool_execution" | "utility_text" | "document_ocr"`. Explicit `backend=`/`model=` pins override
  the family; omit them when family routing is intended. A family with an authored regime (`code_fix`: one call,
  thinking off, 8,192; `code_review`: think then emit, 24,576; `claim_verification`: one call, thinking off,
  4,096) sets thinking and the budget itself: pass neither unless you are testing a lever.
- Report briefs with verbatim quotes (the measured default, harness-plan I1.5, 2026-10-15): `quote_retrieval`
  on a 27B replica, one call, thinking off under the word cap, input + 16,384 <= 65,536, output cap = word cap
  x 1.75; then a claim-check pass through `claim_verification` on every sentence that states what code or
  configuration does, pinning the replica that did not write the report (the family cannot know the author).
  Measured: 3 of 3 unseen briefs accepted on substance, the 2 seen briefs rejected (one false code-behaviour
  claim; one form miss), review 1-5 min against 10-20 manual, the false claim caught in 38 s by the
  thinking-off pass (the thinking-on pass took 200 s and truncated once). `plan_execution(operation="inference.generate",
  task_family=..., prompt_bytes=...)` inspects a route without dispatching.
- Don't paste file contents: pass `files=[...]` and the door packs them scope-guarded. Relative paths
  resolve against the gateway's primary repository (`~/work/commandcenter-linux-flash`); absolute paths
  under `~/work` reach other repositories. Never include tokens, keys, or credential-file contents.
- The offloaded model cannot run tools or see the conversation. Supply a standalone brief with the
  task, constraints, output format, and acceptance criteria.
- Lab intent: substantive briefs to deep seats pass `files=["/home/derek/work/lab/LAB-INTENT.md"]`; seats at or under 16K context get `/home/derek/work/lab/LAB-INTENT.capsule.txt` inline; `submit_local_work` intent and build-request titles start with `[goal:G-...]`; `ct work open` takes `--goal`.

**Bounded code work → `local-work` (skill `/local-work`, ADR-0048):**

`submit_local_work(intent, acceptance_criteria, repo, base_commit, paths, lane="auto")` freezes the
named files at a commit, produces an immutable candidate, and stops at `awaiting_review`. `auto`
sends `task_family` `code_fix`/`code_review` to `deep` (the 27B replicas `omen-dense-27b`, `omen-dense-27b-b`
and `am4-vllm`, balanced by `lane_code_backend`) at any size; otherwise
evidence of 8,192 tokens or more goes to `deep` and less to `fast` (`omen-vllm`). The exact check is
`input + output reserve <= context_tokens`, so on the deep lane input may reach 65,536 − reserve.
Watch with `watch_local_work`, fetch with `get_local_work_artifact`, validate in an isolated worktree,
then `record_local_work_verdict(accepted|rejected|superseded)` with evidence per criterion (each
`criteria[].criterion` repeats a submitted acceptance criterion verbatim) and `bucket="form"|"setup"|"substance"`
naming the miss (HEARTH method section 3). Never apply a candidate before the verdict.
Verdict labels (live since 2026-10-16, ADR-0071 proposed): a `rejected` verdict without `bucket` is refused; pass `attended`
(false only for an unattended Codex verdict, which may only reject and never its own candidate, ADR-0069) and `task_id`
(this session's `cc-`/`cx-` id); criteria rows are `{criterion, status: passed|failed, evidence}`; put `review_minutes_active=<n>`
in one evidence row as your estimate. Fetch the candidate through `get_local_work_artifact`, not from disk: that call is the
`artifact.fetched` event the measured review time starts from. When an accepted candidate is applied, commit with the trailer
`Local-Work: work_<id>`. Two sidecars appear beside each run and are advisory only: `precursor/advice.json` (prior-run evidence,
shadow, never displayed unless a later decision) and `labels.json` (every label with its method; `knowledge/labels.json` rolls up).

Artifact kinds, measured 2026-09-27: prefer `whole_file` (with `target_path`) for any target under
~6K output tokens; the 27B writes correct edits but unreliable `unified_diff` hunks (wrong counts are
repaired by the door's `git apply --recount` step and noted in the manifest as `mechanical.git_apply`;
hallucinated hunk context is not repairable). `max_tokens` is capped at 16,384 per candidate
(`work.produce` ceiling; the dense rung's reserve is 16,384; the default `deadline_s` 1,800 and the ceiling 2,400
were set when that seat decoded at ~10 tok/s and are bounds now: since ADR-0060 it decodes at ~55 tok/s at depth
and reads a prompt at ~1,500 tok/s). Every size on this host is mapped and checked in
`docs/sizing-map.md` (`python tools/ops/sizing_map.py --check`).

**Codex:** unattended, use `codex exec -c approval_policy="never" -c sandbox_mode="workspace-write" "<prompt>" < /dev/null`
(checked by `~/work/lab/tools/cli_doctor.py`; HEARTH read and dispatch tools run through per-tool approvals that
`hearth_cli_policy.py` keeps in `~/.codex/config.toml`, and mutators are refused, not prompted). Do not use
`--approve-for-me`: its automatic reviewer also approves HEARTH mutators (amux receipt
CODEX-UNATTENDED-PROBE-20261014). Interactive Codex prompts only for escalations and mutators. Skills are installed under `~/.codex/skills/` from
`docs/agents/codex-skills/` (the tracked source).

**Memory tie-in (continuity):** for substantial offloads open or reuse a `ct` work item
(`ct work open --repo <id> --objective ...`) and pass its id as `task_id` on `local_generate` /
`submit_local_work`; run validation under `ct receipt --work <id> -- <cmd>`. `ct recall` then finds the
candidate manifest and HEARTH's `query_offload(task_id=...)` finds the spend, correlated by native ids.

**Asynchronous hand-off (harness-plan I1.4):** `~/work/amux/streams/bin/handoff.sh submit|status|fetch|list|track|ack`
wraps `submit_execution` / `get_execution` / `get_execution_artifact` with an idempotency key
(`sha256(task_id + brief sha + family)`), keeps only the association in `streams/stats/handoffs.jsonl` (state is
read from the door), survives a gateway restart and a session change (proved 2026-10-15), and the hook
`~/.claude/hooks/hearth_handoffs.py` prints one line per prompt when a tracked job's state changed. It runs as the
`dev-local` operator caller until the caller key is routed (I2.5), so jobs submitted through the MCP tools are not
yet listed by it.

**Environment (ADR-0053):** `hearth-env show | set dev|prod --by WHO --reason WHY [--until ISO] | check`.
Dev relaxes the night window, re-runs and the presence gate for daytime R&D; approvals, `pause.dispatch`,
ai-mode, tenancy, admission and scope never change. Every receipt carries the environment and the night
audit excludes dev. Code defaults are prod; a missing or expired file reads as prod.

**Validation, reporting, recovery:**

- Trust the result metadata, not the model's self-report: check `ok`, then read `text`; `backend`
  and `routed_by` prove where the work ran.
- `ok:false` or unusable output → name the bucket (HEARTH method §3): a setup miss
  (`output_truncated`, `no_visible_output`, thin brief) gets one lever changed and one rerun; do the work
  directly only after a substance miss under a fair setup, and record the limitation. If the door is
  down, run `/checkmcp` once (or `python -m hearth.callers.doorcheck --revive` from the flash repo
  root with `PYTHONPATH` set) before that rerun.
- Every prompt carries `<hearth-task-id>` from the user-level hook; use that id when no `ct` work
  item exists.

<!-- source: docs/agents/hearth-offload-block-linux.md in commandcenter-linux-flash; ~/.claude/CLAUDE.md is a synced copy — edit the source -->
<!-- layout: the synced ~/.claude/CLAUDE.md may carry a leading span delimited by the lab-intent:begin and lab-intent:end marker comments, owned by ~/work/lab tools, above this offload block.
     A sync writes only the offload span (lines above the source comment) and must preserve that leading span.
     On omen-linux (no node) the sync is tools/ops/sync_claude_block.py: --write for this span, --method for the
     hearth-method span, --target for ~/.codex/AGENTS.md; it proves the bytes outside the span unchanged. -->
