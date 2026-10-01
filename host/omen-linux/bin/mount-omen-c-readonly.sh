#!/usr/bin/env bash
set -euo pipefail

# Read the suspended BitLocker C: volume using its on-disk clear key.
# Both the BitLocker layer and the NTFS layer are read-only.
expected_uuid=26f260c6-c916-43c0-bad5-f7affc6e1b59
device=$(blkid -U "$expected_uuid" || true)
stage=/mnt/omen-c-dislocker
target=/mnt/omen-c-read
bundle=/home/derek/.local/share/dislocker-readonly/root

if [[ $(id -u) -ne 0 ]]; then
  echo 'Run this script with sudo on OMEN.' >&2
  exit 1
fi
if [[ -z "$device" || ! -b "$device" ]]; then
  echo 'C: volume was not found; refusing to continue.' >&2
  exit 1
fi
if [[ $(blkid -s UUID -o value "$device") != "$expected_uuid" ]]; then
  echo 'C: device identity changed; refusing to continue.' >&2
  exit 1
fi

mkdir -p "$stage" "$target"
if ! mountpoint -q "$stage"; then
  export LD_LIBRARY_PATH="$bundle/usr/lib:$bundle/usr/lib/x86_64-linux-gnu"
  "$bundle/usr/bin/dislocker-fuse" -r -c -V "$device" -- "$stage"
fi

for _ in $(seq 1 20); do
  [[ -e "$stage/dislocker-file" ]] && break
  sleep 0.25
done
if [[ ! -e "$stage/dislocker-file" ]]; then
  echo 'No clear-key view appeared; C: was not mounted.' >&2
  exit 1
fi

if ! mountpoint -q "$target"; then
  mount -t ntfs3 -o loop,ro "$stage/dislocker-file" "$target"
fi

options=$(findmnt -no OPTIONS "$target")
if [[ ",$options," != *,ro,* ]]; then
  echo 'C: is mounted without read-only protection; refusing to continue.' >&2
  exit 1
fi

findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS "$target"
for name in commandcenter deepagents-poc; do
  if [[ -d "$target/work/$name" ]]; then
    echo "Readable: $target/work/$name"
  else
    echo "Absent: $target/work/$name"
  fi
done
