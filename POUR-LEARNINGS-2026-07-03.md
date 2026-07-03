# Pour Learnings — 2026-07-03

## What We Learned

- The first bad wave-1 outputs were caused by a stale conductor ontology mirror, not by the local repo or the builder VMs.
- The conductor builds from `~/work/commandcenter-ontology/farmer-repo`, not directly from `C:\work\commandcenter`.
- A green assay is not enough to trust a branch for landing. The current assay is too weak to enforce stream-scoped correctness.
- Single-builder probe runs are invalid for this workflow shape. The conductor requires at least two fan-out targets.
- Claude token exhaustion does not explain the rejected `A2`/`B1`/`C1` branches we inspected. Those builder runs were `openai` / `vllama-planner`.
- Claude capacity may still matter on the assay node (`claudefarm1`), so builder success and assay health should be treated as separate concerns.

## What We Tried

### Conductor fixes

- Confirmed and committed two earlier conductor adapters already in place:
  - `0c30d3f` `feat(conductor): allow per-request target repos`
  - `b122562` `feat(conductor): allow per-request builder subsets`
- Added timeout hardening in the conductor:
  - `17b1048` `fix(conductor): bound route and worker setup hangs`
- Temporarily bypassed the advisory router when it was hanging:
  - `40a3ab7` `fix(conductor): bypass hung advisory router`
- Fixed task retention in the inbox daemon:
  - `47f90c2` `fix(conductor): retain inbox worker tasks`

### Mirror and source sync

- Verified the conductor mirror was stale relative to local `master`.
- Pushed local `master` to the conductor mirror `main`.
- Resynced the conductor source tree from that mirror so builders would clone the current repo state.
- Repeated this sync after landing `A1`, so later reruns would build from `master` at `c8cd2aa`.

### Dispatch and verification attempts

- Landed pilot `B2` successfully.
- Landed `A1` successfully after curating the stream locally from the approved scope and capturing conductor evidence from `pour-a1-r7`.
- Dispatched wave-1 reruns:
  - `pour-a2-r3`, `pour-b1-r3`, `pour-c1-r3`
  - `pour-a2-r4`, `pour-b1-r4`, `pour-c1-r4`
- Fetched and inspected winner and non-winner branches locally with worktrees before deciding whether to land them.

## What Happened Per Stream

### `B2`

- Landed cleanly and verified.

### `A1`

- Landed cleanly and verified.
- Final landing was curated locally because fleet output included stale assumptions and unsolicited edits.

### `A2`

- `r3`: stale-base output; not mergeable.
- `r4`: built from refreshed mirror, but assay selected a false-positive winner whose meaningful diff was only `retro.md`.
- Non-winning branch contained partial `corpus_guard` work, but it was incomplete and not safe to land.

### `B1`

- `r3`: stale-base output; not mergeable.
- `r4`: built from refreshed mirror, but the winner branch rewrote the target HTML documents destructively instead of applying the six scoped amendments.
- Non-winning branch was effectively empty.

### `C1`

- `r3`: produced failing tests but still graded as a winner because assay was weak on the stream.
- `r4`: both builders reached `139/139`, which proved the refreshed base and stream mechanics were working better.
- Even so, the inspected winner diff was still too structurally aggressive to land unreviewed.

## Where We Are

- Landed on `master`:
  - `B2`
  - `A1`
- Not landed:
  - `A2`
  - `B1`
  - `C1`
- Local `master` is ahead through the `A1` evidence landing and has been pushed to GitHub.
- Conductor mirror is synced to current local `master`.
- `POUR-STATUS.md` remains the live operational record.

## Current Best Read

- The stale-base problem is solved.
- The next bottleneck is branch quality selection, not raw dispatch.
- For `A2`, `B1`, and `C1`, the fleet is currently better treated as a draft generator than as an auto-land source.

## Recommended Next Step

- Stop trusting assay alone for these remaining streams.
- Land `A2`, `B1`, and `C1` via curator passes from the approved stream prompts, using the fleet branches only as reference material.
- Continue capturing conductor evidence for each accepted landing so `runs/` and `knowledge/` still reflect the actual pour.

## Update — 2026-07-03 (Claude, curator passes landed)

Acting on the recommended next step above, `B1`, `C1`, and `A2` were curated onto `master` from the
verbatim stream prompts (fleet winner/`alt` branches used only as reference):

- `B1` — `888a02d`: six constitutional HTML amendments; all DoD grep targets + HTMLParser verified.
- `C1` — `3d9cbad`: four raw `model_*` telemetry fields; `model_residency` marked DERIVED. Suite 130 → 141.
- `A2` — `cdad039`: corpus regression guard wired into all six projectors + authored override. Opens `G0`.
  Suite 141 → 148. `knowledge/*.json` deliberately NOT re-projected.

Wave 1 is now fully landed (test baseline `110 → 148`; `POUR-STATUS.md` updated).

The pour's central result — that the assay selected a null-action winner for `A2` and could not grade the
`B1`/`C1` deliverables — was written up as the instrument finding
`ASSAY-ACCEPTANCE-GAP-2026-07-03.html`: **the assay is a regression gate, not an acceptance oracle.** It
proposes a stream-scoped acceptance assay (turn each stream's `DEFINITION OF DONE` into a machine-checkable
manifest: required files/greps/tests + a scope fence) so the next pour can auto-land instead of needing a
curator. That is the recommended next lab investment, and supersedes "curate every stream forever" as the
long-term answer.
