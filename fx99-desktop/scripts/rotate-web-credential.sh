#!/bin/sh
set -eu

password="$(openssl rand -base64 36 | tr -d '\n')"
trap 'password=' EXIT HUP INT TERM

HOME=/home/derek /usr/bin/sunshine --creds derek "$password" >/dev/null
