"""HEARTH kernel context (Stream H-A).

One HearthContext instance is shared by the gateway and its wrapped tools. The
gateway sets `caller` to the resolved identity immediately before dispatching a
tool. Context-local storage also supports the isolated threaded listener without
letting concurrent worker/status requests overwrite each other's identity.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from hearth.kernel.auth import Caller
from hearth.kernel.ledger import Ledger


@dataclass
class HearthContext:
    """Shared kernel state: where the repo is, where events go, who is calling."""

    repo_root: Path
    ledger: Ledger
    _caller: ContextVar = field(default_factory=lambda: ContextVar('hearth_caller',default=None),
                                init=False,repr=False)

    @property
    def caller(self) -> Optional[Caller]:
        return self._caller.get()

    @caller.setter
    def caller(self, value: Optional[Caller]) -> None:
        self._caller.set(value)
