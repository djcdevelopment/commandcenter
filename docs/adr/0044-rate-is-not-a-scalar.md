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
- ~~**The degraded level clusters** — 69.22 / 66.56 / 66.64 / 64.55.~~ ⚠ **FALSIFIED within hours by
  its own follow-on data — see the full episode table below.** The caveat attached to it (*"four
  samples is not a distribution"*) was correct, and the clustering did not survive: the next two
  episodes read **46.13** and **1.24** tok/s. There is no preferred lower level.
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
  ⚠ **"Lockstep" is retracted — see the dissociation section below.** It was written from five
  hand-picked samples; 64 paired samples give Spearman −0.60, and the two signals demonstrably
  come apart. The down-edge observation stands; the word "lockstep" does not.
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

> ⚠ **RETRACTED — the paragraph above reasons wrongly from its own evidence.** "Each token costs it
> more work" and "that disfavours a simple host-side stall" are **both false**, and the GPU busy-time
> counter settles it (see *The GPU does the same work in every state*, below): busy time per token
> moves **+5.5%** while wall time per token moves **+97.4%**. The stall inference failed because it
> assumed a stalled GPU falls toward idle power. **It does not — it stays pinned at 2800 MHz /
> 1.04–1.06 V, which is exactly what the measurement two paragraphs up established.** A clocked-up,
> non-computing GPU burns near-load power while waiting, so a host stall **raises** energy per token
> precisely as observed. The energy result stands; the mechanism read off it does not.
> **Both surviving candidates are now disfavoured**: busy-wait/spin would *raise* busy-ns (it barely
> moves), and an inefficient kernel would raise busy per token by far more than 5.5% while wall time
> doubled.

**Interleaved idle-floor control.** Subtracting a single *assumed* idle value would exaggerate the
per-token gap if the degraded regime carried a higher background floor. It does not. Each arm was
bracketed with its own locally measured floor, taken from the same 1 Hz capture immediately before
and after that arm:

| arm | pre-window floor | post-window floor | local floor used |
|---|---|---|---|
| degraded | 26.6 W / 26.6 W | 26.6 W / 26.6 W | **26.6 W per card** |
| healthy | 26.6 W / 26.6 W | 26.6 W / 26.6 W | **26.6 W per card** |

All eight bracket medians agree (n ≈ 30–34 samples each), so the two regimes share one idle floor
and the J/token figures are unchanged by the control. **Sensitivity:** the degraded floor would have
to be **34.9 W/card — 31% higher than measured** — to erase the difference. This is the same
interleaved discipline that caught the `-ub` single-arm error (R3).

⚠ **Bounds.** n=1 per arm, no replication. The healthy arm is the ~97–99 regime, **not** the ~106
one, so this pairs two of the four observed levels rather than degraded-vs-best. Token count
approximated at 404 (4×100 + warm-up, prefill included). Wall-clock mapping of the telemetry was
validated independently — derived clock-change timestamps matched an externally known load window
to the second.

**Next, in order** (Derek, 2026-08-30): (1) passive capture across a deliberate ≥120 s idle gap —
does ADR-0043's idle-triggered state share this clock/voltage/energy **phenotype**, or separate from
it? The useful outcome is phenotype match-or-separate, not "rate fell". (2) Fixed-shape
`llama-bench` during a *naturally occurring* episode — same bench shape in both states, no restart.
If the fixed kernel slows while clocks stay pinned, the inefficiency lives **below** server
orchestration; if the bench stays normal while server decode degrades, the search moves **up** into
scheduling/runtime. ⚠ Neither may perturb the running 1 Hz passive capture before it completes.


### The 30 s ping was a rate instrument all along — and it breaks the shared-cause reading

The keep-alive's 30-second tick fires `prompt_n:1` and records `prompt_ms`. That is a
**fixed-shape prefill measurement taken every 30 seconds, all night, at zero added load**, and
nothing in this campaign had read it as an instrument. The resolution warning at
[§Resolution limit](#) does not bind: **the finer sample was already being taken**, so reading it
changes nothing about the observed system. No request was issued to `:8082` for any of what
follows.

**Instrument validation first, because the conclusion depends on it.** Every deep probe measures
`prompt_ms` and `decode_tok_s` **in the same request** — 64 perfectly paired samples since
2026-08-29 22:00 local.

| | decode < 90 | decode ≥ 90 | |
|---|---|---|---|
| **prefill > 12.24 ms** | 7 | **8** | precision **0.47** |
| **prefill ≤ 12.24 ms** | 1 | 48 | |
| | recall **0.88** | | Spearman **−0.60**, Pearson −0.30 |

Prefill is a **sensitive** degradation detector and a **poor** one: it catches 7 of 8 degraded
decodes, and cries wolf 8 times out of 56 healthy ones.

⚠ **The dissociation is threshold-independent, so it is not an artefact of the 12.24 ms cut.**

- The **worst prefill of the night — 24.50 ms at 23:41:21, 2.4× the 10.20 ms baseline — carried
  decode 107.83 tok/s.** Fully healthy.
- **00:32:52 ran decode 65.17 with prefill at a normal 10.80 ms.** Already in the log above as the
  onset-ordering fact; it is also a clean counterexample in the other direction.
- The two classes' prefill ranges **overlap almost completely**: false alarms 14.2–24.5, true
  degraded 10.8–21.6. No cut separates them.

**Therefore the "shared cause" hint is weakened, not strengthened.** The observation log calls
prefill "a correlated second signal, and the first hint that the two costs may share a cause", and
the 03:10 continuation escalated that to "recovers in lockstep". Correlated: yes, r = −0.60.
Lockstep: **no**. `INC-2026-08-30-A` has **at least two separable channels**, and a mechanism
hypothesis must now explain why one can move while the other does not.

**Episode coverage the 5-minute grid cannot see.** Grouping hot pings into episodes (a >90 s gap
splits) gives **28 prefill episodes against the 7 the deep-probe grid caught**; **8 are entirely
invisible to it**, including a 93-second, 4-sample episode at 03:22:01. Twelve of the 28 carry
n ≥ 2 samples, so they are states rather than single-sample jitter. Forward test: a hot ping is
followed within 60 s by a degraded deep probe **11 times in 19**; a cold ping **2 times in 86**.

⚠ **The competing hypothesis is not tested, and it is a good one.** The keep-alive sends the *same
prompt every time* against a **2-slot** server (`-np 2`). `prompt_ms` may therefore be measuring
**KV cache-hit versus cache-miss** — slot eviction, or a different slot serving the request — and
not GPU health at all. That would produce hot prefill beside healthy decode with no GPU state
involved, which is exactly the false-alarm pattern above. Until it is excluded, the 28-episode
count is a count of *prefill excursions*, not of incidents.

Further caveats: `prompt_ms` on a 1-token prompt is ~10 ms and dominated by fixed overhead, so it
is sensitive but noisy, and 16 of the 28 episodes are single samples; the one ping at
**22:23:53 reading 2462.2 ms** is almost certainly a restart or model load and is excluded as an
artefact rather than explained; the largest prefill-only cluster (23:19–23:48) **predates the
02:57 telemetry capture** and so has no GPU telemetry; and this is one night, one epoch, one
server instance.

**What it buys, at no cost.** Both phenotypes occur **naturally inside the live 1 Hz capture
window**:

| window | phenotype | decode at the deep probe |
|---|---|---|
| 02:59:23–03:02:59 | prefill hot **and** decode degraded | 67.01 |
| 03:04:32, 03:06:05–03:06:36 | **prefill-only** | 102.34 |
| 03:18:26–03:20:28 | prefill hot **and** decode degraded | 46.13 |
| 03:22:01–03:23:34 | **prefill-only** | 107.02 |

That is a **phenotype match-or-separate contrast between two naturally occurring states, with GPU
telemetry already attached** — no restart, no epoch boundary, no added load. It answers the same
class of question the planned induced-idle probe was meant to answer, without spending the
non-reproducible natural-recurrence record to do it.


### The GPU does the same work in every state — both channels are host-side gaps

Phenotype first, mechanism second: do the two behavioural states share a **hardware** signature?

**The instrument was already in the capture and had never been read.**
`gpu.activity.global_counter` is IGCL, `DirectlyObserved`, and its unit is **nanoseconds of GPU
busy time**. Its B70 idle floor is **~81,300 ns/s — 0.008% busy**. That is why it succeeds where
energy failed: energy carries a 26.5 W/card floor that swamps a 300 ms probe (the cause of the
03:05:17 alignment artefact), whereas busy-time integrated over a multi-second window loses almost
nothing. **A single 20 ms forward pass is resolvable at 1 Hz.**

⚠ **The wall-clock anchor validated itself.** Windows aligned to keep-alive timestamps carry a
median **8,826,152** busy-ns; the same windows offset by +3 s carry **4,340**. A ~2000× separation
confirms the timestamp mapping to sub-second precision — independently of how it was derived.

**Channel B — the 32-token deep probe.** Eleven healthy and two degraded, all inside one capture,
one epoch, no restart:

| per token | healthy (n=11) | degraded (n=2) | change |
|---|---|---|---|
| **GPU busy** | 8.811 ms | 9.297 ms | **+5.5%** |
| wall | 9.272 ms | 18.298 ms | **+97.4%** |
| **host gap** | 0.461 ms | 9.001 ms | **+1851%** |
| GPU busy fraction | **95.0%** | **50.8%** | — |
| energy above idle | 1.022 J | 1.550 J | +52% |

**The GPU is not doing more work. It is idle for half the decode loop.**

**Channel A — the 1-token ping.** `prompt_ms` rises 10.30 → 20.20 ms while GPU busy moves only
8.814 → 9.684 ms. Of the **+9.90 ms** of extra prefill latency, **0.87 ms (9%) is GPU busy and
9.03 ms (91%) is host-side.** In threshold-free form — because the detection cut must not carry the
claim — regressing busy-ms on prompt-ms across **all 117 clean pings** gives a slope of **0.090 ms
of GPU time per ms reported**.

**The phenotype answer.** The two states **share a hardware signature, and the shared signature is
the *absence* of work inflation.** Neither channel inflates GPU work; both add latency outside GPU
execution. So the dissociation is **higher-level** — two *places* a host-side gap appears (request
entry versus the inter-token loop) — rather than two different hardware behaviours.

**"One forward pass" is now measured, not inferred.** Ping GPU busy **8.814 ms** (n=100, one token)
against deep-probe busy per token **8.811 ms** (32 tokens): **0.04% agreement** across two
independent request shapes. The previous lap's arithmetic inference is promoted by a separate
instrument, and the caveat on it can be closed.

⚠ **A derived consistency check, not a measurement.** Solving the healthy and degraded energy
budgets for two unknowns gives ~116 W above idle while computing and **~52 W above idle during the
host gap** — about half of load power, and far above the 0 W that "stall means idle" assumed.
Self-consistent with a clocked-up, non-computing GPU. **n=2; do not cite as a measurement.**

⚠ **What this does not establish.** n=2 degraded deep probes, so the +5.5% busy/token may be a
small real component or noise — the wall/gap split is far too large to be affected either way.
Five ping windows were excluded as contaminated (>50 ms busy): three overlapped an adjacent deep
probe, and **03:01:57 and 03:06:05 caught my own `ff_ratecheck` arms** — the instrument detecting
my own measurements is a validation, but those rows are not data. The counter is a **per-adapter
aggregate**: it cannot name an engine or a kernel, and it cannot separate *"the host did not
submit"* from *"the host submitted late"*. The gap could be host CPU, driver submission, the
server's scheduling loop, or synchronisation — this lap localises it **outside GPU execution and no
further**. 1 Hz sampling cannot say **where inside a request** the gap falls. One night, one epoch,
one server instance, one model.


### ⛔ EXCLUDE 04:29:20–04:31:15 — an artificial episode, caused by me

**This window is NOT an INC-2026-08-30-A recurrence.** It is contention with a 29,313-token
request I fired at production while verifying an auth fix, without checking the prompt file's
size first. The request ran **66.8 s with 61.4 s of prefill**.

| keep-alive sample | reading | note |
|---|---|---|
| 04:29:32 | wall 57.2 ms, prompt_ms 10.5 | healthy, immediately before |
| 04:30:03 | wall **6736.4 ms**, prompt_ms 4945.6 | `prefill_stall: true` |
| **04:30:26** | wall **39110.1 ms**, **decode 1.24 tok/s** | `decode_degraded: true` — **ARTIFICIAL** |
| 04:30:33 | wall 31255.4 ms, prompt_ms 107.2 | still draining |
| 04:31:14 | wall 1457.2 ms, prompt_ms 13.9 | recovering |
| 04:31:44 | wall 83.6 ms, prompt_ms 12.2 | **recovered** |

**1.24 tok/s is by far the lowest decode of the night and it is mine, not the machine's.** Swept
into a dose-response, a dwell-time distribution, a level-clustering argument or an episode count,
it would distort every one of them. **Exclude the window from all natural-recurrence analysis.**
An intervention must not be pooled with spontaneous transitions.

⚠ The irony is the lesson. Earlier in this same session I argued that the natural-recurrence
record is *perishable and non-reproducible* and should not be spent to buy a reproducible
measurement — then damaged it myself with an unchecked input file. **The prompt file's size was
never verified before it was fired at a production server.** The five requests in the preceding
elevated ETW run were harmless only by accident: they all returned HTTP 401 and did no GPU work.

### The full episode table — 12.4 h, 145 deep-probe samples

| onset | span | rates (tok/s) |
|---|---|---|
| 00:32:52 | ≤5 min | 65.17 · 68.37 |
| 00:54:56 | ≤5 min | 69.22 |
| 02:20:15 | **5 min** | 66.56 · 66.64 |
| 03:00:17 | ≤5 min | 67.01 |
| 03:20:19 | ≤5 min | **46.13** |
| 04:30:26 | ≤5 min | **1.24** |

**Six episodes, 8 degraded samples of 145 — 5.5% of sampled time — then 6.0 hours clean
(72 consecutive healthy samples).** Three corrections to what was recorded earlier, all of them
produced by waiting rather than by intervening:

1. ⚠ **The episodes are mostly brief.** The first was characterised as *"a stable ~61% state"*, and
   it is not: most are single-sample, under five minutes. That characterisation came from the one
   episode long enough to be caught, restarted and re-measured by hand. **An observer standing over
   the machine samples the long episodes preferentially** — the impartial record looks different from
   the one an operator assembles while investigating.
2. ⚠ **Depth varies by two orders of magnitude**, not within a band: 69.22 down to **1.24**. The
   clustering claim is withdrawn above. **Whether 46.13 and 1.24 are even the same phenomenon is
   unestablished** — depth does not classify, and per the restart discriminator neither of them was
   tested.
3. ⚠ **The behaviour is bursty**: six episodes inside four hours, then six hours of nothing. Any
   intervention fired during that first window would have landed on an episode about to clear on its
   own, and R10 would have made the resulting "recovery" uninterpretable. **This is the concrete
   payoff of the watch posture**, and it is the shape of evidence an intervention would have
   destroyed rather than produced.

### Deliberate epoch boundary — cutover to llama-swap (decided 2026-09-03, Derek)

**Not an observation of the machine; an observation about the record.** Derek chose to cut production
`omen-arc` over to llama-swap now (ADR-0045 / plan P13) rather than side-port-first. When `cutover.ps1 -Live`
runs, the incumbent process (pid 20416, resident since 2026-08-30 07:40Z — the epoch every INC-2026-08-30-A
row above was taken in) ends and a new llama-server epoch begins under llama-swap on the same `:8082`.
The recurrence record **splits here**: rows before and after are two epochs (R3/ADR-0044), the baseline
106.0 is preserved (a boundary is not a re-baseline — `rate-baselines.json` `epoch_boundaries`), and the
ETW session manifest's `server_pid 20416` becomes stale at execution. The keep-alive is restarted from a
warm rung by the ceremony itself (ratecheck burst immediately after load — ADR-0043 rule 1), so the first
post-cutover rows are warm-state rows, not post-idle ones.
