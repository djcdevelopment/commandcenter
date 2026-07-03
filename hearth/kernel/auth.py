"""HEARTH caller identity & auth (Stream H-A, frozen contract 3).

Callers present a key in the X-Hearth-Key HTTP header; the registry file
``hearth/etc/callers.json`` maps key -> {id, runner_class, node}. A caller is
(who) x (runner class) x (node). Unknown or missing keys are rejected AND the
rejection is itself recorded as a ledger event (tool="__auth__", ok=false) —
raw keys are never written to the ledger, only a sha256 digest.
"""

from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from hearth.kernel.ledger import RUNNER_CLASSES, Ledger, new_event

DEFAULT_CALLERS_PATH = Path(__file__).resolve().parents[1] / "etc" / "callers.json"
AUTH_TOOL = "__auth__"
HEADER_NAME = "X-Hearth-Key"


@dataclass(frozen=True)
class Caller:
    """One authenticated caller identity."""

    id: str
    runner_class: str
    node: str

    def as_dict(self) -> dict:
        return {"id": self.id, "runner_class": self.runner_class, "node": self.node}


def _key_fingerprint(key: Optional[str]) -> str:
    if key is None:
        return "absent"
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class AuthRegistry:
    """Resolves X-Hearth-Key values to Caller identities from callers.json."""

    def __init__(self, callers_path: Optional[Path | str] = None,
                 ledger: Optional[Ledger] = None) -> None:
        self.callers_path = Path(callers_path) if callers_path else DEFAULT_CALLERS_PATH
        self.ledger = ledger
        self._callers = self._load()

    def _load(self) -> dict[str, Caller]:
        raw = json.loads(self.callers_path.read_text(encoding="utf-8"))
        callers = {}
        for key, entry in raw.items():
            if entry.get("runner_class") not in RUNNER_CLASSES:
                raise ValueError(
                    f"callers.json entry {entry.get('id')!r}: runner_class must be one of {RUNNER_CLASSES}"
                )
            callers[key] = Caller(id=entry["id"], runner_class=entry["runner_class"],
                                  node=entry["node"])
        return callers

    def resolve(self, key: Optional[str]) -> Optional[Caller]:
        """Return the Caller for `key`, or None after recording the rejection.

        The rejection event carries a fingerprint of the presented key (never the
        key itself) in `error`, under a synthetic __unauthenticated__ identity.
        """
        caller = self._callers.get(key) if key is not None else None
        if caller is not None:
            return caller
        if self.ledger is not None:
            self.ledger.append(new_event(
                {"id": "__unauthenticated__", "runner_class": "human",
                 "node": socket.gethostname()},
                AUTH_TOOL,
                ok=False,
                error=f"auth: unknown or missing {HEADER_NAME} key ({_key_fingerprint(key)})",
            ))
        return None
