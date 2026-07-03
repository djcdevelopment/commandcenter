# Ops: Backup and Restore Procedure

## Configure Remote

To set up the remote for backup:

```bash
git remote add origin <URL>
```

Replace `<URL>` with the actual remote URL (e.g., `https://github.com/djcdevelopment/commandcenter.git` or `ssh://cc-conductor.mshome.net:/home/claude/work/commandcenter-ontology/farmer-repo`).

## Schedule Backup

### Windows (Task Scheduler)

Use this one-liner in a scheduled task:

```cmd
python tools/ops/push_backup.py --dry-run
```

Replace `--dry-run` with no flag to perform actual backup.

### Linux (cron)

Add to crontab (`crontab -e`):

```cron
# Run daily at 2 AM
0 2 * * * /usr/bin/python3 tools/ops/push_backup.py
```

## Restore Procedure

To restore the system from a backup:

1. Clone the repository:
   ```bash
   git clone <URL> <destination>
   ```

2. Verify the baseline test suite:
   ```bash
   cd <destination>
   python -m unittest discover -s tests/workflow
   ```

Expected output: `Ran 130 tests ... OK` (or similar, depending on current state).

> **Note**: The `knowledge/*.json` files are derived from `runs/` and `fixtures/` — never hand-edited. The restore process re-runs projections to regenerate them.

## Safety Notes

- Never force-push. The backup script does not support `--force`.
- Always use `--dry-run` before first real run to verify behavior.
- The script fails loudly on missing origin remote or git errors.

## Decision Required

The remote URL is not yet configured. Please provide the target URL via `DECISION-NEEDED-A1.md`.