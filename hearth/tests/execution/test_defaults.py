from __future__ import annotations

from unittest.mock import patch

from hearth.execution import defaults


def test_render_subsystem_is_attached_before_recovery() -> None:
    events: list[str] = []

    class FakeService:
        def __init__(self, *, recover_pending: bool = True) -> None:
            assert recover_pending is False
            events.append("construct")

        def recover_pending(self) -> int:
            events.append("recover")
            return 0

    previous = defaults.replace_execution_service(None)
    try:
        with (
            patch.object(defaults, "ExecutionService", FakeService),
            patch.object(
                defaults,
                "_attach_render_subsystem",
                lambda _service: events.append("attach"),
            ),
        ):
            defaults.get_execution_service()
    finally:
        defaults.replace_execution_service(previous)

    assert events == ["construct", "attach", "recover"]
