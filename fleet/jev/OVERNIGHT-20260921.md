# Overnight JEV / Hermes work log

Authority: Derek permitted 4–8 hours of iteration to improve useful local-work
throughput and quality. The goal remains active. Do not confuse a completed
cycle or a healthy endpoint with completion of that larger objective.

Previous goal turn classification: **progress**. It integrated the count fix,
verified actual outputs, and established an HTTP 529 hold. The next cycle used
a new real task after that hold, not a repeat of the completed correction.

## Cycle 1: truthful scheduler status

Useful artifact: `fleet-scheduler status` on FX99 now distinguishes actual
daemon liveness from cached scheduling/reviewer observations. `--json` retains
its existing valid JSON output. Cached ready does not imply a running process;
the output explicitly disclaims live hardware-residency evidence.

Budget: started about 07:10 UTC; latest dispatch 07:15; no generation after
07:29; delivery/state accounting by 07:35. The useful worker candidate was
observed at 07:16:36. The deployed, independently repaired command was verified
at 07:22:42, including a real stopped-daemon check at 07:25:45. No new test files.

Parent receipt: `br-20260921-071234-8b607786`.
Task: `jev-6b6659cbbf258638035da94e`.
Worker receipt: `br-20260921-071259-279d5a84`.
Plan: `hearth-hermes-br-20260921-071259-279d5a84-466437e6`.
Candidate: `afa3331fccb0a9a26e1e2a461c2244c1780eb43f`, unpromoted worker branch.

| Stage | Actual observation | Interpretation |
| --- | --- | --- |
| JEV | 0.373 s; 914 input tokens; fit 2.95, confidence .95, ambiguity .17 | Gates unchanged; one task admitted at 07:12:59 |
| OMEN builder | qwen3-30b-a3b, native 16k, one worker; 177 s, five steps; candidate committed | Completed a real artifact, but not an acceptable patch |
| MechNet assay | B/70; 162 existing tests passed, candidate import failed | Existing broad tests did not cover the requested behavior; not acceptance evidence |
| First Hermes review | 41.919 s, PASS | False PASS; confused baseline with candidate and missed blocking faults |
| Independent inspection/execution | Missing `connect_call`/`lap`; injected build-label suffix accepted; NaN age accepted | Candidate rejected; bool-count rejection did work |
| Evidence-first Hermes review | 32.984 s, NEEDS_WORK | Correctly described all three supplied failures and attributed execution to Codex |
| Integration | Codex repaired/reused the status layout while preserving both original scheduler functions | Real FX99 command passed live/stopped, JSON and malformed-value checks |

The automatic JEV → MechNet → Hermes → release lifecycle completed without
identity repair, receipt reconciliation, or delivery recovery. The reviewer
model was actually released: helper owner null, model absent, NVIDIA memory
88 MiB / 15 MiB. This proves that lifecycle on this one task, **not** trustworthy
automatic code acceptance. The original candidate and false PASS remain intact.

The evidence-first follow-up was an **operator-driven review**, not a second
JEV dispatch or an automatic retry. It used the same candidate/model, a fresh
Hermes session, candidate-only context plus executed findings, and the existing
owned start/stop helper. No KV reuse. Its success is one assisted-review sample;
do not claim independent discovery or a statistically established improvement.

## Evidence and attribution

- [JEV decision](evidence/20260921-status-decision.json).
- [Unedited worker candidate](evidence/20260921-status-candidate.json).
- [Unedited initial review](evidence/20260921-status-review.json).
- [Independent failure evidence](evidence/20260921-status-execution-evidence.json).
- [Unedited evidence-first review](evidence/20260921-status-evidence-first-review.json).
- [Deployed command outputs](evidence/20260921-status-integrated-live-status.json).

Codex fixed daemon-state classification, exact label matching, finite timestamps,
malformed-value handling, and JSON read errors, and kept `connect_call`/`lap`
unchanged. AST comparison against the pre-cycle source proved those two
functions unchanged; the restarted real scheduler also ran its idle cycle.
The worker candidate is **not** the deployed file. Do not attribute Codex's
corrections or the supplied execution evidence to OMEN or Hermes.

This cycle produced one usable deployed status improvement with operator
correction, **zero accepted unassisted worker patches**, one false-PASS review,
and one correct evidence-assisted review. Report those outcomes alongside
latency instead of treating files written or test totals as useful throughput.

## Current live state, 07:29 UTC

- FX99 CPU scheduler active, observed systemd MainPID `1514424`; no HOLD,
  no active build, no pending automatic review, no admissible ready task.
  These are timestamped observations, not durable PID identities.
- Corrected `fleet/jev/cli.py` is deployed on FX99. Previous file retained as
  `.jev-status-cli-20260921.backup`. It was restarted after the live stopped-state
  check and the new code completed an idle scheduling cycle.
- AM4 deliberately remains **unloaded between reviews** for the ongoing
  overnight work, rather than immediately restoring the resident Dense baseline.
  The exact private baseline remains available through `restore-baseline`.
- OMEN resident worker configuration unchanged; no model or KV/topology changes.
- Pilot conductor policy remains installed for subsequent approved jobs;
  baseline backups/manifest are retained. Production 8710/8712 are untouched.
- Pilot gateway/tunnel remain the instances started 07:12:36 UTC: gateway parent
  36036, listener child 39548, tunnel 39600 (verify identities before any action).

The tool policy **rejected the attempted pilot-gateway restart before execution**.
No process was stopped by that rejected command; do not retry it through another
shell or encoding. CPU scheduler restoration was a separate allowed action.
Consequently the new `review_requires_execution_evidence` gate is **saved in
source but not loaded in the current gateway process**. Do not rely on it live.
The earlier optional evidence-attachment implementation is loaded, but the first
automatic review started before independent evidence existed; its packet did not
contain those results. This timing is why a required pre-review gate was added.

## Cost

No additional JEV call for the evidence-first review. New confirmed API estimate:
USD **0.000038388**. Cumulative ledger: five attempts, 2583 confirmed input tokens,
USD **0.005613510** booked. This consists of USD **0.000108486** confirmed estimates
and USD **0.005505024** uncertain reservations from the earlier failed requests.
Actual failed-request billing remains unknown. The existing USD 0.10 ceiling
and 100-attempt cap remain unchanged. Local-model wall time is not metered API
spend; power/hardware costs were not measured.

## Next experiments that would produce useful work

1. Shape the next small implementation as a short helper or targeted patch,
   not a complete rewrite containing large unchanged scheduler functions. A
   useful next task is a bounded cost-accounting view separating confirmed spend
   from uncertain reservations, using the actual existing ledger contract.
2. Require actual execution evidence before the next review. The source now
   supports `review_requires_execution_evidence: true`; its live activation is
   pending the policy-restricted gateway restart. Until that is legitimately
   resolved, use an explicitly operator-driven evidence-first review and do not
   claim the gate is active. Do not repeat the completed status task as a smoke.
3. Preserve first-pass acceptance, false positives, correction work, first-artifact
   latency, and GPU release observations. JEV task/profile confidence is not a
   patch-correctness probability. These failure outcomes should inform future
   task shaping; current queue counts alone do not encode code quality.

Do not expand into new model downloads, a broad benchmark matrix, or the
three-route optimizer before another useful artifact. DeepAgents, multi-profile
placement, and saved-KV waking remain unqualified. Existing shared-main code and
the worker branch were not automatically promoted or pushed by this cycle.
