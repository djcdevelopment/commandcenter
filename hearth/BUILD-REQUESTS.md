# Hearth Build Requests

Hearth build requests are the first-class control-plane lane for infrastructure
building work. They preserve the Comfy FieldLab receipt layout while exposing the
lifecycle through Hearth tools instead of requiring operators to call PowerShell
sidecars directly.

Default receipt storage:

`C:\work\comfy\fieldlab\runs\build-requests\`

Override with `HEARTH_BUILD_REQUEST_DIR` when testing or running a separate lane.

## Tool Surface

- `create_build_request`
- `get_build_request`
- `list_build_requests`
- `update_build_request`
- `execute_build_request`
- `close_build_request`

The request markdown is immutable. Updates, execution records, and closure records are
append-only in `<receipt_id>.events.jsonl`. The `<receipt_id>.receipt.json` file is the
current projection for compatibility with the existing FieldLab scripts and ledger.

## Example Invocation

```json
{
  "title": "Add deployment health check",
  "request": "Add a small operator command that verifies the GCP P7 stack is healthy.",
  "acceptance_criteria": [
    "The command checks Valheim and Lumberjacks health.",
    "The command reports a nonzero exit on failure.",
    "Focused tests pass."
  ],
  "repo": "C:\\work\\comfy",
  "lane": "hearth",
  "backend": "gcp-gemini",
  "task": "cloud-overflow",
  "execute": true
}
```

Close only after validation evidence exists:

```json
{
  "receipt_id": "br-YYYYMMDD-HHMMSS-xxxxxxxx",
  "status": "done",
  "summary": "Implemented and validated the health check.",
  "validation": [
    {
      "criterion": "The command checks Valheim and Lumberjacks health.",
      "status": "passed",
      "evidence": "Smoke test reached both endpoints."
    }
  ],
  "commits": ["<sha>"],
  "changed_files": ["fieldlab/scripts/check-gcp-p7.ps1"]
}
```

`status="done"` is rejected unless every acceptance criterion has a `passed` validation
row with non-empty evidence. `failed`, `blocked`, and `cancelled` can close with partial
or not-run validation rows.

## Design Notes

- Explicit backend selection is honored through the existing backend pool and routing
  policy.
- If no backend is specified, Hearth uses the normal backend default/tag routing.
- Backend name, routing reason, and occupancy result are recorded.
- Repo state is captured before and after work with `git status --porcelain` and `HEAD`.
- Dirty files present before the request are preserved as `pre_existing_dirty_files`;
  request-caused files are projected separately as `request_changed_files`.
- Secrets are redacted before writing request text, receipt projections, and event rows.
- Duplicate closure is idempotent: the existing projection is returned with
  `duplicate_close=true` and no second closure event is appended.
- The PowerShell scripts remain compatible with the same storage lane. Hearth owns the
  portable lifecycle and can replace the script implementation later without changing the
  receipt directory contract.
