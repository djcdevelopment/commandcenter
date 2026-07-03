# DECISION NEEDED: Remote URL for A1 Backup

## Context

The `push_backup.py` script requires a configured `origin` remote to push backups. The current repository has no remote configured.

## Options

1. **Private GitHub repo under `djcdevelopment`**
   - URL: `https://github.com/djcdevelopment/commandcenter.git`
   - Pros: Publicly accessible, well-supported, version history
   - Cons: Requires GitHub account access

2. **Bare repo on `cc-conductor` over SSH**
   - URL: `ssh://cc-conductor/home/djc/backup-repo.git`
   - Pros: Internal, secure, controlled access
   - Cons: Requires SSH access to `cc-conductor`

## Recommendation

Use the GitHub option for simplicity and reliability, unless security requirements mandate the internal repo.

## Decision Request

Please confirm which remote URL to use for the `origin` remote. Once confirmed, run:

```bash
git remote add origin <URL>
python tools/ops/push_backup.py --dry-run
```

Then, if successful, run without `--dry-run` to perform the first real backup.