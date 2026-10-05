# Proposed actual-8090 facade smoke — not invoked

Offline proposal for independent Opus review. Parent owns eventual invocation after the accepted ADR-0064 candidate is staged, Derek has performed the necessary system-facade restart, the activated code/alias hashes are recorded, AM449K is healthy and solely owned, and its guard is running. No service, SSH, inference, credentials or live files were accessed by the builder. Do not use the separate18095 canary or direct-engine inference to bypass8090.

`tools/ops/bench27_am4_facade_smoke.py` fixes inference to `10.44.0.2:8090` and alias `am4-dense-27b`. It uses the existing `Backend.token()` interface with `auth_env=AM4_VLLM_TOKEN`: the parent-owned caller unit supplies the existing EnvironmentFile. It opens no credential files, accepts no token argument, logs no headers/token, and records only static in-script error codes, exception type, stage and sticky stop reason on failure; never external exception text. Never print or source credentials in an interactive command transcript. The script reads remote nonsecret hashes and engine metadata/metrics using bounded read-only SSH; all model inference goes through the authenticated facade.

Expected runtime is1–3 minutes. Driver deadline150 seconds, short socket read cap60 seconds, cancellation drain20 seconds, independent read-only cleanup observation bounded at170 seconds from start (including SSH timeouts), outer transient-unit maximum180 seconds and TimeoutStopSec10. These are smoke bounds; they do not broaden the facade's existing600-second per-request cap or imply a full24576-token generation can finish under that cap.

Checks, in order:

1. NEW evidence directory; live guard/no sticky trip; zero AM4 door leases; read-only SSH audit of actual model root `/home/derek/models/qwen3-27b-gptq-int4`, id qwen3-27b, window49152, source hash `/home/derek/am4-fleet-node/scripts/oxen-facade.py`, alias-map hash `/home/derek/.config/am4-fleet/alias-backends.json` and exact dense script SHA d6d6887f…. Require idle running/waiting counters. Source hashes alone cannot prove which bytes a pre-existing process loaded: parent's recorded system restart/PID/ExecStart verification is mandatory before this script.
2. Exact-tokenize through facade `/tokenize`, reserve24576 and ask a short arithmetic question with thinking enabled. Require stop/DONE, exact prompt usage, content323, nonempty separate reasoning, reported reasoning_tokens>0 and no think tags in visible content. Capture raw SSE, request/response and usage. Counter delta must be exactly one success. This tests reservation and reasoning-mode preservation, not24576 generated tokens or delivery qualification.
3. Construct a synthetic repeated-x prompt, count its complete messages using the actual tokenizer, and require24576+128≤exact input≤49152−128, so input fits independently while its output reservation overflows. Send it unchanged through8090; require HTTP400 and the exact JSON object `{"error":"rendered prompt COUNT + output 24576 exceeds context 49152"}`, no new success, and idle engine. The validator builds the exact message from actual count; a generic engine400 cannot pass. No clipped allowance, bypass or arbitrary byte-to-token assumption.
4. Send one owned long counting request through8090, retaining its first reasoning/content SSE delta. Before closing its socket, verify exactly one running engine request and no waiting requests. Disconnect the owned socket and require zero running/waiting within20 seconds plus a second idle observation and no increase in completed (`stop` + `length`) requests. Record the abort-counter delta separately; a single abort increment is compatible with successful cancellation, while other terminal-counter changes fail. If it already finished, cancellation is **not demonstrated** and smoke fails. Any failure closes owned sockets and attempts a cleanup drain; unconfirmed drain means retain hold and parent inspects counters. No claim of cancellation comes from closing a completed call.

Raw audits include model and metrics evidence, never token files. Sole ownership remains a prerequisite: sampled counters cannot exclude failed/cancelled outside traffic. The script holds no capacity lease itself; it checks read-only and relies on the parent's explicit AM4 pin/drain hold. An existing door/B70 guard must not be stopped to run this smoke.

## Parent invocation proposal — not executed

Replace unit/hash/path placeholders using the reviewed activated state. The current active guard unit must be used, not a guessed name. The EnvironmentFile path below is the existing backend-token source, referenced only; no builder read it.

