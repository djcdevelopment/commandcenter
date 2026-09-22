# Resident production parity replay — 2026-09-22

## Purpose

Replay the knowledge-path guard candidate against the resident production model
and judge it with the same review gates used by local work.  This is a matched
task, not a byte-identical prompt replay: the historical input measured 12,232
tokens; this gateway packed the same pinned source closure and criteria into
10,567 tokens.

## Producer record

- Base: `81a416bcad9cd3aaacc946cb780938df20a9088b`.
- Declared source closure: `hearth/kernel/guards.py`,
  `hearth/tests/kernel/test_guards.py`, and
  `hearth/tests/toolsurface/test_build_requests.py`.
- Provider/model: `omen-arc` / `qwen3-30b-a3b`, explicitly pinned.
- Execution: `job_4d11d1f9a0702c80cca21b17cf1adb57`;
  request `req_12a04692d961063e307906f780ecd887`.
- Candidate artifact: `art_17777430b371e1581e2aafe1c35cddad`, SHA-256
  `3313ddad985338e2cd4e2a0a25b77f986f3b76472fffc313c0b81ceea864e8d2`.
- Input/output: 10,567 / 1,947 tokens; 81.079 seconds; 15 ms queue time;
  4,096-token output limit.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| Exact six-field candidate shape | passed | JSON contains only `schema`, `artifact_kind`, `summary`, `target_path`, `citations`, and `content`. |
| Citation path and range | passed | All three citations name declared paths and stay within the pinned 100/143/724-line source bounds. |
| Diff scope and binary restriction | passed | Diff names only the three declared text paths and has no binary stanza. |
| `git apply --check` at pinned base | failed | `git apply --check --verbose candidate.patch` returns 128: `error: corrupt patch at candidate.patch:16`. |
| Security/semantic review | failed | The proposed trusted-metadata return is below the existing `knowledge_hits and tool not in self.knowledge_tools` rejection, so it cannot permit `close_build_request`; it also references `EXTRA_KNOWLEDGE_READERS` without importing it into `guards.py`. The proposed build-request test mocks `close_build_request` to raise and then calls it, so it cannot demonstrate the intended allowance. |

## Verdict and comparison

The candidate is **rejected**.  It was structurally valid, so the single
automatic repair allowance does not apply; a semantic or diff failure must not
enter a critic loop.

| Population | Candidates | Reached review | Accepted | Rejection pattern |
|---|---:|---:|---:|---|
| Historical resident fast dogfood | 3 | 0 | 0 | one schema failure after its allowed repair; two citation/security failures |
| This resident production replay | 1 | 0 | 0 | diff corruption and guard semantic failure |
| Combined resident evidence | 4 | 0 | 0 | no accepted source-backed guard candidate |

The resident service itself was healthy and at rate during this run (112.61
tok/s versus its 105.33 baseline).  Therefore the failure is candidate quality,
not availability or throughput.  Existing conventional production changes do
not have an equivalent immutable-candidate/citation/verdict record, so they
cannot be claimed to have passed this newer standard retroactively.
