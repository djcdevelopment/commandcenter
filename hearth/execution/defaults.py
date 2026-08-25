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
            _attach_render_subsystem(_service)
        return _service


def _attach_render_subsystem(service: ExecutionService) -> None:
    """Give the service a render dispatcher, if this host can render.

    Deliberately best-effort and non-fatal. A gateway that cannot import the
    render subsystem, or that has no calibrated lanes, must still boot and serve
    inference -- rendering is one operation, not the door. Without a dispatcher
    `media.render` submissions are refused outright with a clear message, which
    is far better than accepting work into a queue nothing drains.

    Imported lazily so `hearth.execution` keeps no import-time dependency on the
    media subsystem, and so a broken media module cannot take the door down.
    """
    try:
        from hearth.media.execution import RenderSubsystem
    except Exception:  # pragma: no cover - optional subsystem
        return
    try:
        service._render_dispatcher = RenderSubsystem(service=service)
    except Exception:  # pragma: no cover - optional subsystem
        service._render_dispatcher = None


def replace_execution_service(service: Optional[ExecutionService]) -> Optional[ExecutionService]:
    """Swap the singleton for tests or orderly process shutdown."""
    global _service
    with _lock:
        previous = _service
        _service = service
        return previous
