# ADR-0064: A scoped AM4 dense output ceiling

- Status: **Proposed — not accepted or deployed**
- Date: 2026-10-05
- Goal: G-bench27
- Decision owner: Parent frontier session under explicit operator delegation; any adoption decision must be marked **(decided by Codex)** in the running decision list. Parent plans D021 separately. This proposal does not record that decision.

## Problem

The AM4 alias facade currently caps every output request at8,192 tokens. The requested dense carry profile needs an explicit24,576-token allowance. Direct probes using a24,000-token request are not facade admission evidence and do not establish that24,576 tokens were generated successfully. A broad ceiling change would give unmeasured allowances to unrelated aliases.

## Proposed decision

Add the trusted alias-map field `output_ceiling`. Its default remains8,192. The only higher supported value is24,576, restricted in code to alias `am4-dense-27b`, API `vllm`, configured model `qwen3-27b`. The tracked alias configuration proposes that value for this entry only. The facade attaches alias identity after reading configuration, so an entry cannot impersonate the dense alias through a configured identity field.

For a configured24,576 capability, every completion request must observe readiness for `am4-dense-27b`, served model `qwen3-27b`, and integer live `max_model_len >= 49152` from `/v1/models`. A32K live model refuses this declaration even if a particular small request would fit. Invalid ceiling types/values, another alias/API/model, unavailable live model or an undersized/invalid observed window fail closed. No request field enables this capability.

Admission still asks the selected engine to tokenize its rendered chat template, including tools and chat-template options. It requires `exact_prompt_tokens + requested_output_tokens + 32 <= actual_context_length`. It never clamps, truncates, silently falls back, or rewrites the requested budget. `max_completion_tokens` continues to normalize to `max_tokens` with existing precedence. Defaults on all other aliases remain8,192.

Authentication/credential restrictions,4MiB body limit, single completion, physical-seat slots, waiting behavior, disconnect cancellation, and the600-second serving deadline are unchanged. A long request that cannot finish inside the deadline remains a failure; this ADR proposes no deadline or other gate relaxation and no direct-endpoint workaround.

## Evidence and deployment gate

This is a bounded capability declaration, not physical or substantive qualification. Offline boundary tests and existing facade protocol tests pass. They do not prove the deployed remote service uses this code/config or that the model completes a long carry successfully.

Before treating the new final serving profile as qualified, the parent must:

1. Review this code/config/ADR independently, decide explicitly under delegation, and record the decision with **(decided by Codex)** and the running-list entry.
2. Own the remote surface and follow its preflight/deployment rules; verify effective facade code/config hashes and the live model/window. No remote file or service was changed by this implementation task.
3. Run a facade-level service smoke through the authorized alias, including the24,576 allowance, exact context refusal, and cancellation/deadline behavior. Record actual output/reasoning counts and finish status; a request allowance is not a produced-token count.
4. Obtain frontier/human accepted verdicts on at least two distinct open briefs on the final serving profile, with form repairs and aids recorded. Do not borrow prior32K/49K recipe evidence or raw direct-probe results. Any profile/window change requires matching evidence.

Until actual success, neither the ADR nor alias map grants report qualification, carry capability, a sustained concurrency claim, or a router table acceptance. The parent must preserve refusal/failure data and report unqualified capacity rather than force the gate green.

## Validation and scope

New tests cover the8,192 default, scoped24,576 acceptance at the exact32-token margin, one-token overflow, larger/invalid ceilings,32K/invalid window refusal, wrong live/configured model/API/alias, alias identity spoofing, caller-provided ceiling ineffectiveness, tool/template tokenization preservation, single-completion restriction, and completion-budget normalization. Existing offline protocol tests retain authentication, streaming/cancellation, shared-slot and context coverage.

The tracked config is reviewable input only; do not copy it wholesale over a live shared alias map. Apply a reviewed one-entry change under the parent's sole-writer procedure. Rollback removes the dense `output_ceiling` field (restoring8,192) and records the resulting profile state; rollback is not a silent retry fallback.
