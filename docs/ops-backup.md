# Ops: Backup and Restore

## Configure Remote

To enable backups, set up the origin remote:

```bash
git remote add origin <URL>
```

Replace `<URL>` with the actual remote URL (e.g., `https://github.com/djcdevelopment/commandcenter.git` or an SSH path like `claude@cc-conductor.mshome.net:/path/to/repo`).

## Schedule Backup

### Windows (Task Scheduler)

Use this one-liner in a scheduled task:

```cmd
python tools/ops/push_backup.py --dry-run
```

Replace `--dry-run` with no flag to perform actual backups.

### Linux (cron)

Add to crontab (`crontab -e`):

```cron
# Run backup daily at 2 AM
0 2 * * * cd /home/claude/farmer-workspace/pour-a1 && python tools/ops/push_backup.py
```

## Restore Procedure

To restore from a backup:

1. Clone the remote repository:
   ```bash
   git clone <URL> <destination>
   ```

2. Verify the corpus:
   ```bash
   python -m unittest discover -s tests/workflow
   ```

Expected: 110 tests run, all pass (baseline count).

> **Note**: The `knowledge/*.json` files are derived from `runs/` and `fixtures/`. Do not hand-edit them. Restore from the repo and re-project to regenerate.

## Important

- Never force-push. This tool pushes non-force.
- Always test `--dry-run` before actual execution.
- The backup includes all evidence, docs, and code. `runs/` and `fixtures/` are part of the corpus.
- The `evidence_watermark` is derived from the newest observation in `runs/`.
