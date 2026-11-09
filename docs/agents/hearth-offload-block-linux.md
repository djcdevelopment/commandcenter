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

**Fleet plan of record (Derek, 2026-10-29): THREE-DENSE**, three Qwen3.8-27B dense seats, all vLLM: `omen-dense-27b` and
`omen-dense-27b-b` on the two B70s (OMEN profile `two-dense`) and `am4-vllm` on AM4 (plan `/home/derek/.claude/plans/delightful-whistling-wirth.md`).
If another model is serving a B70, restore with `omen-profile switch two-dense` when no work is in flight. The 74B is NOT part of
the plan: not a fallback, not a target. The `bolt74-dense27` text below is HISTORICAL. In `bolt74-dense27` (ADR-0075, ADR-0076; since 2026-10-23) there are two deep seats:
the Nemotron Bolt-74B across both B70s, and the Qwen3.8-27B dense thinking model on AM4's two CUDA cards. There is
**no 30B and no 27B on the B70s** in this configuration (see "Not live" below). `host/lab-configurations.toml` names
the live set and `submit_local_work` records it in `conditions.lab_configuration`; check `~/bin/omen-profile status`
and `ssh 10.44.0.2 ~/bin/am4-profile status` before pinning a seat.

The strategy these seats are tuned for: do not emulate frontier models. Thin slices and tiny calls, many of them.
Tiny context skips the prefill tax and frees VRAM (that is what lets the 74B split across both B70s with 8 slots);
the cards are saturated by volume, backed by kernel-level llama.cpp SYCL and vLLM work. Context is shaped before it
reaches a seat: AST symbols, then the JEPA ranker (NPU encoder, `:18097`, `/dev/shm` arena) trims what the call does not
need. Pass `files=` with a specific question so that pipeline runs; pasting file text bypasses it.

- `omen-nemotron-74b` (tags `default, code, research, reasoning, dense, quality, agent`):
  `nemotron-3.5-bolt-74b-a9b` (`NVIDIA-Nemotron-3.5-Bolt-74B-A9B-UD-Q4_K_XL`) across both B70 cards (`SYCL0,SYCL1`, 53/47 split) on `:18095` (`:18091`),
  65,536 ctx, **8 parallel slots (`parallel_slots = 8`)**, output reserve 16,384 (`deliberate_max_tokens = 24576`), SYCL `_mt_reorder` + SLM `Q8_0`/`Q5_K`/`Q6_K` shared-weight/activation kernels + 32-bit `ssm_conv`/`ssm_scan` + non-destructive multi-slot Mamba-2 checkpoint cloning (ADR-0076: 922.6 tok/s
  cold prefill, 41.22 tok/s 1-slot warm decode, 122.76 tok/s 8-slot pure decode / 85.57 tok/s wall decode, and **11.25 s** across an `8 × 25.25K`-token `c=8` wave at **99.70%** checkpoint hit rate across all 8 slots, `151 ms` cross-slot clone, `0` PCIe idle-slot evictions). Primary heavy reasoner, code-review, investigation, and pooled deep seat (qualified for both `one_call` and `carry` at `serving-profile.v2`), paired with `am4-vllm`
  for parallel overflow and cross-seat `claim_verification`. Local-work whole-file code work lands here today
  because `am4-vllm` declares 4,096 output.
  **Saturating `c=8` (Batch Fan-Out Pattern):** `submit_execution` and `submit_local_work` are non-blocking (~100 ms admission) and run concurrently on HEARTH's 16-worker executor (`8` slots on `omen-nemotron-74b` + `8` slots on `am4-vllm` = `16` deep slots), whereas `watch_execution` / `watch_local_work` block. Both delivery (`Shape 1..5`, `carry`) and non-delivery (`Shape 6` `request_doc`) calls automatically extract shared repository context into Turn 1 for Mamba-2 checkpoint cloning (or prefix-first single-turn for non-Mamba vLLM APC) with zero-overhead single-flight cold-prefix coalescing (`begin_single_flight_prefix`). To saturate `c=8` from a single frontier session, issue up to 8 `submit_execution` or `submit_local_work` tool calls in parallel (or back-to-back) **before** calling `watch_execution` / `watch_local_work` on the returned IDs. When driven across multiple AMUX worker lanes (`amux-stream-claude-*`, `amux-stream-codex-*`), concurrent submissions from all lanes automatically share and saturate the deep slots.
