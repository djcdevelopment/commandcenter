"""FX99: receive the operator's key on SSH stdin, never argv or output."""
import os
from pathlib import Path
import re
import sys

root = Path('/home/derek/.config/fleet-scheduler')
os.umask(0o077)
if root.is_symlink():
    raise SystemExit('credential_directory_is_symlink')
root.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(root, 0o700)
key = sys.stdin.readline(512).strip()
if not re.fullmatch(r'apikey_[A-Za-z0-9_]{40,400}', key):
    raise SystemExit('invalid_key_format')
fd = os.open(root / 'typesafe.key', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'w') as out:
    out.write(key + '\n')
    out.flush()
    os.fsync(out.fileno())
print('key ready')
