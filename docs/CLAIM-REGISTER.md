# Claim register — the authoritative lookup

**What this is.** One row per campaign claim that has been corrected, refuted, or bounded, with the
decisive receipt and the rule that caught it. It exists so an old headline cannot escape back into a
future ADR, plan, or article by being quoted from a document that was true when it was written.

**How to use it.** Before citing any throughput, ratio, crossover, or tax figure from this campaign,
look it up here. **If a claim appears in this register, the "corrected claim / bound" column is the
citable form — not the original, wherever the original still appears in prose.** Original receipts
and prose are preserved everywhere and annotated in place; nothing was rewritten.

Receipts: `E:\work\battlemage\ff-probes\ff-receipts.jsonl`, addressed by `probe` +`cell`.
Rules: `docs/FACTORY-FRONTIER-CARDS.md` § *Campaign rules earned the hard way*.

---

## Corrected, refuted, or relocated

| # | Original claim | Status | Corrected claim / bound | Decisive receipt | Rule |
|---|---|---|---|---|---|
| 1 | `-ub 1024` causes a **~4× decode regression** at `-np 2` | **REFUTED** | Decode-neutral (**+0.4%**, inside drift). Prefill −1.3% @512, **+5.8% @2048, +12.3% @8192**. `-ub 1024` is **promoted**. | `UB-AB-WARM` | R1 R3 R6 R8 |
| 2 | FF6c: the `-ub` optimum moves with placement (crossover surface) | **SUPPORTED** | Reproduced server-side: the gain is prompt-length dependent and **negative at 512**. | `UB-AB-WARM` | R3 |
| 3 | Dual-split buys **+27–42% prefill**, costs **−5% decode** | **LOCATED, and it is model-specific** | *MoE surface:* sign change between **512 and 1024**; −1.8% @512, +37.9% @1024, ~+45% @1536–2048, **+72.2% @8192**; decode cost **~5–6%** (not a precise value). | `B3-TOPOLOGY-CROSSOVER` | R3 R4 R6 R7 R8 |
| 4 | Co-residency taxes **Flash** −42% (5.04 → 2.93) | **REFUTED + REASSIGNED** | Flash pays **−2.3% to −3.9%**, inside its own drift floor — *unresolvable from zero, but −42% excluded by >3× that floor*. The cost lands on the **incumbent**: **−15% to −28%** while sharing, **fully recovered** after. | `B4-FLASH-CORESIDENCY` | R1 R9 |
| 5 | Dense seats run **~6× slower per seat** than the MoE | **CORRECTED** | **4.5–5.0×** (4.51× / 4.65× dual, 4.76× / 5.03× single). The dense side reproduced exactly (23.52/23.62 vs 23.54); the **MoE side was a llama-bench number**. | `B5-DENSE-VS-MOE` | R8 |
| 6 | **121.6** tok/s "full-B70 dual-split" | **MISLABELED** | Its receipt reads `tensor_split "1.00"` — a **single-card llama-bench `tg128`** run. Server, solo: **116.35 single / 109.97 dual**. | `W-A-SOLO` | R8 (A4) |
| 7 | **Co-residency poisons the incumbent** (ADR-0041's trigger) | **TRIGGER SUPERSEDED** by `docs/adr#0043` | The loss is real; the trigger is **idle time**. >~60 s idle → a stable ~3.9× degraded state. Co-residency is **neither necessary nor sufficient** — running any experiment merely leaves the incumbent idle. | `W-B2-DELAYED-ONSET`, `W-B3-IDLE-LADDER` | R1 R6 |
| 8 | Sustained-rate decay that recovers after idle | **RETRACTED, then PARTLY VINDICATED** | Real, but only in the **post-idle** state. A *fresh* server holds 102–107 flat over 40 requests — which is why it looked refuted. Two machine states, not one contradiction. | `W-B3-IDLE-LADDER` | R1 R6 |
| 9 | Door overhead is large and non-constant (~91% of a call) | **RESOLVED — non-issue** | Door overhead is a **flat 175–264 ms independent of size** (35.6% of a 32-token call, 11.8% of a 512-token one). The apparent 91% was the rung's own first-request stall, invisible without a server-side join. | `C2-DOOR-ATTRIBUTION` | R3 R8 |
| 10 | `GGML_VK_VISIBLE_DEVICES=1,2` is **load-bearing** — never remove it | **REFUTED** (`docs/adr#0042`) | Removed, not corrected. Enumeration is nondeterministic per process launch; select by device **TYPE**. No index scheme is safe, `-dev`/`--device` included. | `FF-CENSUS` + `-lv 5` load report | R8 |
| 11 | LZ1-A's "favourable anomaly": 12.4–14.9 co-resident vs 10.6 solo | **EXPLAINED AWAY** | There was **no solo LZ1-A comparator** — 10.6 came from a different rig. Solo reads **14.40 / 14.71**, at or above every co-resident figure. Nothing to explain. | `W-A-SOLO` | R8 |
| 12 | The `-ub 1024` promotion is confirmed by the running server | **PROVENANCE-LIMITED** | `config_assertion: behavioral` — inferred from a rate separation (0.15% vs 12.5%), **not** read from a launch parameter. llama-server prints no `n_ubatch` at default verbosity. Strong corroboration; not observation. | `UB-AB-PROVENANCE` | R2 |

## Established here, not previously known

| # | Claim | Bound | Receipt |
|---|---|---|---|
| 13 | The rung **goes cold when idle** | 0/30/60 s idle: 106.5 flat. **120 s: 39.5** (replicated 39.71/39.54). Then a stable ~27.5 plateau. A **1-token ping every 20 s holds 104.83**. ⚠ Prevents the transition; does **not** reverse it. | `W-B3-IDLE-LADDER` |
| 14 | **Two** distinct post-idle costs | A **~11.5 s first-request stall**, size-independent and **often invisible to `print_timing`** (server reported `prompt_ms 47.1` while `launch_slot_`→`release` spanned 11.54 s); and the decode collapse. | `C2-DOOR-ATTRIBUTION`, `ARC-KEEPALIVE` |
| 15 | Contention is **local to the shared card and cheap** | A resident-but-idle neighbour costs the incumbent **nothing**; an actively inferring one on the same B70 costs **8%**, only while it infers; CPU-only and iGPU co-tenants cost nothing. | `W-B-POISON` |
| 16 | **Dense models are topology-insensitive on decode; the MoE is not** | Dense dual→single: **+0.4% / −2.2%**. MoE: **+5.9%**. So the dual-split decode cost is an **MoE property**. | `B5-DENSE-VS-MOE` |
| 17 | The prefill crossover is **model-specific** | MoE: between 512 and 1024. Dense Qwen2.5-32B: dual already wins at 512 (**+11.6%**), so its crossover is *below* the sampled range. | `B5-DENSE-VS-MOE` |
| 18 | The solo noise floor is **venue-dependent** | 0.01% decode spread on dual-split B70; **10.5%** decode and **50.9%** prefill on experts→CPU. A ±10% effect is unmeasurable on host-memory venues at 2 reps. | `W-A-SOLO` |
| 19 | **First eval costs ~12×, per batch shape** | Flash `prefill@512`: 4.80 tok/s cold (105.8 s) vs 57.28 warm — *after* the 22-token shape had already warmed the process. | `W-A-SOLO` |

---

## Open — recorded, not resolved

| # | Observation | Bound | Receipt |
|---|---|---|---|
| 20 | **INC-2026-08-30-A** — a restart-surviving ~61% state | Appeared ~00:28 on 2026-08-30, stable across `n_predict` 8→400 and spaced checks, **survived a restart** (unlike ADR-0043's idle-cold state), cleared on its own by ~01:05. Placement, thermal, spill, co-tenancy and generation length all excluded by direct measurement. **Unattributed.** | `ARC-KEEPALIVE` deep probe, `FF-RATECHECK` |
| 21 | The keep-alive's *"holds the rate"* claim | Validated over **~6 minutes**. An epoch ran ~35 min with pings and degraded anyway. Narrowed to the tested horizon; not evidence the keep-alive failed. | ADR-0043 |
| 22 | The **106.00 baseline** is a reference, not capacity | **Four** stable levels in one night (~106, ~97–99, ~65, ~27.5), with transitions in **both** directions and no intervention. Resolved into `docs/adr#0044`: health = **baseline epoch + observed rate + acceptance envelope**; the baseline is preserved, never silently redefined. | `FF-RATECHECK`, `docs/adr#0044` |

⚠ **R3 caught its own author on #20.** Diagnosing it, a single-arm revert of `-ub 1024` read
65 → 97 and nearly became "my promotion caused a 33% regression". The interleaved re-test found both
configs at ~97–98: a **state change read as a config effect** — the identical error the original B1
claim made, on the same flag, one day after the rule against it was written.

---

## There is no machine-level topology law

⚠ **Do not describe this box as having "a dual-split decode tax" or "the prefill crossover".**
Neither exists as a property of the machine. B3 established the surface for **one MoE**; B5 showed
dense models occupy a **materially different** one — insensitive on decode, and with the prefill
crossover in a different place.

The correct object is a **model × topology × workload surface**. Any claim of the form "dual-split
costs X" or "the crossover is at N tokens" is incomplete unless it names the model and the prompt
length. This is a more useful architectural result than the original headline it replaced, and it
is the form future placement policy should take.

---

## Non-identifiable from historical evidence — bounded, not repaired

Epoch **E2** (2026-08-28 08:32 → 2026-08-29 04:40) holds the entire LZ campaign window and all
pre-fix FF work — **288 of 317 receipts** — and is anchored **single-card**. Those receipts carry
`PLACEMENT_CONTEXT_UNKNOWN` / `PLACEMENT_CONTEXT_INVALID` and `INCUMBENT_HEALTH_UNKNOWN`.

**The policy is to bound, not to repair.**

1. **The labels stay.** They are the finding, not a defect to be cleaned up.
2. **Derive only conclusions that hold across plausible E2 states** — single- or dual-card,
   incumbent healthy or cold. A conclusion robust to all of them is usable as-is.
3. **A conclusion that flips depending on the state is `NON-IDENTIFIABLE FROM HISTORICAL
   EVIDENCE`.** It is marked so and left there. It is *not* an open measurement request.
4. ⚠ **A modern re-measurement is not a historical correction.** Re-running a cell today produces a
   fact about today's machine. It may supersede the old claim going forward, but it does **not**
   tell us what the August 28 machine did, and labelling it as though it did would manufacture
   provenance. Where both exist, they are two rows, not one corrected row.

Reconstruction tooling: `campaign/ff-probes/ff_provenance.py` (byte-stable, `--check` exits 1 on
drift). Its output is a **classification**, never a substitute measurement.

---

## Where the dominant uncertainty now sits

Not in the numerator. The five suspect measurements are closed and the instrument problems that
produced them are now gated (R8).

**It sits in FF1** — the work-slice harness, still unbuilt. Every work-per-machine-hour claim
divides by it, so orientation tax, continuity dividend, and prefill amortization all inherit an
uncertain denominator. With numerator quality substantially improved by this campaign, **FF1 is now
the dominant epistemic weakness in every higher-level efficiency claim**, and it is the priority.
