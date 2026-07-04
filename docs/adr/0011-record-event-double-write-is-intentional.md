# ADR-0011 — The record_event double-write is intentional

**Status:** Accepted (2026-07-04) — ratified by Derek after the CQRS fan-out review.
**Context sources:** docs/CQRS-ES-STANDARDIZATION.md, ADR-0005, ADR-0010,
`hearth/toolsurface/knowledge.py::record_event`, `hearth/kernel/gateway.py::make_wrapper`.

## Context

One `record_event` MCP call produces two ledger entries: the domain event it appends to
`runs/<run-id>/events.jsonl`, and the `hearth-event.v1` the gateway wrapper automatically
appends to the kernel ledger about the call itself. The CQRS review flagged this as a
candidate defect ("the same fact enters two stores"), and considered special-casing the
gateway wrapper to suppress its entry for this one tool.

All three reviewers converged on the same lean: the two entries answer different
questions — the kernel event says *"a record_event call happened, from this caller, at
this cost"* (audit); the domain event says *"this business fact is now part of the
domain history"* (state). Suppressing the kernel entry would carve the first exception
into the "every MCP call is ledgered" guarantee that the kernel ledger exists to provide
(ADR-0005), and touching the universal wrapper is the highest-blast-radius change in the
kernel for near-zero payoff.

## Decision

**Keep the double-write. It is two facts, not one fact twice.**

- The gateway wrapper ledgers `record_event` calls exactly like every other tool call —
  no special-casing, ever, for any tool.
- Consumers must not join the two streams as if they were duplicates: capacity/economics
  projections read the kernel ledger; domain projections read the workflow ledger
  (per ADR-0010). The `ledger_adapter` is the only sanctioned bridge.
- If a future consumer genuinely needs to correlate the two entries for one call, the
  correlation key is the kernel event's `args_digest` against the domain event content —
  an explicit join, not an assumed identity.

## Consequences

- "Every crossing lands on the ledger" stays a structural invariant with zero exceptions,
  which keeps the kernel ledger a complete dataset for the learning loop.
- Anyone reading raw ledgers side by side will see the apparent duplication; this ADR is
  the documentation that it is by design (a doc-comment on `record_event` points here).
- The rejected alternative (wrapper special-casing) is recorded so it is not re-proposed:
  it trades a universal guarantee for cosmetic dedup.
