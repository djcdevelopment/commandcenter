# BUILD-NOTES-B2

Stream B2 - HTML doc-claim checker with waiver support.

## What changed

Three new files and one new test file:

| File | Role |
|------|------|
| `docs/doc-claims.json` | Registry of machine-checkable claims (authored, not a projection) |
| `docs/doc-claims-waivers.json` | Authored waivers for known divergences (Clause-2 object) |
| `tools/workflow/check_doc_claims.py` | Gate script: loads registry + waivers, evaluates checks, prints table, exits nonzero on un-waived FAIL |
| `tests/workflow/test_doc_claims.py` | 20 new tests; baseline was 110, now 130 (all green) |

## How to add a claim

Edit `docs/doc-claims.json` and append an entry:

```json
{
  "claim_id": "my-new-claim",
  "doc": "SOME-DOC.html",
  "description": "human-readable statement of what the doc claims",
  "check": {
    "file": "knowledge/some_projection.json",
    "path": "dot.separated.key",
    "op": "gte",
    "value": 1
  }
}
```

Supported `op` values:
- `gte` - actual >= value (if the resolved path is a list, compares `len(list) >= value`)
- `eq` - actual == value (same list-length rule)
- `exists` - the path resolves to a non-None value; `value` field is not used

The `file` path resolves relative to the repo root, not the `docs/` directory.

When the resolved value is a list, the checker automatically compares its length, not the list
itself. This is documented in the module docstring of `check_doc_claims.py`.

## How to waive a claim

Add an entry to `docs/doc-claims-waivers.json`:

```json
{
  "claim_id": "my-new-claim",
  "reason": "Why the divergence is known and accepted.",
  "author": "your-name",
  "created": "2026-07-03",
  "expires": "2026-10-01"
}
```

- `expires: null` - never expires (use for structural divergences with no planned fix date)
- `expires: "YYYY-MM-DD"` - waiver expires at end of that date; after it, the claim reverts to FAIL

An expired waiver is not silently dropped - the checker still prints the row but marks it FAIL so
the expired waiver is visible in the output.

## How to verify

```powershell
python tools/workflow/check_doc_claims.py
```

Expected output on the current tree:

```text
claim_id                                 expected   actual     result
-----------------------------------------------------------------------
roadmap-first-capability                 >=1        0          WAIVED
candidates-present                       >=1        15         PASS
```

Run the full test suite:

```powershell
python -m unittest discover -s tests/workflow
```

Expected: `Ran 130 tests ... OK`

## Phase 0 - assumption verification

All four stated assumptions verified before any change:

| Assumption | Expected | Actual |
|-----------|----------|--------|
| `knowledge/capabilities.json` has `capability_count` | any int | 0 |
| `knowledge/coverage.json` has `gap_counts` | `{single_workflow_evidence: 8, unmeasured_metrics: 10}` | matches |
| `knowledge/experiment_candidates.json` is a dict | `{contract_version, source_findings: 7, candidate_counts: {...}, candidates: [...]}` | matches (15 candidates) |
| `CAPABILITY-ROADMAP.html` claims S5b capability | readable claim | confirmed |

## Omitted claim - test-count assertion

The plan notes that `CAPABILITY-ROADMAP.html` claims `110/110 tests`. This claim was
intentionally omitted from the registry because:

1. The plan says the test claim is not checkable via this registry and should be documented here.
2. The test count is not a projection output; it is a historical statement baked into the HTML.
3. There is no `knowledge/*.json` field tracking the live test count to check against.

If a test-count claim becomes desirable later, it would require either a projection that writes the
last-known test count to `knowledge/`, or turning the checker into a test runner, which is the
wrong abstraction for this gate.

## Waiver seeded

`roadmap-first-capability` is waived with `expires: null` per the stream spec. Reason: the
capability record was lost in the `2026-07-02` `knowledge/` overwrite; re-derivation is pending.
When `project_associations.py` produces a capability for S5b, the waiver should be removed.

## Constitutional compliance

- Clause 1: `doc-claims.json` and `doc-claims-waivers.json` are authored objects, not projections.
- Clause 2: Both registry and waivers carry audit-trail fields: `author`, `created`, `reason`.
- D18 determinism: `check_doc_claims.py` is a gate, not a projection; it writes no derived time.
- No silent caps: expired waivers remain visible in output as FAIL, not silently dropped.
- Schema changes: none. No existing contract was modified.
