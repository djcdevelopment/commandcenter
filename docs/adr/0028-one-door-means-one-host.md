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

## Remediation status (2026-07-30)

| Step | State |
|---|---|
| 1. Repair Ollama (`lib/ollama` runtime) | **DONE** (2026-07-30, operator used the tray's own "restart to upgrade"). Runtime restored, `serviceable=True`, 0.32.5 on disk. The blanket firewall rules did NOT return through the upgrade. |
| 2. comfy_gateway container → native | **DONE.** Container stopped (`unless-stopped`, so it stays stopped and `docker compose start comfy-gateway` reverts). Native gateway healthy, bound `127.0.0.1:8720` — the container had published `0.0.0.0:8720`. |
| 2b. Persistence for the native gateway | **DONE.** Docker's restart policy was its persistence; going native removed that. `comfy/fieldlab/scripts/start-comfy-gateway.cmd` (mirrors the HEARTH wrapper, boot-safe logging included) + scheduled task `ComfyGatewayBoot` (logon trigger, Interactive, RunLevel Limited — it binds loopback and needs no elevation, unlike `HearthGatewayBoot`'s S4U/Highest, which is why that shape was refused). Verified: task start → healthz → loopback bind → log written. |
| 3. Unset `OLLAMA_HOST` | **DONE.** Removed from the User environment; `OLLAMA_KEEP_ALIVE=30m` left alone. The running Ollama still holds the old value, so the loopback bind lands when step 1 restarts it. |
| 4. Remove the two blanket `ollama.exe` rules | **OPEN — operator.** Firewall rules are security settings. |
| 5. Verify the surface off-box | **BLOCKED on 1 and 4.** |

### Environment inheritance is why "just restart it" did not work

Worth recording, because it cost two rounds. A process keeps the environment it was
*started* with, and `explorer.exe` caches the user environment at ITS start. So after
`OLLAMA_HOST` was removed:

- the running server kept binding `0.0.0.0` — it predated the change;
- the tray's own upgrade restarted the tray app but NOT the elevated server process, so
  the bind never moved (and the server stayed 0.32.1 in memory while 0.32.5 landed on disk);
- anything relaunched from the Start menu or the tray would have inherited the stale value
  again, because explorer still holds it.

Only a launch from a process with a cleaned environment binds loopback — or a reboot. The
posture script reports this case as "stale process, restart pending" rather than as a
regression, precisely so the next reader does not go hunting for a drift that is already
fixed.

Until step 1 lands, `python -m fleet.ollama_sentinel` correctly reports
`serviceable=False`: the runtime is still missing. That is the guard doing its job, not
noise.

## Remediation runbook

**Use the script, not these steps.** `tools/ops/fix-ollama.ps1` reports all five checks and
repairs from the cached installer with `-Repair`. It takes no arguments, needs no working
directory, and quotes nothing — written because handing this over as pasted shell fragments
failed repeatedly on paste (a `python -m` with no working directory; PowerShell one-liners
with fragile escaping). It also distinguishes an off-loopback bind that is a genuine
regression from one that is merely a process predating the env change and awaiting a restart.

```
powershell -NoProfile -ExecutionPolicy Bypass -File C:\work\commandcenter\tools\ops\fix-ollama.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File C:\work\commandcenter\tools\ops\fix-ollama.ps1 -Repair
```

The manual sequence is kept below as the record of what the script automates. Ordered so
nothing breaks mid-sequence. Steps 1 and 4 change system state (installing software;
security settings) and are the operator's to run.

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
