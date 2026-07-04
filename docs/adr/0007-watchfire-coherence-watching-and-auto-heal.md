# ADR-0007 — The guard dog watches coherence, and auto-heals the obvious

**Status:** Accepted (2026-07-04) — Slice 0 built + live; wired into `mechnet_watchdog`'s patrol.
**Context sources:** SESSION-RETRO-2026-07-04.md (addendum 3), WATCHFIRE-FLARE-DESIGN-2026-07-04.html,
the fan-out incident (`e287059`), `hearth/health/gaps.py`, `hearth/toolsurface/{patrol,remediate}.py`,
`fleet/mechnet_watchdog.py`.

## Context

Every false signal in the 2026-07-04 fleet session was the same shape — a **gap between two sources
that should have agreed, which no single view reconciled**:

- the operator board read *in-flight* while the conductor log read *errored (isolated)* (a fan-out crash);
- the assay graded a run *B/70, 162/162 pass* while its deliverable was an empty `QUESTION.md`;
- a builder *ran* while its `~/commandcenter-src` reference checkout was *stale* (missing `hearth/`);
- the occupancy probe read *busy* while the run behind it had already *crashed*.

The existing self-healing watchdog (`mechnet_watchdog`, Banked Fire P4) only watched **liveness** — is a
declared service reachable, and if not, revive it. But nothing this session was *down*; every node was
"up." The failures were **coherence** failures, and they were caught by hand — grepping the conductor
log, reading a result stub — ~50 minutes after they began. Derek caught the first not from any dashboard
but by **hearing the machine's fans stay quiet** while the board claimed work: mechanical truth read
against a logical claim.

## Decision

**The guard dog watches coherence, not just liveness — and auto-heals the obvious, reversible gaps while
flagging the ambiguous ones.**

- **Sense (rules-first, no NPU yet).** `hearth/health/gaps.py` holds pure coherence checks ("spells")
  over run records — `phantom_in_flight`, `crashed_isolated`, `stale_checkout`, `false_success`. The
  `patrol` HEARTH tool gathers records over SSH and casts them; it is a **pure observer** and never
  mutates. The NPU-hosted learned classifier is a deferred upgrade, earned once these rules have produced
  labeled reps.
- **Heal (research-lab policy, Derek 2026-07-04).** `remediate` **auto-heals only the obvious + reversible**
  gaps (v0: `phantom_in_flight` → write an abandoned stub, releasing phantom occupancy) and leaves
  everything ambiguous (`false_success`, `stale_checkout`) **flag-only**. Act, then post-mortem; every heal
  is self-documenting (`_healed_by`/`_healed_at`) and reversible (delete the stub), and every `remediate`
  call is ledgered. **A heal must resolve a gap, never relabel it** (a heal-stub must not re-flag as a
  fresh crash).
- **Patrol.** `mechnet_watchdog` runs the coherence sweep after its liveness heal on every 15-min tick.
  Liveness stays the health gate (the exit code); the coherence sweep is additive and best-effort — its
  failure never fails the patrol.

## Consequences

- The watchdog gains a second, deeper sense without a new daemon — it rides the P4 process already trusted
  to self-heal. `patrol`/`remediate`/`watchfire` extend it; they do not replace it.
- **The auto-heal boundary is the load-bearing guardrail.** It is enforced only by `AUTO_HEAL_KINDS` +
  the flag-only default — a miscast healing spell on a false positive is worse than a missed flag, so the
  set stays deliberately small and grows only on evidence. Ambiguous gaps are eyes handed to a reasoner,
  never auto-fixed (same discipline as ADR-0001: a gate, not an oracle).
- Reversibility + the ledger make the research-lab posture safe: acting on the obvious is cheap because
  every action is undoable and audited.
- The observer/actor split (`patrol` never mutates; `remediate` is the only mutator) keeps the sense
  safe to call anywhere and the healing explicit.
- Deferred: the NPU learned classifier (Slice 1), an on-demand deep mode (`/armdebug` / "Flare"), the
  physical-vs-claim spell ("the fans, digitized" — AM4 GPU-util vs a running claim), and gap
  acknowledgement so already-resolved historical gaps stop re-flagging.
