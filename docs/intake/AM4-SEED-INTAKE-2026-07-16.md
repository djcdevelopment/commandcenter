# AM4 Seed Intake — manifest, ember, gad (2026-07-16)

Cue: Derek — "old work on AM4 we should bake into commandcenter; it's on the 4TB."
Ruling: [ADR-0016](../adr/0016-software-constellation-registry-am4-seed-intake.md).
Operator surface: `AM4-SEED-INTAKE.html` (repo root).

## Provenance

| Seed | Archive of record | Last commit | State | Local mirror |
|---|---|---|---|---|
| manifest | `/mnt/win/work/manifest` | 3d93bf6 2026-06-05 | clean at HEAD¹ | `C:\work\am4-harvest\manifest` |
| ember | `/mnt/win/work/ember` | e12c016 2026-06-17 | clean at HEAD¹ | `C:\work\am4-harvest\ember` |
| gad | `/mnt/win/work/gad` | 6186d08 2026-06-22 | 2 untracked dirs² | `C:\work\am4-harvest\gad` |

¹ AM4's ntfs3 mount showed 136/29 "dirty" files — mode-bit phantoms, not content.
² `pm/analysis/aotc-attendance/` (guild product, out of scope) + `pm/journal/reflect/`
(2026-06-17/20 Reflect journals — captured in the mirror).

Pulled whole (tree + .git, junk excluded) via tar-over-SSH. Deep reads = three
parallel `gcp-gemini-pro` assessments through the HEARTH door with `files=`
packing (~135K tokens in / ~5K out, trial credits; verified via result metadata).

## What landed at intake

- `registry/constellation.toml` — mechnet software registry, 15 organs, 4
  contracts; tomllib-parse + referential-integrity proven.
- `registry/seed/constellation-gad-2026-06-22.yaml` — verbatim donor artifact.
- `registry/README.md` — two-registry doctrine + provenance.
- ADR-0016 — the six intake rulings.

## Harvest map (condensed from the three assessments)

### manifest → commandcenter

| Item | Disposition |
|---|---|
| constellation schema (MANIFEST-SCHEMA.md, SCHEMA-EXTENSIONS-v1.md) | **LANDED** as `registry/constellation.toml` (TOML-adapted) |
| producer_type/topology/conformance ontology | **LANDED** (registry vocab) |
| conformance ladder (canonical/extended/reduced/non-canonical) | **LANDED** — registry enrolls at "reduced", honestly |
| discovery signal cascade (CLAUDE.md > PLAN.md > README.md, 9 priorities) | pattern noted for a future patrol/enrollment slice |
| artifact envelope (source/produced_at/framework_version stamps) | pattern noted — HEARTH ledger already carries most of this |
| Pydantic framework + 249 tests, MAF-WORKFLOW, ADO/Discord enrollment provisioning | **RETIRED** to read-only reference (stdlib-first; superseded by HEARTH/conductor) |

### ember → commandcenter

| Item | Disposition |
|---|---|
| critic-verdict loop + soft gate (ADR-0005) | **Slice E1** (refine-lane upgrade), awaiting cue |
| Reflect dual-judge + XML-cite + comparer (ADR-0014/0016/0018, contracts/) | **Slice E2** (patrol job → belief spine), awaiting cue; judges must be lens-diverse per windtunnel finding |
| overnight backlog planner (ADR-0019) | **Slice E3**, awaiting cue |
| XML-cite grounding (EXP-0001: 0/3 vs 3/3 hallucinations) | **ADOPTED** as prompt doctrine for evidence lanes |
| boot recovery fails-closed (ADR-0008) | reference for conductor boot posture |
| glance-first evidence: working-tree truth (git status --porcelain) over commit-age (ADR-0018) | pattern noted — pairs with gad's glance |
| Discord control surface (ADR-0001) | deferred; if built, as a stateless edge client, HEARTH stays pure |
| build/worktree/queue/SQLite/telemetry C# code | **SKIPPED** — superseded by conductor + HEARTH + scheduler + OTel spine |

### gad → commandcenter

| Item | Disposition |
|---|---|
| constellation.yaml (canonical instance) | **LANDED** verbatim as registry seed |
| atomic digest cards (decision 0002: ✅/❌/💬, one decision per card) | **ADOPTED** as interaction contract for DECISIONS-PENDING |
| constellation glance (working-tree truth across repos) | pattern noted for patrol |
| experiments ledger (EXP-#### index) | pattern noted; EXP-0001 result already absorbed via doctrine |
| agent-maintained PM workspace (dashboard/action-items/decisions) | commandcenter's HTML-plans + DECISIONS-PENDING already fill this; card pattern imported |
| ADO boards mechanics, guild content, direction-shift/alpha docs | **SKIPPED** — other product / superseded |

## Stale assumptions flagged (do not inherit)

- "ember is the brain/planner" — commandcenter (HEARTH + conductor) is.
- ADO Boards as canonical work tracker — local artifacts/ledger are.
- Pydantic as contract enforcer; `framework load --json` subprocess pattern —
  tomllib + dataclasses; no subprocess seam.
- Single-machine worldview, `D:\work\...` hard paths — fleet + two-registry model.
- Ember's "builder gets no push credentials" — workers push via gateway
  `git_commit_push`.

## Assessor "one decision" verdicts (all three ruled, ADR-0016)

1. manifest: vendor the Pydantic framework vs go stdlib-native → **stdlib-native** (D2).
2. gad: merge software registry into fleet inventory vs keep distinct → **distinct** (D1).
3. ember: Discord surface inside HEARTH vs stateless edge client → **edge client
   if/when built**; HEARTH stays pure (D3, deferred with the surface itself).
