# FX99 Hermes implementation — 2026-09-19

Useful artifacts: a pinned SSH/tmux Hermes controller using shared AM4 inference;
a tested, unmerged fleet-capacity correction produced through governed local work.

Current outcome (22:27 UTC): controller, review-first per-run builder routing,
and Codex-authored capacity correction implemented. **Local coding qualification
failed**: neither builder produced required artifacts. Child receipt is failed;
further local experiments await Derek's retry choice. Real compression remains
unqualified. Final regression: 286 passed / 86 subtests. Production door OK,
AM4 native PID unchanged; no B70 inference was used. FX99's resident model changed
outside this workflow's recorded AM4 calls; do not assert a static fleet.

Current reports:
- artifacts/hermes-fx99/qualification/CONTROLLER-REPORT.md
- artifacts/hermes-fx99/qualification/CAPACITY-REPORT.md

The original checkout and main branches are untouched. This isolated branch
contains the implementation candidate; no external push or deployment of the
capacity catalog to the original checkout was performed.

## Approved continuation (21:37:44 UTC)

Replacement scope: everyday 128k controller; both builders share existing AM4
Dense27B via per-run `am4-shared-27b`, never global runner rewrites. Mandatory
manual promotion; no automatic external harvest/push. Automatic model rotation,
256k qualification and cross-host MemSplice recovery are explicit follow-ups.
Receipt: br-20260919-214346-27a442f3. CPU preparation ceiling 22:07:44 UTC;
fresh 90-minute live window begins only after preparation passes. Live deadlines:
useful artifact +20 min, candidate +65 min, final25 review/accounting/restoration.

Deployment baseline: copied conductor SHA256
e320f289cb20ea7dea90fd9db2822fc897cb75301df57e5a08a2cd25425eb0a5;
both worker sources SHA256
3496735cc866e85c7c211a3352aac875c1e773b235d04fa86b9c85d14e113e69.
Remote source under fleet/hermes/remote is a deployment copy, not a claim that
the isolated repository is the conductor's own source checkout. Deployment
refuses changed hashes and retains timestamp-labelled backups. Both original
runner.json hashes are recorded outside model scope in review-deployment.json.

Kernel ledger fix uses OS advisory locking across initialization/append/reindex;
75 events from three independent processes have verified byte offsets. The
production gateway still has old code loaded, so Hermes uses its own canonical
kernel stream under the operator state directory until both writers can be
upgraded together. No old ledger was rebuilt or silently merged. This deliberate
isolation avoids restarting the production gateway during this qualification.

Historical notes below describe the earlier window, not this continuation.

Continuation preparation passed at 21:55:48 UTC: 182 tests/36 subtests in the
broader source regression and 28 current controller/facade/policy/process-lock
tests. Both trusted worker-side passive preset checks passed; original runner
hashes unchanged. Idle conductor updated/restarted; native AM4 model not
restarted. FX99 qwen2.5:7b residency remained unchanged. Logon startup registered
for the current user (requires login, not an unattended boot service).

Fresh live qualification starts **21:56 UTC**, ceiling **23:26 UTC**; useful new
artifact cutoff **22:16**, integrated-candidate cutoff **23:01**, final25 reserved.
Initial farmer main: 303a2f0f7c5cedf192e1f8642d7984822415d742.
The actual task/acceptance is fleet/hermes/capacity-build.md; Hermes must create
and execute its own receipt and return its plan ID. First dispatch has a
five-minute external ceiling; no unbounded framework retries.

Implementation began 16:51:35 UTC. CPU-only preparation checkpoint: 17:21:35 UTC.
Ask if preparation is not ready then; do not turn preparation into an unbounded
experiment. The separately approved live qualification is capped at 90 minutes:
first useful source note by +20, code candidate by +65, final 25 minutes reserved
for source review, accounting, and restoration. Latest useful live start is set
only after CPU integration passes and the fleet is available.

Working branch: agent/hermes-fx99-20260919. Upstream baseline:
f672a13f7fc9259ce540a4613be927970a12a06a. Selected dirty prerequisites are copied
byte-for-byte from the user's checkout and recorded in the baseline commit;
they are not claimed as new Hermes work. Original source remains untouched.
Receipt: br-20260919-165548-b1531f28.

