"""An operator-owned sentinel that holds HEARTH dispatch during dual boot."""

import os
from pathlib import Path


def dispatch_paused() -> bool:
    configured = os.environ.get("HEARTH_DISPATCH_PAUSE_FILE")
    return bool(configured and Path(configured).exists())
