#!/usr/bin/env python3
"""Create private vLLM and HEARTH bearer files once, without printing keys."""

import os
import secrets
from pathlib import Path

root = Path("/home/derek/.config/omen-vllm")
root.mkdir(parents=True, exist_ok=True)
root.chmod(0o700)

paths = [root / "omen-api.env", root / "fx99-api.env", root / "hearth-backends.env"]
if any(path.exists() for path in paths):
    raise SystemExit("Bearer files already exist; leaving them unchanged")

omen = secrets.token_urlsafe(48)
fx99 = secrets.token_urlsafe(48)
contents = (
    f"VLLM_API_KEY={omen}\n",
    f"VLLM_API_KEY={fx99}\n",
    f"OMEN_ARC_TOKEN={omen}\nFX99_VLLM_TOKEN={fx99}\n",
)
for path, content in zip(paths, contents, strict=True):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(content)
print("Created three mode-0600 bearer files")
