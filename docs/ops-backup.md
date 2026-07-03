# Ops: Backup and Restore Procedure

## Configure Remote

To enable backups, set up the origin remote:

```bash
git remote add origin <REPO_URL>
```

Replace `<REPO_URL>` with the actual repository URL (e.g., `https://github.com/djcdevelopment/commandcenter.git` or `claude@cc-conductor.mshome.net:/path/to/repo`).

## Schedule Backup

### Windows (Task Scheduler)

Use this one-liner in a scheduled task:

```cmd
python tools/ops/push_backup.py --dry-run
```

Replace `--dry-run` with no flag for actual execution.

### Linux (cron)

Add to crontab with:

```cron
0 2 * * * python3 tools/ops/push_backup.py
```

This runs daily at 2:00 AM.

## Restore Procedure

To restore from backup:

1. Clone the repository:
   ```bash
   git clone <REPO_URL> <DEST_DIR>
   ```

2. Run the test suite to verify:
   ```bash
   python -m unittest discover -s tests/workflow
   ```

Expected output: `Ran 130 tests ... OK`

## Notes

- The backup script stages all changes, commits only if there are changes, and pushes to origin.
- Use `--dry-run` to preview actions before execution.
- Never force-push; the script will fail loudly if push fails.
- The backup includes all evidence in `runs/` and `knowledge/` as required.
- The `evidence_watermark` is preserved in the corpus and used for deterministic projections.