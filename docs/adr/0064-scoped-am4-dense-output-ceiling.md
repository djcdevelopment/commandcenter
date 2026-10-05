# ADR-0064: A scoped AM4 dense output ceiling

- Status: **Proposed implementation; parent acceptance pending code v2 review. Not deployed.**
- Date: 2026-10-05
- Goal: G-bench27
- D021: Prepare the scoped facade output-limit increase for independent review **(decided by Codex)**; not reviewed by Derek. [Running decision list](/home/derek/work/bench27-plan/CODEX-DECISIONS.md), [D021 approval audit](/home/derek/work/CLAUDE-APPROVED.md). Authoritative paths are `/home/derek/work/bench27-plan/CODEX-DECISIONS.md` and `/home/derek/work/CLAUDE-APPROVED.md`.

## Authority and scope

The latest operator delegation is: “i give permission for you to review and decide on my behalf -- but it must be marked (decided by Codex) and keep a running list of decide items for me to easily review”. D021 records preparation, not acceptance or deployment. Parent acceptance remains pending successful independent code review and an explicit marked decision in the running list.

This **is a scoped output-gate increase**, from 8,192 to 24,576. It is not described as avoiding H-3 or as already personally reviewed by Derek. Within this G-bench27 campaign, the latest user instruction authorizes Codex to review and decide on the operator's behalf and supersedes the earlier Derek-only decision requirement for this decision. The basis is that latest delegation, not ADR-0063's earlier nine-brief grant. Scope ends with the campaign or dev expiry **2026-10-12T07:00Z**; no extension, unrelated delegation, credential publication permission, or non-interactive sudo is inferred. Elevated actions still go to Derek's terminal.

## Problem and proposed behavior

The AM4 alias facade caps every output request at 8,192 tokens. Dense carry requests need an explicit 24,576-token allowance. Direct probes with a 24,000-token budget do not establish facade admission or successful generation of that entire allowance. A global cap increase would expose unrelated aliases to unmeasured requests.

Add trusted alias-map field `output_ceiling`. Default remains 8,192. Its only higher supported value is 24,576, restricted in code to `am4-dense-27b`, API `vllm`, configured model `qwen3-27b`. The tracked alias configuration proposes that value for this entry only. Backend identity is attached after reading config, so an entry cannot impersonate another alias. Caller fields cannot enable the capability.

For **requests above 8,192**, observe ready alias `am4-dense-27b`, served name `qwen3-27b`, and integer live `max_model_len >= 49152` from `/v1/models`. If that declared long capability is unavailable, return HTTP **503** without tokenization or generation. Ordinary requests at or below 8,192 remain available on a ready 32K recipe if they pass exact context admission. No output budget is clamped or silently retried. A caller budget above the configured ceiling remains HTTP 400.

Invalid ceiling types/values or a wrong configured alias/API/model fail closed. A bad alias is marked not ready in `/v1/models` without breaking other aliases. `/tokenize` and chat return a proper 503 for invalid ceiling configuration. The model check is a served-name check, not weight identity proof; deployment preflight must separately compare the served root and recipe manifest.

Admission still uses the selected engine's rendered-template tokenizer, including tools and template options, and requires `prompt_tokens + requested_output_tokens + 32 <= actual_context_length`. Authentication, credential restrictions, 4 MiB body limit, single completion, physical-seat slots, waiting behavior and cancellation are unchanged. Other aliases retain 8,192.

## Deadline and measured limitations

The facade's existing **600-second** connection/watch deadline remains; a proposed door timeout of **1,800 seconds** cannot extend it. This ADR raises only the scoped output allowance, not either deadline.

There is **no measured 24,000-token completion**. The frozen direct probe requested 24,000 but produced **10,435 total output tokens, including 9,644 reasoning tokens, in 234.87 seconds**. The report comparison produced **10,011 output tokens in 205.28 seconds on FP8** versus **13,064 in 272.64 seconds on auto KV**. These different-length outputs do not prove a speed effect, and none proves full 24,576-token completion.

At 40–50 output tokens/s, 24,576 tokens alone would take roughly **492–614 seconds**, plus prefill and other work. Full-budget completion within 600 seconds is unmeasured and may fail. The existing watcher closes the upstream connection on deadline. Before response headers, a non-streaming client may receive no HTTP response; after streaming headers, the stream can terminate incomplete. Where fleet attempt accounting applies, such an interrupted attempt is recorded `unknown`, not success. No deadline relaxation, direct-endpoint fallback, truncation, or success claim is proposed.

## Qualification and deployment gate

This is a capability declaration, not physical or report qualification. Before the final profile is considered qualified, the parent must:

1. Obtain independent code v2 approval, accept or reject the proposed change explicitly under delegation, and record the decision **(decided by Codex)** in the running list. This draft does not self-accept.
2. Own the remote surface and preflight the actual **8090 system-unit facade**, whose pre-change source hash begins `562efcd`, recording full deployed hashes. Do not reroute to a canary. Apply only the reviewed dense-entry map change, not a wholesale shared-map replacement. Initial use of the extended allowance requires the live 49K-or-larger recipe. Any elevated restart goes to Derek's terminal.
3. Run an authenticated facade smoke through 8090 for the 24,576 allowance, ordinary 32K requests, unavailable-window refusal, exact overflow refusal and cancellation/deadline behavior. Verify nonzero reasoning tokens: fleet credentials default `reasoning_effort` to `none`, which can override thinking settings. The smoke must establish the actual caller path, not assume it.
4. Obtain frontier/human accepted verdicts on **two distinct open briefs on the final serving profile**, recording substance, form repairs, aids, actual token counts, finish status, model/recipe hashes and context. No earlier direct 32K/49K probe or different-profile verdict may substitute.

Until actual success, there is no qualified carry/report capability, long-output completion guarantee, concurrency claim, or router acceptance. Preserve and report failures rather than waive admission or deadline gates. Removing the dense field restores the 8,192 allowance; record rollback explicitly, never use it as a silent retry fallback.

## Offline validation

Boundary and HTTP tests cover default 8,192; bounded dense 24,576; exact 32-token context margin and overflow; wrong model/alias/API; invalid settings; 32K long-request 503 with no engine call; ordinary 32K request success; isolated invalid alias listing; invalid `/tokenize` configuration response; tracked map validation; caller override ineffectiveness; tools/template forwarding; and completion-budget normalization. Existing authentication, slot, streaming, cancellation and body/context protocol tests remain in the suite. Offline tests do not certify deployment or long-generation success.
