# JEV scheduler / on-demand Hermes reviewer

Latest status, 2026-09-21: **one clean automatic build/review/release cycle;
code acceptance still requires independent review**. The status command is
deployed on FX99 after Codex corrections to a real OMEN candidate. Hermes first
returned a false PASS, then correctly rejected that same candidate when given
executed evidence. CPU scheduling remains active for Derek's overnight work;
AM4 is unloaded between reviews. The new required-evidence gate is saved but
not loaded because a tool-policy restriction blocked the gateway restart.
Read the [overnight work log and current state](OVERNIGHT-20260921.md),
[human/agent operations page](reports/fleet-operations-20260921.html),
[source-backed execution-route capability matrix](ROUTE-CAPABILITIES-20260921.md),
[short-review calibration](REVIEW-CALIBRATION-20260921.md),
[count-correction handoff](HANDOFF-COUNTS-20260921.md), and
[first-loop retrospective](HANDOFF-20260921.md).

## What runs where

| Component | Placement and authority | GPU use |
| --- | --- | --- |
| JEV evaluation | TypeSafe `jev-1.13.0`; allowlisted task/profile summaries and numeric historical outcomes | Remote API, no fleet reservation |
| Scheduler process | FX99 CPU; prepares candidates and submits only approved local queue IDs | None |
| HEARTH admission | Separate OMEN loopback `8713/mcp`; existing operator contracts, authority checks, native worker admission, shared production coordination DB | None for scheduling |
| MechNet build | Explicit `cc-builder-2`, `omen-resident-hearth`, one attempt, manual promotion | Existing OMEN resident inference, native 16k per slot |
| Hermes review | Pinned Hermes on FX99; fixed AM4 Dense 27B endpoint, native 128k / one slot | Both AM4 NVIDIA cards only during the owned review |

Existing production `8710`, restricted Hermes/worker `8712`, the worker's
global presets, and OMEN's resident model configuration are not replaced.
Port 8713 is forwarded over a separate OMEN-to-FX99 SSH reverse tunnel.
The pilot only admits one builder profile. It is **not yet a three-route
job-shop optimizer**, a DeepAgents integration, a SYCL benchmark, or a MemSplice
demonstration. Existing operator catalogs describe more than this adapter admits.

## The useful artifact / qualification budget

The real smoke task is a local-builder-authored update of
`fleet/hermes/fleet_status_format.py`: distinguish a healthy CPU scheduler from
an intentionally unloaded Hermes model, retain old output, and show bounded
active-work / pending-review / wait fields. Hermes must deliver an independent
saved review. Candidate files stay unpromoted pending human acceptance.

The first live window started **05:59:26 UTC, September 21**:

- Dispatch latest useful start: **06:04:26**.
- First useful worker file target: **06:06:26**.
- Stop generation and review by **06:21:26**.
- Delivery/restoration deadline: **06:29:26**.

One blocking edge ends the lap. A materially different retry needs Derek's
decision; do not lower confidence gates merely to get a successful screenshot.
Derek approved one clearer request after the first lap stopped. The retry used
the same gates; all inference finished before 06:21 and restoration was verified
by 06:23:37. Two JEV calls total, not a threshold-search loop.

## Paths and entry points

Source: `C:\work\commandcenter-jev-scheduler`, branch
`agent/jev-scheduler-20260921`, based on Hermes commit `d69d868`.
The existing operator package/contracts/registries were reused from
`operator/program` commit `459da39a0f63705e8f5f609be01177a08772bb67`.
They are reused implementation, not newly model-authored work.

Private OMEN state: `C:\Users\derek\.fleet-scheduler` (restricted Windows ACL).
Queue: `queue/queue.sqlite`; local source-packed task briefs never go to JEV.
Admission, decisions, candidate captures, and reviews live under `queue/<task-id>`.
Operator snapshots/history have their own `operator/` root; receipts, kernel
ledger, execution stream, and execution artifacts are separate from production.

