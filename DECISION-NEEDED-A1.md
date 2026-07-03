# DECISION-NEEDED: Remote URL for Backup

## Context

The `push_backup.py` script requires a configured `origin` remote to push backups. The current repository has no remote configured.

## Options

1. **Private GitHub repo under `djcdevelopment`**:
   - URL: `https://github.com/djcdevelopment/commandcenter.git`
   - Pros: Publicly accessible, version-controlled, widely supported.
   - Cons: Requires GitHub access.

2. **Bare repo on `cc-conductor` over SSH**:
   - URL: `ssh://cc-conductor/~/commandcenter.git`
   - Pros: Internal, secure, controlled by team.
   - Cons: Requires SSH access and server setup.

## Request

Please confirm which remote URL to use for `git remote add origin <URL>`. Once confirmed, the first real backup push can proceed.