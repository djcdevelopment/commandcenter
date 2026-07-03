# POUR-STATUS

## Streams

- `done`: `B2` on `stream/B2` at `90acc47`, merged to `master` and verified locally.
- `in-flight`: none.
- `paused`: none.
- `blocked`: none.
- `not started`: `A1-remainder`, `A2`, `B1`, `C1`.

## Pilot Landing: B2

- Conductor run: `pour-b2`
- Winner: `cc-builder-1`
- Winning assay: `pytest: 130/130 passed; imports: 0/0 ok; score=70; isolation=docker`
- Cycle time from conductor checkpoints: `2026-07-03T04:02:40.177585+00:00` -> `2026-07-03T04:09:32.603190+00:00` (`412.43s`)
- Evidence captured locally under `runs/pour-b2/`
- Local verification on landed branch:
  - `python -m unittest discover -s tests/workflow` -> `Ran 130 tests ... OK`
  - `python tools/workflow/check_doc_claims.py` -> `roadmap-first-capability WAIVED`, `candidates-present PASS`

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

## Doc Claims

| claim_id | expected | actual | result |
|---|---:|---:|---|
| `roadmap-first-capability` | `>=1` | `0` | `WAIVED` |
| `candidates-present` | `>=1` | `42` | `PASS` |

## Notes

- The pilot required two minimal conductor adapters, committed there immediately:
  - `0c30d3f` `feat(conductor): allow per-request target repos`
  - `b122562` `feat(conductor): allow per-request builder subsets`
- The local re-projection over `runs/` reduced findings and increased gaps/candidates. That is an honest consequence of the current local corpus: only `runs/omen-5070-hwbaseline-2026-07-02` and `runs/pour-b2` are present under `runs/`.
- `runs/pour-b2/conductor/` preserves the raw conductor `result.json`, `nodes.json`, and checkpoint evidence used to materialize the pilot observations.

## Checkpoint

- Pilot complete. Waiting for Derek's explicit go before dispatching `A1-remainder`, `A2`, `B1`, and `C1`.
