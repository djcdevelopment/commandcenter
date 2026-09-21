# Count correction: delivered code, unsuccessful live qualification

2026-09-21. Parent Hearth receipt: `br-20260921-065757-d14fdb68`.
This supersedes the current-state portions of [the first-loop handoff](HANDOFF-20260921.md),
not its preserved candidate, false-PASS review, or execution evidence.

## Useful result

The prior OMEN formatter is now integrated in this worktree with Codex's two
exact-type corrections: `type(active_work_val) is int` and
`type(pending_reviews_val) is int`. Boolean counts render `unknown`; nonnegative
integer counts work. Four actually executed cases passed: exact legacy output,
boolean rejection, normal integer counts, and invalid counts/unsafe wait label.
The legacy comparison is pinned to baseline commit
`d69d8685f153962ae1ef1ad987d34efdec8470ba`, not the newly integrated file itself.

[Execution evidence](evidence/20260921-count-correction.json) includes inputs,
expected/actual outputs, source hash, and explicit authorship. This is a source
integration in `agent/jev-scheduler-20260921`, not a deployment of the human
status command, a shared-main promotion, or a push of this branch.

The local review packet now includes actual execution evidence with independent
executor attribution. Its task-specific verifier accepts only the AST of the
known two-condition correction. Unknown source is not executed. This is not a
general Python sandbox, autonomous acceptance gate, or new test harness.

## What the live lap did and did not do

The useful target was a worker correction, evidence-grounded Hermes review,
and a clean review-model release. The 20-minute window began at 06:52:11 UTC;
dispatch cutoff was 06:57, generation cutoff 07:07, restoration/delivery 07:12.
The setup cutoff was missed without a new local-model artifact. Derek explicitly
approved use of the remaining window without extending the final deadline.

At 07:01:21 the separate gateway/tunnel started. The approved correction task
was `jev-eac82cbcffb2b9b84261c58b`, superseding the first envelope. The existing
conductor policy was temporarily installed with a fresh, retained backup suffix
`.jev-counts-20260921.backup`. The old `ExceptionGroup` hold was retained as
`HOLD.first-lap-20260921` before starting the FX99 scheduler.

The client made two HTTP attempts and ended in `jev_http_529`. Its existing
single short retry only allows HTTP 429/529; the first response code was not
retained, so do not claim both responses were necessarily 529. The scheduler
held before creating a build receipt. **No new worker was dispatched and no
Hermes review ran.** The cached successful `last-decision.json` is from the
previous task and must not be presented as this task's decision.

That edge ended the live lap. No retry loop, changed thresholds, alternative
model, or new topology followed. Codex corrected and integrated the existing
candidate directly so the turn still delivered usable source. This does not
qualify the requested unattended build/review/release path. The exact cause of
the provider response is not established here.

## Cost and attribution

- Prior OMEN candidate: `428aaed3405f5e123f21e6f02055a0045de66f8c`.
- Two count-condition fixes, integration, execution evidence and packet changes:
  Codex, not the local builder or Hermes.
- This lap: two API attempts, no returned token usage. Actual new billing unknown.
- Additional conservative reservation: **USD 0.005505024**, not confirmed spend.
  This exceeds the sub-USD-0.0001 estimate given before the attempts.
- Cumulative ledger: four attempts, 1669 confirmed input tokens, USD 0.005575122
  booked including uncertain reservations; previous confirmed estimate remains
  USD 0.000070098. Do not report the combined ledger as measured API charges.
- No new test files or benchmark runs. The evidence is direct execution of the
  delivered formatter, not model qualification by synthetic test coverage.

## Restored state

Restoration finished and passive checks were complete by 07:06:43 UTC:

- AM4 original Dense baseline healthy, owner `cutover`, native 131072 context;
  no KV restore. GPU use: RTX 5070 11378 MiB, RTX 4070 Ti 10873 MiB. These cards
  are again occupied by the baseline; do not call them free after restoration.
- Conductor original policy/dispatcher restored from hash-verified backups.
- Production/restricted gateways 8710 and 8712: `/healthz` returned `ok`.
- Only verified pilot-owned gateway processes and SSH tunnel were stopped;
  port 8713 has no listener.
- OMEN worker remains native 16384 context, eight slots, zero busy/eight free.
- FX99 scheduler service inactive; current forensic `HOLD` is `jev_http_529`.
- New undispatched task marked `cancelled`, with its full record retained,
  because the correction is already integrated. Do not auto-dispatch obsolete
  correction work on restart. Old candidate task remains
  `awaiting_manual_acceptance`; its false-PASS review was not rewritten.

## Next useful slice

Do not repeat this now-completed correction to manufacture a successful cycle.
Use the next actual one-file change, for example wiring the corrected formatter
into real scheduler status with explicit daemon liveness. Name that artifact
and a fresh short window first. Review the HTTP hold before restarting; keep
JEV thresholds intact. A deliberate operator-direct local build could preserve
useful work if cloud admission stays unavailable, but it must be labeled as a
fallback, not evidence that JEV scheduling worked.

The source-packed `enqueue-counts` command remains a reproducible record of
this lap; it is not a recommended recurring task. Future task types need their
own genuine verification evidence, not reuse of the formatter-specific AST gate.
Broader Hearth/DeepAgents/MechNet routing and saved-KV waking remain unqualified.
