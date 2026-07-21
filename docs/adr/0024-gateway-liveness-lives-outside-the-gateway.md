# 0024 — The gateway's own liveness watch lives outside the gateway

**Status:** Accepted (2026-07-20) — amends [0015](0015-ops-loops-fold-into-the-gateway.md)
(ops loops fold into the always-on gateway) with a single, bounded exception.

## Context

ADR-0015 folded every repeating ops loop — patrol, watchdog, drain, sentinel — into the
always-on gateway as in-process daemon timers, and deregistered the standalone tasks. That was
right for loops that watch *other* things. It is wrong for exactly one loop: the watch on the
gateway's **own** liveness. An in-process watchdog dies with the process it is meant to revive.

A live outage on 2026-07-20 made the gap concrete, and it was not one failure but three
compounding:

1. **The listener died but the process survived.** `WinError 64` ("the specified network name is
   no longer available") on `accept()` tore out the loopback socket while the python process
   stayed alive — the well-known WSL-mirrored-networking fate-sharing (ADR-0022 records the
   benefit of mirrored mode; this is its cost). `fleet/inventory.toml` carries a
   `revive = doorcheck --revive` hook, but since ADR-0015 the watchdog that runs it is *inside*
   the gateway, so it went down with the door. Nothing external noticed.

2. **The boot could not restart because logging was on its critical path.**
   `start-hearth-gateway.cmd` redirected the python launch straight to a fixed
   `gateway-task.log`. The socketless zombie's parent `cmd` still held that file open, so the new
   boot's redirect could not open it, python never launched, and `HearthGatewayBoot` exited 1
   **having written nothing to the very log meant to explain it.** Diagnosis is precisely when
   logging is least available.

3. **The port-based kill could not see a socketless zombie.** `restart-hearth-gateway.cmd` kills
   by matching `127.0.0.1:8710` in `netstat` (already hardened for a *bound* zombie whose accept
   loop died). A zombie whose socket is *fully gone* has no `:8710` row at all, so the scan
   matched nothing.

The compound effect turned what should have been a ~30-second restart into a ~40-minute outage,
cleared only by a reboot.

## Decision

**1. One liveness loop lives outside the door.** A new scheduled task `HearthGatewayWatchdog`
(`hearth/etc/watchdog-gateway.cmd`, `SC MINUTE / MO 3`) runs `doorcheck --json --facet door` and,
on **two consecutive** failed probes, triggers `HearthGatewayRestart`. This is the deliberate,
bounded exception to ADR-0015: the gateway cannot watch its own aliveness, so this one watch is
external. It stays as small as possible — a liveness probe and a restart trigger, nothing else.

Three properties make it safe rather than a thrash source:

- **Revives through the high-integrity restart task, never by launching the gateway itself.** A
  watchdog running at limited integrity that launched the gateway would start a door unable to do
  Hyper-V admin (`checkpoint_vm`). Going through `HearthGatewayRestart` (RL HIGHEST, S4U)
  preserves integrity, and that task's S4U registration is exactly what lets a medium caller
  trigger it without a UAC prompt.
- **Debounced.** A single failed probe — a transient blip, or a probe caught mid-bounce — never
  acts. Only two consecutive failures, ~3s apart, trigger a restart.
- **Its own logging cannot wedge it.** The tick's log is best-effort and falls to `NUL` rather
  than aborting, so a watchdog-log lock can never defeat the watchdog. The sleep is `ping`, not
  `timeout`: `doorcheck --revive` launches with `stdin=DEVNULL`, and `timeout` aborts under
  redirected stdin.

**2. A service must not depend on a log redirect to start.** `start-hearth-gateway.cmd` now
probes the primary log; on a bounce the old wrapper's handle lingers a second or two, so it
retries a few times (consolidating normal restarts onto the primary), and only if the handle is
genuinely wedged does it fall back to a unique per-launch file. The door now comes up regardless
of the log's state. The retry sleep is `ping` for the same redirected-stdin reason.

**3. Socketless-zombie process reaping is deliberately deferred.** With decision 2, a lingering
zombie's log-hold no longer blocks the boot, and a zombie with no socket does not block the port
either — so reaping it is hygiene, not correctness. A command-line-matched kill was prototyped and
rejected: matching a process's command line is unreliable across integrity levels (a medium caller
cannot read a high-integrity process's `CommandLine`) and fragile to quote inside a batch
`for /f`. The zombie is reaped by the next reboot. Simple-and-correct over clever-and-untested.

## Consequences

- The door now self-heals from a crash without a human: the external watchdog revives it within
  one tick (~3 min worst case), through the integrity-preserving restart path.
- The boot is robust to a stale log handle — the specific failure that caused the 40-minute
  outage cannot recur; the worst case is a stray `gateway-task-<n>.log` file.
- `mechnet-watchdog` stays deregistered (ADR-0015); this is a *narrower* task than the one 0015
  retired — gateway-liveness only, not fleet-wide coherence.
- Rollback is one command: `schtasks /Delete /TN HearthGatewayWatchdog /F`. The script changes
  are self-contained and revert cleanly.
- **Verification honesty.** The watchdog's up-path (healthy door → no-op) and the restart-revives
  path were verified live. The full down→revive transition was verified by component
  (`doorcheck --facet door` returns exit 1 when the listener is down, per doorcheck.py:670; the
  restart task revives a down door, seen repeatedly) rather than by a single staged kill, because
  the test shell runs at lower integrity than the gateway and cannot kill it to stage the outage.
- **Follow-ups:** (a) the watchdog runs in the logged-on user context to match the door's
  login-start model; making it S4U (run whether logged on or not) to exactly match the door needs
  elevation and is deferred. (b) The mirrored-WSL fate-sharing that *causes* the listener death
  (ADR-0022) is mitigated here, not removed — the facade's per-call reconnect and this watchdog
  together make it survivable, but a door that never loses its socket would be better still.