- `am4-vllm` (tags `dense, reasoning`): **the 27B** (Qwen3.8-27B dense, thinking-capable; served as `qwen3-27b`, GPTQ-int4, fp8 KV)
  over the direct cable, dense-tp2 across both AM4 cards (RTX 5070 + RTX 4070 Ti SUPER), 49,152 ctx, **8 parallel slots
  (`--max-num-seqs 8`, `--gpu-memory-utilization 0.912` = `75,021` KV tokens; ADR-0076)**, 4,096 output for a one-call
  answer (thinking turns through `inference.deliberate` at 24,576), delivering **96.29 tok/s** cold `c=8` decode,
  **257.65 tok/s** shared-prefix `c=8` decode, and **2.99 s** follower wave completion (`35.3x` faster, **94.92%** APC
  block hit rate, `0.0` preemptions) on `4 × 26.4K`-token waves. HEARTH's capability record for it may still read
  16,384 ctx / 1 slot (stale; open item). Depth specialist for needle retrieval; never route it through the tailnet.
  Name the family (`code_fix`, `code_review`, `claim_verification`, `quote_retrieval`, `long_review`) and let the door
  pick; pin a backend only to test a lever or to keep a judge off the author's seat.
  Thinking: `local_generate` and local-work run thinking off, one call per request. For a thinking turn use
  `inference.deliberate` through `submit_execution` / `watch_execution`: `messages`, `thinking=true`, policy
  `max_tokens` up to 24,576 and `deadline_s` up to 3,600; output, reasoning and the exact wire request come back as
  artifacts. Give a thinking turn room: 4,000 for a lookup, 24,000 for a report. `deliberate` takes no `files=`, so
  nothing is sliced on that path. A result from one thinking-off call is a result for that regime, not its ceiling.
  For a report with quotes submit a delivery brief and name no procedure: `submit_local_work(..., brief=<brief.v2>)`;
  the door chooses from the registry's verdicts and records why in `route.procedure_choice` (ADR-0061; `docs/delivery.md`).
  The carried procedure: one thinking turn writes notes and a report, code carries the text, a second pass attaches
  exact quotes, and the work stops at `awaiting_review`. Held-out rates (early October, measured on the earlier
  OMEN-27B seat): carry 8/12, carry plus check 9/12, one_call 0/12. Read every delivery before relying on it.
- `fx99-vllm` (tag `utility`): Qwen2.5-Coder-7B on FX99, 4,096 ctx. Text-only summaries; it cannot
  produce reliable tool calls.
- `omen-perception` (tags `perception, ocr`; models `tesseract-ocr`, `clip-vit-l14`): the CPU perception lane,
  2 slots. `task_family="document_ocr"` lands here (plain text extraction; no tables, formulas or layout).
  `chart_diagram` and `screenshot_grounded` need a vision seat and are refused in this configuration.
- Context slicer `:18097` (`jepa-router-v3.service`, `HEARTH_JEPA_PACK=auto`): MiniLM encoder on the NPU (OpenVINO),
  JEPA predictor and ranking on the CPU, AST/embedding arena in `/dev/shm/mechsuit-ast-arena`, 0 B70 VRAM. Receipted:
  84% prompt-token cut and 78% wall cut over 12 paired 27B tasks (`loop4_live_efficiency_receipt.json`). Used by
  `local_generate(files=)` (with the prompt as intent) and delivery briefs; `submit_execution(files=)` slices without
  intent; code_fix local-work does not slice; Supports .py, .html (depth-aware HTMLParser dom_section + script/style blocks), .css, and .js/.ts under schema mechsuit-shm-ast-arena.v3 (pass deterministic file_path per slug/sha and exclude generic blueprint_advice via prescription_excluded=True).
  V3 anti-regression contract: Tier A hashes schema/model/byte-length/file-SHA, never path; Tier B hashes full symbol bodies (candidate line spans normalized); Tier C preserves file-SHA sourcemap_sha256_* aliases. Cache lookup precedes extraction in daemon and packager (bump schema on extraction changes); hits use get_file_entry(), boot resets cold-seeding counters before a real warm verification pass. Guard target: /home/derek/work/xtech-search10-rnd-20261015/sovereign_core/jepa/test_shm_ast_arena_v3_invariants.py (8 CPU-only tests passed 2026-10-30); evidence: /home/derek/work/jepa-hybrid-chronicle-20261029/ast-ramdisk-hash-v3-recovery-receipt.json. Legacy path-key reads are supported for migration without changing primary identity/writes; implementation and guard committed as a7f86faa488ad8fd57e9698a0b0206e115ae579a (2026-10-30 UTC). See /home/derek/work/lab/OPERATING-MANUAL/LEVERS.yaml for the four invariants.
