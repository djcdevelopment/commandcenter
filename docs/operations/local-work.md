# Local-work production runbook

## Caller workflow

Submit a bounded task with a full commit SHA, declared source paths, explicit
criteria, and (for substantial work) a build receipt. Watch by cursor, fetch the
immutable candidate, validate independently in an isolated worktree, then record
an evidenced verdict. Never apply a candidate from the HEARTH producer path.

Manifests project to `runs/operator/<work_id>/work-manifest.json`; full prompts
remain private execution artifacts. Reconciliation runs on submit, watch, and
get. A successful model job becomes `awaiting_review`, never accepted.

## AM4 fast cutover — 90 minute ceiling

Pre-stage packages and weights while the prior service remains production.
Reserve the final 30 minutes for validation or restoration. The authenticated
facade and model identity must work by minute 20 and one real candidate by
minute 30. If two-GPU tensor parallel fails, restore the prior AM4 unit; do not
improvise another topology in the window.

Promote only after all gates pass: authenticated `/v1/models` identity, exact
context refusal, four simultaneous admitted requests, recovery after service
restart, one valid real candidate, and complete manifest provenance. Change
`hearth/etc/local-work-routes.toml` fast to `am4-vllm-moe` and then change the
general HEARTH default in a separate reviewed commit.

## OMEN deep cutover

Start only after AM4 carries fast/default work. Use the checked-in dense launch
recipe. Qualify with a real source-backed analysis above 32k input tokens;
confirm both B70s carry layers, shared memory does not grow materially, and the
frontier caller records a verdict. On failure, restore the preserved MoE recipe
or leave deep unavailable; never route deep work to cloud.

## Rollback

AM4: disable `am4-vllm-moe.service`, restore the previous facade alias map and
service, and verify its authenticated health. OMEN: use the ArcServe lifecycle
controls and preserved direct/MoE recipe; never kill or unload the production
port through llama-swap's bare endpoint. Restore the route profile before
closing the cutover receipt.

## Verification

Run the local-work, execution, operator-history, guard, capability-surface, and
gateway suites. Verify both live `/v1/models` surfaces through their authenticated
facades, submit real candidates on each lane, and inspect the final manifests'
provider, model, routing, token, artifact, receipt, and verdict fields.
