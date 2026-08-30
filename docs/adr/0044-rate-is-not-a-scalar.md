# 0044 — A rate is not a scalar: baseline epochs, and degraded states are classified

**Status:** Accepted (2026-08-30)

**Companion to:** `docs/adr#0043` (the rung goes cold when idle — now **one** class of degraded
state rather than the explanation for all of them), `docs/adr#0041` (restart before you measure)

## Context

In a single night this rung served at **four stable levels**: ~106, ~97–99, ~65, and the ~27.5
plateau. Each was flat under repeated measurement — 0.5–1.5% spread — so none was noise. (⚠ "Stable"
is an observation about flatness under measurement; it does **not** assert they are *distinct
states* — see the caution under Decision 2.) And
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

⚠ **Epoch-scoped does not mean epoch-homogeneous.** The epoch identifies **the reference
contract** — which measurement the envelope is drawn against — and asserts *nothing* about the
machine remaining stationary within it. Tonight is the proof: multiple stable regimes occurred
**inside a single operational epoch**. `baseline_epoch` is a provenance label, **never a guarantee
of equivalent state**, and two readings sharing an epoch are not thereby comparable.

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

⚠ **These are OBSERVED REGIMES, not established mechanisms.** ~106, ~97–99, ~65 and ~27.5 are levels
the machine has been seen to hold; nothing yet shows they are discrete states rather than samples
from a continuous process, and the restart discriminator separates them **behaviourally**, not
causally. **No thresholds or state names beyond that are warranted**, and none are defined here.
Naming a regime is not discovering one — the classification stays behavioural until recurrence
supplies the evidence to decide.

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
- **The incident is deliberately not being poked, and "waiting is the experiment" is concrete.**
  Bidirectional spontaneous transitions mean the monitor can accumulate exactly what an intervention
  would destroy:

  | observable | why an intervention would spoil it |
  |---|---|
  | transition **frequency** | each intervention adds an event of unknown class |
  | **dwell-time** distribution per regime | truncated the moment anything is changed |
  | **directionality** (up vs down) | tonight already shows both; a forced recovery hides the up-transitions |
  | correlation with **idle duration** | ⚠ largely *controlled out* already — the keep-alive pins idle at ≤30 s, so whatever remains is not the ADR-0043 mechanism |
  | correlation with **workload** | the door ledger carries this independently |
  | whether **intermediate regimes recur** | the single strongest evidence for discrete states vs a continuum |
  | whether a restart changes **transition probability** | ⚠ observationally only — R10 forbids reading a post-restart recovery as caused by it |

  ⚠ **Recurrence now carries a pre-committed negative result.** Because the keep-alive
  *continuously enforces* ≤30 s idle, a recurrence of the INC-2026-08-30-A signature would **by
  itself** establish that ADR-0043-style prolonged idleness is **not necessary** for it. That is a
  real finding obtainable by waiting, stated in advance so it cannot be reconstructed after the
  fact — and it narrows the behavioural classification **without** pretending to identify a
  mechanism. The mitigation has become an experimental control, which is not why it was built.

  ⚠ **Resolution limit:** the deep probe samples every 5 minutes, so dwell times are bounded to
  ±5 min and a transition faster than that is invisible. Adequate for frequency and directionality;
  **not** adequate for onset dynamics. That is a known limit, not a defect to fix pre-emptively:
  **raising the sampling rate merely because finer resolution is available would change the observed
  system**, trading epistemic cleanliness for prettier timestamps. Tighten it only if a specific
  question requires it.

  This is the first point in the campaign where waiting is genuinely the informative move, and it is
  what the monitoring was for.

## Alternatives considered

- **Extend ADR-0043 to cover the new state.** Rejected: it differs on that record's own defining
  discriminator, and stretching it would destroy the one clean classification we have.
- **Re-baseline to whatever the machine is currently doing.** Rejected: it makes the gate
  unfalsifiable by construction — the rung would be "healthy" at any rate.
- **Chase INC-2026-08-30-A now.** Rejected: it cleared spontaneously, so an intervention would be
  scored against a moving target, and R10 says the resulting recovery would prove nothing.

---

## Observation log — INC-2026-08-30-A recurrences

Appended as the monitor produces them. **No intervention**; this is the watch posture doing its job.

| deep-probe sample | decode | prefill | note |
|---|---|---|---|
| 00:49:54 | 109.37 | 10.1 | healthy |
| **00:54:56** | **69.22** | 21.6 | **DEGRADED** — a single-sample excursion |
| 00:59:56 | 106.44 | 10.1 | recovered spontaneously, **≤5 min dwell** |
| 01:04 → 02:15 | 101.8–110.3 | ~10.1 | healthy, 15 consecutive samples |
| **02:20:15** | **66.56** | 13.6 | **DEGRADED** |
| **02:25:15** | **66.64** | 13.6 | still degraded; a rate check at ~02:30 read 64.55 |

**The pre-committed negative result has fired.** The keep-alive ran throughout — **477 of 482 ticks
ok, idle enforced at ≤30 s** — so **prolonged idleness is not necessary for this signature**. That
was stated in advance precisely so it could not be reconstructed afterwards, and it is now
established on evidence rather than argument.

What the accumulating record shows so far, stated no more strongly than it supports:

