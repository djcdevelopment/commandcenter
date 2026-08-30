# FF1 audit — what does "occupied machine-hour" actually measure?

**The question, deliberately before any building.** The suspect-measurement campaign found that
the expensive errors were not imprecision — they were *quantity confusion*: a llama-bench number
quoted against serving numbers (B5), a tax attributed to the wrong dependent variable (B4), a
"dual-split" figure that was single-card (W-A). So the first FF1 question is not *how precise is
the denominator* but **is it measuring the thing everyone believes it measures.**

Status: **audit only.** Nothing here is a measurement or a proposal to build. Findings are
numbered F1–F6.

---

## F1. The denominator has never been recorded — not imprecisely, at all

Across **405 FF receipts**, the count of rows carrying each specified axis:

| field | rows |
|---|---|
| `occupancy` | **0** |
| `b70_0_s`, `b70_1_s` | **0** |
| `cpu_core_s` | **0** |
| `wall_s` | **0** |
| `render_lane_blocked_s` | **0** |
| `commit_peak_gb`, `ddr5_peak_gb` | **0** |

So *"completed R&D work per occupied machine-hour"* — the campaign's declared unit — has **no
denominator in the data**. This is not a precision problem to improve; the quantity is unmeasured.

⚠ The cards are already honest about this: tier results are scored on *"throughput/memory proxies
and labelled as such"*. That labelling is currently load-bearing and must not be dropped.

## F2. There are two specifications, and neither is implemented

| source | axes |
|---|---|
| cards §0.4 | `b70_0_s, b70_1_s, cpu_core_s, ddr5_peak_gb, commit_peak_gb, render_lane_blocked_s, wall_s` (**7**) |
| the phase plan's ledger extension | adds `igpu_s, npu_s, standby_gb, working_set_gb, interactive_latency_ms` (**12**) |

Same field name, two definitions, in two documents that are both current. This is the shape
ADR-0033 already documented for a different noun — *three tools answered "what is a run?" three
different ways, and the disagreement was load-bearing.* **Fixing the instrument before fixing the
definition would just build one of the two by accident.**

## F3. The GPU-seconds axes have **no instrument on this box**

`b70_0_s` / `b70_1_s` mean "seconds this work occupied card N". Three sources could supply that,
and none can:

| source | what it gives | why it cannot give per-card seconds *for this work* |
|---|---|---|
| **Per-process GPU counters** | nothing usable | `hearth/media/occupancy.py`'s own docstring: llama-server runs under an S4U scheduled task and **its per-process counters read 0 even while it is working** |
| **Adapter-level GPU Engine counters** | utilisation per adapter | attributes to the **adapter**, not the consumer — in any co-resident window it sums *all* tenants |
| **`/slots`** (`probe_omen_arc_slots`) | slot busy state for one server | one server that chooses to expose it; a slot count, not card-seconds, and blind to every other tenant |

⚠ **The campaign's whole premise is co-residency**, which is exactly the regime where adapter-level
attribution is wrong. A denominator built from it would charge one experiment for another's work —
and B4 has just shown how expensive it is to attribute a cost to the wrong party.

## F4. The module that looks like the implementation measures a different dimension

`hearth/media/occupancy.py` is **media-engine** occupancy — QSV decode/encode, what a *render lane*
contends for. Its docstring says so explicitly and separates the two dimensions **on purpose**:
compute occupancy is probed via `/slots`, "NOT via performance counters".

So the file named `occupancy.py` does not implement the compute axes of the occupancy vector, and
was never meant to. Only `render_lane_blocked_s` has anything behind it.

## F5. Fail-closed is correct for a guard and **wrong for a meter**

`occupancy.py` reports `busy=True, known=False` on every failure path, so a lane whose state cannot
be established is withheld rather than scheduled onto. That is right — for a **scheduler guard**.

As a **meter** it inverts: an unreadable counter would contribute *maximum* occupancy, inflating
the denominator and silently understating work-per-hour. A guard should assume the worst; a meter
must record "unknown" and refuse to average. **The same code cannot serve both roles**, and reusing
it for FF1 would import a deliberate safety bias as measurement error.

⚠ Compounding this: `render_lane_blocked_s` keys on **LUIDs from `lanes.json`**, calibrated
2026-08-25 against a box that booted 2026-08-28. LUIDs reallocate per boot (finding A13), so the
one axis with an implementation is currently keyed on stale identifiers — which is also why the
render-lane spill guard has been inert since that reboot.

