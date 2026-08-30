# 0043 — The rung goes cold when idle: keep it warm, don't restart it

**Status:** Accepted (2026-08-29) — measured on three independent runs; mitigation proven and **shipped** (`fleet/fx99-keepalive/`, scheduled from fx99)

**Supersedes the trigger in:** `docs/adr#0041` (co-residency poisons the incumbent). That record's
*rule* — restart before you trust a measurement — remains sound and is what kept the campaign
honest. Its *cause* was misattributed. Co-residency is neither necessary nor, on its own,
sufficient.

**Companion to:** `docs/adr#0034` (the dual-B70 rung), `docs/adr#0038` (a verdict cites only
evidence from the configuration it promotes)

## Context

Production decode on `omen-arc` has repeatedly been found at a fraction of its known-good rate,
with correct placement, no VRAM spill, cool cards, and a passing door proof. Two accounts were
published and retracted the same day: a *sustained-rate decay that recovers after idle*, and
*co-residency poisons the incumbent*. The second was accepted as ADR-0041 on evidence that a
co-tenant's launch preceded a 3.7× loss which survived the co-tenant's exit and cleared only on
restart.

The W-B sweep set out to discriminate the poisoning mechanism across five co-tenant classes and
found **no effect in any of them**. That result was itself wrong: every `after` measurement was
taken **6 seconds** after the co-tenant exited. Minutes later the same server — nothing else on
the cards, correct dual-split placement, a 4-minute-old epoch — read 40% of baseline, twice.

Removing co-residency entirely settles it. A restarted incumbent that **never shared the cards
with anything**, sampled on a schedule (`campaign/ff-probes/wb2_delayed_onset.py`):

| epoch age | reps (tok/s) | mean | of baseline |
|---|---|---|---|
| t+0 | 105.97 · 106.36 · 105.90 · 105.89 | 106.03 | **100%** |
| t+5 min | 74.37 · 57.89 · 31.17 · 26.60 | 47.51 | 45% |
| t+10 min | 74.09 · 59.17 · 32.12 · 27.96 | 48.34 | 46% |
| t+15 min | 47.71 · 27.30 · 28.74 · 27.67 | 32.85 | 31% |

The variable is **idle time**, and the threshold is sharp
(`campaign/ff-probes/wb3_idle_ladder.py`, one server, arms in order):

| idle before the burst | reps (tok/s) | mean |
|---|---|---|
| 0 s | 106.43 · 107.03 · 106.70 · 106.64 · 106.20 | **106.60** |
| 30 s | 106.65 · 106.44 · 106.32 · 106.61 · 106.69 | **106.54** |
| 60 s | 106.13 · 106.37 · 106.43 · 106.73 · 106.60 | **106.45** |
| **120 s** | 68.92 · 46.19 · 29.09 · 28.53 · 25.80 | **39.71** |
| 300 s | 35.55 · 28.29 · 27.01 · 25.92 · 26.54 | 28.66 |

⚠ The arms share one server, so only the ladder **up to and including the first arm that falls**
is independent; the 300 s row started from an already-degraded state. The 0/30/60/120 rows are the
result.

**Replicated on a second fresh server, and it is a two-state transition rather than a decay:**

| arm | reps (tok/s) | mean |
|---|---|---|
| idle 0 s | 106.61 · 106.99 · 106.45 · 106.24 · 106.72 | **106.60** |
| idle 120 s | 68.19 · 42.67 · 29.55 · 29.03 · 28.28 | **39.54** *(first run 39.71)* |
| immediately after, 0 s gap | 27.42 · 27.48 · 27.41 · 27.49 · 27.84 | **27.53** |

The two idle-120 runs agree to within 0.5%. Once collapsed the rung sits **stable at ~27.5 tok/s**
— flat, not noisy — and continued load does not recover it. Two states ~3.9× apart, with a
~4-request transition between them.

**The mitigation is trivial and it works.** On a fresh server, a **1-token request every 20 s**
across a 300 s gap held the rate at **104.83 tok/s** — identical to that server's own fresh
104.83, against 28.66 for the same gap spent idle. Confirmed in production over a **~6 minute** horizon: a 30 s pinger held **105.43 tok/s (99% of
baseline, 0.41% spread)** across a window whose only traffic was the ping itself.

⚠ **That horizon is the limit of the claim.** On 2026-08-30 an epoch ran ~35 minutes with pings
landing every 30 s and degraded to 61% anyway — a state a **restart did not clear**, so it is
likely a different phenomenon rather than a keep-alive failure. But *"holds the rate"* is supported
only out to the horizon actually tested. See the open incident in `docs/FACTORY-FRONTIER-CARDS.md`.

⚠ **But only from a warm start.** A pinger begun on an *already collapsed* rung keeps prefill fast
and does **not** revive decode: with pings landing every 30 s and every receipt showing
`prefill 44 ms, stall false`, a rate check still read **42.54 tok/s (40%)** with the familiar
68.82 → 26.40 within-burst decay. Warming prevents the transition; it does not reverse it. **The
only known cure remains a restart**, which is why the pinger deliberately does not actuate — see
Consequences.

### Two distinct post-idle costs, and only one of them is visible

They have different signatures and want different treatment:

| | cost | shape | visible in `print_timing`? |
|---|---|---|---|
| **First-request stall** | **~11.5 s** | one-shot; the next request is normal | **often NOT** |
| **Decode collapse** | 106 → ~27.5 tok/s | persists until restart | yes |

