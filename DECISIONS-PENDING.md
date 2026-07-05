# Decisions pending — Derek's desk

One register for open decisions accumulated across retros, ADRs, and review docs.
Appended by `/retro` (Phase 2e); check off with a link to where it was decided.

- [x] 2026-07-04 — Ratify "two ledgers = two bounded contexts; one door ≠ one store"
      as an ADR-0005 amendment or new ADR (source: [docs/CQRS-ES-STANDARDIZATION.md](docs/CQRS-ES-STANDARDIZATION.md))
      — DONE 2026-07-04 ("make it so"): [ADR-0010](docs/adr/0010-two-ledgers-two-bounded-contexts.md)
- [x] 2026-07-04 — `record_event` double-write: keep + document (reviewers' lean) vs
      special-case the gateway wrapper (source: [docs/CQRS-ES-STANDARDIZATION.md](docs/CQRS-ES-STANDARDIZATION.md))
      — DONE 2026-07-04 ("make it so"): keep + document, [ADR-0011](docs/adr/0011-record-event-double-write-is-intentional.md)
- [x] 2026-07-04 — Green-light fleet briefs for CQRS plan steps 2–4 (atomic writes +
      capacity.json guard, Ledger.reindex, canonical Corpus enumerator)
      (source: [docs/CQRS-ES-STANDARDIZATION.md](docs/CQRS-ES-STANDARDIZATION.md))
      — DONE 2026-07-04, merged f1f2b8b/bd636d5/ad486d6 (bfaaf9f)
- [ ] 2026-07-03 — known_good/known_bad_models.json guard coverage
      (source: DECISION-NEEDED-A2.md, flagged again by the CQRS review)
- [ ] 2026-07-05 — Canonical AM4 B70 bring-up: how are the **2 per-B70 ports** normally
      served, and are they meant to be up now? (blocks the full cross-machine matrix pilot)
      (source: [SESSION-RETRO-2026-07-05.md](SESSION-RETRO-2026-07-05.md))
- [ ] 2026-07-05 — Fix the knowledge-guard bug blocking 3 read tools (`schedule_hindsight`,
      `query_am4_catalog`, `query_capacity`) — one root cause; unblocks the JS5 regret gate
      (source: [SESSION-RETRO-2026-07-05.md](SESSION-RETRO-2026-07-05.md))
- [ ] 2026-07-05 — Reload the HEARTH gateway to expose the commander door tools
      (`refine_idea`/`refine_result`); CLI already works (source: [ADR-0012](docs/adr/0012-commander-intent-lane-frontier-out-of-loop.md))
- [ ] 2026-07-05 — Harvest + synthesize the 24-pour idle campaign; curate/land the JS5 +
      assay-acceptance branches (source: [SESSION-RETRO-2026-07-05.md](SESSION-RETRO-2026-07-05.md))
