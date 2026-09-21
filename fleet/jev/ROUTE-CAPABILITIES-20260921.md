# Fleet execution capabilities — 2026-09-21

Hearth, DeepAgents and MechNet are composable layers, not three alternative GPU
pools. A DeepAgents harness can use Hearth inference; Hearth can dispatch a
MechNet workspace. Caller identity, admitted route and current capacity still
govern what actually runs.

| Layer | Access points | Useful controls | Observation and feedback |
| --- | --- | --- | --- |
| Hearth: gateway and admission | `local_generate`, `plan_execution`; build-request tools | Backend pin, task-family routing, scoped `files` pack, output/time limits; caller-specific authority. A pin does not override admission. | Execution ledger, backend/routing metadata, task/job IDs, readiness. Inference success measures execution, not correctness. |
| DeepAgents: agent harness | Registry `deepagents_hearth`: `create_deep_agent` in `C:/work/deepagents-poc`. Separate named harness: `python -m poc.run_flash_dense agent <root>`. | Scoped filesystem tools; the named harness declares `evidence-worker` and `independent-verifier` subagents. These are declared capabilities, not new dispatch authority. | Harness artifacts and verifier reports can feed review. Control-plane qualification and truthful provenance are needed before treating them as scheduler feedback. |
| MechNet: delegated execution | Research: `submit_task` → `task_status(plan_id)`. Build: `create_build_request` → `execute_build_request` → `update_build_request` → `close_build_request`. | Standalone brief, explicit deliverables, builder/runner selection, bounded attempt lifetime, isolated candidate and manual promotion. | Plan state, worker result, candidate commit, missing artifacts, receipt criteria/evidence. A completed receipt is not independent proof that a patch is correct. |

Sources: [loop registry](../../hearth/etc/loops.toml),
[harness registry](../../hearth/etc/harnesses.toml),
`hearth/toolsurface/{inference,task_lane,build_requests}.py`.

## What the current pilot actually admits

`hearth/toolsurface/jev_scheduler.py` fixes `TARGET=mechnet_build`,
`BUILDER=cc-builder-2`, `PROFILE=omen-resident-hearth`. `route()` proposes that
single route; `_prepare()` and `_select()` check native worker capacity and local
authority. JEV evaluates approved task metadata. It does **not** currently select
among all three layers, allocate GPUs, rotate models, or invoke MemSplice.

The September 17 registry declares `deepagents_hearth` **LIVE**, while the named
`deepagents-run-flash-dense` harness is **BUILT NOT DEPLOYED**. Neither label is a
fresh readiness probe. `propose_schedule` is a built-but-not-deployed advisory
CP-SAT path, not this pilot's live allocator.

The older AM4 registry's 4k/18084 and reader/18085 entries are historical evidence.
The current pilot uses the existing OMEN 16k-per-slot worker and on-demand Hermes
on FX99 against AM4 Dense 27B, native 18090, 128k/one slot, both NVIDIA cards.
The reviewer unloads between owned runs; KV reuse has not been demonstrated.
Automatic review exists in `fleet/jev/review.py`; acceptance/promotion remains
manual. Older registry statements that no review has run do not describe these
September 21 pilot runs. See [pilot placement](README.md#what-runs-where).

## Observation, learning and limits

`hearth/etc/operator.toml` separates 300-second planning validity from per-field
freshness: occupancy 30s, readiness 120s, reachability 300s, trial runway 3600s.
`capture()` observes this pilot's native worker, not a complete GPU inventory;
free VRAM remains unknown. The separate capacity command is advisory observation,
not an allocation guarantee or an input already wired into JEV.

`recent_outcomes()` supplies numeric inference success and queue-state counts.
These differ from accepted, correct artifacts. Knowledge projections, receipts
and Hermes findings support later decisions; they are not automatic training or
a demonstrated quality-learning loop. `profiles.toml` limits the scheduler and
read-only reviewer; JEV itself never receives fleet credentials.

**Useful now:** produce a fresh human/agent capacity page with
`python -m fleet.jev.capacity_page`, then assign one source-packed, bounded task
through the already-qualified builder, retaining independent acceptance.

**Concrete gap:** the live scheduler profile describes single-Python-file work;
this Markdown investigation was held at confidence 0.54. Broader task/route
qualification and accurate profile summaries are prerequisites to a three-route
optimizer, not reasons to lower the confidence gate.

Provenance: Codex source-checked correction of an unaccepted OMEN draft; raw model
output is retained in `evidence/20260921-route-local-draft.json`. JEV did not
dispatch this report through MechNet. Registry/source baseline: `b8ed544`.
