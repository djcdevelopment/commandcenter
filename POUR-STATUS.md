# POUR-STATUS

## Streams

- `done`: `A1-remainder` on `stream/A1` at `abb75f2`, merged to `master` and verified locally.
- `done`: `B2` on `stream/B2` at `90acc47`, merged to `master` and verified locally.
- `in-flight`: none.
- `paused`: none.
- `blocked`: none.
- `not started`: `A2`, `B1`, `C1`.

## Pilot Landing: B2

- Conductor run: `pour-b2`
- Winner: `cc-builder-1`
- Winning assay: `pytest: 130/130 passed; imports: 0/0 ok; score=70; isolation=docker`
- Cycle time from conductor checkpoints: `2026-07-03T04:02:40.177585+00:00` -> `2026-07-03T04:09:32.603190+00:00` (`412.43s`)
- Evidence captured locally under `runs/pour-b2/`
- Local verification on landed branch:
  - `python -m unittest discover -s tests/workflow` -> `Ran 130 tests ... OK`
  - `python tools/workflow/check_doc_claims.py` -> `roadmap-first-capability WAIVED`, `candidates-present PASS`

## Landing: A1 remainder

- Conductor run: `pour-a1-r7`
- Winner: `cc-builder-2`
- Winning assay: `pytest: 130/130 passed; imports: 0/0 ok; score=70; isolation=docker`
- Cycle time from conductor checkpoints: `2026-07-03T05:51:33.372638+00:00` -> `2026-07-03T05:55:23.332966+00:00` (`229.96s`)
- Evidence captured locally under `runs/pour-a1-r7/`
- Local landing scope:
  - `tools/ops/push_backup.py`
  - `docs/ops-backup.md`
  - `BUILD-NOTES-A1.md`
- Local verification on landed branch:
  - `python tools/ops/push_backup.py --dry-run` -> sane stage/commit/push plan
  - `python -m unittest discover -s tests/workflow` -> `Ran 130 tests ... OK`

## Gates

- `G0`: `CLOSED`
  - Check: `origin` configured = yes; `tools/workflow/corpus_guard.py` exists = no
- `G1`: `CLOSED`
  - Check: `DECISIONS-D1.md` exists = no
- `G2`: `CLOSED`
  - Check: some `runs/*/artifacts/*.json` carries non-null `gpu_temp_c_peak` = no
- `G-budget`: `CLOSED`
  - Check: `operating-budget.v1` with `authored_by != "fixture"` exists = no
- `G3`: `CLOSED`
  - Check: `knowledge/capabilities.json` `capability_count >= 1` = no (`0`)

## Learning Metrics

- Landing `B2`
  - Findings: `18 -> 15` (`-3`)
  - Associations: `0 -> 0` (`+0`)
  - Capabilities: `0 -> 0` (`+0`)
  - Coverage gaps: `18 -> 26` (`+8`)
  - Experiment candidates: `15 -> 42` (`+27`)
  - Workflow test count vs baseline: `110 -> 130` (`+20`)
- Landing `A1-remainder`
  - Findings: `15 -> 15` (`+0`)
  - Associations: `0 -> 0` (`+0`)
  - Capabilities: `0 -> 0` (`+0`)
  - Coverage gaps: `26 -> 24` (`-2`)
  - Experiment candidates: `42 -> 40` (`-2`)
  - Workflow test count vs baseline: `110 -> 130` (`+20`)

## Doc Claims

| claim_id | expected | actual | result |
|---|---:|---:|---|
| `roadmap-first-capability` | `>=1` | `0` | `WAIVED` |
| `candidates-present` | `>=1` | `40` | `PASS` |

## Notes

- The pilot required two minimal conductor adapters, committed there immediately:
  - `0c30d3f` `feat(conductor): allow per-request target repos`
  - `b122562` `feat(conductor): allow per-request builder subsets`
- The daemon path is now validated with a real two-builder inbox run. The failed single-builder A1 probes (`pour-a1-r3` through `pour-a1-r6`) were invalid plans; the conductor requires at least two fan-out targets.
- The local re-projection over `runs/` is a direct function of the current evidence corpus: `runs/omen-5070-hwbaseline-2026-07-02`, `runs/pour-b2`, and `runs/pour-a1-r7` are present under `runs/`.
- `runs/pour-b2/conductor/` preserves the raw conductor `result.json`, `nodes.json`, and checkpoint evidence used to materialize the pilot observations.
- `runs/pour-a1-r7/conductor/` preserves the raw conductor `result.json`, `nodes.json`, and checkpoint evidence for the A1 remainder landing.

## Checkpoint

- Wave 1 is in progress. `A1-remainder` and `B2` are landed. Next up: `A2`, `B1`, and `C1`.
