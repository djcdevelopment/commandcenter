# Conductor Pour How-To

For any agent cold-reading this repo who needs to dispatch work ("pour") through the fleet.
This lives here, on OMEN (this repo's host, the Hyper-V hypervisor), not on the conductor VM —
the conductor is rebuildable infrastructure and can be down or reprovisioned; this doc can't be.

## The two trees

1. **This repo** (`C:\work\commandcenter` on OMEN) — the target: what gets built, tested, promoted.
2. **The conductor** — `ssh claude@100.74.110.91`, repo at `~/work/commandcenter` — a different
   codebase, the fleet orchestration engine. It watches `inbox/*.md`, dispatches builder VMs,
   assay-grades the results, and (by default) fast-forwards the winner into its own local bare
   repo `farmer-repo`.

## Filing work

Drop a markdown file in the conductor's `inbox/` (filename stem = `plan_id`). The body is the
builder prompt, verbatim. Nothing else is required.

## Restricting which builders get dispatched

Prepend an optional `CCMETA` JSON header to the work item body:

```
<!-- CCMETA
{"builders": ["cc-builder-2", "am4-worker-1"]}
-->
<the rest of the prompt>
```

`builders` is an allow-list of node names from `fleet.json`. Omit it to use every ready builder
in the pool.

**To exclude frontier (Claude) builders for a run** — e.g. a Claude token budget ran dry mid-pour —
list only the builders backed by a local model. Check `runner`/`runner_model` in any recent
`runs/<plan-id>/conductor/result.json` to see which is which; as of 2026-07-03:

| builder | runner | model |
|---|---|---|
| `cc-builder-1` | `claude` | `sonnet` (frontier) |
| `cc-builder-2` | `openai` | `vllama-planner` (local, AM4) |
| `am4-worker-1` | `openai` | `vllama-planner` (local, AM4) |

So `{"builders": ["cc-builder-2", "am4-worker-1"]}` dispatches to local models only.

**To exclude a builder permanently**, set `"exclude_from_build_pool": true` on its entry in the
conductor's `fleet.json` (already used for `claudefarm1`, and for `cc-builder-4` — see below).

**`dispatch_pool` is NOT real in production — don't rely on it.** `zen-deepseek-node/node.json`'s
`"dispatch_pool": "exploratory-perspective"` field (and this repo's `CANDIDATE_POOLS`/`schedule()`
in `tools/workflow/reference_runner.py`) is read only by this repo's local test-fixture scheduler —
a simulation used for the belief-layer tests, not the live fleet. The live conductor
(`scripts/conductor_maf.py`'s `load_nodes()`) has no concept of `dispatch_pool` at all; it only
checks `"worker" in roles and not exclude_from_build_pool`. So the actual, enforced way to register
a node as "exploratory only, never the default critical-path pool" is:
- `roles` includes the literal string `"worker"` (a descriptive-only role like `"claude-agent-worker"`
  doesn't count — `load_nodes()` checks for `"worker"` exactly)
- `"exclude_from_build_pool": true` — this is what actually keeps it out of every default dispatch
- opt it into a specific run via the `builders` CCMETA field above

This bit `cc-builder-4` (originally `zen-deepseek-1`, an OMEN-local-MoE overnight critic node,
2026-07-03): its `node.json` template describes `dispatch_pool` as if it were an enforced concept,
but registering it required the `exclude_from_build_pool` + `roles: ["worker", ...]` combination
above to actually keep it off the critical path. Don't add a new `dispatch_pool` value expecting the
conductor to honor it — it won't.

## Targeting a different repo than the conductor's default

The conductor's build/assay/promote target is always `FARMER_REPO`, resolved once at daemon
startup:

```
FARMER_REPO = os.getenv("FARMER_REPO_PATH", "<conductor repo>/farmer-repo")
```

There is **no per-request override** — a work item can never redirect where a build lands or
gets promoted. To pour against a different target repo (this one, for instance):

1. On the conductor, create or designate a bare mirror for the target repo — e.g.
   `~/work/commandcenter-ontology/farmer-repo` (this is exactly what happened during the 2026-07-02
   wave-1 pour, before it was formalized here).
2. Set `FARMER_REPO_PATH=/home/claude/work/commandcenter-ontology/farmer-repo` in the conductor's
   environment (its systemd unit's `Environment=` line, or the shell that starts it) and restart
   `commandcenter-conductor.service` (it's a `--user` systemd unit: `systemctl --user restart
   commandcenter-conductor.service`, not `sudo systemctl`).
3. Dispatch normally. Every build, assay, and promotion for the life of that conductor process now
   targets the mirror, not the conductor's own `farmer-repo`.
4. Land approved results back into this repo from OMEN: fetch the winning branch out of the
   mirror, verify locally (`python -m unittest discover -s tests/workflow`), merge, then push to
   the private GitHub origin from here. Keep GitHub credentials off the conductor and the builder
   VMs — this repo is the only place that holds them.
5. When the campaign is done, unset `FARMER_REPO_PATH` (or restart with it removed) to return the
   conductor to its own default target.

**Why a static env var and not a per-request field:** an earlier version of this doc's mechanism
was a `CCMETA` `repo_path`/`promote` header parsed per work item (commit `0c30d3f` on the
conductor, reverted `566b1f2` 2026-07-03). It worked, but it meant a work item's text could quietly
redirect where a build gets promoted — a config decision masquerading as a per-request one, and it
threaded a parameter through eight functions to do it. Where a pour's output lands is a decision
made once, deliberately, by whoever starts the conductor for that campaign — not something inferred
from a markdown file in `inbox/`.

## Watching a pour

- Live board: `http://<conductor-ip>:8080` (`GET /board/state`).
- Per-run evidence lands under the conductor's `runs/<plan_id>/`: `result.json`, `nodes.json`,
  MAF checkpoints. `result.json` has each builder's `runner`/`runner_model`, the assay scoreboard,
  the winner, and the promotion outcome.
- Pull that evidence into this repo's own `runs/<plan-id>/conductor/` and re-run the projection
  chain (`project_findings` → `project_policy` → `project_capacity` → `project_associations` →
  `project_coverage` → `project_experiments`) to fold it into the belief layer. See
  `CODEX-POUR-ORCHESTRATOR.md` for the full checkpoint/routing/stop-rule protocol this repo used
  for its own wave-1 pour, and `POUR-STATUS.md` for how that pour actually went (including where
  the fleet's assay picked a bad winner and a human/agent curator pass was needed instead —
  tracked in `ASSAY-ACCEPTANCE-GAP-2026-07-03.html`).
