# Start here — the Local Compute Operator control plane

You are in `/home/derek/work/commandcenter-linux-flash` (omen-linux; the Windows checkout `C:\work\commandcenter` is history). It is a **control plane for local compute**:
one repository that can tell you what capabilities exist, what capacity is
available right now, what you yourself are allowed to do, and which execution
route fits a task. You do not need any conversation history to use it, and you
should not assume any.

Read this page, then `AGENTS.md`, then run the inspect command. Three minutes.

## Status words (used everywhere in this repository)

**LIVE** — a maintained surface with current evidence.
**BUILT NOT DEPLOYED** — the implementation exists; it has never run here for real.
**STOPPED** — a real lane that is intentionally not running.
**BLOCKED** — waiting on a named external prerequisite.
**HISTORICAL** — retained evidence of an earlier state.
**ABSENT** — no implementation at all.

Only **LIVE** may be executed in normal mode. Everything else stays visible on
purpose: a lane you can see and must not use is a fact you can plan around, and
a missing row is a silence you cannot.

## The eight entry steps

All eight steps are **LIVE** on omen-linux as of 2026-09-27 (Gates 1–6 of the
operator program, imported in `d6b0271` and mounted on the door in this lap).
Steps 4–8 are the WI-G2/WI-G3 contract; they exist, are tested offline
(`hearth/tests/operator/`), and every physical run so far is a Windows-era record
under `runs/operator/` — read those before trusting a route recommendation.

| # | Step | Status |
|---|---|---|
| 1 | Read this page. | LIVE |
| 2 | Read `AGENTS.md` (how to offload work to the local models through the HEARTH door). | LIVE |
| 3 | `operator inspect --json` — the inspection bundle: capability catalog, capacity snapshot, your authority. | LIVE |
| 4 | `operator task submit <envelope.json>` — freeze the task as a TaskEnvelope. | LIVE (WI-G2) |
| 5 | `operator route draft <run_id>` / `route propose <run_id> <proposal.json>` — a RouteProposal/v1 you then own. | LIVE (WI-G2) |
| 6 | `operator route validate <run_id> <proposal_id>` — independent validation; exit 3 = needs a human `operator approve`. | LIVE (WI-G2) |
| 7 | `operator execute <run_id>` — refuses anything but a validated, recorded proposal. | LIVE (WI-G3) |
| 8 | `operator replay <run_id> --check`, `operator explain <run_id>`, `operator learn report` (recommends only, ADR-0049). | LIVE (WI-G3, Gate 6) |

## Step 3, exactly

Run from the repository root. `operator.cmd` forwards to `python -m
hearth.operator` and does nothing else; use the venv interpreter directly for
anything that calls the door.

```
operator.cmd inspect --json
operator.cmd whoami
python -m hearth.operator inspect --json
~/.venvs/hearth-private/bin/python -m hearth.operator inspect --refresh
```

| Command | What it does |
|---|---|
| `operator inspect [--json]` | Returns the inspection bundle for the **current** planning snapshot. It never observes capacity itself. |
| `operator inspect --refresh [--local]` | Observes capacity now, writes one immutable snapshot, moves `CURRENT.json`. `--local` also probes OMEN's hold files, read-only. |
| `operator whoami [--json]` | Your identity, your capabilities, and the nine authorities. |
| `operator catalog [--check]` | Compiles `knowledge/capability_catalog.json`. `--check` exits 1 when the written catalog no longer matches the tree. |
| `operator verify-ids <file>` | Recomputes every identity a document declares and reports any mismatch. |

**`inspect` refuses rather than guessing.** If `CURRENT.json` is missing,
corrupt, or its planning window has passed, it exits non-zero and tells you
which, plus the remedy. That refusal is the point: two agents that each
silently re-observed capacity would no longer be talking about the same
environment, while both quoted the same identifiers.

## Identity: `HEARTH_API_KEY`

The CLI reads **your own door key** from the environment variable
`HEARTH_API_KEY` and resolves it through `hearth.kernel.auth` — the same code
and the same `hearth/var/callers.json` registry the gateway uses. It is the same
key you present as the `X-Hearth-Key` header when you call the door.

```
set HEARTH_API_KEY=<your key>        (cmd)
$env:HEARTH_API_KEY = "<your key>"   (PowerShell)
```

- **With no key**: `whoami` reports `caller: null` and every authority is
  `denied` with reason `no_identity`. `inspect` still returns the catalog and
  the snapshot — those are caller-neutral and belong to everyone.
- **From a worktree**: `hearth/var/` is git-ignored, so a worktree has no
  registry. Set `HEARTH_ROOT` to the deployed hearth root
  (`$HEARTH_ROOT`, on omen-linux `~/hearth-production`) and the CLI resolves identity exactly as the
  gateway does. `HEARTH_ROOT` finds the **registry**; it never moves where
  operator state is written — that is `HEARTH_OPERATOR_HOME`, defaulting to the
  repository root — so a worktree run can read the deployed registry without
  writing into the deployed tree.
- Never print, echo, commit, or paste a key. Nothing in `hearth/operator/` reads
  `hearth/var/gateway.cmd`, the callers registry, or `.mcp.json`; nothing writes
  a key to any file.

