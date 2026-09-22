# Hermes local-fleet handoff

> **Historical source:** The later JEV experiment that built on this work is
> closed in the [JEV/Hermes final retrospective](../jev/RETROSPECTIVE-20260921.md).
> Re-check live ownership and capacity before using any operational detail here.

Updated 2026-09-20 UTC. Read this before replaying any historical deployment or
R&D script. Companion: [retrospective](RETROSPECTIVE-20260920.md).

## Objective and working agreement

Use Hermes to produce useful, reviewable work on the local fleet while leaving
enough compute for its workers. The objective is not maximum controller context
or maximum GPU occupancy. Derek wants actual model loads and real prompts;
failure is data. In R&D: no new test files unless explicitly requested, one useful
vertical slice before refinement, stop at the first meaningful edge, and record
"can't answer why" when that is the evidence. Name the artifact and time budget
first. Ask when a consequential change threatens delivery; autonomy is not a
reason to conceal a bad tradeoff.

Past permission to use GPUs does not establish that today's seats are idle.
Observe current ownership before a swap. This handoff is documentation, not an
instruction to restart services or reclaim another session's model.

## Where the work lives

| Location | What it is / how to use it |
|---|---|
| `C:\work\commandcenter-hermes-fx99` | This implementation, branch `agent/hermes-fx99-20260919`. Canonical Hermes source and evidence for this handoff. |
| `fleet/hermes/` | Launcher, deployment copies, worker/conductor adapters, reports and experiment helpers. Start with this file and the [operating map](OPERATING-MATRIX.md). |
| `artifacts/hermes-fx99/` | Original model outputs, candidate runs, accounting, qualification and compact-controller evidence. Do not replace raw failures with edited summaries. |
| `C:\work\commandcenter` | Original production checkout; has unrelated dirty work. Its `knowledge/` contains authored catalogs and derived observations, not one universal current truth. Read `knowledge/README.md`; do not rebuild projections just to inspect them. |
| `C:\work\worktrees\commandcenter\operator-program` | Separate `operator/program` branch; observed HEAD `459da39`. Existing operator/route-selection implementation. **Not included in this Hermes branch publication.** |
| `C:\work\deepagents-poc` | DeepAgents implementation referenced by that operator adapter, including its own venv. Not newly integrated into the deployed Hermes profile. |
| `C:\work\memsplice` | Memory/KV-transfer research. Not a live automatic Hermes continuation path. Read its own instructions and evidence before using it. |
| `E:\work\b70tools` | Hardware observation tools Derek built because Windows counters can mislead. Use alongside serving logs and native memory budgets. |
| `E:\work\hermes` | Earlier, unversioned Windows/WSL local-Hermes launcher and paired-context artifacts. Its B70 `:8096` setup is historical, not the current default fleet topology. |

### The framework selector the user remembered

In the `operator-program` worktree, read `START-HERE.md` and `AGENTS.md` first.
Then use its inspection/identity contract. Its source already contains:

- `hearth/operator/catalog.py`, `inspection.py`, `authority.py`: capabilities,
  capacity snapshots and caller authority.
- `proposal.py`, `validate.py`, `execute.py`: route proposals, independent
  validation and execution, including direct HEARTH, DeepAgents and MechNet
  adapters. Some adapters are narrow inventory-task implementations; their
  presence is not proof of general-purpose dispatch for every task.
- `artifacts.py`, `history.py`, `replay.py`, `learn.py`: artifact/outcome history
  and feedback. Reuse and inspect these before writing another selector.
- `hearth/etc/harnesses.toml`, `loops.toml`, `authority-map.toml`, `operator.toml`
  and `knowledge/capability_catalog.json`: declared routes and contracts.

Documentation and source are not synchronized proof of deployment: its entry
page still labels later gates absent while implementations exist. During this
handoff, read-only `inspect --json` refused an expired September 18 snapshot;
`whoami` reported no identity. We did not refresh its state, run its executors,
or certify those routes live. For actual operator work, follow that worktree's
freshness/identity procedure; do not bypass it or reuse a stale planning snapshot.

