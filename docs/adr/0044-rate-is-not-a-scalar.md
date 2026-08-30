# 0044 — A rate is not a scalar: baseline epochs, and degraded states are classified

**Status:** Accepted (2026-08-30)

**Companion to:** `docs/adr#0043` (the rung goes cold when idle — now **one** class of degraded
state rather than the explanation for all of them), `docs/adr#0041` (restart before you measure)

## Context

In a single night this rung served at **four distinct, stable levels**: ~106, ~97–99, ~65, and the
~27.5 plateau. Each was flat under repeated measurement — 0.5–1.5% spread — so none was noise. And
transitions between them happened **without intervention** in both directions: 106 → 65
spontaneously, 65 → 97 spontaneously, and 99.17 → 106.54 spontaneously twenty minutes later, with
nothing changed in between.

Two consequences follow, and they are the two decisions here.

### The baseline was being read as capacity

`rate-baselines.json` held a single `baseline_decode_tok_s: 106.00` and `ff_ratecheck` reported
*"% of known-good"*. That phrasing invites reading 106 as the machine's ceiling. It is not — it is
**the rate observed in one verified epoch**. The temptation, once a new stable level appears, is to
re-baseline to it; that would silently redefine capacity to whatever the machine happens to be
doing and make the gate unfalsifiable.

### "Idle collapse" was becoming a universal explanation

ADR-0043 explained a real phenomenon well, and it began absorbing every low-rate observation. But
**INC-2026-08-30-A does not fit it**, on the single most important discriminator:

| | ADR-0043 idle collapse | INC-2026-08-30-A |
|---|---|---|
| onset | after >~60 s idle | during steady serving, keep-alive running |
| **restart** | **clears it** | **does NOT clear it** (64.14 on a fresh epoch) |
| recovery | on restart | **spontaneous**, ~30 min later |
| level | ~27.5 plateau | ~65, flat across `n_predict` 8→400 |
| prevented by keep-alive | yes (tested to ~6 min) | no |

Restart-clears-it is the *defining* property of ADR-0043's account. A state that survives a restart
is a different state, whatever its mechanism.

⚠ **Both mechanisms remain unknown.** This record classifies observations; it does not explain them.

## Decision

**1. A baseline is an epoch-scoped reference, and health is reported as three things, not one.**

> **baseline epoch + observed rate + acceptance envelope**

`ff_ratecheck` prints the epoch the baseline came from and states the envelope as a fraction *of
that baseline, not of capacity*. `rate-baselines.json` records `baseline_epoch`,
`acceptance_envelope`, and the `observed_stable_levels` seen so far. **The baseline is preserved,
never silently redefined because a new stable level appeared.** Re-baselining stays a deliberate act
requiring verification by other means, and still refuses a high-spread sample.

**2. A degraded state is CLASSIFIED before it is attributed.** The first discriminator is the
restart, because it is cheap and it separates the two known classes:

- **cleared by a restart** → ADR-0043 idle-collapse class;
- **survives a restart** → INC-2026-08-30-A class: cause unknown, **do not assume idle**, and do not
  extend ADR-0043 to cover it.

An observation that fits neither gets its own incident identity rather than being filed under the
nearest existing story.

**3. R10 — recovery after intervention is not evidence the intervention caused recovery.** Record
temporal association only, unless a control or a repeated intervention→response distinguishes it
from a spontaneous transition. Given how often this machine transitions on its own, that
distinction is not academic here.

## Consequences

- **`ff_ratecheck`'s FAIL path now tells the operator to classify before attributing**, and cites
  R10 inline, because the moment of maximum temptation to conclude is the moment the tool prints
  FAIL.
- **The keep-alive's scope narrows.** It addresses the ADR-0043 class and is not evidence against
  the other — INC-2026-08-30-A occurred with pings landing every 30 s throughout.
- **"Is production healthy?" is no longer a yes/no from one number.** It is: which epoch, what rate,
  inside which envelope. Anything comparing rates across epochs inherits R3 — arms measured at
  different times on a machine with spontaneous transitions are not comparable.
- ⚠ **This weakens every cross-epoch comparison in the corpus**, including some that survived the
  suspect-measurement campaign. The interleaved results (B3, B4, B5, the `-ub` A/B) are unaffected:
  interleaving is exactly the control that makes spontaneous transitions show up as within-config
  drift instead of a fake effect. That is now a much stronger argument for the protocol than it was
  when it was written.
- **The incident is deliberately not being poked.** The monitor detects it, it has a distinct
  signature, and spontaneous recurrence will be more informative than intervening until something
  happens. **Waiting is now an experiment** — which is the first time in this campaign that has been
  true, and it is what the monitoring was for.

## Alternatives considered

- **Extend ADR-0043 to cover the new state.** Rejected: it differs on that record's own defining
  discriminator, and stretching it would destroy the one clean classification we have.
- **Re-baseline to whatever the machine is currently doing.** Rejected: it makes the gate
  unfalsifiable by construction — the rung would be "healthy" at any rate.
- **Chase INC-2026-08-30-A now.** Rejected: it cleared spontaneously, so an intervention would be
  scored against a moving target, and R10 says the resulting recovery would prove nothing.
