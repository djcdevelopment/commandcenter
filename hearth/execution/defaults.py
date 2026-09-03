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
            # Recovery must see the render dispatcher. Constructing with the
            # default used to terminally fail queued media jobs during the tiny
            # window before _attach_render_subsystem ran on gateway startup.
            _service = ExecutionService(recover_pending=False)
            _attach_render_subsystem(_service)
            _attach_imagegen_subsystem(_service)
            _attach_mediagen_subsystem(_service)
            _service.recover_pending()
        return _service


def _attach_render_subsystem(service: ExecutionService) -> None:
    """Give the service a render dispatcher, if this host can render.

    The subsystem does NOT execute renders -- the gateway runs in Windows
    session 0, which has no GPU adapter access. It admits jobs to a handoff
    queue and ingests the interactive agent's results into the ledger, of which
    it remains the sole writer.

    Deliberately best-effort and non-fatal. A gateway that cannot import the
    render subsystem must still boot and serve inference -- rendering is one
    operation, not the door.

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


def _attach_imagegen_subsystem(service: ExecutionService) -> None:
    """Attach the Hearth-owned image queue; execution stays in the user session."""
    try:
        from hearth.imagegen.execution import ImageGenerationSubsystem
    except Exception:  # pragma: no cover - optional subsystem
        return
    subsystem = None
    try:
        subsystem = ImageGenerationSubsystem(service=service)
        service._image_dispatcher = subsystem
    except Exception:  # pragma: no cover - optional subsystem
        # ImageGenerationSubsystem.__init__ now tears down its own daemon threads if it
        # fails partway (it has to -- on that path we never get a reference). This handles
        # the remaining case: construction succeeded and the assignment did not.
        service._image_dispatcher = None
        if subsystem is not None:
            try:
                subsystem.close()
            except Exception:
                pass


def _attach_mediagen_subsystem(service: ExecutionService) -> None:
    """Attach the dedicated one-worker MediaGen parent scheduler."""
    try:
        from hearth.mediagen.execution import MediaGenerationSubsystem
    except Exception:  # pragma: no cover - optional subsystem
        return
    subsystem = None
    try:
        subsystem = MediaGenerationSubsystem(service=service)
        service._media_dispatcher = subsystem
    except Exception:  # pragma: no cover - optional subsystem
        service._media_dispatcher = None
        if subsystem is not None:
            try:
                subsystem.close()
            except Exception:
                pass


def replace_execution_service(service: Optional[ExecutionService]) -> Optional[ExecutionService]:
    """Swap the singleton for tests or orderly process shutdown."""
    global _service
    with _lock:
        previous = _service
        _service = service
        return previous