FX99 source: `/home/derek/.local/share/fleet-scheduler`.
Private state: `/home/derek/.local/state/fleet-scheduler`.
Commands: `fleet-scheduler run-once`, `fleet-scheduler serve`,
`fleet-scheduler status --json` under `/home/derek/.local/bin`.
The installed user unit is `fleet-scheduler.service`; installation alone does
not start or enable it. A live hold must be reviewed before enabling unattended use.
`fleet-scheduler status` now shows observed systemd/PID liveness separately from
cached cycle fields and snapshot age. It is deployed on FX99. `status --json`
preserves valid existing JSON output; that document remains a cached observation,
not proof of process liveness or current hardware residency.

Credentials: `/home/derek/.config/fleet-scheduler`, directory `0700`; key files
`0600`. `typesafe.key` is reread on every HTTP attempt. Replace that private file
atomically for rotation; do not paste credentials into a prompt or commit them.
`hearth.key` can only prepare/select/exchange reviews. `reviewer.key` grants only
bounded repository reads and git metadata through the dedicated gateway.

AM4 fixed helper: `/home/derek/.local/share/fleet-scheduler/review_seat.py`.
Private captured baseline and process identity:
`/home/derek/.config/fleet-review-seat`. The baseline includes sensitive process
environment; never display, copy to reports, or commit it.

`fleet-scheduler budget` and `budget --json` provide a read-only breakdown of
known-usage estimates, uncertain reservations, booked total and remaining cap.
They do not alter spending limits or reconcile invoices. Missing/inconsistent
ledger values remain unknown. See overnight cycle 2 for authorship and evidence.

`fleet-scheduler quality` / `quality --json` now reads the operator-curated
`/home/derek/.local/state/fleet-scheduler/quality-history.json`. The repository's
`fleet/jev/quality-history.json` records classifications and evidence hashes.
It is a selected task subset, not a complete benchmark, inferred correctness,
or automatic training. `authored` means tasks reaching the worker; it excludes
`not_run`, and does not mean accepted output. Assisted delivery is separate from
unchanged implementation delivery. The present subset contains six tasks:
four reached a worker (one unchanged, three assisted); two did not run there.

The installed FX99 scheduler prepends only a fixed numeric quality line to the
matching profile's existing `recent_outcomes`. Descriptions, task IDs and source
evidence are not sent to JEV. Missing/malformed history is unavailable, not zero.
The next decision records the local history hash for audit. Request construction
was verified in the installed runtime; a later live decision using this new
feedback has not yet been sampled. Confidence/ambiguity gates and manual
promotion remain unchanged. Recurate/disable this history when qualifying a new
model, runner or context regime; do not transfer these labels automatically.

Local `enqueue()` now runs the same pure `_delegation_brief` guard before storing
a task or requesting paid evaluation. Source examples containing Windows paths
can be refused just like instructions; author repo-relative briefs, do not
silently rewrite or encode around the guard. The known cycle-10 brief was refused
with no queue change. This local submission change required no gateway restart.

The OMEN-side capacity command is now runnable from this worktree:

```powershell
Set-Location C:/work/commandcenter-jev-scheduler
& C:/work/commandcenter/fleet-worker-node/.venv-omen/Scripts/python.exe -m fleet.jev.capacity
# Machine-readable equivalent (performs a fresh observation):
& C:/work/commandcenter/fleet-worker-node/.venv-omen/Scripts/python.exe -m fleet.jev.capacity --json
```

For the combined execution-route/harness registry, qualified pilot limits and
fresh capacity snapshot, run `python -m fleet.jev.operations_page [--out NEW.html]`
with the same interpreter/worktree. Existing output files are refused. The
[saved operations page](reports/fleet-operations-20260921.html) is dated, not live.
Agents can read `script#fleet-operations` (complete parsed registries, source
hashes, normalized declared-status rows, pilot configuration and capacity) and
`script#fleet-capacity` (the original capacity document). Neither is an admission
decision or permission to load a model. The page and normalizer are Codex fallback
work: JEV selected the task, but the delegated brief guard refused a Windows path
in an illustrative registry record before any worker execution. See cycle 10.

It observes the native resident worker, existing `E:/work/b70tools` binary and
AM4 `nvidia-smi` in parallel. It does not load models, change scheduling, or call
JEV. Each source carries its acquisition window and a 30-second TTL; reports are
observations, not reservations. B70 adapter committed memory stays separate from
DXGI observer-process usage, and B70 free memory remains unknown. NVIDIA free
memory comes directly from the driver, not total-minus-used arithmetic.

