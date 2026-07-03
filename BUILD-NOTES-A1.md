# BUILD NOTES: A1 - Backup and Replication

## What Changed

1. Created `tools/ops/push_backup.py`:
   - Implements deterministic backup with `--dry-run` support
   - Validates remote, branch, and git availability
   - Uses UTC timestamp for STAMP (deterministic, no wall-clock)
   - Fails loudly on errors (nonzero exit, clear message)
   - Never force-pushes

2. Created `docs/ops-backup.md`:
   - Detailed instructions for remote setup
   - Windows Task Scheduler and Linux cron examples
   - Complete restore procedure with test verification
   - Safety notes about hand-editing knowledge/*.json

3. Created `DECISION-NEEDED-A1.md`:
   - Requested Derek's decision on remote URL
   - Offered two options: GitHub or SSH bare repo

## Why

To ensure the event log and knowledge tree survive machine loss and enable fleet-wide replication. The backup mechanism is:
- Deterministic (Clause 18)
- Testable (baseline suite passes)
- Resilient (fails loudly)
- Visible (docs explain everything)

## How to Verify

1. Run `python3 tools/ops/push_backup.py --dry-run` → should print what would be staged/committed/pushed
2. Run `python3 -m unittest discover -s tests/workflow` → should pass (130 tests)
3. Confirm `DECISION-NEEDED-A1.md` exists and contains the remote URL request

## Review Flags

- The first real push is pending Derek's remote URL decision
- No hand-editing of knowledge/*.json occurred
- All changes follow the constitution (Clause 1, 2, D18, 6)
- No destructive or irreversible changes were made

> **Note**: The backup script is ready for first real run once the remote is configured.