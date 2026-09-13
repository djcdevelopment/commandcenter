"""One usage row per request, attributed to the friend's IRC account, and the per-day token counter."""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


class UsageLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._day: str = ""
        self._today: dict[str, int] = defaultdict(int)  # key_id -> tokens (in + out) since local midnight
        self._warm()

    def _warm(self) -> None:
        """Rebuild today's counters from the file so a gate restart does not reset the daily budget."""
        day = time.strftime("%Y-%m-%d")
        self._day = day
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("day") == day:
                    self._today[row.get("key_id", "?")] += int(row.get("tokens_in", 0)) + int(row.get("tokens_out", 0))

    def _roll(self) -> None:
        day = time.strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self._today = day, defaultdict(int)

    def today(self, key_id: str) -> int:
        with self._lock:
            self._roll()
            return self._today[key_id]

    def record(self, **row: Any) -> dict[str, Any]:
        with self._lock:
            self._roll()
            rec = {"ts": time.time(), "day": self._day, **row}
            rec.setdefault("principal", {"type": "irc_account", "id": row.get("irc_account", "")})
            rec.setdefault("source", {"transport": "https", "adapter": "friend-gate"})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
            self._today[row.get("key_id", "?")] += int(row.get("tokens_in", 0)) + int(row.get("tokens_out", 0))
            return rec

    def summary(self, irc_account: str | None = None) -> list[dict[str, Any]]:
        """Totals per (day, key_id); for `friendctl usage`."""
        totals: dict[tuple[str, str, str], dict[str, Any]] = {}
        if not self.path.is_file():
            return []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if irc_account and row.get("irc_account") != irc_account:
                    continue
                k = (row.get("day", ""), row.get("key_id", ""), row.get("irc_account", ""))
                t = totals.setdefault(k, {"day": k[0], "key_id": k[1], "irc_account": k[2], "calls": 0, "ok": 0, "tokens_in": 0, "tokens_out": 0, "seconds": 0.0})
                t["calls"] += 1
                t["ok"] += 1 if row.get("status") == 200 else 0
                t["tokens_in"] += int(row.get("tokens_in", 0))
                t["tokens_out"] += int(row.get("tokens_out", 0))
                t["seconds"] += float(row.get("duration_ms", 0)) / 1000.0
        return [totals[k] for k in sorted(totals)]