## Last observed deployment, not a promise of current availability

Passive verification: **2026-09-20 04:31:24 UTC**. FX99 fleet-status reported both
routes ready and no open receipt in its bounded latest-receipt view. AM4 slot
occupancy was unknown; that does not mean idle.

| Host / surface | Observed role and shape | Important limit |
|---|---|---|
| FX99 `hermes-fleet` | Pinned upstream Hermes `v2026.9.14`, commit `345cd2b057a452236de401d3534b8502a7465e8d`; stores controller sessions | Agent process location is not model location. Existing 8 GB GPU/Ollama residents were not evicted. |
| AM4 facade `192.168.12.233:8090/v1` | `am4-dense-27b`, Qwen3.8-27B Q4_K_M; native `:18090`, 131072 context, **one physical slot**, both 12 GB NVIDIA GPUs | Controller and compression share this slot. No 4070 Ti is currently reserved for MemSplice. |
| OMEN native `:8082` | `omen-arc`, `qwen3-30b-a3b`, resident Vulkan worker shape; eight 16384-token slots | Both B70s are worker resources, not empty cards. This is not the demonstrated fast SYCL Dense profile. |
| OMEN HEARTH | Production `:8710`; restricted Hermes gateway `:8712`; Caddy `:8083/hermes-worker/mcp` forwards worker traffic to restricted gateway | The original production gateway was not replaced. Caller authentication remains required. |
| MechNet | Explicit `cc-builder-2` / `cc-builder-3`, per-run `omen-resident-hearth` preset; at most two selected builders | CPU/files/builds in VMs, inference on OMEN; global runner defaults unchanged; candidate promotion manual. |

FX99 runtime: `/home/derek/.local/share/hermes-fleet`; launcher:
`/home/derek/.local/bin/hermes-fleet`; profile:
`/home/derek/.config/hermes-fleet`; sessions under that profile's `sessions/`.
The private SSH tunnel exposes OMEN's restricted MCP at FX99 loopback `8712`.

AM4 native PID after restoration was **2151074**, not the earlier 1738436.
Working directory: `/home/derek/work/am4-dual-nvidia-poc`. Recorded baseline:

```text
./llama.cpp-knee/build/bin/llama-server -m models/Qwen3.8-27B-Q4_K_M.gguf
  --host 0.0.0.0 --port 18090 -ngl 99 -sm layer -fa on
  -c 131072 -np 1 -ub 1024 -b 2048 -ctk q4_0 -ctv q4_0
  --cache-ram 0 --slot-save-path kvslots --slots -lv 3
```

This records argv, not a copy/paste startup command or credential configuration.
Original environment was restored from a private snapshot. CUDA ordinal order
did not match `nvidia-smi` ordering. The sampled 5070 UUID was
`GPU-a1f65cc0-44d9-7854-6785-7d93e686da2f`; re-query rather than assuming index 0.
Both NVIDIA cards are occupied again after restoration.

OMEN native PID was 23268; production gateway 13236; restricted gateway 30096.
No temporary `:18091` listener remained. OMEN startup still depends on Windows
user logon; an always-on FX99 controller does not make OMEN unattended-boot ready.

## First checks for a continuing agent

These are read-only; do not start with model loading or a test suite:

```powershell
git -C C:\work\commandcenter-hermes-fx99 status --short --branch
ssh fx99 /home/derek/.local/bin/hermes-fleet fleet-status --json
ssh am4 nvidia-smi --query-gpu=index,uuid,name,memory.used,memory.total --format=csv
```

Read native props/slots through the existing authenticated path if choosing a
new shape. Configured context is not native context, aliases are not extra seats,
and a ready endpoint is not proof of free capacity. Avoid broad recursive scans
of `E:\work`; concurrent scans previously interfered with model I/O.

On Windows use the B70 tools, logs, allocation/budget evidence and observed
performance together. Task Manager shared-memory values alone neither prove nor
disprove harmful spill; SYCL counters also have blind spots. Keep an explicit
memory margin and stop a launch that crosses the observed performance cliff.

