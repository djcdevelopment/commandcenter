"""Who asked for this dispatch — pushed per-call, read by the observation emitter.

Mirrors `hearth.toolsurface._scope`'s `caller_scope` / `caller_repo_access` exactly: a
ContextVar the gateway sets for the duration of one call, always reset in a `finally` so a
raising tool cannot leak one caller's identity into the next call on this thread.

Why a ContextVar and not a return-value convention like `_ledger_model`: `local_generate`
is called **in-process, bypassing the gateway**, from a dozen sites —
`hearth/commander/refine.py`, `hearth/callers/doorcheck.py`, and the whole
`hearth/experiments/` suite. Those experiment runs are backend x task matrices, the
richest varied-axis evidence in the repo and exactly what the association engine's
"something must have varied" gate is starving for. A hook that only fired on the gateway
path would emit nothing for any of them, and a ContextVar is inherited by nested calls, so
`refine_idea`'s inner model fan is covered too.

ABSENT IDENTITY MEANS NO OBSERVATION. That is a feature, not a gap: a `doorcheck` probe or
an ad-hoc script dispatch is not evidence about anything, and inventing a `workflow_id` for
it would manufacture the independence the ≥2-distinct-workflow gate exists to test. A
script that genuinely wants its dispatches recorded says so:

    with dispatch_identity(DispatchIdentity("experiment-matrix", "local", platform.node())):
        ...
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class DispatchIdentity:
    """The requester of a dispatch. `caller_id` becomes the observation's `workflow_id`
    (the occasion), never its `builder_id` (the executor) — see hearth/observation/emit.py
    for why conflating them would make two independent gates test one axis."""

    caller_id: str
    runner_class: str
    node: str
    task_id: Optional[str] = None
    profile: Optional[str] = None


_identity: ContextVar[Optional[DispatchIdentity]] = ContextVar(
    "hearth_dispatch_identity", default=None)


def current_identity() -> Optional[DispatchIdentity]:
    """The identity in force for this call, or None when nothing pushed one."""
    return _identity.get()


@contextlib.contextmanager
def dispatch_identity(identity: Optional[DispatchIdentity]) -> Iterator[None]:
    """Push a dispatch identity for the duration of one call.

    `None` is a no-op, leaving whatever is already in force — so a nested call does not
    erase its parent's identity by passing nothing.
    """
    if identity is None:
        yield
        return
    token = _identity.set(identity)
    try:
        yield
    finally:
        _identity.reset(token)
