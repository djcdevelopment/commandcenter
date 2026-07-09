# ADR-0015 — Repeating ops loops fold into the always-on gateway; no interactive scheduled tasks

**Status:** Accepted (2026-07-09) — **implemented 2026-07-09** (slice 1: patrol/watchdog/drain).

## Context

OMEN accumulated seven custom Windows scheduled tasks (HearthGatewayBoot, OllamaBoot,
BankedfireDrain 30m, MechnetWatchdog 15m, MechnetWatchdogPatrol 5m, OMEN-Perception-Loop
5m, OmenOllamaTracingProxy at logon), all registered **Logon Mode: Interactive only** as
OMEN\derek. Consequences observed:

- Every 5/15/30-minute firing pops a console window on Derek's desktop. It was bad
  enough that he disabled the tasks to watch a movie (2026-07-08) — which silently took
  the watchdog, patrol, and drain loops dark until he remembered to re-enable them.
  A liveness system that must be turned off for movie night will eventually be off
  when it matters (the exact gap Watchfire exists to close, ADR-0007).
- The two "At system startup" tasks (HearthGatewayBoot, OllamaBoot) have **never
  fired** (last-run 11/30/1999): an Interactive-only task with a boot trigger has no
  desktop session to attach to. The durable-restart story actually rests on logon
  tasks and `doorcheck --revive`, not on the boot tasks.

Options considered:
1. Re-register everything "Run whether user is logged on or not" (stored credential) —
   fixes popups and boot triggers, config-only, but leaves seven separately-registered
   tasks with per-task drift risk and a password-change failure mode.
2. Hidden-window wrappers (wscript/conhost) — hides the symptom, leaves the broken
   boot triggers and the sprawl.
3. **Fold the repeating loops into the always-on gateway process as internal timers** —
   the gateway is already the one always-on, single-writer, ledger-native process
   (ADR-0005: one boundary; Banked Fire principle: one scheduler). Patrol, watchdog,
   drain, and perception become in-process scheduled jobs; Windows Task Scheduler
   shrinks to "start the gateway (and Ollama) at boot, headless."

## Decision

Option 3 (Derek, 2026-07-09: "we want 3"). The four repeating loops (patrol 5m,
watchdog 15m, bankedfire drain 30m, perception 5m) move into the gateway as internal
timers, each tick ledgered like any other kernel event. Scheduled tasks reduce to two
non-interactive boot entries: gateway + Ollama.

## Consequences

- Zero console popups; nothing to disable for movie night; no silent watchdog gaps
  from manual toggling. "Movie mode," if ever needed, becomes an authored, ledgered
  gateway toggle (same spirit as ADR-0006's arming policy) instead of Task Scheduler
  surgery.
- One process to keep alive instead of seven registrations; `doorcheck --revive`
  already covers it. Loop ticks must tolerate gateway restarts (timers re-arm on
  boot; no persistent countdown state).
- The perception loop and tracing proxy need a homing decision during the build:
  fold in, or stay as (non-interactive) tasks if they don't belong in the gateway's
  bounded context (ADR-0010).
- Until the build lands, the seven tasks stay as-is — including the Interactive-only
  popups. If they get annoying before the build, option 1's re-registration is the
  approved interim (it does not conflict with this decision).
- After the build: deregister the superseded tasks; update the checkmcp skill notes
  (gateway revive posture) and `fleet/inventory.toml` revive commands if they change.

## Implementation (2026-07-09, slice 1)

Shipped: [`hearth/kernel/timers.py`](../../hearth/kernel/timers.py) (daemon-thread periodic
runner: staggered first fire, skip-if-still-running, per-tick timeout+kill, failures
never stop the schedule) + [`test_timers.py`](../../hearth/tests/kernel/test_timers.py)
(291 suite green). The gateway arms them on start via `start_timers()`
([`gateway.py`](../../hearth/kernel/gateway.py)), with a `--no-timers` /
`HEARTH_TIMERS=off` escape hatch. Each timer fires the *same* argv the retired `.cmd`
wrapper ran (`sys.executable -m fleet.mechnet_watchdog [--patrol-only] --json`,
`... fleet.bankedfire_drain --json`) into the *same* `hearth/var/*.log` file — a trigger
swap, not a behavior change; the loop modules are untouched. Ticks still land on the
ledger through the subprocess's own writes.

Live cutover verified: gateway restarted, all three timers armed and fired their first
tick `exited with 0` (each with a `ledger_event_id`), then the three superseded tasks
(`MechnetWatchdogPatrol`, `MechnetWatchdog`, `BankedfireDrain`) were deregistered (XML
backed up to `hearth/var/retired-tasks-adr0015/`). `doorcheck --revive` already relaunches
through `start-hearth-gateway.cmd`, so the revive path re-arms the timers unchanged.

**Homing decisions:** perception stays a task (its loop lives out-of-repo at
`C:\work\omen-perception\`, outside the gateway's bounded context, ADR-0010); the Ollama
tracing proxy stays a task (a persistent logon service, not a periodic loop). **Not yet
done (Derek, password-gated):** re-registering the two keeper boot entries
(`HearthGatewayBoot`, `OllamaBoot`) "Run whether user is logged on or not" so their
boot triggers actually fire (they have never run — Interactive-only boot trigger).
