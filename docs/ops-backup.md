# Ops: Backup and Replication

This document describes how to configure, schedule, and restore the commandcenter workflow-ontology repository for fleet-wide replication.

## Configure the Remote

Before using `push_backup.py`, configure the remote to your target repository:

```bash
# For a private GitHub repo (recommended)
git remote add origin https://github.com/djcdevelopment/commandcenter.git

# For a bare repo on cc-conductor over SSH
git remote add origin claude@cc-conductor:/path/to/bare/repo.git
```

> **Note**: The remote URL is Derek's decision. Confirm with him before proceeding.

## Schedule the Backup

### Windows (Task Scheduler)

Use the following one-liner in a scheduled task:

```cmd
python -m tools.ops.push_backup --dry-run
```

> **Tip**: Run with `--dry-run` initially to verify the plan before enabling actual pushes.

### Linux (cron)

Add to your crontab (`crontab -e`) to run daily at 2 AM:

```cron
0 2 * * * python -m tools.ops.push_backup --dry-run
```

> **Note**: Replace `--dry-run` with no flag after verification to enable actual backups.

## Restore Procedure

To restore the repository from a backup:

1. Clone the remote repository:
   ```bash
   git clone https://github.com/djcdevelopment/commandcenter.git
   cd commandcenter
   ```

2. Run the full test suite to verify integrity:
   ```bash
   python -m unittest discover -s tests/workflow
   ```

   > **Expected**: `Ran 130 tests ... OK` (or similar, depending on current suite count).

3. Verify the knowledge and runs directories are intact:
   - `knowledge/*.json` should be present and valid
   - `runs/` should contain at least one run (e.g., `omen-5070-hwbaseline-2026-07-02/`)

## Safety Notes

- `push_backup.py` never force-pushes.
- Always use `--dry-run` first to verify the plan.
- The script fails loudly on missing remote, incorrect branch, or git errors.
- Never modify `knowledge/*.json` by hand — they are projections.

> **Warning**: This backup mechanism is designed for fleet-wide replication. Do not use it for local development without understanding the implications.