# FX99 Hermes controller: implemented, qualification incomplete

2026-09-19. The CPU-only controller runs on FX99. It uses the existing AM4
Dense27B endpoint; no model was installed or loaded on FX99 by this work.
128k is the configured context limit, not a new 128k quality benchmark.

## Use

`ssh -t fx99 tmux new-session -A -s hermes-fleet /home/derek/.local/bin/hermes-fleet`

Hermes is pinned to 345cd2b057a452236de401d3534b8502a7465e8d, MCP 1.28.1.
Its CLI/session state lives under /home/derek/.config/hermes-fleet. The model and
compression profile use am4-dense-27b on authenticated AM4 :8090, which forwards
to the already-running native :18090 seat. 131072 tokens, one physical slot.
Moving the controller does not free the AM4 model's resident weights/KV.

HEARTH :8712 binds loopback on OMEN and reaches FX99 through an OMEN-originated
SSH reverse tunnel, also loopback-only. Restricted tools expose bounded source
and knowledge reads plus receipt-based builds; no shell, direct generation,
model lifecycle, generic writes, or cloud fallback. Global builder runner.json
files remain unchanged. Per-run am4-shared-27b routes builders 2/3 to the same AM4
slot, each with its own credential. Queue waiting is bounded; a disconnected
waiter does not later run, and active client disconnect cancels upstream work.

## Verified

- Genuine Hermes session 20260919_215628_f4a9d7 created and dispatched receipt
  br-20260919-215719-70852cc4. Resume after the corrected URL/path-guard failure
  completed in 19 seconds. This was assisted recovery: Codex fixed the guard and
  supplied the retry instruction; Hermes performed the actual dispatch.
- Saved conductor metadata, checkpoints and results retain manual promotion and
  the preset. Actual result confirms promoted:false and both candidate commits.
  Two live repeat-dispatch calls were suppressed. Receipt sync did not harvest
  or push externally. Missing required artifacts are now exposed independently
  of the runner's exit code or old assay score.
- Live MCP initialization, credential-read denial, bounded authored-catalog
  access, native readiness/context, and stopped old AM4 route passed. See
  controller-check.json. Query guard registration follows the original knowledge
  provider when mounted through the restricted aggregator; read_file still
  cannot bypass the knowledge guard.
- Listener crash recovery and tunnel reconnect were exercised. Hidden Windows
  current-user logon startup is installed; this requires login, not an unattended
  boot service. Native AM4 PID 1738436 stayed running. OMEN's production door
  answered OK throughout final checks.
- 47 physical AM4 attempts were captured: Hermes 7, builder 2: 20, builder 3: 20.
  Canonical ExecutionLedger import and repeated import produced no duplicates.
  Known usage: 526,449 input, 5,920 output tokens across 46 attempts; one attempt's
  usage is unknown. These include physical prompt usage, not just new uncached
  tokens. Framework/session retry grouping and energy are not reconstructed.

## Limits and handoff

The local coding qualification FAILED; see CAPACITY-REPORT.md. Real compression,
interrupted-generation session recovery and long-context task quality remain
unqualified. No extra inference was started after the failed artifact checkpoint.
Automatic model rotation, 256k and cross-host MemSplice recovery remain deferred.

The kernel append lock passed independent-process tests. Hermes uses a separate
canonical kernel stream under the operator state directory because production
still has a pre-lock writer loaded; combining those streams awaits a coordinated
production upgrade. No production ledger was rebuilt. Execution accounting uses
the existing shared, cross-process-safe ExecutionLedger API.

FX99's resident model was qwen2.5:7b at the start and llama3.1:8b at the final
read. No FX99 inference/lifecycle request was made by this workflow; the change's
owner was not established. Therefore an unchanged-fleet assertion is NOT made.
All captured Hermes/builder inference here went to AM4. Neither B70 was used.
