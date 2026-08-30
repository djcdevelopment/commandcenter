# Factory Frontier experiment cards — FF1–FF10

**Date:** 2026-08-29 · **Posture:** /rnd (sampling for edges; vertical slices) ·
**Status:** plan of record for the next research layer; extends, does not replace, the
rotation program (R-series) and the Level-Zero campaign (LZ1–LZ8b).
**Receipts:** `E:\work\battlemage\ff-probes\ff-receipts.jsonl` + `corpus/runs/` manifests ·
**Board:** ROTATION-PROGRAM.html side-lane rows · **Cards format:** identical to
[LZ-EXPERIMENT-CARDS.md](LZ-EXPERIMENT-CARDS.md) (Hypothesis / Moves / Rig / Kill-promote).

---

## ✅ CAMPAIGN BOUNDARY — the suspect-measurement campaign is CLOSED (2026-08-30)

B1–B5 have dispositions, E2 is bounded rather than repaired, topology claims are scoped to
model × workload, ADR-0043 has a shipped mitigation with falsifiable monitoring, and
production is at baseline. **No remaining experiment here has information value exceeding
its opportunity cost.** Do not reopen it to make historical receipts pristine — that is the
work the rules and the claim register exist to make unnecessary. The next investigation is
**FF1**, and it starts with [`docs/FF1-DENOMINATOR-AUDIT.md`](FF1-DENOMINATOR-AUDIT.md),
not with an instrument.

## ⚠ 0.0 · THE RUNG GOES COLD WHEN IDLE — read before citing any throughput number here

> **Before citing ANY throughput, ratio, crossover or tax figure from this campaign, check
> [`docs/CLAIM-REGISTER.md`](CLAIM-REGISTER.md).** It is the authoritative lookup: original
> claim → status → corrected claim/bound → decisive receipt → rule. Twelve claims in these
> cards have been corrected, refuted, or relocated; the prose around them is preserved and
> annotated in place, so a stale headline is still readable here and is **not** citable.

**CORRECTED 2026-08-29 (late).** An earlier version of this section claimed a *"sustained-rate
decay that recovers after idle."* **Both halves were wrong.** The real mechanism:

> **Co-resident GPU work persistently degrades the incumbent server until RESTART.**

Measured cleanly: production at **105.08 / 105.23 / 105.48** tok/s across three checks → launched
Flash (host weights, ~1 GB Vulkan compute buffer on a B70) → production read **28.39 tok/s with
Flash already stopped** → restart restored **104.86**. A ~3.7× persistent loss, not recovered by
stopping the co-tenant, not recovered by idle, fully recovered by restart.

**What the controlled tests ruled out:** sustained use (40 back-to-back on a fresh server held
102–107; another 40 with `cache_prompt=False` held 105.6–106.7); idle (90 s did *not* recover a
slow server); b70tools (105.08→105.23); `llama-bench --list-devices` (105.23→105.48); thermal
(36–56 °C core); VRAM spill; KV depth. The "decay curve" in the earlier version was an artifact of
reading scattered log lines across a session that had already been poisoned at an unknown point.

⚠ **This is a rediscovery, and the prior art was in this very document.** OMEN-LIMIT F3 / denning
H1 recorded a **"0.08× permanent poisoned-load floor; co-tenant eviction −5×"** — quoted in §3's
eviction row — and I failed to apply it all day. Their −5× and today's −3.7× are the same thing.

**Methodology consequence: every co-resident measurement in this campaign is suspect** unless the
incumbent was restarted immediately beforehand. That includes the four-venue seat rates, Flash's
"−42% co-residency tax", the dense-vs-MoE comparison, and the `-ub 1024` "4× regression" — which
was almost certainly poisoning, since ub512 was measured fresh (104) and ub1024 after co-resident
Flash work (22–27). **NEW RULE: restart the incumbent after any co-resident experiment, before
measuring it.** FF-CENSUS checks placement and residency; it must also check *rate*.

### ✅ MECHANISM FOUND — it was never co-residency *(2026-08-29 evening, `docs/adr#0043`)*

**>60 s idle costs ~4×. A 1-token ping every 20 s prevents it entirely.** A server that never
shared the cards with anything reads 100% of baseline at t+0 and 45% / 46% / 31% at t+5 / +10 /
+15 min. The idle threshold is bracketed between **60 s** (holds at 106.45) and **120 s** (falls to
39.71). See the **W-B → W-B3** section below for the full trail, and `docs/adr#0043` for the
decision. ADR-0041's *rule* kept the campaign honest; its *trigger* is superseded.

⚠ **So "co-resident" was never the disclosure that mattered.** The field that matters is **how long
the incumbent sat idle before it was measured** — and running any experiment leaves it idle for
minutes, which is why co-residency looked causal.

### ✅ RESOLVED — production was restored healthy *(2026-08-29 ~19:40)*

After the W-A solo window, production was restarted and measured at **106.02 tok/s = 100% of
the 106.00 baseline** (reps `[105.88, 105.52, 106.25, 106.43]`, repeat spread 0.87%), with the
HEARTH door proof `ok:true`. The snapshot below is kept as the record of what a deeply poisoned
incumbent looks like — it is **history, not current state**.

#### ⛔ The poisoned snapshot *(2026-08-29 ~14:50 — historical)*

**Do not open a measurement window without restarting production first.** Measured during the
P-D harness verification, no restart performed:

| | value |
|---|---|
| decode | **4.80 tok/s** settling floor — reps `[15.71, 5.27, 4.95, 4.92, 4.80, 4.80]` |
| known-good | 104.86 tok/s (this epoch's own post-restart figure) |
| ratio | **0.046×** |
| placement | **correct** — 14.6 / 15.5 GB across both BDFs, temp spread 2 °C, both cards warm |
| co-tenants | **none** — only `llama-server` pid 11780 (:8082) and the HEARTH gateway (:8710) |
| epoch | started 08:03:24, ~6 h old |

This is not the ADR-0042 one-card defect — placement is right. It is consistent with ADR-0041
poisoning accumulated across a ~6 h epoch, and it is **deeper than that ADR's 0.27×**, closer to
the OMEN-LIMIT F3 / denning H1 **0.08× permanent poisoned-load floor**.

⚠ **The HEARTH door proof returned `ok:true` with correct text at this rate**, and `/health` was
fine throughout. Exactly what ADR-0041 says the door proof is worth as a health gate: nothing.
Only the rate assertion caught it.

**Cause is not attributed.** Production was *already* at 0.61× on the first measurement, before
this session ran any b70tools sampling, so the probes did not start it. Whether repeated
read-only probing *deepened* it is **untested** — one sequence, no control. ADR-0041 tested
b70tools as harmless (105.08 → 105.23), but that was a single invocation on a *healthy* server,
while `ff_census` runs it as `--run --ticks 4`, a heavier operation. Registered as a question
for **P-C**, whose Vulkan-probe cell already covers it.

### ✅ RETRACTED: the decay account *(kept for the record — do not cite)*

**Everything in this subsection is superseded by the poisoning account above.** It is retained
because the retraction is part of the evidence, not because any of it is load-bearing. The
account below claimed production decode *"decays 104 → 22 tok/s under sustained use and recovers
after idle."* **Both halves are false.**

<details>
<summary>The withdrawn table and its reasoning (click to expand)</summary>

| uptime | task | decode |
|---|---|---|
| 1:30 | 0 | 104.26 ← fresh after restart |
| 1:32 | 202 | 103.40 |
| 20:15 | 303 | 68.23 |
| 20:21 | 510 | 33.57 |
| 21:14 | 611 | 75.14 |
| 21:29 | 1020 | 22.17 |
| 22:37 | 1627 | 27.67 |
| 22:51 | 1930 | 22.10 |

The leading hypothesis was a **power-limit boost budget** — fast-when-fresh, decay under load,
recovery after idle, cool silicon. Thermal was correctly ruled out (36–56 °C core against a 96 °C
abort), as were spill and KV depth. The conclusion drawn was that *"most throughput numbers in
this document are plausibly boost-window numbers"* and that every figure should be treated as
burst-measured pending a sustained-rate metric.

</details>

**Why it was wrong.** Those rows were not a time series from one controlled run — they were
scattered log lines read across a session that had already been poisoned at an unknown point. The
controlled tests, run afterwards, refute both halves directly: **40 back-to-back requests on a
fresh server held 102–107 with no drift**; another 40 with `cache_prompt=False` held 105.6–106.7;
**18 back-to-back on a slow server were dead flat at 22.0–22.1** (ratio 1.09×, so no decay in the
degraded state either); and a **90 s idle recovered nothing**. A fresh server does not decay, and
a poisoned one does not recover.

**What this costs, and what it does not.** No sustained-rate metric is needed, and the burst
measurement `ff_ratecheck.py` takes is valid *provided the server is fresh* — which is exactly
what ADR-0041's restart rule guarantees. The one live remnant is the `-ub 512` vs `1024` A/B (B1
below), which must be redone with **both arms fresh after restart** rather than "at plateau."

⚠ **Two retractions, one lesson.** The decay account was itself an attempt to explain the `-ub`
anomaly, and it failed for the same reason that finding did: both compared measurements taken at
different, unrecorded machine states. The fix is not a better metric — it is recording the state,
which is what ADR-0041 rule 2 and the nine receipt fields now do.

### Provenance repair — what the two ADRs cost the existing record *(2026-08-29, P-A)*

Neither ADR invalidates the campaign's *conclusions*, but both invalidate the *provenance* of the
measurements behind them. `campaign/ff-probes/ff_provenance.py` reconstructs what survives and
classifies every receipt alongside the originals — no history rewritten. Regenerate with
`python campaign/ff-probes/ff_provenance.py`; `--check` exits 1 on drift.

**Process epochs.** ArcServeBoot is boot-triggered and `serve-arc.cmd` truncates its log on every
launch, so per-epoch load reports do not survive. Epochs were reconstructed from OS boot events,
receipt-evidenced restarts, and the running server's own elapsed-time counter:

| epoch | span | topology | evidence |
|---|---|---|---|
| E1 | 08-26 23:18 → 08-28 08:32 | **confirmed-single** | b70tools capture at 08-28 02:15:37 — 29.69 / 0.17 GB |
| E2 | 08-28 08:32 → 08-29 04:40 | **confirmed-single** | `-lv 5` load report at 04:31:42 — 49/49 layers, 30108 MiB, one card |
| E3 | 08-29 04:40 → 06:30 | **confirmed-dual** | two FF-CENSUS rows — 14.88 / 15.80 GB across both BDFs |
| E4, E5 | 08-29 06:30 → open | unknown | no decisive anchor falls in these |

**The headline: E2 contains the entire LZ campaign window and all pre-fix FF work — 288 of 315
receipts — and E2 is anchored SINGLE-CARD by the highest-authority channel we have.** That is not
inference from absent evidence; it is a positive observation from llama-server's own load report.
The venue matrix was measured on half the hardware it claimed.

**Reach, stated honestly.** The E2 anchor sits at the *end* of a ~20 h epoch, so most receipts
inherit it by epoch membership rather than direct observation: **35 rows are `direct` (within
15 min of a decisive anchor), 268 are `epoch-inferred`**, median reach 3.7 h, maximum 18.8 h.
Every row carries `placement_reach_minutes`, so this is auditable per receipt. The residual risk
is an unrecorded crash-restart inside an epoch — `ArcServeBoot` carries `RestartCount 3 @ PT1M`
and leaves no trace in any surviving channel, and the Task Scheduler operational log is
`enabled=False` with zero records.

| status | count | meaning |
|---|---|---|
| `PLACEMENT_CONTEXT_INVALID` | 288 | epoch anchored single-card; a dual-card reading measures a different machine |
| `PLACEMENT_CONTEXT_UNKNOWN` | 12 | 8 LZ rows carry date-only stamps spanning a reboot; 4 sit in unanchored epochs |
| `PLACEMENT_CONTEXT_CONFIRMED` | 15 | epoch anchored dual-card |
| `INCUMBENT_HEALTH_UNKNOWN` | 275 | co-resident receipt taken before `ff_ratecheck.py` existed |

⚠ **Absence of evidence is labelled as such.** `UNKNOWN` is a distinct verdict from `INVALID`, and
22 of the 25 stored b70tools captures are `indeterminate` rather than informative — they read
~0.00 GB on both cards, which is the activity-window artifact (A12), **not** evidence the cards
were empty. Collapsing those into a verdict would repeat exactly the over-claiming that produced
three same-day retractions.

### W-A solo controls — the venue matrix with **no incumbent at all** *(2026-08-29 evening)*

The campaign had exactly one solo receipt. Everything else was measured beside a production
server whose topology was unprovable (ADR-0042) and whose health was never gated (ADR-0041). With
Derek out, production was stopped for a whole window and the matrix was re-measured clean.

Rig: `campaign/ff-probes/wa_solo_controls.ps1`, receipts `probe: "W-A-SOLO"`,
`receipt_status: SOLO_CONTROL`. Production down via the `arc-maintenance.stop` sentinel +
`ArcServeRestart` (the UAC-free stop-only control `restart-arc.cmd` already provides — a medium
caller **cannot** kill the S4U server directly; `Stop-Process` and `taskkill` both return access
denied). Commit fell 84.7 → 54.0 GB on the stop, which is exactly the server's 30.65 GB of private
bytes — an independent re-confirmation of A6 (GPU residency costs host commit ~1:1).

**One binary for every cell** (`E:\work\llamacpp-qwen38`), because Flash requires the fork (A11)
and a mixed-binary matrix would confound venue with build. S2K measures that choice's cost once.

| cell | venue | decode ×2 (tok/s) | mean | prefill@512 ×2 | placement asserted |
|---|---|---|---|---|---|
| **S5** | 30B-A3B, **one** B70 | 116.43 / 116.26 | **116.35** | 2213.96 / 2213.44 | `Vulkan1=17524.4MiB` (1 GPU buffer) |
| **S2K** | 30B-A3B, dual-split — **knee** build | 110.56 / 110.93 | **110.75** | 2162.60 / 2214.48 | `Vulkan1=8975.6, Vulkan2=8548.8MiB` |
| **S2** | 30B-A3B, dual-split — qwen38 build | 109.97 / 109.96 | **109.97** | 2164.68 / 2235.30 | `Vulkan1=8975.6, Vulkan2=8548.8MiB` |
| **S3** | 30B-A3B, experts→CPU, one B70 | 24.06 / 26.58 | **25.32** | 140.66 / 212.10 | `CPU_Mapped=17447.9, Vulkan1=784.4MiB` |
| **S1** | Flash-Next, experts→CPU, both B70 | 14.40 / 14.71 | **14.56** | 57.28 / 58.52 | `CPU_Mapped 47015.9+41787.2, Vulkan1=2077.7, Vulkan2=2381.8MiB` |
| **S4** | 30B-A3B, full iGPU | 13.41 / 13.31 | **13.36** | 88.01 / 129.03 | `Vulkan0=17524.4MiB`, zero B70 |

#### 1. The ordering **HOLDS** — and the number at the top of it never existed

For the 30B across venues, solo: **B70 (110–116) > experts→CPU (25.3) > full-iGPU (13.4)**. Same
ordering as the co-resident matrix, same rough ratios. The venue conclusions survive the
provenance repair.

