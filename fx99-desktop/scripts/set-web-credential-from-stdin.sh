#!/bin/sh
set -eu

IFS= read -r password
# Windows PowerShell's native pipeline uses CRLF; POSIX read removes LF only.
password="$(printf '%s' "$password" | tr -d '\r')"
if [ "${#password}" -lt 24 ]; then
    echo 'Refusing a short Sunshine Web UI password.' >&2
    exit 1
fi

trap 'password=' EXIT HUP INT TERM
HOME=/home/derek /usr/bin/sunshine --creds derek "$password" >/dev/null