- There is no cloud rung in this pool: the `gcp-gemini` rungs are retired (the GCP trial ended, 2026-10-15).
  A refused local lane is terminal; never substitute cloud.
- **Not live in `bolt74-dense27` (history for that configuration):** `omen-vllm`
  (Qwen3-30B-A3B MoE, retired from the B70s when the 74B took both cards), `omen-dense-27b` / `omen-dense-27b-b` (the
  27B replicas on the B70s; these ARE the seats of the `two-dense` operating plan above), `am4-tool-4070ti` / `am4-tool-5070`
  (Qwen3-8B tool pair; AM4 runs `dense-tp2`). Numbers in older records that name these seats (for example "the 30B
  reads 94.7%") describe those profiles, not the current fleet.

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
sends `task_family` `code_fix`/`code_review` to `deep` (`omen-nemotron-74b` and `am4-vllm`, balanced by
`lane_code_backend`; whole-file work lands on the 74B while `am4-vllm` declares 4,096 output) at any size; otherwise
evidence of 8,192 tokens or more goes to `deep` and less to `fast` (no `fast` seat is live now). The exact check is
`input + output reserve <= context_tokens`, so on the deep lane input may reach 65,536 − reserve.
Watch with `watch_local_work`, fetch with `get_local_work_artifact`, validate in an isolated worktree,
then `record_local_work_verdict(accepted|rejected|superseded, apply=False, target_root=None)` or apply an accepted candidate with `apply_local_work(work_id, target_root=None)`.
In `dev` mode (`APPROVAL_AUTHORITY=frontier`, `VERDICT_STRICT_PAPERWORK=0`), any frontier agent (`claude-frontier`, `codex-cli`, `handoff-claude`, `helm-gemini`) may accept, reject, supersede, and apply any local-model candidate (`criteria`, `evidence`, and `bucket` are auto-filled when omitted). In `prod` mode (`APPROVAL_AUTHORITY=human`, `VERDICT_STRICT_PAPERWORK=1`), verbatim `criteria` rows and `bucket="form"|"setup"|"substance"` are enforced. Never apply a candidate before the verdict.
Fetch the candidate through `get_local_work_artifact`, not from disk: that call is the
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
(checked by `~/work/lab/tools/cli_doctor.py`; all 45 HEARTH read, dispatch, verdict, apply, and build-request tools run through per-tool approvals that
`hearth_cli_policy.py` keeps in `~/.codex/config.toml`). Skills are installed under `~/.codex/skills/` from
`docs/agents/codex-skills/` (the tracked source).

**Memory tie-in (continuity):** for substantial offloads open or reuse a `ct` work item
(`ct work open --repo <id> --objective ...`) and pass its id as `task_id` on `local_generate` /
`submit_local_work`; run validation under `ct receipt --work <id> -- <cmd>`. `ct recall` then finds the
candidate manifest and HEARTH's `query_offload(task_id=...)` finds the spend, correlated by native ids.

**Asynchronous hand-off (harness-plan I1.4):** `~/work/amux/streams/bin/handoff.sh submit|status|fetch|list|track|ack`
wraps `submit_execution` / `get_execution` / `get_execution_artifact` with an idempotency key
(`sha256(task_id + brief sha + family)`), keeps only the association in `streams/stats/handoffs.jsonl` (state is
read from the door), survives a gateway restart and a session change (proved 2026-10-15), and the hook
`~/.claude/hooks/hearth_handoffs.py` prints one line per prompt when a tracked job's state changed.

**Environment (ADR-0053, R&D default):** `hearth-env show | set dev|prod --by WHO --reason WHY [--until ISO] | check`.
`dev` is the permanent default (`APPROVAL_AUTHORITY=frontier`): frontier agents (`claude-frontier`, `codex-cli`, `handoff-claude`, `helm-gemini`) vet, accept, reject, and apply local-model work directly without human-only ceremony, while GPU tenancy, thermal guards, context/KV overflow math, and secret path blocks remain strictly enforced. `hearth-env set prod` activates strict human-only gates (`APPROVAL_AUTHORITY=human`). Every receipt carries the environment and caller provenance.

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
