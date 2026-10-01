#!/usr/bin/env bash
set -euo pipefail

# Mount Windows drives without sudo using udisksctl

E_UUID="109CC4439CC42556"
C_PART_UUID="26f260c6-c916-43c0-bad5-f7affc6e1b59"
C_NTFS_UUID="080A17B10A179B30"

# 1. Mount E: (read-write)
E_DEV="/dev/disk/by-uuid/$E_UUID"
if [ -b "$E_DEV" ]; then
    E_MNT=$(findmnt -no TARGET "$E_DEV" || true)
    if [ -z "$E_MNT" ]; then
        udisksctl mount -b "$E_DEV" --no-user-interaction >/dev/null 2>&1 || udisksctl mount -b "$E_DEV"
        E_MNT=$(findmnt -no TARGET "$E_DEV")
    fi
    ln -sfn "$E_MNT" "$HOME/win_e"
    echo "E: drive mounted at $E_MNT (symlink: ~/win_e) [$(findmnt -no OPTIONS "$E_DEV")]"
else
    echo "E: drive ($E_UUID) not found" >&2
fi

# 2. Unlock & Mount C:
C_MAPPER="/dev/disk/by-uuid/$C_NTFS_UUID"
if [ ! -b "$C_MAPPER" ]; then
    C_PART="/dev/disk/by-uuid/$C_PART_UUID"
    if [ -b "$C_PART" ]; then
        udisksctl unlock -b "$C_PART" >/dev/null 2>&1 || true
    fi
fi

if [ -b "$C_MAPPER" ]; then
    C_MNT=$(findmnt -no TARGET "$C_MAPPER" || true)
    if [ -z "$C_MNT" ]; then
        # Try read-write first, fallback to read-only if volume is dirty
        if ! udisksctl mount -b "$C_MAPPER" --no-user-interaction >/dev/null 2>&1; then
            udisksctl mount -b "$C_MAPPER" -o ro --no-user-interaction >/dev/null 2>&1 || udisksctl mount -b "$C_MAPPER" -o ro
        fi
        C_MNT=$(findmnt -no TARGET "$C_MAPPER")
    fi
    ln -sfn "$C_MNT" "$HOME/win_c"
    echo "C: drive mounted at $C_MNT (symlink: ~/win_c) [$(findmnt -no OPTIONS "$C_MAPPER")]"
else
    echo "C: drive ($C_NTFS_UUID) not unlocked/found" >&2
fi
