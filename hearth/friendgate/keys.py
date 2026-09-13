"""Per-friend keys: minted once, stored as sha256, tied to an IRC account, with limits and an expiry."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

KEY_PREFIX = "fg_"
DEFAULT_MAX_CONCURRENT = 1
DEFAULT_MAX_CONTEXT_TOKENS = 65_536
DEFAULT_TOKENS_PER_DAY = 3_000_000


@dataclass
class FriendKey:
    key_id: str
    sha256: str
    irc_account: str
    display: str = ""
    models: list[str] = field(default_factory=list)  # empty = any eligible model
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS
    tokens_per_day: int = DEFAULT_TOKENS_PER_DAY
    created: float = 0.0
    expires: float | None = None
    revoked: bool = False

    def status(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        if self.revoked:
            return "revoked"
        if self.expires is not None and now > self.expires:
            return "expired"
        return "active"


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class KeyStore:
    """A JSON file of FriendKey records. Reloaded on every lookup when the file changed (friendctl edits it)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._keys: dict[str, FriendKey] = {}
        self._mtime: float | None = None

    def load(self) -> None:
        if not self.path.is_file():
            self._keys, self._mtime = {}, None
            return
        mtime = self.path.stat().st_mtime
        if mtime == self._mtime:
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._keys = {k["sha256"]: FriendKey(**k) for k in data.get("keys", [])}
        self._mtime = mtime

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"keys": [asdict(k) for k in self._keys.values()]}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        self._mtime = self.path.stat().st_mtime

    def all(self) -> list[FriendKey]:
        self.load()
        return sorted(self._keys.values(), key=lambda k: k.created)

    def lookup(self, bearer: str | None) -> FriendKey | None:
        """The record for a presented bearer, or None. Callers check .status() themselves."""
        if not bearer:
            return None
        self.load()
        return self._keys.get(hash_key(bearer.strip()))

    def by_id(self, key_id: str) -> FriendKey | None:
        self.load()
        return next((k for k in self._keys.values() if k.key_id == key_id), None)

    def mint(self, irc_account: str, display: str = "", models: list[str] | None = None, max_concurrent: int = DEFAULT_MAX_CONCURRENT,
             max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS, tokens_per_day: int = DEFAULT_TOKENS_PER_DAY, expires_days: float | None = None) -> tuple[FriendKey, str]:
        """Returns (record, plaintext). The plaintext is shown once and never stored."""
        self.load()
        secret = KEY_PREFIX + secrets.token_urlsafe(24)
        now = time.time()
        rec = FriendKey(
            key_id=f"{irc_account}-{secrets.token_hex(3)}",
            sha256=hash_key(secret),
            irc_account=irc_account,
            display=display or irc_account,
            models=list(models or []),
            max_concurrent=max_concurrent,
            max_context_tokens=max_context_tokens,
            tokens_per_day=tokens_per_day,
            created=now,
            expires=(now + expires_days * 86400) if expires_days else None,
        )
        self._keys[rec.sha256] = rec
        self.save()
        return rec, secret

    def revoke(self, key_id: str) -> bool:
        self.load()
        rec = self.by_id(key_id)
        if rec is None:
            return False
        rec.revoked = True
        self.save()
        return True
