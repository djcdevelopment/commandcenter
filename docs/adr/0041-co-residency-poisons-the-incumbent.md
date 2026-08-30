# 0041 — Co-residency poisons the incumbent: restart before you measure

**Status:** Accepted (2026-08-29), **trigger superseded the same evening by `docs/adr#0043`.**
The loss is real and the rule it produced — restart before you trust a measurement — kept the
campaign honest. But **co-residency is not the cause**: a server that never shared the cards
with anything degrades the same way after ~2 minutes idle, and a 1-token keep-alive ping
prevents it entirely. A co-resident cell degrades the incumbent by leaving it *idle*, which is
a property of running any experiment. See *Subsequent evidence* at the foot of this record.

**Companion to:** `docs/adr#0038` (a verdict cites only evidence from the configuration it
promotes — this ADR extends that rule from *configuration* to *machine state*),
`docs/adr#0040` (serving lifecycle is adopted, not built),
`docs/adr#0034` (the dual-B70 rung this was measured on)

## Context

The Factory Frontier campaign's method is to run experiment cells *co-resident with live
production*, labelling each receipt `coresident: true` and treating that label as sufficient
disclosure. Lap 0 through the four-venue lap all ran that way.

On 2026-08-29 that assumption broke. Production decode was measured at **105.08 / 105.23 /
105.48 tok/s** across three checks; a second server (Qwen3.8-Flash-Next, host weights, ~1 GB
Vulkan compute buffer on a B70) was launched and later stopped; production then read **28.39
tok/s with the co-tenant already gone**. A restart restored **104.86**. The degradation is
**~3.7×, persistent, and survives both the co-tenant's exit and an idle period.** Only a restart
clears it.

Everything else was ruled out by direct test: sustained use (40 back-to-back requests on a fresh
server held 102–107 with no drift; another 40 with `cache_prompt=False` held 105.6–106.7), idle
(90 s recovered nothing), b70tools and `llama-bench --list-devices` (105.08 → 105.23 → 105.48 —
Vulkan *enumeration* is harmless), thermal (36–56 °C core against a 96 °C abort), VRAM spill, and
KV depth (`prompt_chars=0`, `prompt_n=1`).

**This is a rediscovery.** OMEN-LIMIT F3 / denning H1 already recorded a *"0.08× permanent
poisoned-load floor; co-tenant eviction −5×"*. That line was quoted verbatim in
`docs/FACTORY-FRONTIER-CARDS.md` §3 on the morning of the same day and then not applied for the
rest of it. Their −5× and this −3.7× are the same phenomenon.

The cost of not knowing this was two published-then-retracted findings: a `-ub 1024` "4× decode
regression" (the A/B compared a *fresh* arm against a *poisoned* arm) and a "sustained-rate decay
that recovers after idle" (assembled from log lines across an already-poisoned session).

## Decision

**A co-resident experiment invalidates the incumbent's performance until it is restarted. The
incumbent is restarted after any co-resident cell, before any measurement of it is trusted.**

1. **`coresident: true` is no longer sufficient disclosure.** A receipt taken from an incumbent
   that has hosted a co-tenant since its last restart is **not comparable** to one taken fresh.
   Receipts record `incumbent_restarted_since_cotenancy: true|false`.
2. **Comparative A/Bs run both arms from the same machine state** — either both fresh after a
   restart, or both after an identical co-residency history. Comparing a fresh arm to a used one
   measures the state, not the variable.
3. **Liveness is not health.** Serviceability probes, `/health`, and door proofs all passed
   throughout every incident. Every rung therefore carries a **known-good rate assertion**
   (`campaign/ff-probes/ff_ratecheck.py`), checked after every config change, restart, and
   driver update, gated at 80% of baseline — chosen to catch the ~22% loss `serve-arc.cmd`
   attributes to a partial VRAM spill, the quietest real degradation this box produces.
4. **A baseline may not be drawn from data noisier than the effect it must detect.** The tool
   refuses `--set-baseline` above a 10% repeat spread. (It once accepted one from a 74.87%
   spread.)

## Consequences

- **Every co-resident measurement in the FF campaign is suspect** unless the incumbent was
  restarted immediately beforehand. That includes the four-venue seat rates, Flash's "−42%
  co-residency tax", the dense-vs-MoE decode comparison, and the `-ub 1024` A/B. They are marked
  SUSPECT in the cards rather than deleted, and re-measurement is the pickup work.
