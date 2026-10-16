# 0069 — An unattended Codex lane may record a rejection, never an acceptance, and never on its own candidate

**Status:** Accepted (Derek, 2026-10-15: "i approve the others", on the harness-plan rev 2 recommendation "unattended reject-only on non-authored candidates; accept stays attended; conditional on enforceability"). Filed by Claude Fable 5.1 (cc-0ad7aa53) on his words under the ADR-0067 precedent. Extends ADR-0048; nothing in ADR-0053 changes.

## Context

ADR-0048 makes every local candidate stop at `awaiting_review` until a frontier caller or Derek records the verdict.
Codex is a frontier model, but unattended Codex (`approval_policy="never"`) refuses any HEARTH tool that is not
pre-approved, and `record_local_work_verdict` is deliberately not in the pre-approved list
(`~/work/lab/cli-expectations.toml`; `hearth_cli_policy.py` writes per-tool `approval_mode = "approve"` tables and
cannot condition on a tool's arguments). So a Codex lane that reviews a candidate overnight cannot close it, and the
rejection waits for a person or a Claude lane in the morning. The harness plan (I1, 2026-10-15) measured review at
1–5 minutes per report brief against 10–20 manual; the waiting, not the reviewing, is the cost.

## Decision

1. An unattended Codex lane may record `decision: rejected` on a local-work candidate it did not author. "Author"
   means the manifest's `caller.submitted_by` equals the verdict caller, or the candidate's `route.provider` lane was
   driven by the same Codex lane (the lane name in the work's `task_id` stamp, `cx-<lane>`).
2. `accepted` and `superseded` stay attended: an unattended Codex lane that wants to accept writes a wait
   (`streams/lib/waits.py`, source `codex-<lane>`) naming the work id and its evidence; Derek or a Claude review lane
   records the acceptance.
3. `cancel_execution` stays attended.
4. Enforcement is the door's, not the CLI's: `hearth/localwork/service.py::verdict()` refuses an unattended
   Codex caller's `accepted`/`superseded`, and refuses any verdict on a candidate the caller submitted; the refusal
   names this ADR. "Unattended" is declared by the caller on the call (`attended: false`) and defaults to false for
   `codex-cli` until the CLI can carry the flag; a verdict row records `attended` so the ledger can count unattended
   accepts (the number that must stay 0).
5. Every verdict carries the optional `bucket` (form | setup | substance) from harness-plan I1.6.

## Consequences

- Night reviews by Codex close rejections themselves; the morning queue holds only candidates worth accepting.
- The `attended` field and the author check are a bounded code change in `localwork/service.py` and the
  `record_local_work_verdict` tool surface (harness-plan I2, Codex lane after this ADR), plus `cli-expectations.toml`
  gaining `record_local_work_verdict` for Codex once the door enforces the rule. Until that code lands, the rule is
  attended-only and nothing changes on the wire.
- Measure: verdict latency on carried candidates; count of unattended accepts on the ledger (must be 0).
