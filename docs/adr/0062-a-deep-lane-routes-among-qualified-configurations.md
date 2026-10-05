# ADR-0062: A deep lane routes among independently qualified configurations

Status: **Proposed** (2026-10-05). Built in an isolated worktree; independent review and acceptance are pending.

## Context

Bench27 needs delivery capacity across both B70s and AM4. These configurations can differ in context, serving recipe and hardware. Pooling their verdicts or treating a free execution slot as a free multi-step worker would conceal those differences. An unconditional fast-seat tokenizer also prevents the deep-only configuration from admitting work.

## Decision

A deep lane declares either the existing `backend = "name"` or a nonempty, distinct `backends = ["name", ...]` list. Declaring both is invalid. Fast and tool remain optional single-backend lanes. Callers continue to select a lane, never a backend. Explicit absent lanes and the items procedure without its readers refuse. Items also refuses a pooled deep settler; its three-role admission remains a singleton-route procedure.

A list with multiple backends admits only independently qualified configurations. The additive `delivery-procedures.v1` field `backends.<backend>.profiles.<serving_profile_sha256>` contains `families` and `all` counts with the existing distinct-accepted-brief threshold and procedure ordering. Backend aggregate counts remain compatible with legacy single-backend routing; they cannot qualify a pooled route. Once a backend contains profile-scoped evidence, automatic procedure selection uses only its matching profile even on a single route. Counts must be exported from recorded verdicts for that backend and profile, never copied between profiles or replicas. The authoritative fingerprint is produced by `LocalWorkService._serving_profile`: an allowlisted scalar settings projection serialized as sorted compact JSON and hashed with SHA-256, stored in the manifest route. The profile includes the deliberate output budget. It identifies the declared recipe; it is not proof of actual hardware or effective serving flags, which campaign host/sizing checks must validate. A singleton route supports pinned qualification laps before the backend joins the pool.

Among qualified candidates whose declared budgets and exact tokenizer admit the request, choose the fewest outstanding local-work assignments, then fewest active execution leases, then configuration order. Awaiting-review and terminal work no longer consume an assignment. Refuse a candidate whose outstanding assignments or active leases reach its declared parallel slots. A lane with no eligible candidate refuses with each reason. A chosen qualified carry procedure that cannot run does not become an unqualified one-call procedure. The lane need not contain identical recipes.

Serialize selection through persisted assignment across gateway threads and processes using a separate SQLite admission lock under the operator home. Reconciliation retains its existing locks. This deliberately favors correct admission over parallel tokenizer throughput; tokenization is bounded by existing HTTP timeouts. Raw execution clients can still acquire a lease after selection, so execution's lease gate remains authoritative and may queue an admitted job.

Deep-only auto routing selects deep directly and tokenizes each considered candidate's full prompt. An explicit deep request and code-family auto request have no dependency on fast. Carry tokenization uses the actual system and user messages. Existing execution admission and later-stage checks remain in force; initial admission is not a guarantee that all later generated turns will fit.

The manifest records backend, model, serving profile, load snapshot, excluded candidates, selected procedure, qualification counts and table digest. An idempotent retry preserves its recorded lane/backend/procedure/table, even if route order or table changes. A missing or retired recorded provider, changed model or recipe, or conflicting procedure refuses; it never selects another provider. Structural retries and carried stages keep the manifest backend.

## Consequences and deployment

No backend configurations or production table are changed by this build. Deploy after independent review, qualified final-recipe evidence, and a zero-lease door restart. Initially route qualification laps through a singleton deep route, export profile-scoped evidence, then deploy the multi-backend lane. Keep code and delivery verdicts separately attributable. Retain existing raw-execution capacity gates and compare effective backend participation during the capacity lap.

Tests cover concurrent coordinators, heterogeneous exact admission, legacy seeding, invalid routes, missing/profile-mismatched evidence, absent fast/items refusals, lease tie-breaking, exhausted capacity and stable/refused retries. Live qualification and capacity measurements remain campaign acceptance work.
