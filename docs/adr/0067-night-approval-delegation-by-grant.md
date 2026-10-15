# 0067 — Night approval delegation is scoped by the grant, not by a constant

**Status:** Proposed 2026-10-15. Extends ADR-0063; nothing in ADR-0053 changes.

## Context

ADR-0063 let Codex record night approvals under an explicit, hash-pinned delegation. The validator
(`lab-rnd/daily/night_work.py::_delegated_approval`) hard-codes the campaign: `goal == "G-bench27"`,
`prep_root == DELEGATION_PREP_ROOT` (the bench27 prep directory) and `grantee == "codex"`.

On 2026-10-15 Derek approved tonight's ten prepared briefs (`~/work/daily-night-work-prep/briefs/2026-10-15-*.md`,
CCMETA `goal_id: G-ecosystem`) in chat: "consider them approved." The gate cannot record that: the goal and prep root
are not bench27's, and the session carrying it out is a Claude frontier session, not Codex. Relaxing a gate is
Derek's decision and an ADR (lab intent H-3); he made the decision, and this ADR records the bounded change.

## Decision

1. The grant names the scope. `grant.goal` must be a `G-...` id equal to the CCMETA `goal_id` of every brief it covers
   (parsed from the brief file as `night prepare` parses it). `grant.prep_root` must resolve to the prep directory in
   use. The bench27 constant `DELEGATION_PREP_ROOT` is removed.
2. The grantee is `codex` or `claude`. `claude` is a frontier session acting as the recorded decider in its own
   terminal (for example an amux lane). `night approve --by codex|claude` requires `grant.grantee == --by`; decision
   entries must say `decided_by: <grantee>` and the annotation `(decided by Codex)` or `(decided by Claude)`; the
   approval records `approved_by: <grantee>` with that annotation. No record claims Derek personally decided.
3. Every other ADR-0063 check stays: the actual dev environment active and the grant inside both its own expiry and
   the dev expiry; `DELEGATION_CEILING` unchanged; a SHA-256 per brief; a verbatim `source_user_quote`; a
   `night-review-decisions.v1` log with exactly one matching entry and a rationale; the interactive TTY confirmation
   (type `yes`); revalidation after confirmation and again at `window open`; replacing any delegated record (Codex or
   Claude) requires `--change-reason` and preserves the prior record.
4. A grant is authored only after Derek's explicit words, which it quotes. Being able to write a JSON file confers no
   authority; no goal, prep root or future night is inferred from a past grant.

## Consequences

- Derek's chat approval becomes recordable: the session writes the grant with his words and a decision log, then
  approves each brief in a real TTY. The record still shows who decided, under whose words, at which hashes.
- The ADR-0063 bench27 grants keep validating: their goal and prep root now come from the grant and match.
- Open: `DELEGATION_CEILING` is still 2026-10-12T07:00Z, so any grant expiring after it (tonight's expires
  2026-10-16T07:00Z) is refused. Moving or removing the ceiling is a separate Derek decision; until then this change
  is inert for new grants.
- Approval only permits submission of the exact brief; qualification, routing, admission, `awaiting_review` and
  frontier verdicts are unchanged.
