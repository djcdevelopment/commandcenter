#!/usr/bin/env bash
set -euo pipefail

# Mount the existing Windows E: NTFS volume without writing to it.
expected_uuid=109CC4439CC42556
target=/mnt/omen-e-read

if [[ $(id -u) -ne 0 ]]; then
  echo 'Run this script with sudo on OMEN.' >&2
  exit 1
fi

device=$(blkid -U "$expected_uuid" || true)
if [[ -z "$device" || ! -b "$device" ]]; then
  echo 'E: volume was not found; refusing to continue.' >&2
  exit 1
fi
if [[ $(blkid -s UUID -o value "$device") != "$expected_uuid" ||
      $(blkid -s TYPE -o value "$device") != ntfs ]]; then
  echo 'E: volume identity changed; refusing to continue.' >&2
  exit 1
fi

mkdir -p "$target"
if ! mountpoint -q "$target"; then
  mount -t ntfs3 -o ro "$device" "$target"
fi
options=$(findmnt -no OPTIONS "$target")
if [[ ",$options," != *,ro,* ]]; then
  echo 'E: is mounted without read-only protection; refusing to continue.' >&2
  exit 1
fi
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS "$target"
test -r "$target"
echo "Readable: $target"
