# Ops Backup

`tools/ops/push_backup.py` stages the repository, commits only when there are tracked or untracked
changes, and pushes the current `HEAD` to the configured `origin` remote. It never force-pushes.

## Configure Origin

The A1 remote is already the private GitHub repository:

```bash
git remote add origin https://github.com/djcdevelopment/commandcenter.git
```

If `origin` already exists, verify it instead:

```bash
git remote -v
```

## Dry Run

Preview the backup plan without changing anything:

```bash
python tools/ops/push_backup.py --dry-run
```

## Scheduling

Windows Task Scheduler one-liner:

```cmd
cd /d C:\work\commandcenter && python tools\ops\push_backup.py
```

Linux cron line:

```cron
0 2 * * * cd /home/claude/work/commandcenter && python tools/ops/push_backup.py
```

## Restore Procedure

1. Clone the repository.
2. Change into the clone root.
3. Run the workflow suite:

```bash
python -m unittest discover -s tests/workflow
```

Expected result at landing time: `Ran 130 tests ... OK`.

4. Inspect `runs/` and `knowledge/` to confirm evidence and derived projections are present.
