# DECISION-NEEDED: Remote URL for Backup

## Context

The `push_backup.py` script requires a configured `origin` remote to push backups. Currently, no remote is configured.

## Options

1. **Private GitHub repo** under `djcdevelopment`:
   - URL: `https://github.com/djcdevelopment/commandcenter.git`
   - Pros: Public access control, version history, easy collaboration.
   - Cons: Requires GitHub account and access.

2. **Bare repo on `cc-conductor` over SSH**:
   - URL: `claude@cc-conductor.mshome.net:/home/claude/work/commandcenter-ontology/farmer-repo`
   - Pros: Internal, secure, no external dependencies.
   - Cons: Requires SSH access and server maintenance.

## Request

Please confirm which remote URL to use for `git remote add origin <URL>`. This is required for the first real backup push.

> **Note**: This is a decision request, not a stop condition. Tasks 1–4 are complete and the baseline test suite remains green. The first real push will wait for your answer.