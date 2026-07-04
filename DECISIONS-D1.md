# DECISIONS-D1.md — Derek's answers to the Stream D1 economics questions

Answers to `QUESTIONS-D1.md` (items 1, 2, 3, 5 of the ECONOMICS-ARCHITECTURE-REVIEW). These
ratify the D1 builder's recommendations; the reasoning is captured per item. Consumed by D2.

**Author:** derek (recommendations by Claude, endorsed 2026-07-04)
**Governing principle across all four:** do not author what you cannot yet derive — the same
Clause-1/ADR-0002 spine. Where an answer needs a new fact, it is added as an *authored* input, not
an assumption.

---

## Item 1 — Attention as a third economy → **DEFERRED to item 5 (out of scope for now)**

Attention is not modeled as a distinct economic dimension yet. The decision table already returns
`undetermined` for attended sessions lacking an `operating_mode` signal — that is honest and
reportable, not a gap. Adding an `attention_minimized` objective before attendance can be *detected*
would model a dimension we can't observe.

**Action for D2:** once item 5's `operating_mode` lands, add rule **R5: `operating_mode == "attended"`
→ `attention_minimized`** to `project_economy.py`'s decision table. No contract change required.

## Item 2 — Mixed-economy credit → **ATTRIBUTE TO NEITHER; tag `economy: mixed`**

A multi-leg dispatch is not a first-class concept. `economy_influence` is per-decision,
per-candidate. Splitting or duplicating knowledge credit across legs now would be authored
guesswork (Clause-1 violation). A finding that genuinely required both a metered and a sunk leg is
tagged `economy: mixed` and knowledge accounting for it defers until a credit model exists.

**Action for D2:** define a `dispatch.legs.completed` event type before implementing multi-leg
credit attribution. Until then, no mixed-leg accounting.

## Item 3 — Leased resource default → **`undetermined` + add authored `lease_model`**

Do not assume what `ownership: leased` means economically — derive it from an operator-stated fact.
Add an optional authored candidate-pool field **`lease_model: "fixed" | "per_call"`**. Derivation:
- `lease_model == "fixed"` → `knowledge_per_hour` (the billing period is sunk).
- `lease_model == "per_call"` → `cost_per_outcome` (each dispatch accumulates cost).
- absent → keep returning `undetermined`, naming the missing input in the reason (current behavior).

**Action for D2:** add `lease_model` as a one-rule extension to the decision table in
`project_economy.py`. No schema migration (optional pool field, like `ownership`).

## Item 5 — Attendance threshold → **IDLE-TIMEOUT WITH EXPLICIT OVERRIDE**

`operating_mode` flips to `unattended` after an idle timeout, but Derek can reset the clock with an
explicit "I'm here" heartbeat event. Missed-idle (wasting real wind-tunnel time) is the costlier
failure than a brief false-idle, so the design biases toward detecting unattended — but the timeout
is generous enough to absorb short absences.

**Authored value (Clause-2, a statement of will — tunable without a code change):**
`ATTENDED_IDLE_TIMEOUT_S = 900` (15 minutes). Rationale: 5 min is too twitchy for normal
step-aways; 15 min catches genuine idle while an explicit heartbeat covers active-but-quiet work.
Implement as a **named traced constant** (D18) with this rationale comment, sourced from an authored
object so the value can move without a redeploy.

**Action for D2:** implement `operating_mode` detection (idle-timeout + heartbeat-override) and wire
rule R5 from item 1.

---

## Summary of what D2 unblocks

| Deferred concern | Now decided |
|---|---|
| `operating_mode` / attendance | idle-timeout(900s) + heartbeat override |
| Attention objective | R5 added once operating_mode exists |
| Multi-leg credit | deferred to a `dispatch.legs.completed` event |
| Leased economics | authored `lease_model` fact drives it |
