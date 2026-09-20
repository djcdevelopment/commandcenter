# AM4 controller / OMEN workers

Start 2026-09-20 02:41:24 UTC; delivery ceiling 03:41:24. Latest useful dispatch
02:51:24; first delegated artifact 02:56:24; candidate 03:26:24; final 15 minutes
for review, accounting and restoration. Parent: br-20260920-024212-1196097b.

Deliver a Hermes-dispatched operating matrix, reviewed fleet-status command and
attributed worker route. Test counts alone are not delivery. Baseline native PIDs:
AM4 1738436, OMEN 23268. No serving-shape changes, cloud fallback or GPU claims.
Preserve global runner defaults and manual promotion; no external push. Continue
without questions while useful progress stays in scope; one focused retry only.

## Delivered

On FX99: `hermes-fleet` starts the governed controller;
`hermes-fleet fleet-status [--json]` makes bounded read-only observations, no
inference. At 03:20 UTC it returned in 1.99 s: AM4 ready 131072/one slot, OMEN
ready 16384/eight slots, zero OMEN busy, no open receipt among the latest ten.
AM4 occupancy remains **unknown**, not fabricated as idle.

- [Operating matrix](OPERATING-MATRIX.md): Codex-authored integration map.
- [Daily checklist](OPERATIONS-CHECKLIST.md): reviewed OMEN draft, corrected by Codex.
- `fleet_status_format.py`: reviewed OMEN candidate, corrected by Codex; live
  observation collection and CLI integration are Codex work.
- Original model artifacts, local candidate commits, live auth/admission checks,
  final state and physical accounting: `artifacts/hermes-fx99/omen-workers/`.

The second real job needed no intervention between Hermes dispatch and artifact
delivery. Controller inference on AM4 overlapped worker inference on OMEN by
7.655 seconds; separate hardware is demonstrated, not just configured.

Final focused regression: **199 passed, 61 subtests passed** (13.31 s), covering
controller/worker policy, caller isolation, native admission, manual promotion,
task lane, receipt semantics, actual gateway HTTP and the Caddy VM proxy.
Production :8710 and restricted :8712 health checks both passed after delivery.

## Outcome and honest qualification

| Receipt | Outcome | Evidence |
|---|---|---|
| `br-20260920-025127-19eb2ef8` | Failed | Hermes created receipt; preflight environment/callback failures, then assisted dispatch hit a nested-event-loop error. No worker inference or matrix. |
| `br-20260920-030141-e036eff8` | Assisted delivery | Hermes dispatched one builder. Codex repaired singleton graph typing before generation. OMEN wrote formatter on its first action; two turns, 16.602 s total. Candidate `b764b019f4c4be3ba4eab7843a62b6dac00d77e4`. |
| `br-20260920-031116-00f63902` | End-to-end delivery | Hermes dispatched, OMEN wrote checklist first action, then finished; two turns, 12.642 s. Candidate `dc278bab96e469eeded0a28d7c4390cee48d5f32`. |

Raw formatter failed bool/nested-type/stale-capacity/safe-label requirements;
the checklist misstated the command and stop rules. Corrected integrated versions
are accepted, not the raw candidates. Assay's inherited 162-test suite was NOT
treated as validation of new-file behavior. No automatic promotion or external
harvest occurred. Candidate pushes went only to the existing local farmer repo.

The first-quarter artifact target was missed. Useful Codex guide at ~03:01;
first model file at 03:04:31; functional CLI at 03:07; second model file at
03:11:37. Setup took too much of this window; delivery recovered inside the
original ceiling, and no additional generation was run after useful artifacts.

The parent experiment receipt is closed **failed against its strict delegated-
matrix criterion**, not relabeled fully successful: the matrix was completed by
Codex, while Hermes delegated the formatter and checklist. The usable deployment
and both corrected deliveries remain live; this distinction is about the promised
experiment evidence, not a rollback of working functionality.

