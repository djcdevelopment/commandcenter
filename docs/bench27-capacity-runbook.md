# Bench27 final-profile qualification and capacity runbook

Prepared only; no dispatch, approval, verdict, registry write, service call or deployment was performed. ADR-0062 and bench27 `IMPLEMENTATION.md` supersede task 5's old same-recipe-only pool, pooled code-fix queue and direct-dispatch approval fallback. The nine report wrappers are staged at `/home/derek/work/bench27-night-prep-20261005/briefs`. Under accepted [ADR-0063](/home/derek/work/commandcenter-linux-flash/docs/adr/0063-codex-decisions-under-explicit-bench27-delegation.md), the owner has recorded nine exact-hash approvals by Codex under Derek’s explicit scoped delegation; these are not claims of personal review by Derek. Four code fixes remain excluded; their pooled capacity criterion is unmet.

## Delegated approval checkpoint

Read-only reconciliation found all nine current wrapper SHA256 values match `/home/derek/work/bench27-night-prep-20261005/approvals.json` (checkpoint file SHA256 `cfcacb45c387f04ca4d6d01b03075e2089d434ec78489dc4bc0b04db26ea13db`). Admission through tuned-perception carry decision IDs D003–D011 in order, each `approved_by=codex` and `annotation=(decided by Codex)`. The running decision list is `/home/derek/work/bench27-plan/CODEX-DECISIONS.md`; the scoped grant is `codex-delegation.json` under that exact prep root. This observation is not a new approval or a permanent freshness assertion: the CLI must revalidate the grant and hashes on each open. Earlier declined-approval status was superseded by Derek’s explicit delegation; retain its audit history. No sealed work was read and no grant/approval/queue bytes were changed.

## Freeze before qualification

Do not treat the current declarations as final. The read-only snapshot in `bench27-capacity-drafts/observed-profiles.json` was derived by the actual `LocalWorkService._serving_profile` from `/home/derek/hearth-production/backends-linux.toml` (file SHA `fcc348a163ca4446f166821feb87f2a7e4e9844c363b60ee34f79f0bbf24d3bb`).

| Backend | Observed declared budgets | Observed v2 fingerprint |
|---|---|---|
| omen-dense-27b | context 65,536; output 16,384; deliberate 24,576; slots 2; MTP k2; Flash | 43f8c7fb96c73ec7d43b2f29b31f7ada824bc37851cc4852a23f8254b7db3b41 |
| omen-dense-27b-b | same declared settings, separate qualification counts | 43f8c7fb96c73ec7d43b2f29b31f7ada824bc37851cc4852a23f8254b7db3b41 |
| am4-vllm | context 16,384; output 4,096; slots 1; **no deliberate budget** | c787231c73b2f4825aac63b33a798816df6dbe326f5364001a47f4766e1ab94d |

AM4's observed declaration is stale relative to the recipe experiments and cannot qualify carry as-is. The owner must choose the measured final AM4 recipe, complete the toolchain-environment baseline/FP8 decision if relevant, and publish accurate budgets/model identity before starting its qualification. Both B70 effective recipes likewise need a final freeze after paired lever decisions. A shared fingerprint does not share counts: the table's first key is backend.

The fingerprint covers only `SERVING_PROFILE_KEYS` in `hearth/localwork/service.py:67`, plus selected_model and schema. It does **not** hash the entire script, toolchain, chat-template kwargs or effective GPU configuration. Set declared `serving_profile_version` to a version bound to the frozen complete recipe evidence, and use the supported `engine_build`, `model_weight_sha256`, `hardware_profile_id`, cache/batch/device fields where evidenced. Do not invent these values. Record source/control script SHA, effective argv, engine and toolchain versions, weight manifest SHA, observed context/output/slot limits and exact settings outside the fingerprint as physical evidence. Update the version if any unrepresented recipe detail changes.

Pin three final fingerprints using the actual helper after deployment, then require `route.serving_profile_schema == "serving-profile.v2"`, exact `route.serving_profile_sha256`, `route.provider`, and `route.selected_model` on every qualification/capacity work manifest. No inference result on an earlier profile can be relabeled. Do not activate the pool until all three pass. An AM4 limit leaves the three-backend objective explicitly incomplete.

