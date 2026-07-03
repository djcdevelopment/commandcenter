# BUILD NOTES: A1 - Backup and Restore Infrastructure

## What Changed

1. Verified and confirmed existing `.gitignore` contains expected entries (`__pycache__`, `*.pyc`, `.pytest_cache`, etc.) and does not ignore `runs/` or `knowledge/`.
2. Committed all untracked files with message: `chore(A1): track all evidence and docs for replication`.
3. Created `tools/ops/push_backup.py` with:
   - Full functionality to stage, commit, and push to origin
   - `--dry-run` support with clear output
   - Error handling for missing origin remote
   - No force-pushes
   - Uses `python3` explicitly for compatibility
4. Created `docs/ops-backup.md` with:
   - Remote configuration instructions
   - Windows Task Scheduler and Linux cron examples
   - Restore procedure with test verification
   - Notes on backup behavior and safety
5. Submitted `DECISION-NEEDED-A1.md` requesting remote URL decision.

## Verification

- `python3 tools/ops/push_backup.py --dry-run` outputs expected dry-run plan
- `python3 -m unittest discover -s tests/workflow` runs 130 tests successfully (OK)
- No test failures or regressions

## Why This Matters

This establishes a reliable backup and restore mechanism for the fleet, ensuring:
- Evidence and knowledge can survive machine loss
- Remote cloning is possible
- Projections remain deterministic and reproducible
- Operations are auditable and safe

## Review Notes

- The remote URL decision is pending (see `DECISION-NEEDED-A1.md`)
- Once configured, the first real push can be executed
- All components are designed for resilience, visibility, and testability as required by the constitution

## Next Steps

1. Derek provides remote URL
2. Run `git remote add origin <URL>`
3. Execute `python3 tools/ops/push_backup.py` for first real backup
4. Verify successful push and test suite still passes