Latest output and bounded raw observer recordings are retained privately under
`C:/Users/derek/.fleet-scheduler/capacity/`. Each call retains a new recording;
this is an on-demand command, not a new background sampler. Unavailable sources
are reported separately. No new-model-placement decision is authorized by this
report, and it is **not yet wired into JEV's decision inputs**. See overnight
cycle 4 for local-builder authorship, direct corrections and Hermes findings.

For a human/agent-readable page, run `python -m fleet.jev.capacity_page` with the
same OMEN interpreter. It captures fresh observations and saves a new timestamped
HTML file under the private capacity directory. `--out PATH` chooses another
new file; an existing file is refused. The page includes the complete original
JSON in `script#fleet-capacity`, source timestamps/TTLs and explicit stale-snapshot
warnings. No external assets or executable JavaScript are needed. A committed
[example snapshot](reports/capacity-20260921.html) was captured at 08:52:13 UTC
on September 21; it is historical evidence, not current capacity.

`run_hermes(..., reasoning='low')` is now an explicit authoring experiment option;
the default and all normal review calls remain `none`. The first bounded `low`
renderer request timed out without a saved assistant answer. It is **not qualified
as an improved default**. Both the CLI flag and provider wire override are set;
the metadata reports the requested setting, not proof of hidden model behavior.

## Decision and recovery behavior

Pending gateway maintenance: review-packet source now reads baseline files from
the task envelope's full `base_commit` through bounded read-only Git commands,
not the current worktree. It records commit/path/presence/SHA256 in private
`baseline.json`, labels genuinely new files explicitly, and refuses unavailable
commits rather than treating them as empty files. This was exercised manually on
a real prior task and used for a saved Hermes review. The already-running gateway
has **not** loaded this change; its restart restriction has not been bypassed.
Normal daemon reviews must not be claimed commit-bound until allowed maintenance
loads and verifies that version. See overnight cycle 5 for evidence and limits.

Live on FX99: `run_hermes()` now makes recognized execution-evidence sections
failure-first. It preserves every failing row, all non-case evidence fields and
candidate source; passing rows retain their names/results. An explicit summary
lists the failing names and counts. Unrecognized input or a non-smaller result is
left unchanged. Every run saves private `packet-original.md`, `packet.md` and
`packet-evidence.json` with byte counts and hashes. The full original remains the
audit record; no evidence was deleted to make a verdict look better. This client
change was deployed independently of the still-pending gateway changes.

The actual qualification packet shrank from 27,284 to 16,070 bytes. The observed
review used no lookup tools, but still made factual mistakes in its explanation.
Use direct check results as the authority for acceptance; the prose review is
advisory. See overnight cycle 6 for authorship and the exact deployment backup.

Only an operator can enqueue an approved source-packed task. JEV receives an
explicit field allowlist, never raw code, diffs, logs, credentials, commands,
repository paths or the entire gateway response. Existing knowledge is read,
not rebuilt. Historical successful inference is labeled as such, not patch
acceptance. Local outcome counts are evidence feedback, not weight training.

Candidates must pass local schema/identity/authority/deadline/context/native
readiness checks before JEV sees them. JEV supplies suitability Score and
ambiguity Noul answers. The pilot gates are fit >= 2/3, confidence >= 0.60 and
ambiguity <= 0.25. Confidence describes the score distribution; it is **not**
the probability of producing a correct patch. These thresholds are provisional
and currently uncalibrated.

The scheduler reserves worst-case API charges before each request. The pilot
allows at most 100 requests and USD 0.10 total, persisted across restarts, not an
automatically resetting daily budget. HTTP timeout is ten seconds; only one
short 429/529 retry is allowed. Authentication/transport errors hold execution.
Repeated identical held inputs do not cause repeated paid calls.
The cache identity includes the actual question payload; changing the rubric
does not silently reuse an old decision. Decisions are retained by request hash.

