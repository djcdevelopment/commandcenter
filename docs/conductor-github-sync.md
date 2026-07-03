# Syncing the Conductor Repo to GitHub

The conductor VM (`cc-conductor`, `ssh claude@100.74.110.91`) runs its own git repo at
`~/work/commandcenter` — a different codebase from this one (the fleet orchestration engine,
not the workflow-ontology belief layer). Its `origin` is a local bare repo on the same box
(`/home/claude/work/commandcenter.git`), so until 2026-07-03 it had no off-box backup or
external visibility.

It's now also mirrored to **https://github.com/djcdevelopment/conductor** (private).

## Dataflow

```
cc-conductor (local bare origin, no GitHub credential)
    --ssh-->  OMEN  (this machine, already gh-authenticated as djcdevelopment)
    --push--> github.com/djcdevelopment/conductor
```

OMEN pulls from the conductor over SSH and pushes to GitHub from here. **The conductor VM never
holds a GitHub credential** — same credential-isolation principle used for pouring work into this
repo (see `docs/conductor-pour-howto.md`): keep GitHub tokens off the VMs, push only from OMEN.

## Running it

```
python tools/ops/sync_conductor_to_github.py [--dry-run]
```

First run clones a full mirror to `C:\work\conductor-mirror.git` (a sibling of this repo, not
inside it — it's a different codebase's full history, not something this repo's test suite or
corpus guard should ever see). Every run after that just fetches the conductor's current `main`
into that mirror and pushes it to GitHub. It's idempotent — safe to run any time, prints
`before -> after` SHAs so you can see whether anything moved.

## When to run it

There's no automation wired up (not a cron job, not triggered by conductor commits) — run it
manually after conductor-side work you want visible on GitHub, e.g. after landing a fix like the
2026-07-03 target-repo-adapter revert (`566b1f2`, `07fec83`).

## If it fails

- SSH to the conductor times out or refuses: conductor is a VM, it can be down/rebuilding — this
  is expected occasionally, not an emergency.
- Push to GitHub is rejected (non-fast-forward): something was pushed to
  `github.com/djcdevelopment/conductor` `main` from somewhere else since the last sync (unlikely —
  this script is currently the only writer). Investigate before force-pushing.
