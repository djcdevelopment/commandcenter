"""Conservative scheduled recovery for an abandoned image-generation session."""

from __future__ import annotations

import argparse
import json
import socket
import time
from typing import Optional

from hearth.execution.coordination import GpuTenancyStore
from hearth.imagegen import handoff
from hearth.imagegen.session import (
    ARC_SENTINEL,
    POOL,
    ImageSessionController,
)

STALE_SECONDS = 120.0
BACKEND_PORTS = (18188, 18189, 18190)


def _backend_listening() -> Optional[int]:
    for port in BACKEND_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return port
        except OSError:
            continue
    return None


def recover(*, now: Optional[float] = None, verify_timeout: float = 240.0) -> dict:
    current = time.time() if now is None else now
    store = GpuTenancyStore()
    snapshot = store.get(POOL)
    if snapshot is None or snapshot.owner != "imagegen":
        return {"ok": True, "action": "none", "reason": "ArcServe owns the pool"}
    fence_live = snapshot.expires_at > current
    stale = current - snapshot.updated_at > STALE_SECONDS
    agent = handoff.agent_status(now=current)
    if fence_live and not stale:
        return {"ok": True, "action": "none", "reason": "session fence is being renewed"}
    if agent.available and fence_live:
        return {"ok": True, "action": "none", "reason": "interactive agent is healthy"}
    claims = handoff.list_claims()
    if claims:
        return {
            "ok": False, "action": "deferred",
            "reason": "stale session still has claimed jobs",
            "claims": [path.stem for path in claims],
        }
    listening = _backend_listening()
    if listening is not None:
        return {
            "ok": False, "action": "deferred",
            "reason": "image backend still owns a localhost port", "port": listening,
        }

    controller = ImageSessionController(store=store, autostart=False)
    try:
        controller._event("session.recovery_started", snapshot,
                          reason="stale or expired image session")
        ARC_SENTINEL.unlink(missing_ok=True)
        controller._run_restart_task()
        deadline = time.monotonic() + verify_timeout
        while time.monotonic() < deadline:
            if controller._verify_arcserve():
                if not store.release(
                    resource=POOL, session_id=snapshot.session_id,
                    epoch=snapshot.epoch, reason="scheduled stale-session recovery",
                ):
                    return {"ok": False, "action": "failed", "reason": "lost session fence"}
                released = store.get(POOL)
                if released is not None:
                    controller._event("session.recovered", released,
                                      reason="scheduled stale-session recovery")
                return {"ok": True, "action": "restored", "session_id": snapshot.session_id,
                        "epoch": snapshot.epoch}
            time.sleep(5)
        return {"ok": False, "action": "failed",
                "reason": "ArcServe failed its warm serviceability probe"}
    finally:
        controller.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-timeout", type=float, default=240.0)
    args = parser.parse_args()
    result = recover(verify_timeout=args.verify_timeout)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