```bash
# Requisite never starts the guard; the shell chain also refuses launch if inactive.
systemctl --user is-active --quiet ACTUAL_AM4_GUARD_UNIT && \
systemd-run --user --unit=bench27-am4-facade-smoke \
  --property=Requisite=ACTUAL_AM4_GUARD_UNIT \
  --property=After=ACTUAL_AM4_GUARD_UNIT \
  --property=RuntimeMaxSec=180 \
  --property=TimeoutStopSec=10 \
  --property=EnvironmentFile=/home/derek/.config/omen-vllm/hearth-backends.env \
  --working-directory=/home/derek/work/commandcenter-linux-flash \
  /usr/bin/python3 tools/ops/bench27_am4_facade_smoke.py \
  --out /ABS/NEW-FACADE-SMOKE \
  --guard-log /ABS/ACTUAL-GUARD.jsonl --trip /ABS/ACTUAL-GUARD.jsonl.tripped \
  --coordination-db /home/derek/hearth-production/var/execution/coordination.sqlite \
  --facade-sha256 REVIEWED_ACTIVATED_8090_SOURCE_SHA \
  --alias-sha256 REVIEWED_ACTIVATED_ALIAS_MAP_SHA
```

Use the existing door EnvironmentFile without inspecting credential bytes or claiming its class from inspection. Actual nonzero separated reasoning and numeric reasoning-token usage through8090 are mandatory behavioral proof that the supplied credential preserves thinking. A missing token refuses before output creation/network. This is an explicitly parent-invoked smoke, not a timer, capability approval or automatic dispatch. Exit0 means smoke_passed_not_qualified; every other result retains the qualification hold. No elevated operation is in the script.

## Offline versus live coverage

Targeted isolated unit tests run without network/services/credentials: exact model/root/window/hash checks; 32K/unavailable-model audit refusal; actual8090/alias/reservation constants; missing-token refusal; owned socket shutdown; observed-running requirement; exact headroom check; two idle drain observations, connect-time guard race, post-failure cleanup, unique overflow response, recorded engine reasoning usage and exact24544 admission boundary. They do **not** claim a live32K facade refusal. ADR-0064's separate handler tests cover ordinary32K and unavailable-window budget behavior offline; attach their reviewed result separately. Physical smoke here uses only the currently selected49K recipe. Do not switch the recipe merely to manufacture those status cases. A live failure is evidence to diagnose, not authority to extend the deadline or disable a gate.

## Historical audit pointers

`audit-pointers.json` binds the existing AM4/OMEN shard SHA manifests and toolkit wheel/compile records. They are historical: before qualification, parent still verifies current weights/config/tokenizer, extracted toolkit/runtime and cache state, and records current8090 source/alias hashes separately. No historical record is a current content attestation. The final49 runbook's fail-closed audits/explicit-pin hold apply unchanged.

## Review evidence and limits

The accepted canonical facade `am4-fleet-node/scripts/oxen-facade.py` implements alias `/tokenize` in `tokenize_alias` and calls `guard_context` before upstream inference. Its exact overflow `ValueError` becomes the JSON `error` string with HTTP400; `proxy_chat_completions` forwards upstream SSE chunks unchanged. `tools/ops/fixtures/bench27-am4-reasoning-usage.json` copies only usage from the hash-bound canonical Nfp8-32 report work SSE:10011 completion tokens,8829 reasoning tokens. That capture is engine-direct evidence, not a prior8090 smoke. Source establishes passthrough; this live smoke must still demonstrate nonzero numeric reasoning usage through8090. Missing usage fails, never silently weakens the gate.

The driver explicitly loads `/home/derek/hearth-production/backends-linux.toml`, ignoring HEARTH_BACKENDS/default-pool selection, records that path and file SHA256, and refuses endpoint/auth-env drift; its summary hashes the imported probe, report and backend helpers. The parent must still bind the reviewed driver/helper versions and the actual restarted system-unit PID/start time/ExecStart before invocation. File hashes alone cannot prove loaded code. No automatic service start/restart is performed.

Saved AM4 metrics at `/home/derek/work/lab-rnd/research/evidence/bench27-am4-report-20261005/Nfp8-32/before.metrics.txt` expose `finished_reason` labels stop, length, abort, error and repetition. The smoke retains total success accounting for the short and overflow tests, and checks stop+length separately for cancellation, allowing only a zero-or-one abort delta and no other terminal increment. This avoids rejecting a correct cancel merely because vLLM records it under abort. The offline production-TOML test reads declarations only, never the token EnvironmentFile or its values. Cleanup ends at170 seconds from driver initialization, leaving additional process-start/join/summary margin under the180-second unit limit.
