# 0063 — Codex decisions under explicit bench27 delegation

**Status:** Accepted 2026-10-05 (decided by Codex), under the explicit user delegation below. Independent Opus review v2 passed; all 34 targeted night tests passed.

## Context

Derek first chose to leave nine prepared bench27 report briefs unapproved, then explicitly superseded that decision with: “i give permission for you to review and decide on my behalf -- but it must be marked (decided by Codex) and keep a running list of decide items for me to easily review”.

The existing `night approve` records `approved_by=derek` unconditionally. Calling it as Codex would misattribute the decision. Dev mode alone does not authorize night approval (ADR-0053); the new authority is this explicit user delegation.

## Decision

1. Scope this implementation to G-bench27 and its nine hash-pinned report wrappers under `/home/derek/work/bench27-night-prep-20261005`. The grant expires no later than 2026-10-12T07:00Z and is revoked at campaign closure or sooner on Derek’s instruction. The full user quote, grantor, grantee, exact prep root, wrapper hashes and validity period are recorded in an authored delegation document.
2. Add an opt-in `night approve --by codex --delegation FILE --decision-id ID`. Keep the explicit interactive terminal confirmation and all existing brief/source/delivery-pin validation. Ordinary human approval remains available and a fresh human-to-human reapproval refreshes its timestamp; the brief is revalidated after confirmation. Effort recording is unchanged. Codex-related replacement requires an explanation and preserves the previous approval. Missing, expired, revoked, out-of-scope or non-dev delegation refuses both new approval and later queue admission; queue admission also requires the recorded grant hash to match. No environment setting creates approval authority.
3. Before approval, Codex reviews the brief and records an individual decision, exact wrapper hash and rationale. The machine-readable decision record must say `decided_by: codex`, `decision: approve`, and `annotation: (decided by Codex)`. The human-readable running list is `/home/derek/work/bench27-plan/CODEX-DECISIONS.md`.
4. The approval records `approved_by: codex`, the required annotation, decision ID and delegation SHA. It never claims Derek personally reviewed or approved the item. No existing human approval is retroactively relabeled.
5. Approval only permits later submission of the exact reviewed brief. Qualification, routing, thermal checks, sole ownership, candidate `awaiting_review`, frontier verdicts, admission and scope remain required. The six sealed briefs remain sealed. Public-site credential use remains Derek’s separate step.
6. The grant is an audit artifact authored after explicit user authorization and individual review; its issued_at records document creation, not the first moment of user authority. It is not a cryptographic proof or a generic authority service. Only the campaign owner writes it; another agent must not infer authority merely from being able to create a JSON file. No production or future-campaign delegation is inferred.

## Consequences

Codex can carry out Derek’s requested review and decision without impersonating him. Every delegated item is reviewable by name, exact hash, rationale and evidence. The nine dispatch decisions do not pre-accept model outputs. Failures and refusals remain evidence. Manual-work economics remains unknown because Derek confirmed no measured baseline exists.

Revocation prevents new queue admission; withdrawing already queued work requires `window close`, and running work requires separate cancellation. The terminal prompt records deliberate confirmation, not proof of the operator’s identity.