## Available levers versus missing integration

| Surface | Useful decisions/access | Observation and feedback | Current restriction |
|---|---|---|---|
| Hermes | Discover tools, query capacity, create receipts and dispatch bounded builder work | Session/tool history and reviewed delivered artifacts | Deployed restricted profile has no general shell, arbitrary generation/backend selection, or model-rotation control. |
| HEARTH | Accounted generation, knowledge, advisory scheduling; existing rotation-window tools under appropriate authority | Physical attempts, backend identity, native slots, receipts and outcome projections | Hermes worker admission is pinned to `omen-arc`; advisor output alone does not dispatch or bypass admission. |
| DeepAgents | Existing filesystem/tool-loop implementation and operator adapter in the sibling worktree | Tool trace, artifacts and operator attempt/outcome records | Not a qualified selectable executor through today's restricted Hermes profile; inspect narrow adapter assumptions and live status. |
| MechNet | Isolated VM edits/builds, explicit builder selection, per-run inference preset | Candidate branches, logs, deadlines, assays and manual review | No automatic harvest/merge; bounded concurrency and source/task packs must fit worker context. |
| Model/seat lifecycle | Context, batch/ubatch, KV type, quant, device/layer split, MTP, rotation, CPU offload, saved state | Fit, native capacity, time to accepted artifact, restoration evidence | Research and controls exist in different places; not all are exposed to Hermes. MemSplice orchestration is not implemented here. |

Hard pins to inspect before enabling a new worker shape:
`hearth/kernel/hermes_worker.py`, `hearth/kernel/governed_operator.py`,
`fleet/hermes/remote/worker/runner_presets.py` and
`fleet/hermes/remote/conductor/hermes_run_policy.py`.
Existing task-family preferences live in `hearth/etc/routing-families.toml` and
`hearth/scheduler/families.py`; explicit pins override family routing. Changing
only the scheduler cannot make a pinned worker launch a different model.

Learning means feeding verified artifacts, failures, latency and route evidence
back into selection. It does not mean weight training, automatic permission
expansion, or trusting a model's account of its own successful tool calls.

## Evidence to keep straight

### B70 capabilities already measured; do not rerun them just to believe them

Source: [SYCL workflow](../../docs/rnd/sycl-vs-vulkan/WORKFLOW.md) and its linked
journey/evidence. Different rows used different configurations:

| Shape | Historical result |
|---|---|
| Dense 27B, dual B70, SYCL f16 KV | 694-713 prompt tok/s near 119k; no MemSplice required |
| Same GPU pair with MTP `n_max=3` | 19.98 decode tok/s at 119k; 14.37 at 248k |
| Four GPUs, `-ts 2,2,1,1`, MTP | 24.9 decode tok/s at 119k; **not** a dual-B70 result |
| Four GPUs at 256k | About 13-14 decode tok/s, not an improvement over the dual-B70 result |

These do not establish one B70 hosting a 128k 27B with two/four slots, automatic
4070 Ti prefill-to-B70 thinking continuation, or the quality value of doubling
context. The 128k-versus-256k utility question remains workload-dependent and
unresolved by these speed measurements. Preserve the distinction between a
saved-state restore on a qualified build/layout and general live migration.

### Compact controller: failure data, not a deployment preset

See [full record](RND-COMPACT-CONTROLLER-20260920.md) and
`artifacts/hermes-fx99/compact-controller/`.

- 14B Q4 on the 5070 at 16k fit, but installed Hermes refused below 64000.
  At 64k all-GPU it failed KV allocation. That minimum was already documented
  in `hearth/etc/backends.toml`; don't rediscover it by loading a model first.
- CPU-offloaded 14B requested 64k but native capacity was 32k. Short real calls
  reached 1124 prompt tok/s and 17.72 decode tok/s on the first request. It
  queried the worker, then failed receipt arguments/IDs. No worker file.
