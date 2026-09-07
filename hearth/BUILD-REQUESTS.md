# Hearth Build Requests

Hearth build requests are the first-class control-plane lane for infrastructure
building work. They preserve the Comfy FieldLab receipt layout while exposing the
lifecycle through Hearth tools instead of requiring operators to call PowerShell
sidecars directly.

Default receipt storage:

`C:\work\baseline\fieldlab\runs\build-requests\`

Override with `HEARTH_BUILD_REQUEST_DIR` when testing or running a separate lane.
(The receipts moved here on 2026-08-24 when the comfy tree was retired; the old
`C:\work\comfy\fieldlab\...` path no longer resolves and the ledger travelled with
them.)

The `repo` a receipt names defaults to the primary `HEARTH_SCOPE` root and must
resolve inside the sandbox roots and exist as a directory — the old
`C:\work\comfy` default is gone with the tree it pointed at.

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
  "deliverables": ["fieldlab/scripts/check-gcp-p7.ps1"],
  "lane": "hearth",
  "backend": "gcp-gemini",
  "task": "cloud-overflow",
  "execute": true
}
```

`repo` is omitted above, so it defaults to the primary `HEARTH_SCOPE` root.
`deliverables` are the relative glob patterns the work must produce. They are
validated with the task lane's own `requires` rules (relative, no drive letter, no
`..` segment, no NUL/newline, non-empty strings) plus two bounds of this lane's
own: at most 32 patterns, and no literal `\` — a deliverable is matched with
`fnmatch` against `git ls-tree` output, which is always `/`-separated, so a
backslash glob could only ever read as a silent miss. Deliverables are optional at
create time and **required** to delegate.

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

## Delegating a build to the fleet

`execute_build_request(mode="manual"|"agent")` records that the *caller* is doing
the work. `mode="delegate"` actually dispatches: it renders the receipt as a
self-contained fleet brief and submits it through the existing task lane
(`task_lane.submit_task`). No second scheduler — the conductor still dispatches,
and nothing here fires on a timer.

```json
{
  "receipt_id": "br-YYYYMMDD-HHMMSS-xxxxxxxx",
  "mode": "delegate",
  "builders": ["cc-builder-2", "cc-builder-3"],
  "max_age_s": 21600,
  "task_class": "build"
}
```

- `builders` — the fleet workers to pin (default: the task lane's `DEFAULT_BUILDERS`).
- `max_age_s` — the lifetime this build is expected to need, so the coherence
  watchdog does not stub a live multi-hour run as a phantom. Positive integer
  seconds, at most 7 days (validated by `task_expectations.validate_max_age_s`).
- `task_class` — defaults to `"build"`.
- `submit_fn` — a test seam. It is typed `object` rather than `Callable` because
  FastMCP builds each tool's JSON schema from its signature and pydantic cannot
  render a callable; the type is enforced at runtime instead.

The brief carries the PREAMBLE discipline of `campaign/mechnet_exerciser.py`
(read-only source at `~/commandcenter-src`, cite real repo-relative paths, commit
only the deliverables), then the title, the authored request text sliced out of the
immutable `*.request.md`, the numbered acceptance criteria, the deliverable globs,
and the repository **by directory name only**. A brief that would carry a Windows
absolute path (drive letter or UNC) is refused before anything is submitted — the
worker has no `C:` drive, and the brief leaves this box.

What delegation writes on the receipt, under `execution.delegation`:

```json
{
  "plan_id": "hearth-br-...-xxxxxxxx",
  "builders": ["cc-builder-2", "cc-builder-3"],
  "requires": ["fieldlab/scripts/check-gcp-p7.ps1"],
  "max_age_s": 21600,
  "task_class": "build",
  "submitted_at": "…Z",
  "state": "submitted",
  "harvested": false,
  "completed_at": null,
  "result": null
}
```

Rules:

- **Exactly one delegation per receipt.** A second `delegate` returns the current
  projection with `duplicate_delegation: true`, submits nothing, appends no event,
  and writes nothing.
- **Deliverables are required.** A delegated build with no required deliverables
  cannot be accepted mechanically, so it is refused (`ValueError`) before any submit.
- **Failure is loud.** A submit that returns `ok:false` *or raises* blocks the
  receipt, records the error in `execution.evidence`, appends one
  `delegation_failed` event, and writes **no** delegation record — so a fixed lane
  can delegate again.

## Syncing a delegated build

`update_build_request(receipt_id, sync_delegation=True)` is the poll. A human or a
cadence caller drives it; the lane never polls itself and never marks a receipt
`done`. Seams `status_fn` / `harvest_fn` / `list_files_fn` default to
`task_lane.task_status`, `fleet_harvest.harvest_fleet_run` and the real
`git ls-tree` (same `object`-typed reason as `submit_fn`).

Still running:

- `delegation.last_sync` and `delegation.last_status` are refreshed
  (`pending` | `unreachable` — an SSH failure is *absence of observation*, not
  evidence the run is alive, so the two are kept apart).
- A `delegation_synced` event is appended **only when the observed state changed**.
  The `delegated` event already established "submitted, not yet done", so the first
  `pending` observation is not a change — a polling cadence writes no event lines
  for hours.

Done — in this order:

1. **Harvest once, persist-first.** `delegation.harvested` is written to disk
   *before* `harvest_fn` is called, so a crash mid-harvest cannot be replayed into a
   second harvest by the next sync. That single write carries no event; the outcome
   event records what happened. Honest residual: two syncs that both *load* the
   projection before either writes could still both harvest — the window is the
   read-to-write gap, and the gateway is a single writer today. A crashed harvest is
   **not** retried automatically; it lands as `harvest_incomplete` for a human,
   because silently re-running a failing fetch/push on a cadence hammers the remote.
   A run with **no winner still gets its harvest**: the branches are exactly what a
   human needs to curate a run the assay could not crown, and the mirror is
   idempotent and non-force.
2. **Acceptance** against the winner's branch — `origin/fleet/<plan_id>/<winner>/lap1`,
   taken from the harvest's own `github_branch` — via
   `tools/workflow/assay_acceptance.check_lap_acceptance`.
3. One terminal `delegation_completed` event carrying
   `{result, winner_present, winner, missing_globs, harvested, branches_count}`,
   and `state: "completed"` + `completed_at` on the record.

| `delegation.result`      | meaning                                                   | receipt status |
| ------------------------ | --------------------------------------------------------- | -------------- |
| `accepted`               | winning lap carries every required deliverable             | unchanged (`running`) |
| `acceptance_failed`      | winning lap is missing globs (recorded in `missing_globs`) | `blocked` |
| `no_winner`              | the run finished with no winner; branches still harvested  | `blocked` |
| `winner_branch_missing`  | a winner was named but no harvested branch matches it      | `blocked` |
| `harvest_failed`         | `harvest_fleet_run` returned `ok:false`                    | `blocked` |
| `harvest_incomplete`     | harvest was started but its outcome was never recorded     | `blocked` |

Every later sync returns `already_synced: true` with no calls to `status_fn` /
`harvest_fn`, no new events, and no writes — checked *before* the closed-receipt
guard, since the blocking results above are themselves final statuses.

**Deliverable presence only passes criteria that name a deliverable.** When
acceptance passes, each acceptance criterion whose text contains a deliverable glob
**verbatim** gets a `passed` validation row with evidence `"<branch>: <glob> matched"`.
Every other criterion stays `not_run` — a file existing is evidence for exactly one
claim, and `close_build_request(status="done")` keeps refusing until a human or agent
supplies the rest. This lane never marks a receipt `done`.

Event names appended by the delegation lane: `delegated`, `delegation_failed`,
`delegation_synced`, `delegation_completed`. The prompt body is **never** stored in
an event — the `delegated` event carries `prompt_sha256` instead, and the receipt
already holds the authored request.

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
