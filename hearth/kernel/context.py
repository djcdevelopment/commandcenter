"""HEARTH kernel context (Stream H-A).

One HearthContext instance is shared by the gateway and its wrapped tools. The
gateway sets `caller` to the resolved identity immediately before dispatching a
tool (tools run synchronously on the event loop, so the field is stable for the
duration of one call); built-in kernel tools read it for ceremony events.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from hearth.kernel.auth import Caller
from hearth.kernel.ledger import Ledger


@dataclass
class HearthContext:
    """Shared kernel state: where the repo is, where events go, who is calling."""

    repo_root: Path
    ledger: Ledger
    caller: Optional[Caller] = None
