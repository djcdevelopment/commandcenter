#!/usr/bin/env bash
# omen-offbox-backup.sh — offbox sync of OMEN run records and ledgers to FX99
set -euo pipefail

DEST_HOST="derek@192.168.12.220"
DEST_BASE="/home/derek/backups/omen-linux"

EXCLUDES=(
  --exclude="callers*.json"
  --exclude="*.key"
  --exclude="*.secret"
  --exclude="*.env"
  --exclude="caller-key"
  --exclude="web.token"
  --exclude="token"
  --exclude="arc-*.log"
  --exclude="caddy-*"
  --exclude="gateway-task-*.log"
  --exclude="funnel-*"
)

echo "Starting off-box backup to $DEST_HOST:$DEST_BASE at $(date -u +%FT%TZ)"

# 1. hearth-production/var
if [[ -d "$HOME/hearth-production/var" ]]; then
  echo "Syncing ~/hearth-production/var..."
  ssh "$DEST_HOST" "mkdir -p $DEST_BASE/hearth-production/var"
  rsync -avz --delete "${EXCLUDES[@]}" "$HOME/hearth-production/var/" "$DEST_HOST:$DEST_BASE/hearth-production/var/"
fi

# 2. hearth-production/runs
if [[ -d "$HOME/hearth-production/runs" ]]; then
  echo "Syncing ~/hearth-production/runs..."
  ssh "$DEST_HOST" "mkdir -p $DEST_BASE/hearth-production/runs"
  rsync -avz --delete "${EXCLUDES[@]}" "$HOME/hearth-production/runs/" "$DEST_HOST:$DEST_BASE/hearth-production/runs/"
fi

# 3. lab-rnd/runs and evidence
if [[ -d "$HOME/work/lab-rnd/runs" ]]; then
  echo "Syncing ~/work/lab-rnd/runs..."
  ssh "$DEST_HOST" "mkdir -p $DEST_BASE/lab-rnd/runs"
  rsync -avz --delete "${EXCLUDES[@]}" "$HOME/work/lab-rnd/runs/" "$DEST_HOST:$DEST_BASE/lab-rnd/runs/"
fi

if [[ -d "$HOME/work/lab-rnd/evidence" ]]; then
  echo "Syncing ~/work/lab-rnd/evidence..."
  ssh "$DEST_HOST" "mkdir -p $DEST_BASE/lab-rnd/evidence"
  rsync -avz --delete "${EXCLUDES[@]}" "$HOME/work/lab-rnd/evidence/" "$DEST_HOST:$DEST_BASE/lab-rnd/evidence/"
fi

# 4. deepagents-linux/runs
if [[ -d "$HOME/work/deepagents-linux/runs" ]]; then
  echo "Syncing ~/work/deepagents-linux/runs..."
  ssh "$DEST_HOST" "mkdir -p $DEST_BASE/deepagents-linux/runs"
  rsync -avz --delete "${EXCLUDES[@]}" "$HOME/work/deepagents-linux/runs/" "$DEST_HOST:$DEST_BASE/deepagents-linux/runs/"
fi

echo "Off-box backup finished successfully at $(date -u +%FT%TZ)"
