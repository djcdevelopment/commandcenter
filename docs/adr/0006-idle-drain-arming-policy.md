# ADR-0006 — Unattended autonomy is an authored, suspendable toggle, earned by a supervised cycle

**Status:** Accepted (2026-07-04) — realized in `fleet/bankedfire_drain.py` (bankedfire P5)
**Context sources:** TWO-ECONOMIES-WIND-TUNNEL.html (Δ4 idle-drain); the constitution's
suspendable-override pattern; `knowledge/operating-budget.json` (ratified D1 economics);
this session's live supervised cycle.

## Context

Bankedfire P5 lets the lab dispatch experiment candidates to idle mechnet hardware with no human
present — stage-6 autonomy. The wind-tunnel doctrine already authored the policy shape ("the queue
may self-serve when the marginal cost is zero — an authored policy, on the record, suspendable
like any override"), and the operating budget was ratified with D1. What remained was deciding how
the switch itself behaves, and what earns the right to flip it.

During P5's live acceptance, the B70s were genuinely busy; the correct outcome of the supervised
cycle was a ledgered **no-op** (reason: `busy`). That boring outcome — not a showy dispatch — is
what demonstrated the tick logic was safe to leave running.

## Decision

1. **Arming is an authored object, not a config flag.** The arm state lives at
   `hearth/var/bankedfire_drain_arm.json` in the same authored-field shape as the operating
   budget: on the record, attributable (`--authored-by`), suspendable at any time
   (`python -m fleet.bankedfire_drain --disarm "<reason>" --authored-by <who>`).
2. **Default is DISARMED.** Nothing auto-arms — not installation, not registration of the
   scheduled task, not a passing test suite.
3. **Armed state is earned by a clean supervised cycle** observed end-to-end, where "clean" means
   the tick did exactly what the policy says — including declining to act (a busy no-op counts;
   forcing a dispatch onto contended hardware to make a prettier demo does not).
4. **Opportunistic work always yields.** Mechnet jobs win: occupancy unknown ⇒ treated as busy;
   one drain dispatch in flight at a time; every tick (including every no-op, with its reason) is
   ledgered on the kernel ledger.
5. **Budget enforcement is honest about its limits.** The drain enforces what the budget object
   actually expresses (suspended / unattended-allowed / active hours) and *reports* declared
   ceilings it cannot yet measure (thermal/power — pending Δ2 telemetry) rather than pretending
   to check them.

## Consequences

- Autonomy arrives through economics and stays governable: one command, with a reason and an
  author, stops the wind tunnel; the stop itself is on the record.
- The supervised-cycle bar generalizes: any future unattended loop (watchdog escalations, auto-answer
  classes, replay-driven dispatch) should earn arming the same way — a boring, correct, observed
  cycle, not a green test suite alone.
- No-op ticks are data, not noise: the drain's ledger trail is itself the idle-time observability
  Δ4 promised (when the tunnel *didn't* spin, and why).
- Point 5 creates a standing TODO with teeth: when Δ2 telemetry lands, the declared thermal/power
  ceilings move from "reported" to "enforced" — until then the budget's protection there is the
  hardware's own limits, and the ADR says so out loud.