## Qualification: six minimum accepted deliveries, no borrowed verdicts

The initial bounded set is **admission and arbiter on each backend**, with `procedure="carry"`, temperature 0, one in flight. These are two distinct open brief JSON hashes; repeat runs of one brief do not satisfy the two-distinct-accepted-brief threshold. If either is rejected, retain it and use another of the remaining authorized open IDs: timers, rungstate, recovery, tenancy. At least two distinct accepted briefs for the intended procedure are required independently on each final profile. More open qualification is appropriate if errors expose setup problems; never lower the threshold. These reruns are not a fresh held-out procedure rate.

All request drafts are in `/home/derek/work/worktrees/bench27-config/docs/bench27-capacity-drafts/request-*.json` (prepared inputs, not deployed). They were built with the drain's own `submit_args_from_brief(parse(wrapper).body)` using only the two open JSON briefs. They include full pinned source commits, criteria, brief object, `lane: deep`, `procedure: carry`, temperature 0, no max_tokens override, and caller-supplied idempotency keys with an explicit `FROZEN_PROFILE_SHA` placeholder. Replace that placeholder with the verified final fingerprint in a **new** request copy before submitting. Retry one attempt with the same key; a new measurement uses a new repetition suffix. Do not submit the placeholder drafts.

There is no backend parameter on `submit_local_work`. To qualify an unqualified backend, the owner temporarily deploys its reviewed singleton deep route (`qualify-<backend>.toml`), then submits the matching request through the registered **codex** MCP caller. Keep dispatch timers stopped and wait for zero leases/no unfinished local-work stages before changing routes or restarting the door. A pinned carry refuses unsupported carry rather than providing a successful one_call substitute. Qualification must not route around an approval refusal: revalidate the existing exact-hash authored-brief authorization and scoped delegation first; no direct submission of night work to evade it.

Exact MCP API sequence (request JSON provides arguments, no credential files or curl headers):

```text
submit_local_work(**reviewed_request_json)
watch_local_work(work_id=returned_id, after_sequence=last_sequence, wait_seconds=30)
get_local_work(work_id=returned_id)
get_local_work_artifact(work_id=returned_id)
```

Stop at `awaiting_review` for independent frontier grading. Preserve failures and the actual backend/profile. A fresh Opus grader reviews each open report using the existing open criteria; record per-criterion substance evidence separately from form repairs and enabling aids. The frontier owner records the final verdict as caller codex:

```text
record_local_work_verdict(
  work_id=returned_id,
  decision="accepted" or "rejected",
  criteria=[{"criterion": EXACT_STATEMENT, "status": "passed" or "failed" or "unknown",
             "evidence": OBSERVED_EVIDENCE}, ...],
  summary=FRONTIER_CONCLUSION,
  evidence=[REVIEW_ARTIFACT_REFERENCE, ...])
```

Acceptance requires every expected statement to have a passed row with evidence (`service.py:2042`). Do not prefill an accepted verdict, apply the candidate, count a local judge as the frontier verdict, or copy counts. Capture review start/end wall time and any repair/retry work, including rejected deliveries. The generic driver `delivery-plan/evidence/run_delivery_brief.py --caller codex --procedure carry --lane deep --temperature 0 --samples 1` supports the same operation, but the MCP request drafts provide explicit stable idempotency keys and avoid credential handling in this runbook.

## Import and validate the v2 qualification table

The owner creates a new campaign evidence root (for example `/home/derek/work/lab-rnd/research/evidence/bench27-capacity-20261005-final`), with `qualification-notes/<work_id>/run.json` for conditions/arm and `selected-work/<work_id>` symlinks to **only this campaign's reviewed open work directories**. Do not alter immutable work directories or include sealed runs. The importer requires the original work-manifest.json and delivery.json; the driver's copied work.json is insufficient.