- **Co-residency research costs a restart per cell.** The convenience that made Lap 0 cheap is
  gone; a cell that shares the cards now pays ~10 s of weight load plus a health gate before the
  incumbent can be measured again. That is the honest price and it is worth paying.
- **The mechanism is still unknown.** We have characterised the behaviour, not its cause — clocks
  could not be checked because IGCL frequency is unusable on the top slot per b70tools' README.
  Confirming it needs HWiNFO power/clock telemetry, which is installed but whose VSB export is not
  enabled. Registered as open.
- **Prior art must be reachable at decision time.** The finding existed in our own cards and was
  quoted that morning. Documents are not a control; the rate assertion is.

## Alternatives considered

- **Keep labelling and adjust afterwards.** Rejected: the effect is 3.7× and persistent, far
  larger than any correction factor could carry honestly.
- **Never run co-resident cells.** Rejected: co-residency is the *subject* of the factory layer,
  not an inconvenience. The lab must be able to measure what it is designed to do.
- **Trust the door proof as the health gate.** Rejected on evidence: the door proof returned
  `ok:true` at 105 tok/s and at 22 tok/s alike.

---

## Subsequent evidence — W-B / W-B2 / W-B3, 2026-08-29 evening

**This record's rule survives; its cause does not.** The full account is `docs/adr#0043`; what
follows is the evidence trail from this record's own claim to that one.

### Step 1 — W-B appeared to refute the effect entirely

Five co-tenant classes, measured before / during / after, restart and rate gate on each:

| class | before | during | after (at **+6 s**) |
|---|---|---|---|
| control — nothing | 106.70 | 105.87 | 105.68 |
| Vulkan server, loads, **never infers** | 106.48 | 105.64 | 105.98 |
| Vulkan server, loads **and infers** | 105.50 | **96.73** | 105.64 |
| CPU-only (`--device none -ngl 0`) | 106.00 | 106.20 | 106.24 |
| iGPU-only | 106.49 | 105.68 | 106.01 |

Every `after` at 99–100% of its own `before`. **The `after` column is worthless** — it was sampled
6 seconds after co-tenant exit. The `during` column is sound and is worth keeping: a
resident-but-idle neighbour costs the incumbent nothing; an actively inferring one on the same B70
costs **8%**, and only while it infers; a CPU-only or iGPU co-tenant costs nothing. **Contention is
local to the shared card and it is cheap.**

### Step 2 — the loss arrived minutes later, with nothing on the cards

Class 3's epoch was left running. At ~+5 min, no co-tenant, correct dual-split placement
(14.516 / 15.485 GB, 0 °C spread at 50 °C), a 4-minute-old epoch: **42.50 tok/s (40%)**, and
**43.77 (41%)** a minute later. Eight requests, all sequential on slot 1, one `llama-server`
process, nothing else listening.

### Step 3 — co-residency removed entirely; it still degrades

A restarted incumbent that never shared the cards with anything: **100%** at t+0, **45% / 46% /
31%** at t+5 / +10 / +15 min. The trigger is **idle time**, bracketed between **60 s** (holds at
106.45) and **120 s** (falls to 39.71). A **1-token ping every 20 s** across a 300 s gap holds the
rate at **104.83** — identical to fresh.

### What this means for the decision above

- **"Restart the incumbent after any co-resident cell" is right for the wrong reason.** It works
  because a freshly loaded server is measured immediately. Warming is cheaper and does not cost a
  weight load. Superseded by ADR-0043's rule.
- **`coresident: true` was never the disclosure that mattered.** The field that matters is *how
  long the incumbent sat idle before it was measured*.
- **The suspect list this record generated stands, with the reason rewritten.** Every co-resident
  receipt is still suspect — not because a co-tenant poisoned the cards, but because running a cell
  necessarily leaves the incumbent idle for minutes, and a post-idle measurement is a cold one.
- **The `-ub 1024` A/B is explained.** ub512 fresh after restart (104) against ub1024 measured
  after co-resident Flash work (22–27) is exactly a warm-vs-cold comparison. The retraction stands
  and now has a mechanism behind it.

⚠ Preserved deliberately: the reasoning in this record was sound given what was measured, and the
rule it produced prevented further bad data all evening. It is superseded, not withdrawn.