The stall is size-independent — 10 735 ms to prefill **11 tokens**, and 11 540 ms for a **1-token**
ping. ⚠ And it hides: on that 1-token ping the server reported `prompt_ms = 47.1` and
`eval_ms = 0.0` while its own `launch_slot_` → `release` pair spanned **11.54 s**. On the 11-token
call the same cost landed *inside* `prompt_ms` instead. **Any monitor that trusts llama.cpp's
timing counters will miss this**, which is why `warm-arc.ps1` records wall time as well.

**Spill and thermal are excluded by direct measurement in the degraded state**: an `ff_census`
taken immediately after a collapsed burst read `local` 14.516 / 15.485 GB with `non_local`
0.002 / 0.446 GB and a 0 °C temperature spread at 50 °C. Nothing was evicted and nothing was hot.

## Decision

**A rung that has been idle for more than ~60 seconds is not at its known-good rate, and no
measurement of it is valid until it has been warmed. The lab keeps rungs warm rather than
restarting them.**

1. **`ff_ratecheck` and every health gate must warm before they measure.** A single discarded
   warm-up request is not enough, and a single *measured* request is not a detector either: observed
   rep-1 values after an idle gap are **68.19, 68.92, 74.37, and 91.75** — the last on a real door
   call, which would have read as healthy. Only a sustained burst reveals the state. Warm until the
   rate is flat, then measure.
2. **Restart is no longer the prescribed remedy.** It works only because a freshly loaded server is
   immediately measured; it treats the symptom at the cost of a full weight load. Warming is
   cheaper and does not disturb the epoch.
3. **ADR-0041's trigger is re-stated.** The rule fires on **idle**, not on co-residency. A
   co-resident cell still invalidates the incumbent's rate — but by leaving it idle for minutes,
   which is a property of *running any experiment*, not of sharing the cards.
4. **Every throughput figure in the corpus carries an implicit warm/cold state.** Figures taken
   immediately after a load or inside a sustained burst are warm; anything measured after a gap is
   not comparable to them.

## Consequences

- **The door is exonerated; the cold rung was the whole story.** Finding C2 suspected the gateway
  after a call showed ~91% of its wall time outside the server's decode. A bracketed size ladder
  (`campaign/ff-probes/c2_door_attribution.py`, join verified by ordinal assignment plus
  `tokens_out == predicted_n` on every row) puts door overhead at a **flat 175–264 ms independent
  of size** — 35.6% of a 32-token call and 11.8% of a 512-token one, so its *share* falls as calls
  grow. The apparent 91% was this ADR's own first-request stall, invisible because there was no
  server-side join. **C2 is resolved as a non-issue.** ⚠ The earlier ledger reconstruction
  (`tokens_out / duration_ms` = 4.0–44.1 tok/s, median ~15) therefore measures the cold regime, not
  door cost — which is evidence, though not proof, that real traffic has been running cold.
- **The keep-alive is SHIPPED, and it runs from fx99** (Derek's call, 2026-08-29).
  `fleet/fx99-keepalive/` holds the systemd units; `fleet/arcserve/warm-arc.ps1` is the OMEN-side
  action. A keep-alive that runs on the box it keeps alive dies with that box, so fx99 owns the
  schedule while OMEN owns the action **and the secret** — fx99 never holds the bearer token, and
  `:8082` stays loopback-bound because SSH is the transport. ⚠ It runs over the **tailnet**, which
  crosses ADR-0014/0015 (LAN is the machine lane); the LAN path is firewalled to the `Private`
  profile while `Ethernet 3` is `Public`. The script tries LAN first and will switch itself when a
  scoped rule is added.
- **The pinger observes; it never actuates.** It cannot revive a collapsed rung anyway, and a
  keep-alive that reached for a restart would be an outage generator on a flaky link. It therefore
  needs a companion decision — who restarts, on what evidence — which is registered rather than
  invented here.
- **A 1-token ping cannot see the collapse it prevents**, because it generates no measurable
  decode. `arc-keepalive-deep.timer` runs the same script with 32 tokens every 5 minutes purely to
  produce a decode rate and flag it below 80% of baseline. Roughly 0.3 s per 5 minutes buys the
  monitor its own falsifiability.
- **Three retracted or narrowed findings are explained by one mechanism.** The "sustained-rate
  decay" was real but only post-idle, which is why 40 back-to-back requests on a *fresh* server
  held flat and appeared to refute it. The "co-residency poisoning" was real but the co-tenant was
  a bystander: running an experiment leaves production idle. The `-ub 1024` "4× regression"
  compared a fresh arm to a cold one.
- **The mechanism is still unknown.** Spill, eviction and thermal are excluded above. GPU
  clock/power state is the surviving candidate and IGCL cannot measure it on this box (b70tools
  lists voltage/frequency as unusable on the top slot), so confirming it needs HWiNFO — the same
  telemetry gap ADR-0041 registered, now with a sharper question to ask of it.
- **The ~60–120 s threshold is bracketed, not resolved.** 60 s holds, 120 s falls; nothing between
  was tested, on one model at one context. The collapse at 120 s is replicated (39.71 / 39.54); the
  threshold's *location* rests on a single bracket.

## Alternatives considered

- **Keep restarting.** Rejected: it costs a full weight load, disturbs the epoch, and treats a
  symptom whose cause is now known to be cheaper to prevent than to cure.
- **Ship the keep-alive immediately.** Rejected as out of scope for a measurement window — the
  finding is one evening old and the placement of the timer is a real design choice. Recorded as a
  recommendation with the evidence attached.
- **Treat it as a benchmarking artifact and ignore it operationally.** Rejected on the numbers: a
  4× gap between benchmark and production conditions is not an artifact, it is the operating point.