Invariants: no FX99 GPU/model displacement; OMEN's eight production slots stay;
no cloud fallback; AM4 primary/compression/builders share one physical serving
slot, not three; no automatic candidate merge/push/deploy; no credential-bearing
files exposed to model tools; no knowledge projection rebuilds.

Status: controller read-only source work qualified; delegated build held pending
an explicit review-only conductor gate. Windows Hermes installation is preserved.

Live qualification began 17:10 UTC (10:10 PDT), ceiling 18:40 UTC. Source note
due 17:30; candidate cutoff 18:15; review/restoration 18:15–18:40. CPU integration
passed 50 tests / 50 subtests. An unrelated full-launcher test lacks this Python
environment's opentelemetry dependency (media providers); controller-only imports
and real MCP initialization passed. MCP extra is pinned to 1.28.1 after an
unbounded optional dependency selected incompatible MCP 2.0 for the probe.

No Windows firewall permissions: tunnel direction reversed using OMEN's trusted
SSH to FX99. Both ends bind 127.0.0.1:8712; no new public MCP listener. The
restricted listener reuses HEARTH kernel/guards/ledger/providers without ops
timers or duplicate execution workers. Production gateway is untouched.

## Useful result and dispatch blocker

First real source note completed at 17:11:44 UTC, 99.966 seconds after launch;
raw answer and independent corrections are under artifacts/hermes-fx99/first-task.
No assisted recovery. Session-reported 8 API calls, 14,869 input / 1,783 output /
56,309 cache-read tokens; these are not reconstructed per-attempt receipts.

At 17:17 UTC, source inspection found conductor_maf.py line 437 unconditionally
calls _promote; lines 479–521 fast-forward the winning branch to farmer-repo/main
when its assay/risk checks pass. No per-request manual-promotion setting exists.
Prompting a builder to leave QUESTION.md is not an enforceable review gate.
Therefore no build was dispatched, HERMES_LOCAL_BUILDERS_QUALIFIED stays 0,
and the existing conductor was not edited or restarted. Approval/coordination
is needed to add a per-run review-only gate before the promised candidate run.

Builder 3's temporary AM4 runner configuration was restored from its byte backup
(SHA256 70179c9ae10152c467e655785095631c86c049ffe52c26e90b9d6ed10fc7a8ab).
Its temporary credential copy is removed. Builder 2 was never repointed. Read-only
source copies (baseline b2b7341, 6,666,240-byte tar) remain at
/home/claude/hermes-capacity-source-20260919 on both builders for the approved run.

Remaining acceptance: review-only build dispatch and tested integrated capacity
candidate; real compaction and interrupt/resume; live stream-disconnect/admission
stress; idempotent physical-attempt import; named AM4 recovery ownership; final
supervised startup/reconnect qualification. These are not claimed complete.

## Current entrypoint

`ssh -t fx99 tmux new-session -A -s hermes-fleet /home/derek/.local/bin/hermes-fleet`

FX99 config/session state: /home/derek/.config/hermes-fleet; pinned install:
/home/derek/.local/share/hermes-fleet. Model and compression use the existing
AM4 alias only, 131072 context, compression threshold .85. Native terminal/file
tools are excluded; only the bounded HEARTH MCP toolset is selected. Model
failure does not start a GPU seat or select a cloud model.

OMEN listener: 127.0.0.1:8712. Current launcher is fleet/hermes/start-controller.ps1;
it refuses duplicate port ownership. Reverse tunnel is supervised, but logon/reboot
startup and listener crash recovery are not yet qualified. Credential registry
and logs live outside the model-readable source scope at
C:/Users/derek/.hermes-fleet. The production gateway and B70 model are untouched.

AM4 facade backup: /home/derek/.config/am4-fleet/hermes-backup-20260919. Only
facade source, Dense alias port, and a dedicated credential-file setting changed;
the manually launched :18090 model PID 1738436 and inactive :18084 service were
not restarted. Physical-attempt SQLite capture is installed for subsequent
Hermes-key requests; its importer/schema extension is tested but not yet run
against a live outbox. Correlation gaps are explicit, usage is never invented.