## F6. What currently depends on the denominator

| claim | dependence |
|---|---|
| FF1–FF5 (orientation tax, continuity dividend, prefill amortization, workflow bake-off) | **total** — each is a ratio over it |
| FF5's kill/promote gate | quantitative: *"wins on scored work per occupied machine-hour by more than FF1's noise floor"* |
| FF10's Pareto set | computed **over the vector**, so it inherits every axis' defects |
| The campaign's stated unit | definitional |
| B1–B5, W-A, W-B, C2 | **none** — all are throughput/memory measurements and stand on their own |

The corrected campaign results do **not** depend on this. The higher-order framing does, entirely.

---

## ⛔ IMPLEMENTATION IS BLOCKED, pending one semantic decision

**Do not build an occupancy meter yet.** The audit's finding is not that `b70_*_s` is unimplemented
or imprecise — it is that **the concept currently has no observable corresponding to its semantics
under co-residency**. Adapter time would reproduce B4 *structurally*, by charging one tenant for
another's consumption; `occupancy.py` is deliberately measuring something else. Choosing wall-clock
occupancy would therefore be a **specification decision**, not an instrumentation task, and it
should be made as one.

> **The blocking question: what is `work per machine-hour` intended to PRICE?**
>
> 1. **elapsed possession** — the resource was held, whatever was done with it;
> 2. **attributable resource consumption** — this work's share of the hardware;
> 3. **scheduling opportunity cost** — what else could not run because this ran;
> 4. **none of the above as a scalar** — the metric is **renamed or split**, because one number
>    cannot honestly represent all three.
>
> These are different economic quantities. They rank configurations differently, and (2) has no
> observable on this box. **Choose the quantity first; only then choose an observable.**

⚠ **(4) is a real answer, not a way of avoiding the question**, and it may be the honest one. The
campaign has already shown this box refusing to collapse into single numbers twice: there is **no
machine-level topology law** (B3/B5 — it is a model × topology × workload surface), and **a rate is
not a scalar** (ADR-0044 — health is epoch + rate + envelope). A denominator that must simultaneously
price possession, consumption and opportunity cost is the same shape of demand. If the answer is (4),
the deliverable is a **named split with a stated relationship between the parts**, not a compromise
average — and "work per machine-hour" stops being a single reportable figure.

⚠ Building before that decision risks a beautifully measured denominator for the wrong economic
quantity — an error no amount of precision would reveal, and exactly the class R8 was written to
catch on the numerator side.

## The three candidates, as economic quantities

Three candidates. They are **not interchangeable**, they rank configurations differently, and one
of them cannot be built on this hardware.

**(1) Wall-clock occupancy** — elapsed seconds during which the work held a resource.
*Measurable today, trivially, from timestamps already in every receipt.* Ignores intensity: a job
idling on a card counts the same as one saturating it.

**(2) Exclusive-resource seconds** — this work's *share* of a card. What `b70_*_s` implies.
⚠ **Not measurable on this box** (F3), and the honest options are to obtain per-process attribution
(unavailable under S4U) or to schedule exclusively — which would change the lab into the benchmark
rig §0.4 explicitly says it is not.

**(3) Opportunity-cost occupancy** — what else could not run because this ran.
Arguably what "occupied machine-hour" *means* for a lab optimising finished work, and it is
compatible with co-residency rather than defeated by it. ⚠ But it is a **scheduling** quantity, not
a counter reading: it needs a model of what would otherwise have been admitted, which does not
exist yet.

**The audit's recommendation is to choose between (1) and (3) and to state the choice as a
definition before any instrument is built.** (2) is the one the vector's axes currently imply and
the one this machine cannot supply — so the specification, not the instrument, is what is out of
contact with the hardware.

⚠ Per R1, this audit identifies a definitional gap; it does **not** establish that (1) or (3) is
the right unit. That is a judgement about what the lab is for, and it is Derek's.

### What not to do

- **Do not instrument `b70_*_s` from adapter-level counters.** It would produce a plausible number
  that charges co-tenants to each other — F3, and precisely the B4 error class.
- **Do not reuse `occupancy.py` as the meter.** It measures a different dimension (F4) and carries
  a deliberate fail-closed bias (F5).
- **Do not build to either specification until they are reconciled** (F2).
- **Do not drop the "throughput/memory proxy" labelling** on existing tier results until a
  denominator exists (F1).
