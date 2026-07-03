# Ops: Backup and Restore Procedure

## Configure Remote

To enable backups, configure the origin remote:

```bash
git remote add origin https://github.com/djcdevelopment/commandcenter.git
```

> **Note**: The actual URL must be confirmed with Derek. Use a private GitHub repo under `djcdevelopment` or a bare repo on `cc-conductor` over SSH.

## Schedule Backup

### Windows (Task Scheduler)

Use this one-liner in a scheduled task:

```cmd
python -m tools.ops.push_backup --dry-run
```

> **Tip**: Replace `--dry-run` with no args after testing.

### Linux (cron)

Add to crontab (`crontab -e`):

```cron
0 2 * * * python -m tools.ops.push_backup --dry-run
```

> This runs daily at 2 AM UTC. Replace `--dry-run` with no args after testing.

## Restore Procedure

To restore from backup:

1. Clone the repository:
   ```bash
   git clone https://github.com/djcdevelopment/commandcenter.git
   cd commandcenter
   ```

2. Run the test suite to verify:
   ```bash
   python -m unittest discover -s tests/workflow
   ```

   > Expect: `Ran 130 tests ... OK`

3. Verify `runs/` and `knowledge/` are intact.

## Notes

- `push_backup.py` is idempotent and safe to run multiple times.
- Always test with `--dry-run` first.
- The backup includes all evidence, docs, and projections.
- Never force-push; this preserves history.

> **Warning**: This procedure assumes the remote is correctly configured. If `git remote -v` shows no origin, run `git remote add origin <URL>` before backing up.