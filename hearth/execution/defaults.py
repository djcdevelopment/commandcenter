"""Process-wide execution service shared by every protocol adapter."""

from __future__ import annotations

import threading
from typing import Optional

from .service import ExecutionService

_service: Optional[ExecutionService] = None
_lock = threading.Lock()


def get_execution_service() -> ExecutionService:
    global _service
    with _lock:
        if _service is None:
            _service = ExecutionService()
        return _service


def replace_execution_service(service: Optional[ExecutionService]) -> Optional[ExecutionService]:
    """Swap the singleton for tests or orderly process shutdown."""
    global _service
    with _lock:
        previous = _service
        _service = service
        return previous
