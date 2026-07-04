# CQRS / Event-Sourcing Standardization — review synthesis

**Status:** recommendations, not yet ratified. Produced 2026-07-04 by a fan-out review
(1 Sonnet explorer → 3 parallel reviewers: ES-purist / pragmatic-migration / query-side →
frontier synthesis). Fleet second opinion pending: plan_id
`hearth-retro-2026-07-04-cqrs-fanout-d164cdf2`.

**Status update 2026-07-04:** steps 2–4 landed and merged (677 tests green, up from 653
baseline). Step 5 (`rebuild --from-zero`) is now unblocked.

**Status update 2026-07-04 (later):** step 5 landed — `hearth/projection/rebuild.py`
(`rebuild_knowledge`, also exposed as an MCP tool via `hearth/toolsurface/knowledge.py`)
replays the full projection DAG (six event-derived kinds + the ledger-native
capacity.json) into a `<out>/.staging-rebuild/` dir, validates the complete staged set
(every expected file present, parses, carries `contract_version`), then atomically
swaps every file over the live one with `os.replace`. Staging is cleaned up on both the
success and failure path; a mid-rebuild projector fault leaves the live knowledge/ dir
byte-untouched. Golden determinism test added: two from-zero rebuilds of the identical
fixture corpus are byte-identical for every knowledge file, and a from-zero rebuild is
byte-identical to the equivalent incremental `project()` + `project_capacity_knowledge()`
run — no projector fixes were needed for this (none of the existing projectors embed a
wall-clock field; everything already sorts deterministically before writing). 12 new
tests, full suite green (687 tests + 37 subtests, up from 677).

## Verdict

HEARTH is already ~80% of a classic CQRS/ES system: the gateway auto-ledgers every command
(including guard rejections), `query_*` tools are pure reads, projectors are pure functions,
the ledger is append-only by API. What's missing is three structural properties — a rebuild
button, stream integrity, and canonical corpus definition. Once they exist, the cleverest
compensating code in the repo (`corpus_guard`'s override machinery, the `"fixtures"`
path-sniffing) becomes deletable.

## Unanimous calls (all three reviewers, independently)

1. **Do NOT unify the two ledgers.** Kernel ledger (`hearth-event.v1`, digest-only) = audit/
   telemetry bounded context; workflow ledger (`runs/*/events.jsonl`, full payloads) = the
   real domain event store. ADR-0005's "one boundary" means one *door*, not one *store* —
   worth an ADR amendment saying so (pending Derek).
2. **Keep kernel events digest-only.** Full-payload replay belongs in domain events; payloads
   in the audit firehose are the wrong layer and a privacy hazard.
3. **The rebuild button is the load-bearing fix.** `rebuild --from-zero` (replay full DAG into
   staging, atomic-swap in) + `Ledger.reindex()`/`verify()` for the SQLite index + a golden
   determinism test (rebuild twice → byte-identical). Today the index has no recovery path.
4. **Demote `corpus_guard` from gate to tripwire — only after the mechanisms land.** A guard
   that must fire to prevent data loss is a guard the design leans on.
5. **Root cause of 2026-07-02 = ambient corpus definition** (caller-supplied `sources` +
   `rglob`). Fix: canonical `Corpus.enumerate(root)` — the only place globbing lives —
   stamping `corpus_digest` + `event_count` into every output doc. Tests get their own
   `HEARTH_ROOT` (mechanism exists) instead of the `"fixtures"` dir-name convention.

## Sharpest findings

- **LIVE BLIND SPOT — CLOSED (bd636d5):** `hearth/toolsurface/knowledge.py::project_capacity_knowledge`
  wrote `knowledge/capacity.json` via bare `write_text`, bypassing `corpus_guard` entirely; the
  guard's extractor table only knew `capacity_estimates.json`. Cheapest real risk-closure.
  Closed by routing the write through `guard_write` + adding `bucket_count` to the
  `capacity.v1` contract.
- **Atomic writes:** every knowledge write is in-place `write_text`; a crash mid-`project`
  leaves torn files or fresh `findings.json` beside stale `policy.json`. One
  `atomic_write_json` (temp + `os.replace`) routed through all writers.
- **`stream_seq`** per-run contiguous sequence on domain events → lost events become
  *detectable* gaps (what the incident actually was). Optimistic-concurrency rejection stays
  behind a flag.
- **Idempotency:** retried MCP calls mint fresh uuid4s → duplicate events → inflated counts.
  Optional `idempotency_key` column for mutating tools; `(stream_id, event_id)` dedup in
  `append_event` with caller-suppliable `event_id`.
- **Projection DAG registry** replaces the hardcoded `_PROJECTION_KINDS` tuple; partial runs
  expand to downstream dependents; `project --check` staleness oracle for the watchdog.
- **`projector_version`** source-hash stamped in each output doc → logic changes flag every
  doc as rebuild-needed (the silent sibling of corpus overwrite).
- **Upcast seam now, while free:** `schema: "workflow-event.v1"` on domain events + empty
  upcaster registry applied on read; never rewrite the store.

## Explicit don't-bothers

Event-store DBs, cross-process locking, message buses, snapshots (add when rebuild crosses
seconds), event signing/encryption, full-payload kernel events. Single-writer-by-topology is
fine for this lab.

## Execution order

| Step | Work | Size (sessions) | Status |
|---|---|---|---|
| 1 | ADR: two ledgers = two bounded contexts; one door ≠ one store | 0.5 | |
| 2 | `atomic_write_json` everywhere + guard `capacity.json` (live bypass) | 0.5 | DONE bd636d5 (2026-07-04) |
| 3 | `Ledger.reindex()` + `verify()` + CLI | 1 | DONE f1f2b8b (2026-07-04) |
| 4 | Canonical `Corpus` enumerator + digest stamped in docs | 1 | DONE ad486d6 (2026-07-04) |
| 5 | `rebuild --from-zero` + golden determinism test | 1 | DONE (2026-07-04) |
| 6 | `HEARTH_ROOT` isolation for tests; retire fixture-name guard | 0.5–1 |
| 7 | `stream_seq` + `schema` field + upcast seam on domain events | 1 |
| 8 | Idempotency keys + `record_event`/`ledger_adapter` directional dedup | 1 |
| 9 | Projection DAG registry + `--check` + `projector_version` | 1 |
| 10 | Demote `corpus_guard` to post-rebuild tripwire; delete override machinery | 0.5 |

Steps 2–4 are independent and fleet-briefable in parallel; 5 depends on 3+4; 10 strictly last.

## Open decisions for Derek

- Ratify "two ledgers stay separate" as an ADR-0005 amendment or new ADR.
- `record_event` double-write (domain event + kernel event about the call): all reviewers
  lean **keep it, document it** — two different questions answered — rather than
  special-casing the gateway wrapper (high blast radius, low payoff).
- Green-light which steps to brief to the fleet first (2–4 recommended).
