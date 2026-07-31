"""Opaque identifiers for HEARTH execution entities."""

from __future__ import annotations

import secrets


def _new(prefix: str) -> str:
    # 128 random bits keeps identifiers globally safe without coordinating a
    # database sequence. Lowercase hex is deliberately shell/IRC friendly.
    return f"{prefix}_{secrets.token_hex(16)}"


def new_request_id() -> str:
    return _new("req")


def new_job_id() -> str:
    return _new("job")


def new_invocation_id() -> str:
    return _new("inv")


def new_artifact_id() -> str:
    return _new("art")


def new_event_id() -> str:
    return _new("evt")