```bash
cd /home/derek/work/lab-rnd
python3 research/cli.py import-delivery \
  --operator-runs /home/derek/work/lab-rnd/research/evidence/bench27-capacity-20261005-final/selected-work \
  --run-notes /home/derek/work/lab-rnd/research/evidence/bench27-capacity-20261005-final/qualification-notes \
  --dry-run
# Owner only after dry-run evidence review; same command without --dry-run writes the registry.
python3 research/cli.py export-procedures \
  --out /home/derek/work/lab-rnd/research/evidence/bench27-capacity-20261005-final/delivery-procedures-linux.json
```

Exporter CLI has only `--out`; there is no `--backend`, `--profile`, `--schema-v2` or minimum-threshold CLI option. It exports recorded delivery verdicts using default `min_accepted_briefs=2`. Table schema remains `delivery-procedures.v1`; **profile records** must be stamped `serving-profile.v2`. Unversioned fingerprints go to `legacy_profiles`. Check each `backends[NAME].profiles[FINAL_SHA].all.carry.accepted_briefs >= 2` and its record_ids against the actual six or more reviewed runs. Never seed or hand-edit counts. Rejected records remain visible.

For every distinct task_family in `/home/derek/work/worktrees/bench27-config/docs/bench27-capacity-drafts/queue-hashes.json`, call `hearth.delivery.procedures.choose(table, backend, family, serving_profile_sha256=FINAL_SHA, require_profile=True)` offline. Require a non-none qualification level and intended procedure `carry`. A family with no family-specific qualification may use that profile's `all` counts; that is the actual approved ADR-0062 rule. Existing family counts can change the selected procedure: verify rather than assuming all.carry alone determines it. Inspect the exported threshold (2), table digest, profile and record identities before deployment.

Do not publish a partial v2 table while any affected singleton lacks its final-profile threshold. Once v2 profiles exist for a backend, even singleton automatic selection no longer borrows legacy aggregate carry evidence. Publish the completed table and pool route in one paused deployment window.

## Reviewed route/table deployment through host configuration

`tools/ops/host_config.py` is a **read-only drift checker**, not an installer. Tracked/live pairs are:

| Tracked beneath commandcenter-linux-flash | Live path |
|---|---|
| host/omen-linux/hearth-production/backends-linux.toml | /home/derek/hearth-production/backends-linux.toml |
| host/omen-linux/hearth-production/local-work-routes-linux.toml | /home/derek/hearth-production/local-work-routes-linux.toml |
| host/omen-linux/hearth-production/delivery-procedures-linux.json | /home/derek/hearth-production/delivery-procedures-linux.json |

For qualification, the owner reviews/commits the selected singleton route to its tracked path, backs up the live file and copies reviewed bytes to the live pair. The pool candidate lists all three backends and removes absent fast/tool routes. At activation, stage/review/commit the qualified export and `pooled-routes.toml` into their tracked paths together, back up both live files, deploy both while admissions/drain are paused, then restart only the door at zero leases. Exact final steps after those prerequisites:

```bash
cd /home/derek/work/commandcenter-linux-flash
cp host/omen-linux/hearth-production/local-work-routes-linux.toml /home/derek/hearth-production/local-work-routes-linux.toml
cp host/omen-linux/hearth-production/delivery-procedures-linux.json /home/derek/hearth-production/delivery-procedures-linux.json
python3 tools/ops/host_config.py --check
systemctl --user restart hearth-production.service
/home/derek/.venvs/hearth-private/bin/python -m hearth.callers.doorcheck
```

Backends declaration changes are likewise reviewed/tracked/deployed **before qualification**. Do not change them after qualification without repeating it. Do not restart any seat during route activation. Restore the backed-up singleton/table pair if activation checks fail; do not install legacy counts to pretend the pool qualifies.

## Fixed cap 1 / 2 / 3 capacity laps

The queue is exactly the nine staged wrappers in `/home/derek/work/worktrees/bench27-config/docs/bench27-capacity-drafts/queue-hashes.json`: admission, arbiter, timers, rungstate, recovery, tenancy, tuned-backoff, tuned-sizing, tuned-perception. Source JSON pins, wrapper bytes, procedure policy, final recipes and ordering remain identical at all caps. No code-fix, sealed or unrelated queue item may enter. Match every wrapper's newly approved exact hash before each lap. Approvals predating D003–D011 do not cover these wrappers. Dev schedule/rerun knobs do not waive approval. No direct-dispatch fallback.

