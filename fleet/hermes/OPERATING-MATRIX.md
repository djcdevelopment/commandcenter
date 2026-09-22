# Hermes local-fleet operating map

Continuation and existing framework-selector source locations: [HANDOFF.md](HANDOFF.md).
The 16k worker shape below is a deployment choice, not a B70 hardware limit;
previous SYCL Dense measurements are linked in the handoff.

Placement: FX99 runs Hermes and stores sessions. Its model and compression use
AM4's existing Dense 27B on the 4070 Ti + 5070: **128k, one shared physical slot**.
OMEN's B70 pair remains the worker resource; Hermes does not load a model there.
The existing OMEN serving shape is **eight 16k slots**, not eight 128k slots.

| Layer | Access and decisions | Observations and feedback | Boundary |
|---|---|---|---|
| Hermes | `hermes-fleet` on FX99; bounded HEARTH reads and receipt-based dispatch | Live capacity takes precedence over historical knowledge; review candidate artifacts | No shell, model rotation, arbitrary backend or automatic merge through this profile |
| HEARTH | Authenticated MCP; knowledge queries, advisory scheduler, native worker admission, accounted `local_generate` | Model identity, per-slot context, occupancy, maintenance windows, execution receipts and outcome evidence | Worker model pinned to resident `omen-arc`; reserve output context; refuse overflow/unavailable route; no fallback |
| MechNet / conductor | Explicit `cc-builder-2` and/or `cc-builder-3`, immutable per-run `omen-resident-hearth` preset | Candidate branches, changed deliverables, run logs, deadlines, static assay and manual review | CPU work and file edits stay in existing builder VMs; model inference goes to OMEN; global runner defaults unchanged |
| DeepAgents | Existing separate framework; not connected as a new Hermes dispatch option in this rollout | Existing framework receipts may inform later selection | Do not describe it as a qualified interchangeable executor yet |

The controller reaches AM4 through `192.168.12.233:8090/v1`, alias
`am4-dense-27b`. Its restricted HEARTH tunnel reaches OMEN loopback `8712/mcp`.
Builders reach `http://omen.mshome.net:8083/hermes-worker/mcp` with individual
worker credentials. The upstream OMEN model credential stays on OMEN.

Choose bounded edits, extraction, drafts and CPU builds for current 16k workers.
Split larger work before dispatch. Wait or refuse when native capacity is busy;
do not count aliases or theoretical context as extra hardware. Keep model
rotation, MemSplice migration and 256k work as separately authorized follow-ups.
The B70s are available for worker inference, not necessarily empty or at zero
VRAM residency. This rollout does not qualify SYCL throughput or eliminate
previously observed Windows shared-memory pressure.

Learning here means accepted-artifact outcomes and measured route evidence
feeding later decisions—not weight training or permission to self-promote.

Source pointers: `fx99_cli.py`, `remote/worker/runner_presets.py`,
`remote/worker/agent_hearth.py`, `remote/conductor/hermes_run_policy.py`,
`hearth/kernel/hermes_worker.py`, `hearth/toolsurface/scheduler.py`, and
`hearth/execution/service.py`. This guide is Codex-authored integration guidance;
qualification evidence and any model-authored artifacts are recorded separately.
