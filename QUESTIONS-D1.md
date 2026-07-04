# QUESTIONS-D1.md — Stream D1 Review Questions for Derek

These are the four unresolved questions from the
[ECONOMICS-ARCHITECTURE-REVIEW.html](ECONOMICS-ARCHITECTURE-REVIEW.html) Unresolved Questions
section (items 1, 2, 3, 5 by position) that stream D1 hands back rather than answers.

Items 4 and 6 are out of scope for this stream.

---

## Item 1 — Attention as a third economy

**The question:**
Is Derek's own wait-time meant to be modeled explicitly as a distinct economic dimension, or is it
deliberately out of scope for the current economics layer?

**Option space:**

| Option | What it means | Tradeoff |
|--------|---------------|----------|
| Out of scope (forever) | Dispatcher ignores attention; it is a UX concern, not an economic one. | Simplest; misses the case where a fast interactive dispatch is worth more than the most knowledge-efficient one. |
| Out of scope for now | Attention deferred until `operating_mode` (attended/unattended) is implemented. | Consistent with the review's own Δ1′ note: operating_mode arrives "with the answer to item 5." Low risk. |
| Explicit third objective | Add `"attention_minimized"` (minimize time-to-result for attended sessions) as a third optimization objective alongside `cost_per_outcome` and `knowledge_per_hour`. | Richer model; requires detection of operating_mode first (item 5) and a definition of "attention cost." |

**My recommendation:**
Out of scope for now, second option. The decision table already returns `"undetermined"` for
attended sessions without an `operating_mode` signal, which is honest and reportable. Once item 5
is resolved and `operating_mode` is authored, a fourth rule (R5: operating_mode == "attended" →
`attention_minimized`) can be added to the table in `project_economy.py` without touching the
contract.

**Stub status:**
`operating_mode` / attendance detection is **entirely absent by design** in this implementation —
no stub, no placeholder field. It arrives with the answer to item 5.

---

## Item 2 — Credit splitting for mixed-economy dispatches

**The question:**
When a finding requires both a metered and a sunk leg (e.g. planner ensemble comparisons where one
leg runs local and another runs cloud), how is knowledge-per-hour credited — split, duplicated, or
attributed to neither leg individually?

**Option space:**

| Option | What it means | Tradeoff |
|--------|---------------|----------|
| Attribute to the leg that produced the finding | Credit whichever leg's output became the finding's evidence. | Practical; breaks down when both legs are necessary (neither alone produces the finding). |
| Split proportionally | Divide the knowledge credit by the number of legs. | Mechanically simple; the proportions are arbitrary without a cost model. |
| Attribute to neither, record a mixed-leg finding | A finding with two economic legs is tagged `economy: mixed` and knowledge accounting defers until a credit model exists. | Conservative; avoids false precision. Compatible with today's `economy_influence` per-dispatch design since a multi-leg dispatch is not yet a first-class concept. |
| Duplicate (credit each leg independently) | Each leg earns the full knowledge credit independently. | Over-counts; violates the "no organizational truth may be authored if it can be derived" law if the duplication creates a spurious belief change. |

**My recommendation:**
"Attribute to neither, record a mixed-leg finding" until a multi-leg dispatch concept exists.
Today's `economy_influence` block is attached to a single scheduler decision, which maps to a
single candidate. Mixed-economy findings require a new event type (e.g.
`dispatch.legs.completed`) before credit attribution is defined. Anything earlier would be
hand-authored guesswork.

**Stub status:** No mixed-leg support in D1. The `economy_influence` block is per-dispatch,
per-candidate. Multi-leg credit is entirely deferred.

---

## Item 3 — Leased resources default economic objective

**The question:**
Farm VMs and the cc-farmer tunnel are neither owned nor pay-per-call. What does
`resource.ownership: leased` default to economically, and should that be observed rather than
assumed?

**Option space:**