The night-gate close fix must be reviewed/deployed before custom-prep open: default midnight close must see registered campaign roots and quarantine queue contents on catalog damage; default audit must preserve dev_excluded. Approval/grant records remain campaign-owner controlled. The existing nine Codex approvals are authorized only by ADR-0063’s explicit delegation, not by dev mode. Revalidate the recorded grant SHA, actual dev environment, scope, expiry, revocation, decision IDs and current wrapper/delivery-source pins at queue admission; no new grant, approval or queue record is created by this runbook. Verify nine valid/nine freshly approved via `night prepare --prep ...`, and an empty global queue, before proceeding. If only a subset was approved, stop rather than silently changing the cohort between caps.

Save prior installed/transient timer states, configuration/table/backends hashes, guard evidence, environment expiry, and cap. Keep the installed `bankedfire-drain.timer` stopped throughout controlled laps. Require the existing arm to remain `armed=true`, `scope="authored"`, no experiment in flight; the observed arm already has that scope. Do not widen it, since otherwise the timer could dispatch other source classes after the cohort empties. Authored priority is file mtime oldest-first (sources/select code); window copies names in sorted order. Record the resulting order, including mtime ties, and verify it is identical across caps. The guard covers both B70 cards and AM4; all no-trip/freshness/temperature, final recipe parity, exact admission, sole-writer and zero-lease gates remain. Check backend participation, not merely total concurrency: least-outstanding assignment then execution leases then config order controls placement, and low cap may never choose AM4.

For N=1,2,3 the owner copies the corresponding reviewed `dev-capN.env` draft into tracked `host/environments/dev.env`, commits that tracked change, then renders the environment with the existing expiry (do not extend permission silently):

```bash
/home/derek/bin/hearth-env set dev --by codex \
  --reason '[goal:G-bench27] reviewed capacity cap N' --until 2026-10-12T07:00:00Z
/home/derek/bin/hearth-env check
cd /home/derek/work/commandcenter-linux-flash
python3 tools/ops/host_config.py --check
HEARTH_BACKLOG_ROOT=/home/derek/hearth-production/var/backlog \
  /home/derek/.venvs/hearth-private/bin/python -m fleet.bankedfire_linux --dry-run --json
```

The drain service reads environment files and process overrides can take precedence over rendered knobs. Confirm dry-run reflects the intended effective deep cap and every physical/admission gate; do not print service credential environment files. Keep all other lane caps unchanged. The drafts preserve original `fast=3,experiment=1,deepagents=1,tool=2`.

With approval and preflight satisfied, owner queues the selected cohort and starts a uniquely named short timer (example for cap 1; use distinct cap2/cap3 names):

```bash
cd /home/derek/work/lab-rnd
python3 daily/cli.py night window open --prep /home/derek/work/bench27-night-prep-20261005
```

Verify exactly nine queued filenames, hashes and the effective dry-run ordering before starting the timer; stop if they differ. Only then:

```bash
systemd-run --user --unit=bench27-cap1-tick --on-active=1s --on-unit-active=120s \
  /usr/bin/systemctl --user start bankedfire-drain.service
```

Record actual queued filenames before the first tick. `window` uses original names on first dispatch and `<stem>.rerun-<UTC>.md` after a prior dispatch when dev's already configured NIGHT_ALLOW_RERUN=1 applies. Drain idempotency is `bankedfire:<queue slug>:<brief-json-sha-prefix>`; preserve bytes but require **new slug/work IDs at each cap**. A still-dispatched hash is skipped; reconcile prior done/dispatched state and pending jobs before the next open. No manual rename/copy into queued and no manually manufactured approvals. If queued count is not exactly nine, stop and close the campaign window before ticking.

Stop the cap's transient timer when all nine have either reached awaiting_review or a recorded terminal failure. Record elapsed wall time from first actual submit to last candidate/failure, actual successful candidate count, per-delivery latency, stage/queue delays, per-backend assignment counts, guard/thermal/memory evidence, and any refusals. Separately count accepted throughput after frontier review. Watch helpers must inspect complete work manifests because older driver's short route summary omits profile/provider fields. Use the same fresh-Opus review method and frontier recorded verdicts at all caps; no local candidate is applied.