But the recorded headline **121.6 "full-B70 dual-split" is mislabeled**. Its receipt carries
`"tensor_split": "1.00"` — it is a **single-card llama-bench `tg128`** run, not a dual-split one,
which is finding A4 biting exactly where A4 predicts (llama-bench splits `-ts` on `[;/]+` and
reads a comma as its *config* separator, so `-ts 1,1` there means two separate single-card runs).
llama-bench also has no `-np`, so it cannot express a serving topology at all. There was never a
121.6 dual-split figure. The server, solo, reads **116.35 single-card** and **109.97 dual-split**.

#### 2. Dual-split costs 5.5% decode and buys **nothing** at 512-token prefill

| | decode | prefill@512 |
|---|---|---|
| one B70 (S5) | 116.35 | 2213.7 |
| dual-split (S2) | 109.97 | 2200.0 |
| delta | **−5.5%** | **−0.6%** |

The −5% decode cost reproduces. The prefill *gain* does not — because the recorded "+27% / +42%"
was measured at **pp2048**, and 512 tokens is below the crossover. This is the first server-side,
`-np`-capable, solo measurement of dual-vs-single the campaign has; it upgrades **B3** from
SUSPECT to a bounded statement: *dual-split is a prefill-length bet, and at 512 tokens the bet has
not paid yet.* It does not settle pp2048 — that needs the same treatment.

#### 3. The build choice is free: knee vs qwen38 = **0.7%**

S2K vs S2 on identical config: decode 110.75 vs 109.97, prefill within 1%. Production's binary is
not leaving performance on the table, and the one-binary matrix above is honest.

#### 4. Signed prediction: **FALSIFIED**, and the anomaly it chased was an artifact of a missing control

The prediction was that clean LZ1-A Flash decode would fall from the "anomalous" co-resident
**12.4–14.9** toward **10.6–11.5**. Solo, it reads **14.40 / 14.71** — at the *top* of the
co-resident band. Every Flash measure is at or above its co-resident counterpart:

| measure | solo (W-A) | co-resident on record |
|---|---|---|
| decode 64 | 14.40 / 14.71 | 12.36 / 13.19 / 12.21 / 10.98 |
| prefill @22 | 24.72 / 27.36 | 20.94 – 29.06 |
| prefill @512 | 57.28 / 58.52 | 32.01 – 59.38 |

Solo ≥ co-resident is the direction physics predicts, so there is nothing left to explain. **The
"favourable anomaly" was never an anomaly.** It came from comparing LZ1-A against a "10.6 solo"
figure that is not a solo LZ1-A receipt at all — it is from the qwen38 expert-placement ladder at
+6.3 GB commit, a different rig. The empty-second-card hypothesis is not merely insufficient; the
thing it was invented to explain does not exist. ⚠ Closed as **explained-away**, not as *refuted
mechanism* — nothing here says an empty second card is harmless, only that it was never needed.

#### 5. First eval costs **12×**, and it is paid **per batch shape**

The first S1 pass had no prefill warm-up. Same server, same requests, warmed vs cold:

| measure | cold (first eval) | warm | ratio |
|---|---|---|---|
| prefill @22 | 2.16 tok/s (12.5 s) | 24.72 | **11.4×** |
| prefill @512 | 4.80 tok/s (**105.8 s**) | 57.28 | **11.9×** |
| decode 64 | 12.43 / 13.86 | 14.40 / 14.71 | 1.15× |

The 512-token shape paid its own 105.8-second penalty **after** the 22-token shape had already
warmed the server — so this is not one warm-up, it is one per batch geometry. Any harness that
reports rep 1 publishes a pipeline-compile time as a throughput number. LZ1 warmed up for exactly
this reason; W-A's first draft did not, and it produced a 9.93 tok/s "solo Flash prefill" that was
pure artifact. Fixed in `Invoke-Prefill`.

⚠ This also puts a floor under FF4's prefill-amortization work: on Flash, **cold-shape cost is the
dominant term at short horizons** — larger than the co-residency tax and larger than the venue
choice. Model load itself is comparatively cheap, and cache-warm cheaper still: Flash's first load
took **84.3 s**, its second **12.1 s**.

#### 6. The solo noise floor, per venue

Repeat spread with nothing else on the box — this is the real floor, and it is venue-dependent:

| venue | decode spread | prefill@512 spread |
|---|---|---|
| B70 dual-split (S2) | **0.01%** | 3.2% |
| B70 single (S5) | 0.15% | **0.02%** |
| full iGPU (S4) | 0.75% | **46.4%** |
| Flash experts→CPU (S1) | 2.1% | 2.1% |
| 30B experts→CPU (S3) | **10.5%** | **50.9%** |

B70-resident work is metronomic; anything that crosses to host memory is not. A ±10% effect is
unmeasurable on the experts→CPU venue at 2 reps, and the iGPU venue cannot support a prefill claim
at all without many more reps. This is a **per-venue** rule, not a global one.

#### Kit defects found and fixed by running this window

Five, four of them in code written earlier the same day to prevent exactly this class of error:

1. **`placement.ps1` counted host buffers as devices carrying weights.** `CPU_Mapped=17447.9MiB`
   made `one-b70` reject every valid `-ot exps=CPU` cell — and, far worse, would have let
   `both-b70` **PASS on a single-card load** that happened to have a host buffer. That is the
   ADR-0042 defect surviving inside the assert built to catch it. GPU buffers are now counted
   separately from host buffers.
2. **`Get-VulkanDevices` could not run under `$ErrorActionPreference = 'Stop'`.** llama-bench
   writes its device list to stderr; PowerShell 5.1 wraps each redirected stderr line of a native
   exe in an ErrorRecord, so `--list-devices` succeeding at exit 0 still aborted the caller. The
   iGPU cell could not start at all.
3. **`ff_cell.wait_for_ready()` had a stale-marker race.** `restart-arc.cmd` kills the old server,
   waits 3 s, *then* starts the new one; for those seconds the log still holds the previous
   epoch's `model loaded`. A caller that restarted and immediately waited got `True` from a log
   describing a server that no longer existed. Now gated on `since=`.
4. **`Stop-Probe` slept a fixed 4 s.** Flash's teardown with ~88 GB mmap'd outlives that, so the
   next cell's solo guard saw the dying process as a foreign tenant and voided the window. The
   guard was right to fail closed; the stop was wrong to be impatient. It now waits for real exit.
5. **No prefill warm-up** (§5 above).

⚠ The pattern is consistent with lesson D6: none of these produced a wrong *answer*, they produced
plausible ones. Only defect 2 announced itself.

**Window closed clean.** Production restored, `ff_ratecheck` **106.02 tok/s = 100% of baseline**
(spread 0.87%), HEARTH door proof `ok:true` / `routed_by: pinned:omen-arc`.

### W-B → W-B3 — the mechanism found: **the rung goes cold when idle** *(2026-08-29 evening)*

> **The result: >60 s idle costs ~4×, and a 1-token ping every 20 s prevents it entirely.**
> Co-residency was a bystander. Full decision record: `docs/adr#0043`, which supersedes ADR-0041's
> trigger.

This started as a five-class sweep to discriminate *how* a co-tenant poisons the incumbent. It
ended somewhere else, via a wrong answer that was worth having.

#### Step 1 — W-B: five co-tenant classes, and an `after` column that proved nothing

Per class: restart → **before**-rate → placement by PCI BDF *inside the window the before-rate just
opened* → start co-tenant → **during**-rate (co-tenant resident, and for class 3 still inferring) →
stop co-tenant → **after**-rate. Rig: `campaign/ff-probes/wb_poison_classes.py`.

Every class ran the **same model with the same tensor override** (`Qwen3-30B-A3B`,
`-ot .ffn_.*_exps.=CPU`), so the only variable is *which device the co-tenant touches*. That also
keeps the GPU buffer at **784 MiB** — production is dual-split at ~15 GB of a 32.5 GB card, so a
full-weight co-tenant would have needed 17.5 GB on card 1 and the cell would have measured an
allocation failure instead of co-residency. It is also the right analogue: the co-tenant in the
original ADR-0041 event was Flash holding ~1 GB of Vulkan compute buffer, not a full card.

| # | class | before | during | after (**+6 s**) | during/before |
|---|---|---|---|---|---|
| 1 | control — nothing runs | 106.70 | 105.87 | 105.68 | 0.99× |
| 2 | Vulkan server, loads, **never infers** | 106.48 | 105.64 | 105.98 | 0.99× |
| 3 | Vulkan server, loads **and infers** | 105.50 | **96.73** | 105.64 | **0.92×** |
| 4 | CPU-only (`--device none -ngl 0`) | 106.00 | 106.20 | 106.24 | 1.00× |
| 5 | iGPU-only | 106.49 | 105.68 | 106.01 | 0.99× |

Incumbent placement asserted dual-split on every class (`0000:04:00.0` 14.516 GB /
`0000:09:00.0` 15.437 GB, `idle_read=False`). Co-tenant placement read back from its own load
report: class 2/3 `Vulkan1=784.4MiB`, class 4 `gpu_buffers=0`, class 5 `Vulkan0=784.4MiB`.

**The `during` column is sound and is the part worth keeping.** It needs no waiting period:

- **A resident-but-idle neighbour is free.** Class 2 loaded a model onto a B70 and never received a
  request; the incumbent did not move.
- **An actively inferring neighbour on the same B70 costs 8%, and only while it infers.** Class 3
  is the identical config differing in one bit. 8% is cheap for a second model on the card.
- **A co-tenant that never touches a GPU costs nothing** (class 4), and neither does one on the
  iGPU (class 5). Contention is local to the shared card.

**The `after` column is worthless** — sampled 6 seconds after co-tenant exit, before the effect
arrives.

#### Step 2 — the loss showed up minutes later, with nothing on the cards

Class 3's epoch was left running. At roughly +5 min — **no co-tenant**, correct dual-split
placement, a **4-minute-old** epoch:

| burst | epoch age | reps (tok/s) | mean | of baseline |
|---|---|---|---|---|
| 1 | 3:58 → 4:07 | 62.95 · 47.46 · 30.29 · **29.31** | 42.50 | **40%** |
| 2 | ~1 min later | 69.67 · 48.32 · 29.50 · **27.59** | 43.77 | **41%** |

All eight requests sequential on slot 1; one `llama-server` process; nothing else listening; and
the census taken immediately afterwards read the two cards at **exactly production's own
footprint** (`local` 14.516 / 15.485, `non_local` 0.002 / 0.446) — so nothing else held memory on
them. Not contention, and not another session.

#### Step 3 — remove co-residency entirely; it still degrades

`campaign/ff-probes/wb2_delayed_onset.py` restarts the incumbent and samples on a schedule with
**no co-tenant at any point**:

| epoch age | reps (tok/s) | mean | of baseline | within-burst slope |
|---|---|---|---|---|
| t+0 | 105.97 · 106.36 · 105.90 · 105.89 | 106.03 | **100%** | 0.999 |
| t+5 min | 74.37 · 57.89 · 31.17 · 26.60 | 47.51 | 45% | 0.358 |
| t+10 min | 74.09 · 59.17 · 32.12 · 27.96 | 48.34 | 46% | 0.377 |
| t+15 min | 47.71 · 27.30 · 28.74 · 27.67 | 32.85 | 31% | 0.580 |

**Co-residency is not necessary.** And it does not accumulate cleanly with epoch age — t+5 and
t+10 are the same curve.

#### Step 4 — the threshold, and a mitigation that works

`campaign/ff-probes/wb3_idle_ladder.py`, one server, arms in order:

| idle before the burst | reps (tok/s) | mean | of fresh |
|---|---|---|---|
| 0 s | 106.43 · 107.03 · 106.70 · 106.64 · 106.20 | **106.60** | 100% |
| **30 s** | 106.65 · 106.44 · 106.32 · 106.61 · 106.69 | **106.54** | **100%** |
| **60 s** | 106.13 · 106.37 · 106.43 · 106.73 · 106.60 | **106.45** | **100%** |
| **120 s** | 68.92 · 46.19 · 29.09 · 28.53 · 25.80 | **39.71** | **37%** |
| 300 s | 35.55 · 28.29 · 27.01 · 25.92 · 26.54 | 28.66 | 27% |

⚠ The arms share one server, so the ladder is independent only **up to and including the first arm
that falls**. The 300 s row started already degraded. The 0/30/60/120 rows are the result.

**Replicated on a second fresh server**, and it is not a decay — it is a transition between two
stable states:

| arm | reps (tok/s) | mean |
|---|---|---|
| idle 0 s | 106.61 · 106.99 · 106.45 · 106.24 · 106.72 | **106.60** |
| idle 120 s | 68.19 · 42.67 · 29.55 · 29.03 · 28.28 | **39.54** *(run 1: 39.71)* |
| immediately after, **0 s gap** | 27.42 · 27.48 · 27.41 · 27.49 · 27.84 | **27.53** |

The two idle-120 runs agree to within 0.5%. The third arm is the informative one: once collapsed,
the rung is **stable at ~27.5 tok/s** — dead flat, not noisy, and continued load does not recover
it. So the picture is **two states ~3.9× apart** with a ~4-request transition between them, not a
gradual decline.

⚠ **The first post-idle request is variable and is not a reliable detector.** Observed rep-1 values
after a gap: 68.19, 68.92, 74.37, and — on a real door call after ~4 minutes idle — **91.75**. Any
health check that samples once after an idle period can easily read near-healthy. It takes a
sustained burst to see the state.

