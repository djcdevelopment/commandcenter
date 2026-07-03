# Ops: Backup and Restore Procedure

## Configure Remote

To enable backup, set up the origin remote:

```bash
# For GitHub (private repo under djcdevelopment)
$ git remote add origin https://github.com/djcdevelopment/commandcenter.git

# For bare repo on cc-conductor over SSH
$ git remote add origin ssh://cc-conductor/home/djc/backup-repo.git
```

## Schedule Backup

### Windows (Task Scheduler)

Use this one-liner in a scheduled task:

```cmd
python tools/ops/push_backup.py --dry-run
```

Replace `--dry-run` with no args after testing.

### Linux (cron)

Add to crontab (`crontab -e`):

```cron
# Run daily at 2:00 AM
0 2 * * * /usr/bin/python3 tools/ops/push_backup.py
```

## Restore Procedure

To restore from backup:

1. Clone the remote repository:
   ```bash
   git clone https://github.com/djcdevelopment/commandcenter.git
   cd commandcenter
   ```

2. Run the test suite to verify:
   ```bash
   python -m unittest discover -s tests/workflow
   ```

Expected output: `Ran 110 tests ... OK`

> **Note**: The baseline test count may vary slightly over time. Verify the test suite passes with the current codebase.