```bash
systemctl --user stop bench27-cap1-tick.timer bench27-cap1-tick.service
cd /home/derek/work/lab-rnd
python3 daily/cli.py night window close --prep /home/derek/work/bench27-night-prep-20261005
```

At any trip or invalid cohort: stop the transient timer, close the queue, preserve failed rows and use existing cancellation/guard restoration procedures. Do not restore/change recipes while work still holds leases. Advance to the next cap only after no unfinished work remains and the preceding cohort is fully accounted for. Do not equate an idempotent return of an old awaiting_review record with a new delivery.

## Review cost, completion and exit

Manual-completion baseline and human review minutes are **unknown**. Derek confirmed no measured manual baseline exists. Do not substitute model runtime, agent runtime, historical estimates or invented minutes. ADR-0063 leaves the existing interactive human effort recorder unchanged: `daily/cli.py night effort --work-id ... --review-minutes ... --manual-minutes ... --manual-source ...`. Codex/Opus measurements below are separate campaign evidence, not human effort records; do not invoke that recorder with unknown or substituted human/manual values. H-1 benefit remains unknown until a comparable measured manual baseline exists. If measured comparable review cost exceeds manual effort, stop the lane and fix setup.

Prospective measurement proposal, to be recorded by the owner before each reviewed cohort (not reconstructed from model runtime afterward):

- For parent review, actor=`codex`, log each UTC start/end interval with cap/cohort, work ID, activity and evidence reference. Start when actively reading/grading/repairing that delivery, pause when switching to unrelated work or waiting, resume as a new interval. Include rejected reviews, diagnosis and actual form/substance repair review; distinguish repair execution if separately timed. Missing intervals remain unknown, not zero.
- For each independent grader, actor=`opus` with a unique review/session ID, record actual invocation-to-completion wall duration and UTC boundaries separately. This is grader elapsed time, not human attention or an assertion of uninterrupted active reasoning. Preserve failed/rejected grader attempts and repair re-reviews too.
- Report parent active minutes and grader wall minutes separately. Also report **sum of worker intervals** (after merging overlaps within each actor/session) and **union elapsed intervals** across actors. Concurrent Codex and Opus work contributes to worker-minutes for both, but only once to union elapsed minutes. Label both measures; never add worker-minutes to union elapsed minutes or call either human minutes. Retain raw interval rows so overlap can be checked.
- For each cap’s identical nine-item cohort, the review-cost numerator includes all observed review/repair effort, including rejected and failed deliveries. Divide each labeled numerator by the number of accepted deliveries in that cohort. With zero accepted deliveries the rate is undefined, not zero or a successful rate. Incomplete timing yields a labeled partial observation, not a complete comparison. Keep qualification review intervals separate from capacity-cohort intervals so the same review is never counted twice.

These timings cannot establish manual-work savings. Report human minutes=unknown and manual baseline=unknown until independently measured; no numerical manual estimate is authorized here.

Restore tracked dev cap1 bytes, render/check the environment without extending expiry, restore prior timers only as authorized, retain required guards, and check empty campaign queue/zero leases. Keep final qualified routing only if its operational review passed; otherwise restore the recorded prior route/table pair. At full campaign closure restore day before dev expiry as IMPLEMENTATION requires. Publish cap rows even if one cannot complete; three-backend qualification/participation and pooled-code limitations remain explicit.

## Source audit

Read source only: ADR-0062; `hearth/localwork/service.py` profile, selection and verdict code; `hearth/toolsurface/local_work.py` API signatures; `hearth/delivery/procedures.py`; `fleet/bankedfire_linux.py` wrapper parser/idempotency/caps; `tools/ops/host_config.py`; `tools/ops/hearth_env.py`; `host/omen-linux/systemd/bankedfire-drain.{service,timer}`; lab-rnd `research/{cli,delivery_records}.py`; staged nine wrapper metadata and admission/arbiter open JSON for request construction. No sealed brief/bar/output or candidate was opened. Draft files were parsed offline; none was applied.