**Keep-alive, run on its own fresh server** (the first attempt ran after the 300 s arm and so
tested *"does pinging revive a collapsed server"* — no — instead of *"does pinging prevent
collapse"*):

| arm | reps (tok/s) | mean |
|---|---|---|
| fresh, idle 0 | 104.43 · 105.04 · 105.17 · 104.58 · 104.94 | **104.83** |
| **300 s gap, 1-token ping every 20 s** | 104.48 · 104.77 · 105.02 · 104.76 · 105.10 | **104.83** |

**Identical.** One trivial request every 20 seconds buys back ~4×.

#### What this explains, and what it costs

Three separate published-then-retracted findings collapse into this one mechanism:

- **"Sustained-rate decay that recovers after idle"** — the decay is real but only *post*-idle,
  which is why 40 back-to-back requests on a **fresh** server held 102–107 flat and looked like a
  refutation. Both observations were true of different machine states.
- **"Co-residency poisons the incumbent"** — real loss, wrong agent. Running any experiment leaves
  the incumbent idle for minutes.
- **The `-ub 1024` "4× regression"** — ub512 fresh after restart (104) against ub1024 after
  co-resident Flash work (22–27) is exactly a warm-vs-cold comparison.

⚠ **The operating point may be far below the benchmark point.** An agent that asks a question every
few minutes is past the threshold on *every* call.

The kernel ledger has been checked, and it does **not** settle this — for a reason worth recording.
Reconstructing `tokens_out / duration_ms` for `omen-arc` `local_generate` calls gives door-observed
rates of **4.0 – 44.1 tok/s, median ~15**, never close to 106. But `duration_ms` is the *whole door
call*, so that number confounds the cold-idle regime with **door overhead**, which finding C2
already flags as large and non-constant. A live test separated them once: a door call made after
~4 minutes idle returned 100 tokens in **11 781 ms** (8.5 tok/s end-to-end) while the server's own
log recorded that same task decoding at **91.75 tok/s** — about **1.09 s of decode inside an
11.78 s call**. On that sample the door, not the rung, was ~91% of the cost. ⚠ Overhead is not
constant: a 10-token proof the same evening completed in 281 ms. **C2 is strengthened, not
resolved**, and it now looks at least as consequential for real throughput as the idle state is.

**Mechanism still unknown, but the field is narrowed.** Spill, eviction and thermal are excluded by
direct measurement *in the degraded state* — `non_local` 0.002 / 0.446 GB, 0 °C spread at 50 °C.
GPU clock/power state is the surviving candidate, and IGCL cannot measure it on this box
(b70tools lists voltage/frequency as unusable on the top slot), so it needs HWiNFO — the same gap
ADR-0041 registered, now with a much sharper question to ask of it.

**Bracketed, not resolved:** 60 s holds and 120 s falls; nothing between was tested, on one model
at one context. The 120 s collapse itself is **replicated** (39.71 / 39.54), but the threshold's
location is a single bracket.

#### Harness defects found along the way

- **`CoTenantDriver` shadowed `threading.Thread._stop`.** `Thread` owns a private `_stop()` and
  calls it at teardown; binding an `Event` to that name killed the thread with `'Event' object is
  not callable` — and killed it on **the one cell that discriminated**, which returned
  INCONCLUSIVE while the four less-informative classes reported cleanly.
- **The verdict threshold was picked for the wrong effect size.** `DROP = 0.85` was chosen to catch
  a 3.7× loss and could not see an 8% one, so class 3's real contention drop was first labelled
  NO-EFFECT. Now **derived from the control class** — which drifted ≤1% across an identical
  schedule — and set to **0.97**. Verdicts were recomputed from the originally recorded rates and
  appended as a `W-B-RECLASS` row; **no measurement was re-run and no original row altered.**
- **The keep-alive arm shared a server with the arms that preceded it**, so it measured recovery
  rather than prevention. Documented in the probe and re-run standalone.
- **Readiness for a co-tenant must be its own `model loaded` marker, never a test completion.** The
  obvious probe — send a short request and see if it answers — makes the co-tenant **infer**, and
  *whether it infers* is the single bit separating class 2 from class 3. A completion used as a
  health check would have quietly turned the control into the treatment.

### `-ub 1024` — PROMOTED on a valid A/B, and B1 is resolved *(2026-08-29 evening)*

The first warm-vs-warm re-measurement, and the one that had been sitting invalid since the
morning. Rig: `campaign/ff-probes/ub_ab.py`, receipts `probe: "UB-AB-WARM"`.

**Both arms on the live server at production flags, both warm after restart, interleaved
A-B-A-B, measured at `-np 2`.** Not llama-bench: it has no `-np` (finding A5) and so tests one
slot, which is precisely the gap that let the first promotion ship unvalidated. The keep-alive
timers were stopped for the run — they are production's warmth control, but here they would
inject an uncontrolled third request into a two-slot concurrency measurement.

| metric | `-ub 512` | `-ub 1024` | delta |
|---|---|---|---|
| **within-config drift** — repeats of the *same* config | 0.23% | 0.30% | *the control* |
| decode, single stream | 105.66 | 106.11 | **+0.4%** |
| aggregate at `-np 2` | 129.37 | 128.92 | −0.3% |
| prefill @512 | 2190.66 | 2162.28 | **−1.3%** |
| prefill @2048 | 2498.78 | 2643.45 | **+5.8%** |
| prefill @8192 | 1307.87 | 1468.79 | **+12.3%** |

**The drift control is what makes this readable.** Repeats of the same config agreed to
**0.30%**, so a 5–12% between-config difference is real and the +0.4% decode figure is not. An
A-then-B ordering could not have separated "1024 is faster" from "the machine got faster";
interleaving forces drift to show up as within-arm disagreement instead.

⚠ **The gain is prompt-length dependent and NEGATIVE at 512 tokens.** This is a bet on long
prompts, and it corroborates FF6c's crossover surface rather than replacing it — **B2 is
upgraded from SUSPECT to supported**, on a server-side measurement this time.

#### The promotion gate, all three parts

1. **Gain at production context** — above.
2. **No shared-usage spill.** `serve-arc.cmd` mandates re-running the assert after `-c`/`-np`,
   and `-ub` moves the same compute buffers. Measured under an *identical* 8192-token load on
   both arms — a figure from one arm alone would have proved nothing:

   | | local (per card) | non_local total |
   |---|---|---|
   | `-ub 512` | 14.559 / 15.451 | **0.476 GB** |
   | `-ub 1024` | 14.908 / 15.802 | **0.732 GB** |

   +0.35 GB VRAM per card, +0.26 GB shared, against a **10.24 GB** spill signature and ~16 GB
   free per card. Clean.
3. **Door proof** `ok:true`, `routed_by: pinned:omen-arc`. Post-promotion `ff_ratecheck`
   **106.31 tok/s = 100% of baseline**; `arc-serve.log` free of TDR/WHEA/device-lost.

⚠ **`config_assertion: behavioral`** — and the label is the point. llama-server does not print
`n_ubatch` at the default verbosity, so the promoted server cannot testify to its own ubatch; the
same class of blindness as ADR-0042. The evidence is a rate separation: `prefill@8192` on the
running server reads **1470.97**, which is **0.15% from the ub1024 arm and 12.5% from the ub512
arm**. That is strong corroboration and it is **not** a logged launch parameter.

**Provenance *type* is recorded separately from provenance *quality*, because they are
independent properties.** This evidence is strong *and* indirect. Carrying both prevents a later
audit from reading the numbers and mistaking inference for direct observation — the failure mode
that let a single-card campaign run for days on an assumed placement. Direct observation would
need a relaunch at `-lv 5`, or an upstream change making llama-server print `n_ubatch` by default.

#### B1 is resolved: refuted, not merely withdrawn

The morning's claim was that `-ub 1024` causes a ~4× decode regression at `-np 2`. That was
retracted the same day as *unsupported*; it is now **refuted**. At `-np 2`, warm against warm,
ub1024 is decode-neutral (+0.4%, inside the 0.30% drift band's reach and certainly not −75%).

The entire 104 → 22–27 spread that produced the original claim is explained by
[ADR-0043](adr/0043-the-rung-goes-cold-when-idle.md) with the ubatch value **held constant**:
the ub512 arm was measured on a fresh server and the ub1024 arm after co-resident work had left
the rung idle for minutes. It was a warm arm against a cold one.

**What this says about the method, not the flag:** the retraction was right to withdraw the
causal claim and wrong about which variable had moved. Naming a confound is not the same as
identifying it — and the honest version of "we don't know" left the flag correctly reverted for
the wrong reason for a day. The mechanism campaign is what made the re-measurement possible;
without ADR-0043 there was no way to put two arms in the same machine state.

### B3 — dual-split vs single-card: **the crossover is between 512 and 1024 tokens** *(2026-08-29)*

The second warm-vs-warm re-measurement. W-A had settled 512 tokens on a serving topology
(dual-split costs decode and buys nothing there), but the recorded "+27% / +42% prefill" came from
**pp2048 on llama-bench**, which has no `-np` (finding A5) and so cannot express a serving topology
at all. The crossover was real and its *location* unmeasured.

Rig: `campaign/ff-probes/b3_topology_crossover.py`, receipts `probe: "B3-TOPOLOGY-CROSSOVER"`.
Protocol carried over from the `-ub` A/B deliberately, so the result is directly comparable to it
and to FF6c: interleaved `dual-single-dual-single`, within-config drift as the noise floor,
warm-only arms, identical load on both arms for the spill figure, placement asserted from each
server's own load report (GPU buffers only).

**Two independent runs.** The 512 anchor reproduced to the digit and 2048 to within 0.6 points:

| prompt tokens | dual (tok/s) | single (tok/s) | **dual advantage** | run |
|---|---|---|---|---|
| 512 | 2161.14 | 2201.30 | **−1.8%** | 1 |
| 512 | 2164.14 | 2202.78 | **−1.8%** | 2 |
| 1024 | 2418.44 | 1753.51 | **+37.9%** | 2 |
| 1536 | 2435.29 | 1679.63 | **+45.0%** | 2 |
| 2048 | 2513.55 | 1741.82 | **+44.3%** | 1 |
| 2048 | 2522.59 | 1740.49 | **+44.9%** | 2 |
| 8192 | 1313.64 | 762.74 | **+72.2%** | 1 |

| cost of dual-split | run 1 | run 2 |
|---|---|---|
| decode, single stream | −6.3% (104.92 vs 111.99) | −5.3% (105.33 vs 111.21) |
| aggregate at `-np 2` | −3.8% (134.53 vs 139.78) | −2.9% (136.72 vs 140.78) |
| within-config drift | **0.24%** | 2.34% |
| `non_local` (spill) | 0.055 GB both arms | 0.038 / 0.034 GB |

#### The shape, which is the actual answer

**Dual-split changes sign between 512 and 1024 tokens.** Below that it is a small net loss; above
it the advantage is large immediately (+37.9% at 1024) rather than ramping in. The curve then
**plateaus around +45% through 1536–2048 and rises again to +72% at 8192** — not a smooth
monotone, which is worth noting because a two-point sample at 512 and 2048 would have implied one.

⚠ **The shape may not be one crossover at all.** An abrupt gain by 1024, a plateau across
1536–2048, and a second rise at 8192 is the signature of **multiple prompt-processing regimes**
rather than a single smooth transition — plausibly batching or dispatch thresholds that change
which kernel path runs. This is a reading of the shape, **not** a mechanism: nothing here
identifies a regime boundary, and per R1 an explanation is not promoted merely because it fits.
Recorded so the shape is not later flattened to "dual wins above 512", which would discard the
part most likely to be informative.

⚠ **A single pp2048 cell would have been the wrong shape of answer.** It would have returned
"+44%" and left open whether the crossover sat just below 2048 or far below it, and it would have
missed the second rise entirely.

**The llama-bench figure was directionally right and understated.** +27% / +42% at pp2048 against
**+44.3% / +44.9%** measured on a serving topology at `-np 2`. So FF6c's crossover surface is
corroborated, not replaced — this locates it and shows it is steeper than the bench suggested.

#### What it means for production

Production runs dual-split at `-c 131072 -np 2`, and **that was already forced by capacity**: at
that context the KV block is ~12 GB, so a single card would hold model + KV + compute ≈ 30.1 GB of
32.5 GB — precisely the ADR-0042 defect footprint. B3 says the forced choice is also the right one
on merit: real agent prompts are essentially never under 1024 tokens, so production sits far on the
dual-split side of the crossover, paying ~5–6% decode for ~45–72% prefill.

⚠ **Single-card is not thereby useless** — it wins decode by 5–6% and aggregate by ~3%, and it uses
one card. For a short-prompt, decode-heavy seat on *one* B70 it is the better topology, which is
exactly the one-per-card replica case T1 raises.

#### Scope limits, stated on every row

- **Measured at `-c 32768 -np 2`, not production's `-c 131072`.** A single-card arm cannot hold
  production's context (above), and neither arm fits *beside* production, which holds ~15 GB on
  each card while a single-card arm needs ~19 GB on one. So this answers the **topology** question,
  not production's operating point. Production comes down for the window.
- **Run 2's drift was 2.34%**, ten times run 1's. The prefill deltas (37.9–45%) are far outside it,
  but run 2's decode figure (−5.3%) is only ~2.3× its own drift floor. **Run 1's −6.3% against
  0.24% drift is the stronger decode number**; treat the decode cost as ~5–6%, not a precise value.
- No spill either way — `non_local` was identical across topologies under identical load.

#### The bug this run found: `-np` splits the context

The first attempt died mid-run, with production already down, on `HTTP 400` at the 8192 arm.
**With `-np N` each slot gets `c/N` tokens, not `c`** — at `-c 16384 -np 2` a slot holds 8192, so
an 8192-token prompt exactly exhausts it. Production's `-c 131072 -np 2` gives each slot 65536.

The probe now **refuses before taking production offline** if the largest prompt exceeds a slot.
That is the general lesson: a rig that takes a resource offline must validate its own parameters
*first*, because the cost of a late failure is not a failed run, it is an outage that bought
nothing.

### B4 — Flash's "−42% co-residency tax" is **refuted**, and the cost lands on the other party *(2026-08-29)*

Rig: `campaign/ff-probes/b4_flash_coresidency.py`, receipts `probe: "B4-FLASH-CORESIDENCY"`.

**A config mismatch had to be resolved before anything could be measured.** The −42% (5.04 → 2.93)
was recorded on Flash at **`-ngl 0`** — weights on host, a ~1 GB Vulkan compute buffer doing the
work. W-A's clean solo baseline of **14.56** is a *different* venue: `-ot .ffn_.*_exps.=CPU`, with
attention and KV resident on both B70s (2.1 / 2.4 GB). Scoring one against the other would compare
two venues and call the difference a co-residency tax. So both configs were bracketed: `ngl0`
answers the historical claim on its own terms, `expcpu` is the venue the matrix actually uses.

**Local bracket per config — solo → co-resident → solo** — so Flash carries its own within-session
drift control instead of leaning on a baseline measured on another day by another rig.

| config | solo (pre / post) | solo mean | co-resident | **Flash tax** | local drift | epoch |
|---|---|---|---|---|---|---|
| `ngl0` | 5.60 / 5.32 | 5.46 | 5.25 | **−3.9%** | 5.24% | **VALID** |
| `expcpu` | 12.98 / 13.34 | 13.16 | 12.85 | **−2.3%** | 2.72% | **VALID** |

Incumbent health, gated pre and post, recorded as its own observation and never folded into the
tax:

| config | incumbent pre | **during** | incumbent post | verdict |
|---|---|---|---|---|
| `ngl0` | 105.25 (99%) | **76.24** | 105.14 (99%) | VALID — full recovery |
| `expcpu` | 105.57 (100%) | **89.54** | 106.20 (100%) | VALID — full recovery |

#### Two questions, and only one of them is answerable here

**Is −42% excluded? Yes, decisively** — by more than 3× the drift floor in both configs. A −42%
tax would put `ngl0` co-resident at ~3.17 against a measured 5.25.

**Is the residual tax distinguishable from zero? No.** −3.9% against 5.24% drift, and −2.3%
against 2.72%, are both inside their own noise. The honest statement is **"at most a few percent"**,
not a point estimate.

⚠ **Conflating those two questions is a real trap, and the first version of this probe fell into
it** — it returned INCONCLUSIVE because it could not resolve a 3% effect, and in doing so would
have thrown away a decisive refutation. *Unresolvable* and *unexcluded* are different verdicts.
The probe now computes both and reports them separately.

#### The finding that actually matters: the cost lands on production, not on Flash

The original claim was that co-residency taxes **Flash**. Under controlled warmth it does almost
nothing to Flash — and takes **15–28% off the incumbent** while they share:

| | Flash pays | production pays |
|---|---|---|
| `ngl0` | −3.9% (unresolvable) | **−27.6%** |
| `expcpu` | −2.3% (unresolvable) | **−15.2%** |

Both fully transient: the incumbent returned to 99–100% of its own pre-rate the moment Flash
exited, so this is **contention, not ADR-0043 poisoning**. And it is far larger than W-B's 8% for a
30B co-tenant — consistent with C3, which found Flash **CPU-bound** at 4.52 core-seconds per token
with 23.9 of 24 cores busy. Flash does not fight production for the GPU; it fights it for the host.

**So the historical figure was not merely inflated by cold-state contamination — it was assigned to
the wrong party.** ⚠ Per R1, that is a description of where the cost falls, **not** a mechanism: it
is consistent with CPU contention and this run did not test that. What can be said is that the
*direction* of the original claim does not survive.

⚠ **This does not make Flash free.** A co-resident Flash seat costs production up to a quarter of
its throughput for as long as it runs. That is a real scheduling cost — it simply belongs in the
incumbent's column, where a planner can see it, rather than being charged to the tier that is
barely affected.

#### Original receipt preserved

The T1b four-venue entry recording 5.04 → 2.93 stands unaltered; this section is added alongside it
and the entry now carries a pointer here. No measurement was deleted or rewritten.

#### Two harness defects found

- **A failed pre-rate skipped the cell silently.** `expcpu`'s first co-resident attempt died on a
  transient `WinError 10054` — the incumbent reports its ready marker a moment before it reliably
  accepts sockets — and the probe printed *nothing*, so a missing arm looked like a design choice
  in the output rather than a failure. Now retried once and, if it still fails, announced loudly.
- **The verdict conflated resolvable with excluded** (above).

### B5 — dense vs MoE: the ratio is **4.5–5.0×, not ~6×**, and the error was on the MoE side *(2026-08-29)*

Rig: `campaign/ff-probes/b5_dense_vs_moe.py`, receipts `probe: "B5-DENSE-VS-MOE"`. Last in the
queue deliberately: until B3 and B4 landed there was no way to stop topology or warmth
masquerading as an architectural effect.

**The historical claim carried four confounds, all now identifiable.** The dense seats each sat on
**one card**, were measured **simultaneously with each other and with Flash**, at **different
contexts** (27B @32k, coder-32B @16k), with **no warmth gate**. The ~121 they were compared against
was a `llama-bench tg128` run whose receipt reads `tensor_split "1.00"`.

So: one model at a time, fixed topology, same context, warm, placement asserted, models
**interleaved** within each topology so cross-model drift shows as within-model disagreement.

| topology | model | decode | within-model drift | prefill @512 | @2048 |
|---|---|---|---|---|---|
| dual | **MoE 30B-A3B** | **106.17** | 0.02% | 2162.76 | 2470.18 |
| dual | dense Qwen3.8-27B | 23.52 | 0.04% | 615.27 | 838.91 |
| dual | dense Qwen2.5-32B | 22.84 | 0.04% | 679.98 | 841.43 |
| single | **MoE 30B-A3B** | **112.42** | 0.22% | 2127.25 | 1687.08 |
| single | dense Qwen3.8-27B | 23.62 | 0.13% | 636.36 | 614.81 |
| single | dense Qwen2.5-32B | 22.34 | 0.04% | 609.52 | 533.77 |

| ratio MoE : dense | dual | single |
|---|---|---|
| vs Qwen3.8-27B | **4.51×** | **4.76×** |
| vs Qwen2.5-32B (size/quant-matched) | **4.65×** | **5.03×** |

Drift of 0.02–0.22% against a ~4.5× effect is the cleanest signal-to-floor ratio in the campaign,
so these are not close calls.

#### The dense measurement was always right; the MoE number was wrong

**Dense reproduces the historical figure almost exactly: 23.52 / 23.62 against the recorded 23.54.**
Four confounds were removed and it did not move. The entire discrepancy sits on the other side —
**~121 from llama-bench against 106.17 / 112.42 measured on a serving topology, warm.**

So the ~6× was not inflated by contention or cold state. It was inflated by **comparing a bench
number to serving numbers** — the same class of error as B1 (`llama-bench` has no `-np`, A5) and as
the mislabeled "121.6 dual-split" W-A caught. ⚠ Three separate findings this campaign have now been
distorted by one habit: *quoting a llama-bench figure alongside server measurements as if they were
the same quantity.*

#### A new result that extends B3: the topology effect is **architecture-dependent**

| | decode, dual → single | prefill @2048, dual vs single |
|---|---|---|
| MoE 30B-A3B | 106.17 → 112.42 (**+5.9%**) | **+46.4%** for dual |
| dense Qwen3.8-27B | 23.52 → 23.62 (**+0.4%**) | +36.4% for dual |
| dense Qwen2.5-32B | 22.84 → 22.34 (**−2.2%**) | +57.6% for dual |

**Dense decode is essentially topology-insensitive** — ±2% — while the MoE gains ~6% from a single
card. B3's decode cost is therefore an **MoE property, not a general one**, which no measurement
before this could have separated.

⚠ **And the prefill crossover moves with architecture.** B3 located the MoE's sign change between
512 and 1024. For dense Qwen2.5-32B, dual already wins at 512 (**+11.6%**), so its crossover is
*below* the range B3 sampled. A single crossover point is a property of a model, not of the box.

#### What this does and does not settle

**Settles:** the per-seat cost of a dense planner/builder/reviewer split against the MoE incumbent
is **~4.5–5×**, not ~6×. Still large — a dense seat is a genuinely expensive way to buy
specialisation — but a fifth cheaper than the figure the design discussion has been using.

**Does not settle:** ⚠ a 3B-active MoE doing less work per token than a 27B dense model **is what
MoE is for**. B5 asks only whether the *ratio* survives the confounds. Per R1, finding that it
moved to 4.5–5× licenses no mechanism story, and nothing here says the gap is or is not worth
paying — that depends on output quality per seat, which this does not measure.

**Scope:** `-c 16384 -np 2`, one model at a time, production down for the window.

### Confidence-sorted state of knowledge

(full ledger:
`E:\work\battlemage\ff-probes\ff-receipts.jsonl`):

**CONFIRMED — safe to build on.** Production was on one card (A1) and is fixed by *removing* the
index filter (A2); Vulkan enumeration is nondeterministic so **no index scheme is safe, including
`-dev`** (A3); `llama-bench` parses `-ts` differently from `common/arg.cpp` (A4) and **has no
`-np`** (A5); **GPU residency costs host commit ~1:1 while mmap host residency is nearly free**
(A6); three venues co-reside and serve (A7); Flash needs GPU compute even with host weights (A8)
and **cannot bank prefill — it is a hybrid attention/SSM model** (A9); **KV rates span ~10× and
the GQA formula is unreliable** (A10); Flash needs the qwen38 fork (A11); `local_committed` is an
activity-window counter (A12); `lanes.json` LUIDs are stale so the render spill guard is **inert**
(A13).

**RESOLVED (was B1) — `-ub 1024` is promoted.** The "4× regression" is **refuted**, not merely
withdrawn: warm against warm at `-np 2`, ub1024 is decode-neutral (+0.4%) and buys +5.8% / +12.3%
prefill at 2048 / 8192 tokens, with repeats of the same config agreeing to 0.30%. The whole
104 → 22–27 spread behind the original claim is ADR-0043's idle collapse with the ubatch value
held constant. **B2 (the FF6c crossover) is upgraded to supported** by the same run — the gain is
prompt-length dependent and negative at 512 tokens. See the `-ub 1024` section above.

**RESOLVED (was B3) — the topology crossover is located.** Dual-split changes sign between **512
and 1024 tokens**: −1.8% at 512, +37.9% at 1024, ~+45% through 1536–2048, **+72.2% at 8192**, for a
decode cost of ~5–6% and ~3% aggregate at `-np 2`. Two independent runs, the 512 anchor reproducing
to the digit. FF6c's surface is corroborated and the llama-bench figure was understated. See the B3
section above.

**RESOLVED (was B4) — Flash's −42% co-residency tax is refuted.** Bracketed and health-gated, the
tax on Flash is −2.3% to −3.9%, inside its own drift floor; −42% is excluded by >3× that floor on
both Flash configs. The cost is real but lands on the **incumbent** (−15 to −28% while sharing,
fully recovered after). See the B4 section above.

**RESOLVED (was B5) — the dense-vs-MoE ratio is 4.5–5.0×, not ~6×.** The dense measurement
reproduces exactly; the MoE side was a llama-bench number quoted against serving numbers. B5 also
found the topology effect is **architecture-dependent**: dense decode is topology-insensitive
(±2%) while the MoE gains ~6% from a single card, so B3's decode cost is an MoE property.

**SUSPECT — none of the original five remain.** B1 (refuted, `-ub 1024` promoted), B2 (supported by
the ub A/B), B3 (crossover located), B4 (refuted; the cost falls on the incumbent), B5 (corrected to
4.5–5.0×) are all closed. What remains open is not a suspect measurement but the **provenance
repair**: pre-2026-08-29 receipts still carry `PLACEMENT_CONTEXT_UNKNOWN` /
`INCUMBENT_HEALTH_UNKNOWN` and fall in **E2, the single-card epoch**.

**OPEN.** The **poisoning mechanism** (C1) — behaviour characterised, cause unknown; IGCL
frequency is unusable on the top slot per b70tools, so confirming it needs HWiNFO power/clock
telemetry, which is installed but whose VSB export is not enabled. (This replaces the former
"decay mechanism" entry, which is retracted — there is no decay to explain.) Also open: door
overhead 2212 ms of inference inside a 14250 ms call (C2); NPU occupancy — Flash is **CPU-bound at
4.52 core-s/token, 23.9/24 cores busy, GPU 3d only 14.27%**, which supports the NPU reframe but
there is no NPU serving path to measure (C3); placement is un-targetable (C4); the FF1 harness is
unbuilt (C5).

---

## 0 · Where this layer sits

Three campaigns now run against the same two B70s. They are **not** peers; they stack:

| Layer | Campaign | Unit of measurement | Question |
|---|---|---|---|
| Mechanism | LZ1–LZ8b | GB/s, tok/s, seconds | *What is the hardware physically doing?* |
| Serving topology | R0–R10 / W-phases | tok/s, commit GB, swap seconds | *Which model should be resident where?* |
| **Factory (this)** | **FF1–FF10** | **completed R&D work per occupied machine-hour** | *Which whole configuration produces the most finished autonomous work?* |

**The unit change is the entire point of this layer.** Every existing card measures
throughput. None measures *work*. A configuration can win every tok/s row and still lose
the factory, because it rebuilds context four times, burns a window on re-prefill, or
occupies both cards while producing one merged change.

### 0.1 · The load-bearing integration fact

`corpus/schema/bench-row.v1.json` already normalizes the configuration axes this brief
names — `model`, `model_quant`, `context_size`, `concurrency`, `n_gpu_layers`,
`split_mode`, `tensor_split`, `placement`, `topology`, `device_kind`, `device_count`,
`replica_id`, `engine_build`, `kv_type`, `flash_attn`, `threads`, `task_family`,
`workload`, `hw_id`, `card_identity`, `commit_headroom_bytes`, plus `confidence`, `valid`,
and `failure_class`. `corpus/schema/run-manifest.v1.json` carries hardware fingerprint,
engine build commit, device resolution, model SHA-256, flags, environment, and a
`telemetry` block with an explicit `complete` flag.

**Therefore: this campaign does not run a new throughput sweep.** The denominator largely
exists. What is missing is a numerator (work completed and its quality), an occupancy
measure, and a join. New machine-hours go to FF1–FF5 and the FF6 telemetry, not to
re-measuring tok/s across a config grid we have already crossed many times.

### 0.2 · Already answered — do not re-run

Reps from 2026-08-20→29 have closed or narrowed most of the configuration space. Re-running
these buys nothing; cite the receipt instead.

| Brief item | Status | Authority |
|---|---|---|
| Single vs dual B70; replica-per-card | **Measured** — 1.85× symmetric two-server scaling; replica topologies executed | OMEN-LIMIT-TEST-2026-08 Stage 5 |
| Heterogeneous co-residency | **Measured** — 30B@card1 + 27B@card2 held solo rates (99.2 / 21.6 tok/s) | W0 probe P5 |
| Quantization + context ladder, 3.8 family | **Measured** — `do_not_promote` (12/15); operating-point inversion reproduced in 2 harnesses | qwen38 campaign; `corpus/backfills/qwen38-campaign-full-*` |
| Context value at depth | **Partly measured** — 27B-vs-MoE jobs/hour 0.687× / 2.63× / 5.49× at 512 / 8K / 32K | campaign backfill jsonl |
| Memory placement ladder (`-ngl` blocks) | **Measured** — 4.86 / 7.41 / 9.24 / 11.66 / 16.38 / 27.70 tok/s at 0/16/24/32/40/48 blocks; 60.4 GB at full | Flash-Next placement ladder |
| Load/swap vs simultaneous residency | **Measured** — KV save 1.74 s, restore 1.19 s, ~3 s round trip vs ~100 s re-prefill; swaps drain | W0 P2/P3/P7 → ADR-0040 |
| Weight-load cost | **Measured** — 30B 7.9–8.3 s, 27B 8.5–12 s, Flash mmap 15.5→38.9 s by placement | 44 launch logs |
| Prefix/KV reuse value | **Measured** — ~306× prefix-miss penalty (39.75 s → 0.13 s at 26K); 157× for 27B+MTP | ROTATION-PROGRAM receipts |
| Host-RAM expert residency | **Measured (endpoint)** — `-ot exps=CPU` runs beside production at zero pagefile tax; file-backed experts never become commit | R0 / LZ1 Stage 0 |
| Flash prefill "cliff" | **Refuted** — 512-tok prefill 32.6–52.5 tok/s co-resident; 11.7 was a first-eval + 22-token artifact | LZ1 Stage 0 |
| Wire bandwidth | **Frozen** — ~13 GB/s H2D per B70 (3 estimators agree); D2D symmetric ~6.7 GB/s | LZ3 |
| B70↔B70 P2P | **Dead by silicon** — `canAccessPeer=0` | LZ brief Q1 |
| iGPU participation in serving | **Retracted** — CPU experts (23–24 tg) beat the hop-taxed iGPU hybrid at idle; whole 30B-A3B on iGPU = 13 tok/s | LZ8/LZ8b, commit `ff1d926` |
| NPU as router LLM | **IGNORE** — single-digit tok/s class + ~1–2 min compile | LZ brief; NPU-20/21 are compile-admission probes only |
| SYCL as production backend | **IGNORE**, with named re-check triggers | LZ brief Q6 — see FF7 |
| Unbuffered weight loads ([#26014](https://github.com/ggml-org/llama.cpp/pull/26014)) | **Parked** — E: is drive-bound at ~3.0 GB/s | LZ4a |

### 0.2b · Two axes we have *not* crossed, surfaced 2026-08-29

Reviewing [discussion 27593](https://github.com/ggml-org/llama.cpp/discussions/27593) against
our own corpus exposed two axes that are in the schema but unpopulated. Both are cheap and both
can move a FF10 row.

| Axis | Our state | Why it matters |
|---|---|---|
| **KV cache quantization** | ~~`kv_type` is `"f16"` in all 1638 corpus rows~~ → **throughput half MEASURED 2026-08-29 (FF4)**; ceiling half still open. | 27593 reports q4_0 KV fitting **131072** context on a *single* 32 GB B70 (f16 49152, q8_0 65536). Our 131072/262144 quarantine is a **dual-split thermal** limit, so single-card + quantized KV remains untested. **Result so far: KV quant is not a speed lever** (see FF4) — its entire case is the ceiling, and that needs a window. |
| **SYCL built with F16** | Never built. Our SYCL verdict rests on third-party numbers whose build flags we did not control. | See FF7 — `-DGGML_SYCL_F16=ON` defaults **OFF**, and is reported worth 3.72× on prefill. |

**Standing controls (inherited verbatim from the LZ cards, every FF cell):** env vars latch
at backend registration — set before process start; `--no-repack` on every `-ot` run; unique
prompts or `cache_prompt:false`; never time rep 1; timing from server-internal `timings`,
never wall clock; High Performance power plan + fixed `--threads` for CPU-involved cells;
record HAGS state once; TDR (WDDM 2 s) is the first suspect on device-lost; every receipt
row records co-residency + BF6 render-queue state; `ZE_AFFINITY_MASK` is never set.

**Amendment 2026-08-29 (c) — run FF-CENSUS before every cell in a series.**
`campaign/ff-probes/ff_census.py --label <run> --phase pre` pings every known GPU and GPU
consumer and appends one `FF-CENSUS` row. The load-bearing field is **the Vulkan enumeration
order THIS run saw**, because that order is *nondeterministic on this box* — it reshuffles
between runs, so an index-based device filter correct in one process is wrong in the next with
no error. That is not hypothetical: production was found running **all 49 layers on ONE B70**
(30108 MiB = 92.5% of one card, second card idle) because `GGML_VK_VISIBLE_DEVICES=1,2`
resolved to `[B70, iGPU]` in the scheduled-task context. **No index scheme is safe, including
`-dev`/`--device`** — its `VulkanN` names are positional too. The fix was *removing* the filter:
with none set, `ggml-vulkan.cpp:7479-7495` selects by device TYPE and llama.cpp drops the iGPU
at placement, which is order-independent.

⚠ **And do not gate placement on `gpu.adapter.vram.local.bytes_committed`.** It is an
**activity-window** signal, not steady-state residency: on one unchanging, healthy server it
read 14.86/15.78 GB after start, **0.002/0.004 GB while idle**, then 14.88/15.80 GB after a
single inference — the model never moved. Authority order: (1) llama-server's own load report
at `-lv 5`, (2) per-card **temperature** delta (credible per b70tools where voltage/frequency
are not), (3) that counter, inside its activity window only.

**Amendment 2026-08-29 (b) — assert placement, never assume it.** Every FF cell claiming a
multi-card placement must capture the per-device `model buffer size` lines and **verify both
cards are non-zero before any timing is trusted**. Learned the hard way: `llama-bench`'s `-ts`
takes **slash**-separated values (`-ts 1/1`). Passing `-ts 1,1` parses as the single value `1`,
loads the entire 17524.42 MiB model onto `Vulkan0`, leaves `Vulkan1` empty — and emits **no
warning at all** while returning entirely plausible numbers. That is the silent-fallback failure
mode in its purest form, and it mislabeled a full lap of cells before a verbose load log caught
it. LZ7 already mandates counting `buffer type overridden` lines to validate `-ot` placement;
this is the same rule for `-ts`, and it would have caught the error at cell 1 instead of cell 4.

**Amendment 2026-08-29 — the noise floor is the repeat spread, not the sample stddev.**
Measured in FF4: `llama-bench`'s stddev over 3 *consecutive* samples is 0.01–0.13 (≈0.03–0.4%),
while the **repeat-to-repeat spread of the identical condition minutes apart is 1.6%** on
prefill. Consecutive-sample stddev understates real noise by ~50×. **Every FF cell runs its
grid at least twice and uses the between-repeat spread as the noise floor.** Reporting
confidence from back-to-back samples manufactures false positives — the exact failure §3's
confidence contract exists to prevent. This is why FF6's +34.4% is trustworthy (far above
1.6%) and FF4's prefill "differences" are correctly called flat.

### 0.3 · Three concerns stated once, then executed around

1. **The numerator is an instrument that does not exist yet.** Everything downstream of
   FF1 is a comparison, and a comparison across a noisy work-slice is noise. FF1 is
   therefore gated on a *measured replay variance bound* before any FF5 config comparison is
   believed. A slice never observed replaying consistently is a hypothesis, not a benchmark.
2. **Power telemetry is known-degraded and degrades silently.** `corpus/verdict.py:186-190`
   records the Z890 case: the bus-4 B70 reported 206 samples against bus-9's 824 because
   IGCL power telemetry kept dropping out on one slot, and nothing in b70tools' own verdict
   noticed — it silently degraded every power and thermal number attributed to the quieter
   card. Every FF6 saturation figure **gates on `verdict.py`'s `symmetry_check` ratio** and
   records it. A missing power sample is `null`, never `0`.
3. **The SYCL ask splits in two.** Q6 already answers *SYCL as a production backend*:
   IGNORE, on four independent receipts including our exact shape (dual-B70 MoE layer split
   ignores `--tensor-split` → single 25.4 GB alloc → OOM). That verdict is not re-litigated
   here; it is re-checked only by its own cheap canary. *SYCL as an attribution oracle* is a
   different question that the IGNORE does not answer — an instrument does not need to be
   fast to be informative. FF7 does the oracle half only.

### 0.4 · The denominator: what "occupied machine-hour" means

> ⚠ **AUDITED 2026-08-30 — the vector below is a specification, not a measurement.**
> **0 of 405 FF receipts carry any of its axes**, the `b70_*_s` axes have **no instrument on
> this box** (per-process GPU counters read 0 under S4U), and `hearth/media/occupancy.py`
> measures a *different dimension* (media, not compute) with a deliberate fail-closed bias
> that is correct for a guard and wrong for a meter. Read
> [`docs/FF1-DENOMINATOR-AUDIT.md`](FF1-DENOMINATOR-AUDIT.md) before building anything
> against this. The first question is which denominator is wanted, not how precise it is.

OMEN is not a benchmark rig. It simultaneously carries production serving, the BF6 render
lane, and campaign windows, and `hearth/media/occupancy.py` establishes that **media and
compute occupancy are independent dimensions**. A configuration that finishes work while
leaving a card free for renders is worth strictly more than one that does not.

Occupancy is therefore recorded as a vector, never a scalar:

```
occupancy = { b70_0_s, b70_1_s, cpu_core_s, ddr5_peak_gb,
              commit_peak_gb, render_lane_blocked_s, wall_s }
```

`useful work per occupied machine-hour` is reported against **each** axis, and the Pareto
set in FF10 is computed over the vector. Collapsing this to "GPU-hours" is what produces a
false universal winner.

### 0.5 · Upstream lane — the two live threads this campaign feeds

**Citation rule (house):** always full URLs. A bare `#27652` auto-links into commandcenter's
own issue space and 404s.

| Thread | What it is | What FF owes it |
|---|---|---|
| [PR 27652](https://github.com/ggml-org/llama.cpp/pull/27652) — *vulkan: add `GGML_VK_MMV_MAX_COLS` override for mul_mat_vec dispatch* (ours, **open**) | Replaces the hardcoded `static constexpr uint32_t mul_mat_vec_max_cols = 8;` with a runtime env override. Measured: Mistral-Small-24B on Arc Pro B70 at ub=12 — MMV cols=12 **5.8 s/pass**, cols=16 **3.1 s/pass**, matmul cols=8 **40.7 s/pass**. | **The maintainer's stated blocker is our FF8 rung-2 deliverable**: the default stays 8 and *no per-vendor defaults are proposed without measurement data*. FF6's surface is exactly that data. |
| [Discussion 27593](https://github.com/ggml-org/llama.cpp/discussions/27593) — B70 SYCL tuning report (third-party, open) | Single Arc Pro B70, **Linux** (xe kernel 7.0.0-28-generic, Level Zero 1.15.39122), Qwen3.8-27B-Q8_0. | Generates four hypotheses for FF4/FF6/FF7 and one cross-lane lead for R7 — **all Linux-sourced, none yet true on our box**. |

**What 27593 actually reports** (verified against the source, 2026-08-29 — it is *not* a
prefix-cache patch, despite the title's suggestion):

- `-DGGML_SYCL_F16=ON` (**defaults OFF**): pp2048 **389.2 → 1446.8 tok/s = 3.72×**. Full build
  line: `-DGGML_SYCL=ON -DGGML_SYCL_TARGET=INTEL -DGGML_SYCL_F16=ON -DGGML_SYCL_DEVICE_ARCH=bmg_g21 -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx`
- Vulkan on the same box: prompt processing **2.2× slower** than that SYCL build.
- `-ub 2048`: **+35%** prefill. `-fa 1`: **+6%** prefill.
- KV `q4_0` → **131072** context on one 32 GB card (f16 → 49152, q8_0 → 65536).
- MTP via `--spec-type draft-mtp`, **no separate draft model**: 50.92 t/s (3.2×) at n-max 6;
  33.53 t/s (2.1×) at n-max 3.

⚠ **The platform gap is the whole story, and we have direct precedent for it.** Every number
above is Linux. LZ3 already established that Linux-class Arc figures do not transfer to this
box — the Linux-class 20–28 GB/s wire did not materialize on Windows 8974, where we measured
~13 GB/s across three independent estimators. Treat 27593 as a **hypothesis generator with a
named platform gap**, never as a result. Nothing from it enters a FF row without a Windows
re-measurement, and any cell that reproduces there is a finding in its own right.

⚠ **Do not compare 27593's SYCL numbers to our Vulkan corpus directly.** Their 1446.8 pp2048 is
27B *dense* Q8_0 on *one* card; our production control is 30B-A3B *MoE* Q4_K_M dual-split at
pp512 = **2399 tok/s** (depth 0), 469 (depth 8192), decode 112.5 — different model, quant,
depth, prompt length, and card count. The only sound comparison in 27593 is its **internal**
Vulkan-vs-SYCL delta on one unchanged box.

**Cross-lane, MTP — 27593 corroborates a number we already own, and it is not the blocker.**
27593's `--spec-type draft-mtp` result (3.2× at n-max 6, no separate draft model) lands almost
exactly on our own measurement: [ADR-0038](adr/0038-a-verdict-cites-only-evidence-from-the-configuration-it-promotes.md)
records MTP roughly tripling the 27B dual-production shape, **510 → 1591 jobs/hour (~3.1×)**.
Two platforms, two backends, same model family, same answer — so the *mechanism* is settled
and needs no further measurement.

⚠ **Do not read this as unblocking MTP-on.** The blocker is ADR-0038's own rule — every
deterministic assay row feeding that scorecard was measured **MTP-off**, so a verdict may not
cite it for an MTP-on configuration. Unblocking is a **quality re-run on the MTP-on config**,
not a throughput question. What 27593 adds is confidence that the re-run is worth scheduling.

**The separate R7 (Flash) question is now ANSWERED — 2026-08-29, zero GPU.** The check was a
GGUF metadata read; it took minutes and it closes a registered blocker with a definite
architectural reason.

| Model | Arch | Blocks | `nextn` tensors | Reading |
|---|---|---|---|---|
| Flash-Next UD-IQ4_XS | `qwen4exp` | 48 (512 experts, 10 used) | **0 of 1224** | **No built-in MTP head.** |
| Qwen3.8-27B Q4_K_M | `qwen35` | 64 | 0 of 851 | Base carries none either… |
| `mtp-Qwen3.8-27B-Q4_0.gguf` | `qwen35` | **65** | 4 of 18 (`blk.64.nextn.*`) | …because the 27B's head ships as a **separate sidecar** (base 64 + 1 nextn layer). |

⚠ **A regex nearly produced a false positive here.** Flash scores 192 "MTP-ish" tensor hits —
every one is `hc_attn_{down,inject,norm,up}`, a `qwen4exp` architecture tensor present on all
48 blocks. Match on **`nextn`**, not on a loose pattern, or Flash looks MTP-capable when it
is not.

**Consequences, in order of value:**

1. **27593's "no separate draft model required" does not transfer to Flash.**
   `common/speculative.cpp:1293-1300` documents three modes and the no-sidecar one —
   *"neither (qwen35 / qwen35moe): a single trained MTP head"* — is a **qwen35-family**
   property. Flash is `qwen4exp`. The claim was true and simply not about our model.
2. **W3's failure is now fully explained.** The community sidecar refused with `hc_attn_norm`
   missing because a `qwen4exp` draft target requires `hc_attn_*` on every block and a
   qwen35-layout sidecar has none. That was a layout mismatch, not a bad artifact.
3. **Two of R7's three registered paths are dead.** *"cafe as a 2nd binary"* — dead, the
   mismatch is tensor layout, not the binary. *"extract the head ourselves"* — dead, there is
   no head in Flash's weights to extract. Only *"wait for fork-native"* survives, and it is
   now precisely stated: **waiting for someone to publish a `qwen4exp`-layout MTP draft
   carrying `hc_attn_*`.** That is an ecosystem dependency, not a local task — so R7 should
   stop consuming window time and become a watch item.

Receipts: `E:\work\battlemage\ff-probes\ff-receipts.jsonl`, probe `FF-MTP`.

---

## FF1 — the replayable long-horizon R&D slice *(keystone; everything else depends on it)*

**Hypothesis:** a long-horizon autonomous R&D task can be made replayable enough that the
same work, run under different configurations, differs by less than the effect sizes we
intend to measure. If replay variance swamps configuration effects, the entire factory
question is unanswerable by this method and we should learn that in one afternoon rather
than after ten windows.

**Moves:** whether FF3/FF5/FF10 mean anything at all. This is the gate on the campaign.

**Design — the slice is a *re-run of a probe whose true answer we already hold.*** Do not
invent a synthetic task. Pick a completed LZ/R probe with a full receipt trail, strip the
repo back to its pre-probe commit, and hand the agent the original card's hypothesis. This
solves the scoring-oracle problem that normally sinks agentic benchmarks: **we already know
the correct verdict, the correct failure, and the evidence a correct answer must cite.**

Two candidate slices, both carrying a genuine failed hypothesis and recovery:

- **Slice A — LZ4a (unbuffered weight loads).** True verdict: gate FAILED, E: is drive-bound
  at ~3.0 GB/s, the [#26014](https://github.com/ggml-org/llama.cpp/pull/26014) port is PARKED. Contains a real dead end reached honestly.
  Requires: planning, a read ladder, measurement, a negative result, and a park decision.
- **Slice B — LZ8→LZ8b (iGPU expert venue).** True verdict: mechanism real but quant-gated,
  then RETRACTED at idle when CPU experts beat the hop-taxed hybrid. Contains a
  **reversal** — the agent must first find a positive, then overturn it. Harder, and the
  better test of continuity.

Run Slice A first (cheaper, unambiguous); promote Slice B once A's variance is bounded.

**Scored dimensions** (all recorded per run, never collapsed to pass/fail):

| Class | Fields |
|---|---|
| Outcome | `verdict_match` (vs the known true verdict), `evidence_cited`, `artifact_quality`, `regressions_introduced` |
| Time | `wall_s`, `prefill_s`, `decode_s`, `human_intervention_s` |
| Cost | `tokens_in`, `tokens_out`, `b70_0_s`, `b70_1_s`, `cpu_core_s`, `commit_peak_gb`, `ddr5_peak_gb` |
| Process | `context_rebuilds`, `human_interventions`, `repeated_mistakes`, `rework_cycles`, `tests_run`, `tests_passed` |
| Stability | `device_lost_events`, `server_restarts`, `thermal_aborts` |

`verdict_match` is scored by a rubric written **before** any run and frozen with the slice —
same freeze discipline as the Promotion Gate. Rubric authorship is a human act, once.

**Rig:** slice repo state pinned by commit; the agent gets the card's Hypothesis and Moves
sections and nothing downstream of them; all receipts land in `corpus/runs/<run_id>/` with a
full `run-manifest.v1` so the config identity joins to `bench-row.v1` rows.

**Kill / promote:** run Slice A **5× on one fixed configuration** (the current production
default) before any comparison. Coefficient of variation on `wall_s` and on the rubric score
≤ 25% → the slice is an instrument; proceed to FF2–FF5. CV > 25% on the rubric score → the
task is not replayable at this granularity; **stop, and either constrain the slice further
or abandon the work-per-hour framing for a proxy metric.** Do not proceed to config
comparison on an uncalibrated instrument.

---

## FF2 — orientation tax

**Hypothesis:** the compute and wall-clock cost of reconstructing project state after a
context reset or handoff is large, measurable, and **asymmetric across workflow shapes** —
and it is currently invisible because every existing receipt measures a warm agent.

**Moves:** the context-length and workflow-shape decisions in FF5; whether "short context +
frequent reset" is cheap in practice or only cheap in memory.

**Definition (frozen):**

```
orientation_tax = (tokens + wall_s + b70_s) consumed between a context reset
                  and the first subsequent action that advances the slice's
                  scored state, minus the same measured for a warm agent at
                  the identical slice position.
```

The subtraction is what makes it a tax rather than a startup cost. "Advances the scored
state" is defined by the FF1 rubric, so this metric inherits FF1's calibration and cannot be
computed before FF1 passes.

**Rig:** instrument the slice harness to force a reset at three fixed slice positions
(early / mid / late — after the failed hypothesis is the interesting one). Measure the tax at
each. Vary the external-memory scaffold: none / plain notes file / structured state doc.

**Kill / promote:** tax measurably grows with slice position → continuity has compounding
value and FF3 is worth running in full. Tax flat across positions → context reset is a fixed
cost, structured external memory is sufficient, and the very-long-context arm of FF5 can be
cut to a single confirmatory cell.

---

## FF3 — continuity dividend

**Hypothesis:** retaining experiments, failures, decisions, code history, and evidence
*directly in context* produces measurable improvement over reconstructing them from
artifacts — and the improvement concentrates in the **recovery** phase after a failed
hypothesis, not in the initial build.

**Moves:** the central FF5 comparison; the context-length-versus-cost frontier in FF10.

**Definition (frozen):** the paired difference in FF1 rubric score and in `rework_cycles`
between an agent that carried the failure in-context and one that re-derived it from
artifacts, at identical slice position and identical model/quant/placement. **Paired on the
slice, not averaged across slices** — the effect is expected to be smaller than
cross-slice variance.

**Rig:** the same slice run twice per configuration, differing only in whether the
failed-hypothesis segment remains in context at the recovery point. Slice B (the reversal) is
the discriminating case; Slice A may show nothing, and a null on A is not a null on B.

**Kill / promote:** dividend positive and larger than FF1's replay CV → long-context arms
earn their prefill and memory cost, and FF10 gets a real "context length worth paying for"
answer. Dividend inside the noise band → **structured external memory is as good as
context**, which is the single most decision-changing negative result available in this
campaign — it would collapse the FF5 space to shapes 1, 2, and 4 and cut the memory budget
for the whole lab.

---

## FF4 — prefill amortization

**Hypothesis:** there is a break-even context depth beyond which the prefill and KV cost of
ingesting more context is not repaid by the autonomous execution it enables — and, given the
~306× prefix-miss penalty, that break-even moves sharply depending on whether the context is
*reused across turns* or re-ingested.

**Moves:** "best context length before marginal capability stops paying" (FF10); the KV
manifest / rotation design's caller model.

**Definition (frozen):**

```
prefill_amortization = scored_slice_progress_after_ingest / (prefill_s + kv_bytes_cost)
```

reported per context point, with `kv_bytes_cost` expressed in the same occupancy vector as
FF1 (commit GB-seconds), not as a bare byte count.

**KV quantization is a first-class axis here, and we have never varied it** (§0.2b: all 1638
corpus rows are `kv_type: f16`). 27593 reports q4_0 KV reaching **131072** on a single 32 GB
card where f16 stopped at 49152 and q8_0 at 65536 — i.e. the KV dtype, not the card, was the
context ceiling. Since `kv_type` is already a `bench-row.v1` field, adding it costs a flag and
a re-run, not a schema change. ⚠ Quantized KV trades accuracy for depth: pair every KV-quant
cell with the FF1 rubric score, or the extra context will look free when it is not.

**Rig:** the FF1 slice at context points **8K / 32K / 64K / 128K**, crossed with
`kv_type` ∈ {f16, q8_0, q4_0} — plus 256K **only if a config admits it without spilling** (R0: commit is 96.9 GB of a 135.3 GB limit with
production up; and depths 131072/262144 are thermally quarantined on dual-split, so a 256K
dual-split cell is a *thermal* refusal, not a capability datum). Each point measured twice:
cold-prefix and warm-prefix, so the 306× reuse factor enters the amortization explicitly.

**Reuse note:** the throughput half of this curve is largely already in the corpus — the
27B-vs-MoE jobs/hour ladder (0.687× / 2.63× / 5.49× at 512 / 8K / 32K) and the qwen38
operating-point inversion. FF4 supplies only the numerator; join, don't re-measure.

**Kill / promote:** a knee appears below 128K → publish it as the lab's default context and
size the KV manifest to it. Monotonic improvement to the memory ceiling → context is
capacity-limited, not value-limited, and the interesting lever moves to FF9's hierarchy.

### FF4 first result — KV quantization is not a speed lever *(2026-08-29, Lap 0, co-resident)*

30B-A3B Q4_K_M, **single-B70** (see amendment b — this cell carried the same `-ts` mislabel;
all three arms shared the identical placement, so the comparison stands), `-ub 512`,
depth 8192, `-r 3` × 2 repeats:

| test | f16 | q8_0 | q4_0 | reading |
|---|---|---|---|---|
| pp512 @ d8192 | 461.4 | 461.1–468.5 | 465.9–472.6 | **flat** — within-type repeat spread (1.6%) exceeds every between-type gap |
| tg128 @ d8192 | **33.23** | 31.68 (**−4.7%**) | 32.28 (**−2.9%**) | real, reproducible to 0.4% |

- **Quantized KV costs decode and buys no prefill.** Adopting it for speed would be a
  mistake; its entire value proposition is the **memory/context ceiling**, which a co-resident
  cell structurally cannot test. The 27593 ceiling claim is still open and needs a window.
- ⚠ **Non-monotonic in bit-width: q4_0 decode *beats* q8_0 by +1.9%, reproducibly.** The q8_0
  dequant path costs more than q4_0's on Vulkan/Battlemage. That is a mechanism observation,
  not a benchmark artifact, and it is a **candidate upstream note** — nobody would predict the
  wider type being slower.
- Also banked: decode falls **122 → 33 tok/s** from depth 0 to 8192 (both co-resident).

Receipts: `ff-receipts.jsonl`, probe `FF4-kv`.

---

## FF5 — workflow architecture bake-off *(the headline comparison)*

**Hypothesis:** workflow shape dominates model choice for long-horizon autonomous work. A
mid-tier model in the right workflow finishes more scored slices per occupied machine-hour
than the best model in a naive one.

**Moves:** everything in FF10's "best long-horizon autonomous configuration" row; the seat
design for R8/R9.

**Arms** (each run against the frozen FF1 slice, identical rubric):

| # | Shape | Notes |
|---|---|---|
| 1 | Short context + frequent checkpoint/reset | Pays FF2's tax repeatedly; cheapest per-turn |
| 2 | Medium context + structured external memory | The FF3-null-hypothesis arm |
| 3 | Very long continuous context | Pays prefill + KV once; FF4 decides if it repays |
| 4 | Planner → builder → reviewer handoffs | Three orientation taxes; parallelism possible |
| 5 | Long-lived primary + specialized secondaries | The rotation program's natural shape |
| 6 | Empirical hybrid | Authored only after 1–5 report; not pre-specified |

**Config cross:** do **not** cross all six arms against the full config space. Cross them
against **three** configurations chosen from existing receipts as the representative corners
— current production default (30B-A3B dual-split), the single-card 27B depth specialist, and
the best Flash-lite hybrid rung LZ7 promotes. Nine-plus arms × three configs is the entire
window budget; a full cross is not affordable and would not change a decision.

**Kill / promote:** an arm wins on scored work per occupied machine-hour by more than FF1's
CV → promote it as the lab's default autonomous shape and record the margin. All arms inside
the noise band → workflow shape does *not* dominate at this slice length, and the honest
finding is that the slice is too short to discriminate — lengthen it before concluding
indifference.

---

## FF6 — prefill saturation surface + the saturation oracle

**Hypothesis:** prefill fails to drive the B70s into the sustained high-power regime that
generative image/video workloads demonstrably reach, and the gap is a *software dispatch*
property — insufficient exposed parallel work per submission — rather than a hardware
ceiling. Making the reference regime explicit turns "the GPU seems idle" into a measured
duty cycle.

**Moves:** whether the prefill investigation is chasing a real headroom or a physical
ceiling; which upstream contribution in FF8 is worth authoring.

**The oracle:** the BF6 render lane already drives these exact cards through sustained
high-compute operation on the same host, the same driver, and the same telemetry path. It is
the reference for what a saturated B70 looks like on this box — no new workload needs
building. Capture a render-lane telemetry trace once and freeze it as the saturation
reference profile.

**New metrics (frozen definitions):**

```
time_to_saturation   = seconds from prefill start until board power crosses
                       (reference_p50_power × 0.9) for 3 consecutive samples
saturation_duty_cycle = fraction of prefill wall time spent above that line
```

Both are `null` — not `0` — whenever the telemetry gate below fails.

**Telemetry gate (mandatory, per §0.3.2):** every FF6 row records `verdict.py`'s
`symmetry_check` sample-count ratio between the two identical cards. Ratio below a frozen
floor → the row's power-derived fields are `null` and the row is marked
`partially_scored`. Thermal and clock figures attributed to the quieter card inherit the
same flag. This is the one place the campaign is most likely to fool itself.

**Surface axes:** prompt length × `-b`/`-ub` × concurrency × dense-vs-MoE × single-vs-dual
B70 × stock-vs-tuned dispatch. Correlate PP tok/s against board power, clocks, memory
behavior, kernel/dispatch timing, submission gaps, and CPU utilization **where each is
actually available** — and record which were `null` per row rather than dropping the row.

**Two named candidate points from 27593, pre-checked against our own corpus:**

- **`-ub 2048` (+35% prefill claimed).** Our production control runs `n_ubatch: 512` with
  `n_batch: 2048` — so this is a **genuinely untested delta on our box**, and it sits on the
  axis FF6 already sweeps. Promote it to an early cell; it is one flag.
- **`-fa 1` (+6% prefill claimed).** ⚠ **Already on for us** — `flash_attn: true` in the
  production control rows. There is no gain to collect here; do not schedule a cell for it.

The difference between those two is the reason the corpus is checked before the sweep is
designed: one is free headroom, the other is a number we already banked.

### FF6 result — the `-ub 2048` win is a **single-card artifact** *(2026-08-29, corrected)*

⚠ **Read the correction first (§ standing controls, amendment b).** The initial FF6 run passed
`-ts 1,1`, which `llama-bench` parsed as the single value `1`, loading the whole model on one
card with no warning. Those numbers are valid **as single-B70 results**; the placement label was
wrong. The corrected dual-split run reverses the conclusion.

30B-A3B Q4_K_M, `GGML_VK_VISIBLE_DEVICES=1,2`, `-r 3`, co-resident, **3 repeats**, placement
asserted (`Vulkan0` 8975.63 + `Vulkan1` 8548.79 MiB, both non-zero, `tensor_split 1.00/1.00`):

| test | ub 512 | ub 2048 | Δ | noise floor | call |
|---|---|---|---|---|---|
| pp512 | 2378.06 | 2383.44 | +0.2% | 1.50% | within noise |
| **pp2048** | **2529.25** | **2447.36** | **−3.2%** | 0.64% | **REAL — a loss** |
| tg128 | 105.10 | 104.06 | −1.0% | 1.20% | within noise |

**Production recommendation RETRACTED. Do not raise `-ub` on the serving config.**

**Why the reversal:** dual-split already captures what `-ub 2048` bought on one card. At
`ub 512`, pp2048 is **2529.25 dual vs 1783.43 single (+42%)** — the micro-batching penalty that
a larger ubatch repaired on a single card **does not exist across two**.

**Single vs dual at `ub 512` — layer split trades decode for prefill:**

| test | single-B70 | dual-split | Δ |
|---|---|---|---|
| pp512 | 2373.57 | 2378.06 | ~0% |
| pp2048 | 1783.43 | **2529.25** | **+42%** |
| tg128 | **121.90** | 105.10 | **−14%** |

That is a real, reproducible tradeoff and it **answers FF10's "best use of dual B70s" row**,
which the mislabel had only raised as a question.

**The irony is worth recording.** 27593 measured a *single card* — so the `-ts` mistake
accidentally reproduced their exact topology, and reproduced their number almost exactly
(**+34.4% vs their +35%**). Their claim is correct *for their configuration*; it simply does
not transfer to our dual-split production shape. The cards' platform-gap caution was aimed at
Linux-vs-Windows. **The axis that actually mattered here was card count.**

**Feeds FF8 rung 3:** the correct ubatch depends on **placement** as well as prompt shape, so
no per-architecture constant can be right — which is a stronger argument for shape-aware
selection than the original (wrong) reading was.

Receipts: `ff-receipts.jsonl`, probes `FF6-ub` (single-card), `FF-CORRECTION`,
`FF6-ub-dualsplit`.

### T1b — four-venue residency: **the lab holds it** *(2026-08-29, windowed)*

Production stopped for the window (needed elevation). Three venues resident **and serving
simultaneously**:

| venue | model | decode | VRAM | host commit |
|---|---|---|---|---|
| B70 `09:00.0` | Qwen3.8-27B @32k | 23.54 tok/s | 20.40 GB (64%) | 20.4 GB |
| B70 `04:00.0` | qwen2.5-coder-32B q5 @16k | 20.64 tok/s | 25.45 GB (80%) | 25.8 GB |
| host RAM | Flash-Next (87.25 GiB) | 2.93 tok/s | — | **2.57 GB** |

**⚠ The finding that reshapes the design: GPU residency costs host commit ~1:1; host residency
via mmap is nearly free.** Per-process private bytes matched each seat's VRAM almost exactly,
while the 87 GiB Flash committed 2.57 GB with its weights in a 35.65 GB standby cache. The sum
(48.77 GB) accounts for the entire system commit move, 54.9 → 104.6 GB.

So **the 135.3 GB commit limit couples GPU and host capacity** — two full B70 seats reserve
~64 GB of commit before any host-resident model loads. "How many simultaneous projects" is a
**commit** question, not a VRAM question. (b70tools' README already said the real wall is commit
charge, not free RAM.)

**Two costs a residency table alone would hide:**

- **Co-residency taxes the host tier −42%**: Flash fell 5.04 → 2.93 tok/s once both seats went
  live. Its weights are in RAM but its *compute* rides the same cards, so the strategic tier is
  not isolated from GPU contention.
  > ⚠ **REFUTED 2026-08-29 by B4 — see the B4 section above.** The observation above is preserved
  > as recorded. Under a bracketed solo→co-resident→solo test with the incumbent health-gated, the
  > tax on Flash is **−3.9%** on this very config, inside its own drift floor, and −42% is excluded
  > by >3× that floor. The cost is real but falls on the **incumbent** (−27.6% while sharing, fully
  > recovered after), not on Flash. The mechanism guess in this bullet — GPU contention — is also
  > not supported: C3 shows Flash is CPU-bound, so the contention is for the host.


- **Dense seats decode ~21–24 tok/s against the 30B MoE's ~121.** A planner/builder/reviewer
  split built from dense models runs ~6× slower per seat than the incumbent. That is the price of
  specialisation, and residency does not reveal it.
  > ⚠ **CORRECTED 2026-08-29 by B5 — see the B5 section above.** The dense half of this is right
  > and reproduces exactly (23.52 / 23.62 against the 23.54 recorded here). The **MoE half is not**:
  > ~121 is a `llama-bench tg128` figure, and on a serving topology the same model reads 106.17
  > (dual) / 112.42 (single). The ratio is therefore **4.5–5.0×**, not ~6×. Still a large cost, but
  > a fifth smaller than the number the design discussion has been using.



**Placement remains un-targetable.** The *lighter* model landed on the *cool* card, inverting the
constitution's "hot card gets the lighter model" rule. Symmetric `-ts` does not care; this seating
arrangement does. Identity-based placement is the only fix.

### T2 — Flash as strategic tier: viable, with two hard limits *(2026-08-29)*

**Memory holds convincingly:** 87.25 GiB of weights for **+2.2 GB commit** (mmap keeps them
file-backed). But:

| config | prefill | decode |
|---|---|---|
| `-ngl 0`, GPU compute available | 5.77–7.94 | 5.04–5.50 |
| `-dev none` (GPU fully excluded) | **0.15** | **0.37** |

⚠ **"Fully CPU, no GPU" is a dead end** — ~38×/15× worse. With `-ngl 0` the *weights* are on host
but llama.cpp offloads the *compute* to a B70 via a ~1 GB buffer, and that buffer does nearly all
the work. **Flash therefore needs a ~1 GB tenancy, not a dedicated venue** — it can share a card
with a dense seat.

⚠ **Prefill cannot be banked, architecturally.** Slot save/restore round-trips the bytes fine
(138 MB in 52.7 / 31.0 ms) but the prefix is unusable: llama-server reports *"forcing full prompt
re-processing due to lack of cache data (likely due to SWA or hybrid/recurrent memory)"*, and the
metadata confirms it — Flash declares **SSM parameters** (`ssm.conv_kernel`, `ssm.state_size`,
`ssm.group_count`, …). It is a hybrid attention/state-space model; recurrent state is not
reconstructible from a KV slot. The 30B has no such keys, which is why W0 got `prompt_n=1`.
**The prefill-elsewhere pattern belongs to the B70 dense/MoE seats, not the strategic tier.**

Consequence: Flash pays its prefill *every conversation* (a 2k brief ≈ 6 min) and is viable only
for **short-prompt, long-generation** work. ⚠ Flash also needs the **qwen38 fork** binary — the
knee build rejects `qwen4exp` — so the lab requires two llama.cpp builds.

### Measured KV rates — never derive them *(2026-08-29)*

| model | layers / n_kv / key_len | **measured** | formula |
|---|---|---|---|
| Qwen3.8-27B (`qwen35`) | 64 / 4 / 256 | **64 KiB/tok** | 256 — **4× wrong** |
| qwen2.5-coder-32B (`qwen2`) | 64 / 8 / 128 | 256 KiB/tok | correct |
| Qwen3-30B-A3B (`qwen3moe`) | 48 / 4 / 128 | 96 KiB/tok | correct |
| Flash-Next (`qwen4exp`) | 48 | **622 KiB/tok** | — |

**A ~10× spread across models we would seat together**, and the standard GQA formula is 4× wrong
on Qwen3.8-27B (likely compressed/MLA-style KV). **Take the server's own `memory_breakdown` as
authority; never derive KV from metadata.** Context sizing is a per-model property and the
router's catalog needs *measured* rates.

⚠ Self-correction from this lap: I measured 64 KiB/tok on seat 1, noted seat 2 might differ, then
generalised anyway and gave seat 2 32k — landing at **92.7%** of its card, the level production sat
at when it spilled. Re-ran at 16k: 79.7%, 6.4 GiB margin.

### FF6c — the crossover surface: **the optimum is `ub 1024`** *(2026-08-29)*

Prompt × ubatch × placement, prefill-only (decode measured unaffected by ubatch), 2 repeats,
placement asserted on both arms (`layer 1.00/1.00` / `none 0.00`). **Prefill tok/s:**

| | ub 256 | ub 512 | ub 1024 | ub 2048 | best |
|---|---|---|---|---|---|
| **dual-split** pp512 | 2085.0 | 2344.8 | **2380.0** | 2356.3 | ub1024 |
| **dual-split** pp2048 | 2012.6 | 2541.7 | **2709.2** | 2462.9 | ub1024 |
| **dual-split** pp8192 | 1067.0 | 1227.3 | **1242.8** | 1089.1 | ub1024 |
| single-card pp512 | 1700.5 | **2386.3** | 2370.8 | 2311.8 | ub512 |
| single-card pp2048 | 1304.4 | 1756.0 | 2125.6 | **2334.3** | ub2048 |
| single-card pp8192 | 658.4 | 824.6 | 963.8 | **1042.9** | ub2048 |

**`ub 1024` wins at every prompt length on production's shape** — and it is a value neither our
earlier cells nor 27593 ever tested. Against production's current `ub 512`: **pp2048 +6.6%**
(floor 1.02%, REAL), pp8192 +1.3% (floor 0.06%, REAL), pp512 +1.5% (inside a 2.71% floor).
Against the `ub 2048` I wrongly recommended: **+10.0% at pp2048 and +14.1% at pp8192.**

**Placement changes the optimum's shape-dependence.** On a single card the best ubatch *moves
with prompt length* (512 → 2048 → 2048). On dual-split it is *pinned* at 1024 across all three.

**Mechanism — dual-split and large ubatch are substitutes.** Both supply work per dispatch, so
the dual/single ratio shrinks monotonically as ubatch grows:

| | ub 256 | ub 512 | ub 1024 | ub 2048 |
|---|---|---|---|---|
| pp2048 | 1.54× | 1.45× | 1.27× | 1.06× |
| pp8192 | 1.62× | 1.49× | 1.29× | 1.04× |

Once the pipe is full from either source the other stops paying — and past the optimum, doing
both *regresses*.

**A second, independent signal says 2048 crosses a regime boundary.** The compute-buffer base is
**exactly** linear from ub512→ub1024 (V1 316.80 → 633.59, precisely 2×) but **superlinear** to
ub2048 (→ 1587.19, 2.5×, 25% above linear), while the context slope stays exactly
`ub × 7.629e-6 MiB/token` at all three values. Throughput and allocation agree independently
that 2048 is past a threshold — stronger than either alone.

⚠ **Production recommendation: `-ub 512` → `1024`.** Measured cost at `c=131072`: V0 612 → 1224
MiB, V1 829 → 1658 MiB (+0.60 / +0.81 GiB); totals 16.2 / 15.7 GiB against a 32558 MiB card,
~16 GiB spare. Buffers at ub1024 are **measured** at two contexts, not interpolated. **The one
remaining gap is `-np 2`**, still untested because `llama-bench` has no `-np` — that is what
stands between this evidence and a config edit.

**Feeds FF8 rung 3 in exactly the form 27652's maintainer asked for:** the crossover is not a
per-vendor constant but a function of prompt length **and** placement *on one vendor's card*. No
architecture-keyed default can express *"ub1024 always on two cards, but 512→2048 by prompt
length on one."*

Receipts: `ff-receipts.jsonl`, probes `FF6c-surface`, `FF6c-buffers`.

### FF5b — VRAM was never the constraint *(2026-08-29)*

Compute buffer, dual-split, measured at two contexts:

| n_ctx | ub | Vulkan0 | Vulkan1 | Host |
|---|---|---|---|---|
| 2048 | 512 | 108.04 | 324.80 | 16.05 |
| 2048 | 2048 | 432.16 | 1619.19 | 64.22 |
| 18432 | 512 | 172.04 | 388.80 | 80.05 |
| 18432 | 2048 | 688.16 | 1875.19 | 320.22 |

Fits an exact two-term model per card: `compute(ub, ctx) = base(ub) + ub × 7.629e-6 MiB/token`.
**The context slope scales with ubatch** (64 MiB per 16384 tok at ub 512; 256 MiB at ub 2048 —
exactly 4×), so the ubatch penalty *grows with context* and cannot be read off a short-context
run. KV is linear in context (192 MiB @ 2048 → 1728 @ 18432, ratio exactly 9).

Extrapolated to production's `-c 131072`: ubatch delta **+1.79 GiB (V0) / +2.74 GiB (V1)**;
totals ub512 15.6 / 14.9 GiB vs ub2048 17.4 / 17.6 GiB — **both well inside the 32558 MiB card
with ~14 GiB spare either way.** So memory never was the blocker; throughput was.

⚠ Caveats: (1) a **fit extrapolated 7× beyond the measured range** — it reproduces both points
exactly, but it is not a measurement at 131072; (2) `llama-bench` has no `-np`, so the `-np 2`
interaction is **untested** — compute buffer is sized by total ubatch so it should not multiply,
but that is reasoning, not evidence; (3) Windows Vulkan reports a **per-process** budget, so
these are what a server *would* allocate, not an observation of live free VRAM.

⚠ `GGML_VK_PERF_LOGGER=1` **crashes the qwen38 fork** (known). Kernel-timing attribution on
Flash-family models needs a different instrument or a fork fix; do not plan a cell that
silently depends on it.

**Kill / promote:** duty cycle rises materially with exposed work (larger ub, higher
concurrency, dual-card) → the headroom is real and FF8's ladder is the payoff path. Duty
cycle pinned low across the whole surface while the render oracle saturates the same cards →
the bottleneck is submission-side, and the credible upstream target is dispatch/submission
batching, not kernel tuning. Duty cycle already high while tok/s is low → the cards *are*
saturated and prefill is bound by something other than compute; stop hunting dispatch.

---

## FF7 — Vulkan ↔ SYCL attribution, and the F16 confound

**Hypothesis (revised 2026-08-29):** the "brutally bad SYCL on Battlemage" evidence our Q6
IGNORE rests on was very likely measured **with `GGML_SYCL_F16` at its OFF default**, making
it a confounded comparison rather than a backend verdict. Where a Vulkan↔SYCL prefill gap
survives a matched-flag build, it is attributable to a specific mechanism (dispatch shape,
XMX engagement, memory residency), and naming that mechanism is worth more than the ratio.

**Moves:** the confidence behind Q6's IGNORE; whether FF8's ladder has a target; whether an
upstream Vulkan issue can cite a concrete unused capability rather than a benchmark delta.

**The arithmetic that motivated the revision.** Q6 cites Gemma-4-26B on B50/B70 at **SYCL 351
pp vs Vulkan 1169**. [Discussion 27593](https://github.com/ggml-org/llama.cpp/discussions/27593)
reports `-DGGML_SYCL_F16=ON` worth **3.72×** on prefill on this exact card class. 351 × 3.72
≈ **1306** — the same class as the 1169 it supposedly lost to. Different model and box, so
this is *suggestive, not proof*; but it is a coherent explanation of the entire reported gap,
and it means the receipt we treated as decisive may be measuring a build flag.

**What does and does not change.** Q6's IGNORE for SYCL **as a production backend still
stands on independent grounds** that F16 cannot touch: our exact shape (dual-B70 MoE layer
split) ignores `--tensor-split` and attempts a single 25.4 GB allocation → OOM
([#22885](https://github.com/ggml-org/llama.cpp/issues/22885), closed not-planned), and
Battlemage **Windows** correctness produced garbled output where Vulkan was correct
([#20169](https://github.com/ggml-org/llama.cpp/issues/20169), closed not-planned). What
changes is our *confidence in the throughput half* of that verdict, and therefore **Q6's
re-check triggers are amended**: add *"a matched-flag (`GGML_SYCL_F16=ON`) head-to-head has
never been run on our box."*

**Rig — cheapest decisive cell first:**

1. **Build cell.** SYCL build with the 27593 line, adapted to Windows/oneAPI:
   `-DGGML_SYCL=ON -DGGML_SYCL_TARGET=INTEL -DGGML_SYCL_F16=ON -DGGML_SYCL_DEVICE_ARCH=bmg_g21`
   (`icx`/`icpx`). ⚠ This is a **toolchain acquisition**, not a runtime flag — budget it as a
   build-lane task, not a window. If the Windows oneAPI build does not produce a working
   binary in one sitting, **stop and record that**: "SYCL is not buildable here today" is
   itself the answer to the production question and closes the card.
2. **Matched head-to-head.** Dense model, **one** B70, small context, identical prompts,
   matched `-b`/`-ub`, Vulkan vs SYCL-F16. Correctness gate first — greedy output compared
   against the Vulkan baseline, per the [#20169](https://github.com/ggml-org/llama.cpp/issues/20169) garbling risk. A fast wrong answer is not a
   datum.
3. **Attribution.** Compare PP tok/s **and** the FF6 saturation pair. SYCL reaching a higher
   duty cycle at equal or lower tok/s is the attribution signal: it indicates Vulkan is
   leaving submission-side work unused, not arithmetic.

**Kill / promote:** SYCL-F16 fails to build or fails the correctness gate on Windows →
**Q6's IGNORE is reconfirmed on stronger grounds than before**; record and close. Matched
build shows Vulkan competitive → the "brutal" receipts were a flag artifact; say so, and the
oracle lane closes having removed a bad assumption from our own record. A reproducible
duty-cycle gap with a nameable mechanism → author the upstream issue with the render-lane
oracle trace attached as the "this hardware can do this" control.

---

## FF8 — dispatch selection: from constant to measurement

**Hypothesis:** [PR 27652](https://github.com/ggml-org/llama.cpp/pull/27652)'s finding — that
a static Vulkan dispatch threshold (`static constexpr uint32_t mul_mat_vec_max_cols = 8;`)
leaves large performance on the table and that the correct crossover is architecture-dependent
— generalizes into a ladder, and each rung is independently shippable upstream.

**Moves:** which upstream contribution to author next; whether the lab ships a B70 constant
(bad) or removes an assumption (good).

**The ladder** — the point is that each rung *removes an assumption* rather than adding a
special case:

| Rung | Change | Status | Upstream character |
|---|---|---|---|
| 0 | Static threshold `= 8` | upstream today | The assumption under test |
| 1 | Runtime override (`GGML_VK_MMV_MAX_COLS`) | **already authored — [PR 27652](https://github.com/ggml-org/llama.cpp/pull/27652), open** | Trivially safe; makes the constant *measurable by users* |
| 2 | Architecture-aware selection | **next target; blocker named by the maintainer** | Encodes that one constant is provably wrong cross-vendor |
| 3 | Shape-aware selection | not started | Threshold depends on matrix shape, not just device |
| 4 | Measured / autotuned dispatch | not started | One-time probe at load; no constant survives |

**Rung 1 is already shipped as a proposal — the campaign's job is rung 2's evidence.** The
maintainer's position on 27652 is explicit: the default stays 8, and **no per-vendor defaults
are proposed without measurement data**. That is not an objection, it is a specification. FF6's
saturation surface — prompt length × `-b`/`-ub` × concurrency × dense-vs-MoE × single-vs-dual
card × stock-vs-tuned, with the crossover located per architecture — is precisely the dataset
that unblocks rung 2. Publishing it is worth more than another B70 number.

**Standing rule:** cross-vendor evidence already indicates a universal threshold is unlikely
to be correct (ftoleedo validated on RDNA3.5, per the knee campaign). **Prefer discovering
and removing a bad assumption over adding a B70-specific hack.** A patch that helps only our
cards is a lab hack; a patch that makes the threshold measurable helps every vendor and is
far likelier to land.

⚠ Register discipline for the 0cc4m lane is unchanged: reply only when addressed, short,
Derek-typed. Never ghost-write PR prose.

**Kill / promote:** the crossover measurably differs across ≥2 architectures → post that data
to 27652 as the per-vendor evidence the maintainer asked for, and rung 2 becomes authorable.
Crossover turns out to be uniform outside the B70 → the finding is narrower than we believed;
**say so in the existing PR rather than escalating** — a narrowed claim, volunteered, is worth
more to that review than a defended one.

---

## FF9 — MoE memory hierarchy: prove or kill cheaply

**Hypothesis (to be killed if it does not pay):** B70 VRAM = hot tier → 128 GB DDR5 = warm
expert tier → NVMe = cold/backing tier is a *useful* hierarchy for MoE, and CPU-driven
routing/prefetch/residency management can hide enough DDR5/PCIe latency to make
host-resident experts worth serving from.

**Moves:** whether Flash-family models are servable beside production at a useful operating
point; the Flash-lite seat design.

**Do not restart this from zero — most of it is already carded or measured:**

- The two endpoints exist: `-ot exps=CPU` (+6.3 GB, ~10.6 tok/s, zero pagefile tax,
  file-backed experts never become commit) and full residency (60.4 GB, 27.7 tok/s).
- The frontier between them is **LZ7** (rungs 0/25/50/75/100% experts on CPU) — already
  designed, windowed, and gated on LZ1 freezing the prefill config.
- The decode-side ceiling for any experts-from-host design is **LZ6** (copy-then-execute DMA
  vs BAR execute-in-place).
- The wire is frozen: ~13 GB/s H2D per B70; D2D symmetric ~6.7 GB/s; P2P dead by silicon.

**FF9 therefore adds exactly one thing LZ6/LZ7 do not cover: the *prefetch* question.** LZ7
measures static placement; FF9 asks whether CPU-side prediction of expert residency can beat
static placement at the same memory budget.

**Rig:** at LZ7's best static rung, add a CPU-side expert-residency predictor driven by
observed routing, and compare against that rung at identical commit GB. Keep CPU work in
scheduling, prefetch, batching, and memory movement — **not** GEMM. The 10-of-512
near-uniform routing already noted in LZ7 is the strongest prior *against* this working:
uniform routing gives a predictor nothing to exploit.

**Kill / promote:** predictor beats static placement by more than the LZ7 rung spacing at
equal commit → a residency manager is worth building. Within noise, or routing measured
near-uniform → **kill the tier idea explicitly and record it**; the hierarchy is then a static
placement question LZ7 already answers.

### FF9 status — DOWNGRADED *(2026-08-29)*

⚠ **Correction to this card's own claim.** It said routing entropy is "a cheap read" that could
kill FF9 before any window. **It is not cheap on this build.** The routing tensor is emitted
only through the graph callback (`src/llama-graph.cpp:2058-2060`, cb names `ffn_moe_argsort` /
`ffn_moe_topk`), there is no env-gated expert dump in the Vulkan or CPU path, and
**`llama-eval-callback` is not built** in our pin (`build/bin` has only bench, cli, server).
Measuring entropy is a **build-lane task**, not a Lap-0 read.

**What *is* free is an analytical bound, and it is unfavorable.** Under uniform routing the
distinct-expert working set per layer saturates fast:

| model | experts | used | 50 tokens | 100 tokens | 500 tokens |
|---|---|---|---|---|---|
| 30B-A3B (`qwen3moe`) | 128 | 8 | **96.0%** | 99.8% | 100% |
| Flash-Next (`qwen4exp`) | 512 | 10 | 62.7% | **86.1%** | 100% |

A VRAM tier holding a *subset* is therefore missing almost constantly past a few dozen tokens
unless routing is both strongly skewed **and** temporally stable. Uniform is the worst case for
the tier, so this bounds rather than settles it — but it relocates the burden of proof onto FF9.

**It also converges with a call LZ7 already made** on the same reasoning: *"per-expert
(popularity) pinning is not `-ot`-reachable and has no expected edge at 10-of-512 near-uniform
routing — don't burn window time on it."* FF9's prefetch variant is that idea with a predictor
bolted on, and the arithmetic supports LZ7. It also explains why `-ot exps=CPU` performs as well
as it does: if nearly every expert is touched anyway there is no subset worth caching — you
stream them all or hold them all, which is exactly the two endpoints LZ7 measured.

**Decision: do not schedule the prefetch cell.** If revived, the prerequisite is building
`llama-eval-callback` and demonstrating skew large enough to keep the per-layer working set well
under the tier size. That is the gate, and it is a build task, not a window.

---

## FF10 — STOCK LAB vs TUNED LAB, and the Pareto set

**Hypothesis:** there is no universal winner, and the honest deliverable is a Pareto set over
the occupancy vector plus a stated rule for choosing among its members.

**Moves:** the lab's default configurations; what goes in `backends.toml`; what gets written
up publicly.

**Two matrices, identical axes, differing only in configuration source:**

- **STOCK LAB** — default/runtime-baseline: stock dispatch, stock batching, default
  placement, no tuned concurrency, no KV manifest reuse.
- **TUNED LAB** — best validated dispatch (FF8), batching (FF6), placement (LZ7/FF9),
  caching and KV reuse, backend, concurrency, and workflow shape (FF5).

The pair is what makes the campaign publishable: the delta between them is the measured
value of everything this lab has learned, stated as work per machine-hour rather than as
tok/s.

**Required Pareto answers** (each names a configuration *and* the axis it wins on):

- Best interactive configuration
- Best long-horizon autonomous configuration
- Best throughput configuration
- Best quality per GPU-hour
- Best single-B70 configuration
- Best use of dual B70s
- Best context length before marginal capability stops paying for its prefill/memory cost
- When a larger model beats a smaller one despite lower tok/s
- When multiple specialized agents beat one long persistent context
- Whether host RAM / iGPU / NPU add meaningful lab capacity *(prior: iGPU serving already
  retracted; NPU narrow to embeddings/triage — a positive here would be a surprise and
  should be treated as one)*
- Which optimizations exposed previously unused B70 compute
- Which remaining bottlenecks are credible upstream llama.cpp / Intel-runtime targets

**Kill / promote:** the Pareto set collapses to one member across every axis → say so
plainly; a genuine universal winner is a finding, not a failure. Members that differ only
inside FF1's CV are **not** distinct members and must be merged rather than reported as
choices.

---

## Receipts, invariants, and upstream readiness

### Campaign rules earned the hard way *(add to these; do not quietly drop one)*

**R1. Noticing a confound is not identifying it.** The `-ub 1024` retraction correctly saw that
two arms were not comparable and correctly withdrew the causal claim — and then attributed the
spread to the wrong variable, because "co-residency" was the confound in view and idle time was
the one that had actually moved. The result was a flag reverted for the wrong reason for a day.
**A withdrawal is not a diagnosis.** State what is unknown as unknown, and do not let the
confound you happened to name stand in for the one you have not found. (2026-08-29; B1, ADR-0043)

**R2. Provenance TYPE is recorded separately from provenance QUALITY.** They are independent:
evidence can be strong *and* indirect. `config_assertion: behavioral` marks a config inferred
from a rate separation rather than read from a launch parameter — even at a 0.15% vs 12.5%
separation, which is excellent corroboration and still not an observation. An audit that reads
only the numbers must not be able to mistake the two. (2026-08-29; the ub promotion)

**R3. Comparative arms are interleaved, never sequential.** A-then-B cannot separate "B is
faster" from "the machine got faster". A-B-A-B makes drift appear as *within*-config
disagreement, which is also the only honest noise floor: repeats of the same config agreeing to
0.30% are what license reading a 5–12% between-config difference as real. (2026-08-29; the ub A/B)

**R4. Both arms of a resource comparison are measured under an identical load.** A shared-usage
figure from one arm alone proves nothing — 0.732 GB looked elevated against a 0.24 GB reference
until the default arm was measured the same way and read 0.476 GB. (2026-08-29; the spill gate)

**R5. A control that shares a server with the arm before it stops being a control** once that arm
degrades the machine. Run it on its own epoch. (2026-08-29; the keep-alive arm in W-B3)

**R8 — INSTRUMENT ADMISSIBILITY (a gate, not guidance).**

> **A ratio, delta, crossover, or promotion claim is inadmissible unless both sides were produced
> by the same instrument and compatible execution semantics, or an explicit instrument-equivalence
> experiment exists.**

Not a caution to weigh — a precondition. A claim that fails it is not *weak*, it is **not a claim**,
and it does not enter a card, an ADR, or a commit message.

llama-bench has no `-np`, so it cannot express a serving topology (A5), and it parses `-ts`
differently from the rest of llama.cpp (A4). **One habit distorted three separate findings**: the
`-ub 1024` promotion (validated only where the harness could reach), the "121.6 dual-split" headline
(actually `tensor_split "1.00"`, single-card), and dense-vs-MoE "~6×" (a bench MoE number against
serving dense numbers — really 4.5–5.0×). Each was internally plausible, which is precisely why a
gate is needed rather than vigilance. (2026-08-29; B1, W-A, B5)

**R9. Effect resolution and hypothesis exclusion are separate verdicts. Report both.** "Can I
distinguish this effect from zero?" and "can I exclude the claim under test?" have different answers
far more often than they look. B4 measured a −3.9% Flash tax against a 5.24% drift floor: the point
estimate is **unresolvable**, while −42% is **excluded by more than 3× that floor**. The first
version of the probe returned a single INCONCLUSIVE and would have thrown away a decisive
refutation. An INCONCLUSIVE point estimate must never be allowed to obscure a strong exclusion
bound — a probe that cannot say *"I don't know the value, and I know it isn't that"* is
under-reporting. (2026-08-29; B4)

**R7. A constraint detectable before production shutdown must never be discovered after it.**
A probe that takes a resource offline validates that its requested workload can actually complete
under the proposed server configuration *first*. At minimum compute
`effective_slot_context = total_context / parallel_slots` and reject any cell whose prompt plus
generation and template overhead cannot fit it — `-np N` **splits** the context, so `-c 16384 -np 2`
gives a slot 8192 tokens, not 16384. B3's first attempt died on HTTP 400 at its third prompt length
with production already down. **A late validation failure is not a failed benchmark; it is an
outage that purchased no evidence.** (2026-08-29; B3)

**R6. Warm-vs-warm or it is not a comparison.** Every arm restarts and warms before it is
measured; a post-idle measurement is a cold one and is not comparable to anything.
(ADR-0041, ADR-0043)


**Rows.** Machine rows per trial appended to
`E:\work\battlemage\ff-probes\ff-receipts.jsonl` in the LZ convention
(`{ts, probe, cell/variant, rep, ...metrics, coresident}`), **plus** a full
`run-manifest.v1` per slice run under `corpus/runs/<run_id>/` so every factory row joins to
the existing `bench-row.v1` corpus on `hw_id` + `engine_build` + config axes. Verdict rows
per card on close, mirrored to the ROTATION-PROGRAM.html side lane and committed (docs →
master, house convention).

**Invariants to preserve for upstream credibility:** exact runtime/driver/build/model
versions (`run-manifest.v1` already carries engine `build_commit`, model `sha256`, device
`resolved`, and the hardware fingerprint); reproducible command lines verbatim; the telemetry
`complete` flag and the FF6 symmetry ratio; co-residency and render-queue state per row;
and the frozen definitions of orientation tax, continuity dividend, prefill amortization,
time-to-saturation, and saturation duty cycle. A metric whose definition moved mid-campaign
is not comparable and must be re-run or dropped.

**Null discipline (carried from the routing-harvest contract):** `null ≠ 0` anywhere in this
campaign. Unmeasured telemetry, absent power samples, and skipped cells propagate as `null`
with a `partially_scored` flag; they never become zeros in a mean. A card supported by two
of five measured dimensions is not equal-confidence to one supported by five.

**Stop rule (/rnd).** A card whose evidence cannot explain what was seen gets a "can't answer
why" row and the lap **stops** rather than repeating input.

---

## Sequencing

| Order | Card | Window tier | Gate |
|---|---|---|---|
| 1 | FF1 Slice A ×5 | Lap 0, co-resident | **Campaign gate** — CV ≤ 25% or stop |
| 2 | ~~FF9 routing-entropy read~~ | ⚠ **NOT cheap — reclassified 2026-08-29** | Needs `llama-eval-callback` built; **FF9 downgraded** on the analytical bound instead |
| 3 | ~~R7 `draft-mtp` head check~~ | ✅ **DONE 2026-08-29** | Flash has no head; 2 of 3 R7 paths dead (§0.5) |
| 4 | ~~`-ub 2048` cell~~ | ✅ **DONE 2026-08-29** | **+34.4% on pp2048**; shape effect → rung 3 (FF6) |
| 5 | ~~KV-quant ladder q8_0/q4_0~~ | ✅ **DONE 2026-08-29** | Not a speed lever; ceiling half still needs a window (FF4) |
| 5b | ~~`-ub 2048` VRAM/slot cost~~ | ✅ **DONE 2026-08-29** | VRAM never the blocker; the win was a single-card artifact — **change retracted** |
| 5c | **Dual-split decode/prefill tradeoff** | Lap 0, deliberate cell | −14% decode for +42% prefill measured in passing; FF10 needs it measured on purpose |
| 6 | FF6 oracle trace capture | Lap 0 (render lane already runs) | Telemetry symmetry gate |
| 7 | FF2 / FF4 | Lap 0 | Needs FF1 pass |
| 8 | FF3 (Slice B) | Lap 0 | Needs FF2 |
| 9 | FF7 SYCL-F16 build cell | **Build lane, not a window** | Stop-and-record if it won't build |
| 10 | FF5 bake-off | Windowed | The expensive one; needs FF1–FF4 |
| 11 | ~~FF9 prefetch~~ | ❌ **DESCHEDULED 2026-08-29** | Working set saturates (96% of experts in 50 tok on 30B-A3B); converges with LZ7's existing call |
| 12 | FF8 rung-2 evidence → 27652 | Follows FF6 | The maintainer's named blocker |
| 13 | FF10 synthesis | Desk work | Needs all above |

Steps 3–5 are new as of 2026-08-29 and are deliberately ahead of most of the campaign: each is
cheap, each was surfaced by reading the two upstream threads against our own corpus, and each
can change a decision before any window is spent.

FF1 first is not a formality. Every card from FF2 onward divides by it.