One task may be dispatching/running/reviewing. The queue stores the receipt
before external dispatch. An interrupted dispatch is held for reconciliation,
not guessed and duplicated. Failed task/review states block new work. A client
exception writes `HOLD`; inspect its evidence before removing it and restarting.
`STOP` requests an orderly stop after the current bounded lap; neither file is
a promise to instantly interrupt a subprocess or model request.

Hermes receives the actual candidate, original, diff and criteria locally. Its
unedited final assistant message is exported from its new session database.
Model review is not proof; independently inspect the candidate and any claimed
checks. No automatic branch promotion, merge, push or deployment follows review.

The AM4 helper captures the exact existing Dense process argv/env/cwd and GPU
UUIDs once. Start/stop checks its owner, PID start ticks and argv digest. It
refuses another compute owner or an occupied baseline. It never kills a process
just because it happens to listen on the familiar port. Review failures retain
the candidate and expose recovery state. **KV restore is disabled/unqualified**;
session SQLite persistence is not a KV cache and must not be reported as one.
The observed empty-command-line startup race is fixed by recording the argv
actually launched, then verifying process identity after native readiness.
`repair-start-identity` is an operator-only recovery for that exact historical
empty-argv fingerprint; it still requires matching owner, PID start ticks and
the complete captured baseline argv. It is not exposed to JEV.

On an unsuccessful cutover, explicitly release any task-owned review and use
`review_seat.py restore-baseline` on AM4. Verify native health/capacity and GPU
ownership afterward. The helper uses the original argv, not a new guessed model
configuration. The separately installed conductor files have
`.jev-20260921.backup` copies; restoring them also requires an idle conductor and
a check that current files are still this deployment's versions.

## Deployment / source map

`python -m fleet.jev.local init` creates private caller state and builds the
isolated catalog. `python -m fleet.jev.deploy stage` installs only this pilot's
files, refusing unexpected existing targets. `deploy conductor` hash-checks
the two existing policy/dispatcher sources, updates only those while idle, and
restarts that service. `start-omen.ps1` starts hidden dedicated gateway/tunnel
processes without touching existing listeners. These helpers are intentionally
not blind repeatable overwrite commands; inspect a previous deployment first.

`python -m fleet.jev.local enqueue-smoke` enqueues the approved first artifact.
Do not rerun it to manufacture duplicate work. `local inspect` lists queue IDs,
states and receipts without source/secret payloads.

`python -m fleet.jev.local enqueue-counts` prepares the separate, source-packed
correction of the two boolean-accepting count conditions. It explicitly
supersedes the first envelope; it does not promote or replace the old candidate.
Only run it during an approved live window, with the prior hold reviewed.
For this task, `artifacts.py` captures the worker's file, verifies that its AST
matches exactly the reviewed two-condition correction, and executes four
bounded formatter cases. The saved `execution-evidence.json` goes into Hermes's
packet with expected and actual outputs and independent-executor attribution.
Unexpected source is **INCONCLUSIVE**, not executable permission. This is a
task-specific check, not a generic Python sandbox or automatic acceptance gate.

Other tasks may supply independently produced `execution-evidence.json` with
`candidate_files_sha256` matching every captured file's UTF-8 source hash. The
packet rejects mismatched/oversized evidence. New source also supports the task
flag `review_requires_execution_evidence: true`, leaving review pending until
that artifact exists, but **the currently running gateway has not loaded this
gate**; see the overnight log's policy-restricted restart note.

- `policy.py`, `client.py`: exact cloud allowlist, questions, gates, spend.
- `cli.py`: FX99 loop, material-change detection, process lock, hold/status.
- `hearth/toolsurface/jev_scheduler.py`: queue, operator validation, dispatch,
  candidate/review exchange; no cloud call in the gateway.
- `artifacts.py`: bounded real builder capture and fixed AM4 helper transport.
- `review.py`, `review_seat.py`: bounded Hermes run and owned model lifecycle.
- Existing `task_lane.py` and conductor policy retain explicit single-builder,
  manual-review behavior for both Hermes and JEV provenance.

Primary API references: [introduction](https://docs.typesafe.ai/introduction),
[API](https://docs.typesafe.ai/api), [models](https://docs.typesafe.ai/models),
[confidence](https://docs.typesafe.ai/confidence).
