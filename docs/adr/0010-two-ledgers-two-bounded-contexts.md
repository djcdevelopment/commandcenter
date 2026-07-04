# ADR-0010 — Two ledgers, two bounded contexts: one door is not one store

**Status:** Accepted (2026-07-04) — ratified by Derek after the CQRS fan-out review.
**Context sources:** docs/CQRS-ES-STANDARDIZATION.md, ADR-0005, `hearth/kernel/ledger.py`,
`tools/workflow/append_event.py` + `ontology.py`, SESSION-RETRO-2026-07-04.md (addendum 5).

## Context

HEARTH has two event systems. The kernel ledger (`hearth/var/ledger/events.ndjson`,
`hearth-event.v1`) is written automatically by the gateway wrapper for every MCP call:
caller identity, tool, sha256 digests of args/result, a 400-char preview, ok/error,
duration, cost. The workflow ledger (`runs/<run-id>/events.jsonl`) carries typed domain
events with full payloads (`work.accepted`, `candidate.produced`, `promotion.*`, …).

The 2026-07-04 CQRS review — three independent reviewers (ES-purist, pragmatic-migration,
query-side) — unanimously rejected the purist instinct to unify them into one event store.
They answer different questions: the kernel ledger is an **audit/telemetry fact stream**
("a call happened, by whom, at what cost"); the workflow ledger is the **domain event
store** ("a business fact happened, with full payload, replayable"). Stuffing full
payloads into the audit stream would drag PII and megabyte results into telemetry;
making the audit stream replayable is solving replay at the wrong layer.

ADR-0005's "one boundary, one ledger" language invited the conflation: it argues for one
*door* (every crossing goes through HEARTH and gets captured), not one *storage format*.

## Decision

**The two ledgers are two bounded contexts and stay separate.**

- The kernel ledger is authoritative for *what happened* (audit truth). It stays
  digest-only, append-only, non-replayable-by-design.
- The workflow ledger is authoritative for *domain state*. It is the stream that must
  satisfy event-sourcing replay, and the target for stream-integrity hardening
  (stream_seq, idempotent append — CQRS plan steps 7–8).
- `hearth/projection/ledger_adapter.py` is the anti-corruption translator between the
  contexts; it must be directional and idempotent, never a second birthplace for domain
  facts.
- ADR-0005 is clarified, not amended: "one boundary" = one MCP door. This ADR records
  that one door ≠ one store.

## Consequences

- Standardization work targets each ledger by its own contract: replay guarantees go to
  the workflow ledger; audit completeness and cost/capacity analytics go to the kernel
  ledger. Neither inherits the other's requirements.
- No future "unify the ledgers" proposal should be entertained without superseding this
  ADR — the review found the separation is the design, not an accident of history.
- Derived stores (`knowledge/*.json`) may consume both streams, but each document's
  provenance (`corpus_digest`) names which corpus produced it.