Physical costs for this rollout: **20 AM4 calls**, 112787 input / 4418 output
tokens; **4 OMEN calls**, 6874 input / 1841 output tokens. Input is measured
request usage, not unique uncached context. Controller receipts were imported
idempotently from AM4's physical outbox (all 20 replay checks duplicate).
OMEN calls have original execution job/invocation IDs in this isolated checkout's
`hearth/var/execution/events.ndjson`; they were not reconstructed from totals or
imported a second time. No dollar-savings claim.

## Admission, ownership, limits

Individual worker keys expose only route status and pinned resident generation.
Controller cannot generate directly, shell, rotate models, or auto-promote.
Fresh native health/model/props/slots, output-reserved template token counting,
bounded waits, supervised job deadlines, immutable per-run routes and explicit
one/two-builder selection are enforced. Unknown occupancy or unreadable tenancy
fails closed. Live unknown-key, forbidden worker reads, AM4 override and actual
oversized token-template refusals passed without running generation.

The isolated listener uses **production's coordination SQLite and rotation-window
files**, not a private empty tenancy store. Kernel and execution event streams
remain separately owned to avoid mixing live writer versions. Historical
knowledge and global aggregate dashboards may not include this isolated worker
stream automatically; receipt-linked evidence is authoritative for this rollout.
Production gateway, timers, models and global builder defaults were not replaced.

Current limitations: resident OMEN is the existing **Vulkan** serving shape, not a
new SYCL benchmark. This is qualification for small reviewed jobs, not arbitrary
autonomous coding. No spill-free/placement certificate, 128k worker proof, 256k
qualification, compression-at-capacity test, cross-host thinking splice or new
DeepAgents executor. VM worker shell actions use the existing trust boundary,
not a newly created OS sandbox. OMEN-side startup still requires the configured
Windows user logon; FX99 being always-on does not remove that dependency.

## Deployment and rollback

Runtime sources: this checkout's `fleet/hermes`; deployed copies on FX99 under
`/home/derek/.local/share/hermes-fleet`, builders under
`/home/claude/fleet-worker-node/scripts`, conductor under
`/home/claude/work/commandcenter/scripts`. Per-run preset is
`runner-presets/omen-resident-hearth.json`; default `runner.json` hashes are
unchanged on both builders. Hash-guarded initial deployment and final-state
hashes are recorded; do not blindly rerun the baseline-only installer on a
changed deployment.

OMEN Caddy's existing :8083 listener received only `/hermes-worker/mcp`, forwarding
the individual worker header to loopback :8712. No upstream bearer leaves OMEN.
Only `hearth/etc/caddy/Caddyfile` was edited in the original dirty checkout;
other user source changes remain untouched. The original Caddy file was validated
and hot-reloaded. Dedicated old AM4 worker keys now get HTTP 401; Hermes's
controller key remains live. Revocation backup is
`/home/derek/.config/am4-fleet/fleet-callers.json.pre-omen-20260920.backup`.

To hold the new lane, revoke only `hermes-cc-builder-2` / `hermes-cc-builder-3`
in the private caller registry using `hearth.callers.callerctl`, then reload the
restricted :8712 listener after its jobs drain. For full rollback, restore the
specific versioned source backups only after checking current hashes and idle
jobs; remove only the new Caddy handler and validate/reload Caddy. Preserve all
other registry entries and Caddy edits. FX99 wrapper backup suffix is
`.omen-status-20260920.backup`; worker/conductor baseline backups use
`.omen-workers-20260920.backup` (conductor graph baseline:
`.omen-workers-fix-20260920.backup`). Later `.omen-final-*` backups are intermediate,
not the original baseline. Restoring AM4 worker access is an explicit reversal,
not needed to disable OMEN workers. No model restart is part of rollback.

Final native IDs: AM4 1738436, OMEN 23268 (same baseline processes).
Both farmer main refs retain July 3 reflogs; production ontology main remains
`97d23883d7435f4138e2bc96a313b25fce419b08`, legacy farmer main
`303a2f0f7c5cedf192e1f8642d7984822415d742`.
