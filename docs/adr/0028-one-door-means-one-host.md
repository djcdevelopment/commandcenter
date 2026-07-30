# ADR-0028 — One door means one host: a containerized MCP must not become HEARTH's second mouth

**Status:** Accepted (2026-07-30) — remediation runbook below; the code change required is
none.
**Amends:** ADR-0005 (one boundary, three planes), ADR-0022 (container access needs no
network exposure).
**Context sources:** operator account (Derek, 2026-07-30), `comfy/fieldlab/status/program-status.json`,
`comfy/fieldlab/autonomous/valheim-lab.compose.yml`, `fleet/ollama_sentinel.py`,
live inspection of OMEN's Ollama bind, firewall rules and container env.

## Context

### How it happened (operator account, 2026-07-30)

While building the comfy Valheim fieldlab, an MCP server was added for its actual job:
RPC to headless testers — gather log data, post commands. A scoped, project-local surface.

Then it drifted. That standalone Valheim-mod MCP **got merged into HEARTH tooling**, at a
time when `comfy` and `lumberjacks` had not yet been folded into `baseline`. So a
project-scoped surface inherited HEARTH's responsibilities without inheriting HEARTH's
placement — and it was hosted **inside the Docker image used for telemetry**.

The consequence followed mechanically. Reaching Ollama "via HEARTH" from inside that
container could not use loopback, so the access was effectively **reverse-proxied into the
Docker image**. In the operator's words: *"which worked for a while because I was only
building inside comfy… until, I wasn't."*

That is the whole failure shape. It was not a bad decision; it was a correct local decision
whose blast radius grew when the work moved out of comfy.

### What was verified on OMEN (2026-07-30)

- `OLLAMA_HOST=0.0.0.0:11434`, persisted as a **User** environment variable. Ollama binds
  every interface.
- `comfy/fieldlab/autonomous/valheim-lab.compose.yml` sets
  `COMFY_OLLAMA=http://host.docker.internal:11434`; the running container carries it; and
  two files inside the image read it, so the dependency is **live, not vestigial**.
- Windows had auto-added **two `ollama.exe` inbound ALLOW rules** — Public profile,
  `remote=Any`, `LocalPort=Any`, one TCP one UDP. Wi-Fi is categorized **Public** with
  Internet connectivity. The deliberate, correctly-scoped rule
  (`Ollama LLM (tailnet only)`, `remote=100.64.0.0/10`, port 11434) was made redundant by
  them.
- `comfy_gateway` exposes its **own** `local_generate` beside HEARTH's — the surviving
  artifact of the merge, and the reason a second inference path exists at all.
- Separately and concurrently: Ollama's `lib/ollama/` runtime had been emptied to a single
  `Ollama.lnk`, so the server answered `/api/tags` and `/api/version` normally while every
  generate returned HTTP 500. Unrelated cause, but it is what exposed all of the above.

## Decision

**A containerized tool surface may not be the reason a host service leaves loopback.**

1. **The Valheim/telemetry MCP is a project-scoped surface, not a HEARTH plane.** Its job is
   RPC to headless testers. It must not carry inference responsibilities on HEARTH's behalf.
2. **`comfy_gateway` runs natively on the host** (`fieldlab/scripts/start-comfy-gateway.ps1`,
   binds `127.0.0.1:8720`). Containerizing it is the alternate, and choosing it does not
   license a non-loopback Ollama.
3. **Ollama binds loopback.** `OLLAMA_HOST` is unset; the default is `127.0.0.1:11434`.
   No code change is needed for this — `comfy_gateway`'s `DEFAULT_ENDPOINT` is already
   `http://127.0.0.1:11434` and `COMFY_OLLAMA` merely overrides it, so removing the
   override restores the original design by itself.
4. **Inference has one door.** `comfy_gateway`'s duplicate `local_generate` should be
   retired in favour of HEARTH's; until it is, it is a second path kept honest only by both
   living on loopback. (Follow-up, not done here — it changes a tool surface in active use.)
5. **A non-loopback bind of any inference service is an explicit, scoped, authored act** —
   the ADR-0019/0022 shape: named rule, narrow `remote`, recorded reason. Never a blanket
   program-scoped allow, and never a side effect of a container's convenience.

## Consequences

- Ollama becomes unreachable from containers, VMs, the LAN and the tailnet. That is the
  point. Anything that needs inference from off-host goes through HEARTH, which
  authenticates (`X-Hearth-Key`); Ollama does not authenticate at all, which is precisely
  why it must not be the thing exposed.
- The `Ollama LLM (tailnet only)` rule becomes moot under a loopback bind. Left in place as
  a deliberate operator artifact; it is correctly scoped and harmless.
- `fleet/ollama_sentinel.py` gains a **serviceability** check beside its bypass detection:
  every 120s tick verifies the `llama-server` runtime exists on disk, and on a throttled
  interval (30 min default) performs a real one-token generate. Rationale: on 2026-07-30
  every liveness signal in the lab read green for ~30 minutes while no dispatch could
  succeed, because reachability and serviceability are different questions. The tick exits
  non-zero when Ollama is up but cannot serve, and the gateway timer logs exit codes.
  A skipped generate probe reports `ok: null` and never counts as a pass.
- Recurrence is now detectable rather than merely documented: an `OLLAMA_HOST` that leaves
  loopback shows up as off-loopback peers in `ollama-direct.ndjson`, which the sentinel
  already records and labels by source (`lan`, `tailnet`, `hyperv-nat`).

## Remediation runbook

Ordered so nothing breaks mid-sequence. Steps 2–5 change system or security settings and are
the operator's to run.

1. **Repair Ollama** (fixes the outage; independent of the rest). Reinstall over the top
   from ollama.com — it restores `lib/ollama/`. Verify:
   `python -m fleet.ollama_sentinel --probe-generate` → `serviceable=True`.
2. **Switch comfy_gateway to native.** `docker compose -f comfy/fieldlab/autonomous/valheim-lab.compose.yml stop comfy-gateway`
   then `pwsh comfy/fieldlab/scripts/start-comfy-gateway.ps1`. Confirm
   `curl http://127.0.0.1:8720/healthz`. It will pick up loopback Ollama by default.
3. **Unset the drift.** `[Environment]::SetEnvironmentVariable('OLLAMA_HOST', $null, 'User')`
   then restart Ollama. Confirm the bind: `netstat -ano | findstr 11434` should show
   `127.0.0.1:11434`, not `0.0.0.0:11434`.
4. **Close the blanket firewall rules.**
   `Get-NetFirewallRule -DisplayName 'ollama.exe' | Remove-NetFirewallRule`
   (removes both auto-added Public rules; leaves the tailnet-scoped one).
5. **Verify the surface.** From another host on the LAN and over the tailnet,
   `curl http://<omen>:11434/api/version` must fail. From OMEN, loopback must succeed and
   `local_generate` through HEARTH must succeed.
