# Carry work emit recovery

Status: deployed opt-in, 2026-10-17 UTC. Default behavior is unchanged.

`HEARTH_CARRY_WORK_EXECUTION_PROCEDURE` accepts `one_call` (default) or `think_then_emit`. A new carry manifest freezes the selection in `carry.work_execution_procedure`; old manifests without the field keep their prior request behavior. This is an internal execution procedure, distinct from the delivery procedures `one_call`, `carry` and `items`.

With the opt-in selected, only the carry work stage adds `procedure="think_then_emit"` to its existing `inference.deliberate` execution. The execution service owns the bounded emit recovery; carry does not build another inference path. Check and quote-attachment stages retain their behavior. The carry draft consumer and render metadata prefer the canonical `result_artifact_id`, falling back to the original output-role artifact when no canonical result exists. Historical manifests are not rewritten.

## Evidence and limits

A known caller-identity task exhausted 24,576 thinking tokens and emitted a visible answer. The first native attempt exposed consumption of its empty first artifact. After the canonical-draft correction, one standing setup retry was independently accepted with 42 seconds native fetch-to-verdict turnaround. The final metadata-pointer correction was validated by exact controller replay with unchanged candidate bytes, then deployed; that replay is not another fresh inference success.

Research evidence lives in `/home/derek/work/lab-rnd/research/regime_advisor/audit/20261016/followup-01/RESULT.md`. The source worktree commits are b2ab5da, 719ee00 and c3c4ff1; deployed equivalents are a418f18, 6ff27b2 and 853bb8e. The source worktree is integrated by equivalent cherry-picked changes; do not reapply it.

This establishes a useful known-task recovery, not general regime selection or review-time savings. Follow-up summaries used the ordinary one-call path and deterministic source aids, not this emit flag. Existing candidates still stop at awaiting_review and require an authorized independent verdict.

## Runtime and research closeout

The experiment's temporary singleton route, carry-check override and emit override were removed. Pooled routes/default work procedure, authenticated readiness and Banked Fire were restored; no model or seat restart was required. Enabling this flag later is a new recorded experiment under live environment, ownership and preflight rules, never a silent default change.

The selection pilot produced 17 original deliveries, 13 reviews and five acceptances; four carry generation setup misses and one held summary arm remain recorded. The subsequent shadow reached 40 timely runs. An exact 80-prediction audit linked those to 27 documented task groups and found no stable advantage under time-aware equal-task diagnostics. Keep the precursor association-only and shadowed under ADR-0070. The research-side dependent comparison extension remains Proposed; no route recommendation or adoption is authorized here.

Current readout and retrospective: `/home/derek/work/lab-rnd/research/regime_advisor/README.md` and `RETROSPECTIVE-20261017.md`. Forty-eight-hour observations remain pending after the current dev expiry; the then-live environment governs them. No background reviewer is scheduled.