- The FX99 Llama3.1-8B Q4_K_M candidate was never sampled. A Windows-relayed copy
  stopped at 353 MB; the incomplete destination was removed. Original weights
  remain. Transfer slowness is unexplained, not evidence against that model.
- No smaller modern controller comparison, genuine 64k 14B qualification,
  lower-context Hermes support, concurrent work on the freed 4070 Ti, automatic
  framework selection, MemSplice, or B70 rotation was qualified in this lap.

If continuing compact-controller research, first choose one already-available
candidate and one actual worker file to produce. Verify genuine context support
or explicitly investigate the installed Hermes limit; never lie to the context
check. Use the existing tools and source packs. Stop at the first meaningful
failure, record it, and deliver the evidence inside the reserved window.

## Artifact attribution and receipts

| Receipt / evidence | Interpretation |
|---|---|
| `br-20260920-024212-1196097b` | Rollout parent **failed** its strict delegated-matrix criterion. Published matrix is Codex-authored. |
| `br-20260920-030141-e036eff8` | OMEN formatter candidate; assisted before inference; Codex corrected boolean/nested-null/stale handling. |
| `br-20260920-031116-00f63902` | Checklist reached a worker file from Hermes dispatch without intervention; Codex corrected a command and stop rules during review. |
| `br-20260920-025127-19eb2ef8` | Matrix attempt failed; no delivered matrix. |
| `br-20260920-035537-21f37ea1` | SYCL route refused by resident-model pin; not a SYCL benchmark failure. |
| `br-20260920-040805-4b481cc7` | Compact experiment recorded/restored, receipt done; controller qualification failed. Evidence committed in `c20c186`. Closed receipt's commits list was not populated; do not rewrite closed history to conceal that gap. |

Reviewed deliverables: [operating map](OPERATING-MATRIX.md),
[operations checklist](OPERATIONS-CHECKLIST.md), `fleet_status_format.py` and
`fleet_status.py`. Raw candidate formatter commit:
`b764b019f4c4be3ba4eab7843a62b6dac00d77e4`; checklist:
`dc278bab96e469eeded0a28d7c4390cee48d5f32`. These identify remote candidate
history, not commits promoted to the farmer repository's main branch.
Accounting and originals live in `artifacts/hermes-fx99/omen-workers/`.

## Restoration, credentials and publication boundary

- `rnd-am4-seat.py` and `rnd-14b-hermes.py` are historical operator-run helpers,
  **not safe unattended lifecycle tools**. They rely on a recorded PID/private
  snapshot. Do not replay their stop/restore operations against a new owner.
- AM4's snapshot `/home/derek/.local/state/hermes-rnd-20260920/resident.json`
  contains the original environment and may contain secrets. Keep it private.
  The temporary `native.key` and both temporary SSH forwards were removed.
- Windows private profiles/keys are under `C:\Users\derek\.hermes-fleet`.
  Never commit those, deployed caller registries, gateway credentials or raw
  private environment snapshots. Old AM4 builder credentials were revoked;
  don't restore backups wholesale and silently reactivate them.
- Restricted gateway shares the production coordination SQLite database but
  uses separate execution/kernel streams. Don't merge streams or import the
  same physical attempt twice merely to make a report look complete.
- Live Caddy's Hermes route is already included in the branch (`e722a2c`). It
  is not an unrecorded deployment patch. The original checkout's other dirty
  edits belong to other work and were left untouched.
- This handoff is published on `agent/hermes-fx99-20260919`, not merged into
  `master`. Its history includes earlier prerequisite commits and existing
  test files; this documentation turn added no tests or model-service changes.
  The separate operator branch and unreviewed remote builder candidates are
  not automatically merged or pushed with it.
- The July `knowledge/am4_catalog.json` hardware snapshot predates the present
  12 GB NVIDIA pair. Do not size current models from its old GPU entries.

For the next change, reuse the existing branch/worktree or make an explicit new
one, preserve other writers' edits, record the useful result, and publish only
the intended changes. Do not treat this report as a backlog requiring every
untried lever to be exhausted before producing the next file.