| Option | What it means | Tradeoff |
|--------|---------------|----------|
| Default to `cost_per_outcome` | Treat leased like metered: someone is paying per unit time regardless. | Conservative; consistent with "if in doubt, watch the budget." Over-cautious for fixed-fee leases. |
| Default to `knowledge_per_hour` | Treat leased like owned: the lease rate is sunk for the billing period. | Appropriate for fixed-term leases; wrong for metered leases (e.g. spot instances billed per second). |
| `undetermined` until observed | Do not assume; require an explicit authored `lease_model: fixed | per_call` alongside `ownership: leased`. | Clause 1–compliant: derives from an authored fact rather than assuming. Requires a second authored field. |
| Observe from signals | Infer from a live price/rate signal whether the lease is accumulating cost at this moment. | Most accurate; requires live signals infrastructure (out of scope for D1). |

**My recommendation:**
`undetermined` with a clear reason (current behavior), plus a new optional authored fact
`lease_model: "fixed" | "per_call"` on the candidate dict. When `lease_model` is present, the
derivation maps `fixed → knowledge_per_hour` (billing period is sunk) and
`per_call → cost_per_outcome` (each dispatch accumulates cost). This is a minimal Clause-2-
compliant extension: the operator states a fact about the world (how the lease is billed), and the
derivation follows. Adding `lease_model` to the decision table in `project_economy.py` is a
one-rule extension with no schema migration on the contract (it is an optional candidate-pool
field like `ownership`).

**Stub status:** D1 returns `"undetermined"` for `ownership=leased` and names the input in the
reason string. The `lease_model` extension described above is deferred pending this decision.

---

## Item 5 — Attendance threshold

**The question:**
Who/what authors "unattended" — idle timeout, explicit session-end event, or both? Wrong either
way: false-idle burns electricity, missed-idle wastes real wind-tunnel time.

**Option space:**

| Option | What it means | Tradeoff |
|--------|---------------|----------|
| Idle timeout only | Mark `operating_mode: unattended` after N minutes of no operator activity. | Easy to implement; false positives (Derek steps away briefly); requires a tunable timeout. |
| Explicit session-end event only | `operating_mode` flips to `unattended` only when Derek sends an explicit event (e.g. via CLI or UI gesture). | Zero false positives; requires Derek to remember to signal. Missed-idle is the dominant failure mode. |
| Idle timeout with explicit override | Default to idle-timeout; Derek can override with an explicit "I'm here" signal that resets the clock. | Best of both; more complex; still requires a tunable timeout. |
| Both, with conservative merge | `unattended = (idle_timeout AND no_explicit_session_active)`. | Conservative; less false-idle risk. |
| Out of scope pending item 1 | Defer until item 1 (attention economy) is decided, because the economic consequence of a wrong `operating_mode` depends on how attention is modeled. | No wrong threshold now means no wrong behavior now. |

**My recommendation:**
Idle-timeout with explicit override, third option. The timeout should be a **named traced
constant** (e.g. `ATTENDED_IDLE_TIMEOUT_S = 300`) with a rationale comment, per D18. Derek can
keep a session alive with an explicit heartbeat event. The threshold value itself is a
**DECISION-NEEDED** (not a stop condition), so it should be authored as a Clause-2 object (a
statement of will, with reason and audit trail) rather than a hard-coded constant.

**Stub status:** `operating_mode` / attendance detection is **entirely absent** in D1 by design.
It arrives with the answer to this item.

---

## Summary of stubs in this implementation

| Concern | Status |
|---------|--------|
| `operating_mode` / attended-vs-unattended | Not present; deferred to item 5 answer |
| Multi-leg dispatch credit attribution | Not present; deferred to item 2 answer |
| `lease_model` for leased resources | Not present; deferred to item 3 answer |
| `battery_percent` live feed wiring | Plumbing only — `signals` object accepted, never populated by a real feed; wiring is out of scope for D1 |
| Economy affecting candidate ranking | Out of scope by design; economy explains, never re-ranks |