- **At least three episodes**, and transitions occur in **both directions without intervention**.
- **Dwell time is variable**: ≤5 min (00:54), ~30 min (the first), and ≥10 min ongoing (02:20).
  The ±5 min sampling limit bounds the short ones.
- **The degraded level clusters** — 69.22 / 66.56 / 66.64 / 64.55. ⚠ Suggestive of a preferred lower
  regime rather than a continuum, but **four samples is not a distribution** and per ADR-0044 these
  remain observed regimes, not established states.
- **Prefill rises with it** (13.6–21.6 ms against ~10.1 ms healthy) — a correlated second signal, and
  the first hint that the two costs may share a cause. ⚠ Correlation only.

⚠ **Deliberately not restarted.** A restart did not clear the first episode, the 00:54 episode
cleared itself within one sampling interval, and intervening would consume the dwell-time
measurement that is currently the only thing accumulating. Per R10 a recovery following a restart
would not be attributable to it anyway.

### Continuation, 02:25 → 03:10 — episode 3 closed, episode 4 caught with GPU telemetry attached

The log above was written mid-episode. Both episodes have since closed, without intervention.

| deep-probe sample | decode | prefill | note |
|---|---|---|---|
| 02:30:15 | 106.68 | 10.3 | **episode 3 cleared spontaneously** — dwell bounded **5–10 min**, not "≥10 ongoing" |
| 02:35 → 02:55 | 107.10–109.19 | ~10.2 | healthy |
| **03:00:17** | **67.01** | 12.8 | **DEGRADED — episode 4** |
| 03:05:17 | 102.34 | 10.3 | cleared spontaneously; dwell again **5–10 min** |
| 03:10:18 | 100.68 | 10.2 | healthy |

Three facts the earlier entries do not carry:

- **Prefill recovers in lockstep, not just degrades in lockstep.** 13.6 → 10.3 ms at the same sample
  decode returned. The correlation now has a **down-edge**, not only up-edges.
- **Onset ordering.** At the episode-1 onset the 00:32:52 row read decode 65.17 with prefill still
  **10.8 ms**, and prefill only rose to 17.3 ms at 00:33:01. **Decode fell before prefill rose.**
- ⚠ **Provenance gap.** The "rate check at ~02:30 read 64.55" cited above has **no receipt** —
  `ff-receipts.jsonl` has no entry between `00:40:38` and `10:01:54Z`. It was run `--no-ledger` or
  ad hoc. The value is corroborated by an independent receipted check at 03:01:54 (64.55 exactly),
  but the original reading should not be cited as a receipt.

### ⚠ The clock/power hypothesis is REFUTED by direct measurement

ADR-0041, ADR-0043 §"mechanism still unknown", `FACTORY-FRONTIER-CARDS.md` and
`DECISIONS-PENDING.md` all name **GPU clock/power state** as the surviving candidate, blocked on
HWiNFO telemetry. **That blocker was false and the hypothesis is now dead.**

`b70tools` already emits `gpu.frequency_hz`, `gpu.voltage_v` and `gpu.energy_j_counter` per card at
1 Hz — every consumer script filtered those fields out, keeping only temperature. The
*"IGCL frequency is unusable on the top slot"* limitation is inherited from a **different rig**
(5900X / Win10 / driver 8826 / DisplayPort-attached card, `b70tools/README.md:105-121, 390-394`) and
does not hold on OMEN, where neither card drives a display. Its tell was 5.117 V / 8.55 GHz; nothing
like that appears here. The collectors self-declare `PassiveSafe` (no VkDevice, no GPU allocation),
which is stronger than ADR-0041's empirical harmlessness finding.

Two `ff_ratecheck` arms, ~4 min apart, inside one continuous 1 Hz capture, same epoch, no restart
between them (`E:\work\battlemage\ff-probes\statewatch-20260830\`):

| | DEGRADED 03:01:54 | HEALTHY 03:06:01 |
|---|---|---|
| decode | **64.55 tok/s** (61%) | **98.47 tok/s** (93%) |
| GPU clock, both B70s | **2800 MHz** | **2800 MHz** (no change event fired between the windows) |
| GPU voltage | 1.04 / 1.055–1.06 V | 1.045 / 1.055 V |
| peak power, both cards | 160.9 W | 173.5 W |
| **energy per token above idle** | **1.564 J** | **1.240 J** |

**The degraded GPU is fully clocked, fully volted, drawing comparable power — and spends 26% *more*
energy per token.** It is not throttled; each token costs it more work. That also disfavours a
simple host-side stall, which would leave energy-above-idle per token roughly unchanged rather than
raising it. Two candidates survive: **a less efficient kernel/dispatch path** selected in the
degraded state (the same shape as this lab's own `mul_mat_vec` knee finding), or **busy-wait/spin**
burning power without productive work.

⚠ **Bounds.** n=1 per arm, no replication. The healthy arm is the ~97–99 regime, **not** the ~106
one, so this pairs two of the four observed levels rather than degraded-vs-best. Token count
approximated at 404 (4×100 + warm-up, prefill included); idle floor taken as the measured
26.2–26.6 W/card. Wall-clock mapping of the telemetry was validated independently — derived
clock-change timestamps matched an externally known load window to the second.