## What you get back

- **Capability catalog** (`knowledge/capability_catalog.json`, committed,
  caller-neutral, no wall clock). Hosts and GPUs, rungs with `pin_only` and
  `retired`, models with measurements **only where a structured source measured
  them**, the door's tools and their capabilities, harnesses, loops and their
  implementation statuses, deterministic tools, worktree and test policy,
  artifact systems. Its `catalog_version` is a SHA-256 of its own content.
- **Capacity snapshot** (immutable, caller-neutral). Door liveness, rung
  readiness and residency, holds and leases, host reachability, trial-credit
  runway. Two horizons: the snapshot's **planning window is 300 seconds**, and
  **every dynamic field carries its own `observed_at`, `ttl_s` and
  `fresh_until`** — readiness 120 s, occupancy and active slot state 30 s,
  reachability 300 s, trial runway 3600 s. A field may go stale inside a valid
  planning window. It will never be described as fresh: `presentation.freshness_now`
  is computed against **your** clock and the snapshot file is never modified.
  A null field always carries the reason it is null.
- **Your authority** (`authority.v1`, caller-specific). Nine authorities, each
  `granted`, `human_required`, or `denied`: `read_repo`, `write_worktree`,
  `run_tests`, `call_door_generate`, `submit_mechnet_task`,
  `create_build_request`, `lease_gpu`, `change_machine_or_network`,
  `merge_push_deploy`. `change_machine_or_network` and `merge_push_deploy` are
  **always human-gated**. **Approval never adds a capability**, so a `denied`
  authority cannot be approved into a `granted` one — it needs a different
  profile, decided by a human, in a diff.

Two agents reading the same `CURRENT.json` get the **same** `catalog_version`,
the **same** `snapshot_id` and byte-identical snapshot documents. Only
`authority` and `presentation` differ. If you and another agent disagree on
either identifier, one of you refreshed — say so rather than reconciling
quietly.

## Before changing anything

1. **Find the owner.** `CURRENT.json` and `hearth/var/operator/snapshots/` are
   owned by `operator inspect --refresh`; `knowledge/capability_catalog.json` by
   `operator catalog`; `runs/operator/_system/history.ndjson` by the operator
   history writer. One writer per mutable surface.
2. **Work in your own worktree**, on your own branch. Never edit another
   writer's worktree, and never commit on the control-plane checkout's branch on
   behalf of a work item.
3. **Never hand-edit a generated file.** Run its generator instead
   (`operator catalog`, `python -m tools.adr_index`, `npm run roadmap:render`).
   A file whose header says it is generated means it.
4. **Never hand-edit a snapshot.** They are immutable. If capacity changed,
   `--refresh` and let the history record the invalidation.
5. **Run the tests with the venv interpreter**, and write the output to a file —
   the hearth-private venv has no pytest, unittest discovery is the runner:
   `~/.venvs/hearth-private/bin/python -m unittest discover -s hearth/tests/operator -t .`.
   The baseline suites (`hearth/tests/toolsurface`, `hearth/tests/imagegen`,
   `hearth/tests/projection`) stand at **1017 passed with 2 known failures** in
   `hearth/tests/toolsurface/test_scheduler_gpu.py` (lines 99 and 169). Those two
   are the recorded baseline: do not "fix" them as a side effect of other work.
6. **Do not restart the gateway, change a service, launch a model, or touch the
   firewall** because a document here said to. Those are human-gated, and the
   production door on `127.0.0.1:8710` runs the code it was started with.
7. **Cite by exact local path**, and cite an ADR as `<register>#<number>` — 130
   records across 11 registers collide on bare numbers. Resolve one against
   `/mnt/omen-c-read/work/handoffs/decision-architecture/adr-index.json` (read-only Windows mount; regenerate with `python -m tools.adr_index` when the mount is up).

## Where things are

| What | Where |
|---|---|
| Entry contract (this page) | `START-HERE.md` |
| Offload rules for agents | `AGENTS.md` |
| Generic fallback | `README.md` |
| Operator package | `hearth/operator/` (`python -m hearth.operator`) |
| Door provider | `hearth/toolsurface/operator.py` (mounted on the live door since 2026-09-27: `hearth.toolsurface.operator` in `hearth-production.service --providers`) |
| Contracts | `hearth/contracts/capability-catalog.v1.schema.json`, `capacity-snapshot.v1.schema.json`, `authority.v1.schema.json`, `inspection-bundle.v1.schema.json` |
| Configuration | `hearth/etc/operator.toml` (horizons, TTLs), `authority-map.toml`, `harnesses.toml`, `loops.toml`, `deterministic-tools.toml` |
| Capability policy | `hearth/etc/profiles.toml` (roles), `hearth/kernel/capabilities.py` (tool → capability) |
| Rungs and hosts | `hearth/etc/backends.toml`, `fleet/inventory.toml` |
| System history | `runs/operator/_system/history.ndjson` (append-only, digests only) |
| Local state (git-ignored) | `CURRENT.json`, `hearth/var/operator/` |
