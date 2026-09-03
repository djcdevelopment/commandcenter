"""Conservative scheduled recovery for an abandoned image-generation session.

Runs out-of-process, from fx99's five-minute deep-probe timer, over SSH. That makes three
properties non-negotiable, and each one is enforced below rather than assumed:

  * It must be able to SUCCEED. Every ArcServe probe needs `OMEN_ARC_TOKEN`; without it the
    probes fail closed and the verify loop can never pass. The caller
    (`Invoke-ImageGenRecovery.ps1`) resolves the token and refuses to run without it, and
    `_probe_preconditions` re-checks here so a hand-run never destroys state it cannot then
    restore.
  * It must be SELF-LIMITING. The repair action force-kills llama-server. An attempt is
    banked BEFORE the destructive step, so a run interrupted mid-flight still counts and a
    failing recovery escalates instead of re-killing production every five minutes.
  * It must not steal a maintenance window. The ArcServe sentinel is a shared lock; it is
    only removed when it names the session being recovered.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
from pathlib import Path
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

# Must stay BELOW the SSH timeout in fleet/fx99-keepalive/recover-omen-imagegen.sh. The two
# used to be 240 here against 180 there, which guaranteed that the only branch doing real
# work was cut off before it could verify -- leaving the fence held and the next tick
# re-arming the same force-kill.
DEFAULT_VERIFY_TIMEOUT = 120.0

# How many times we will bounce ArcServe for one stale session before refusing to act and
# escalating instead. The fence stays held on escalation: a rung that will not come back is
# correctly OUT of rotation, and holding it there is much cheaper than killing it on a loop.
MAX_ATTEMPTS = 3


def _backend_listening() -> Optional[int]:
    for port in BACKEND_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return port
        except OSError:
            continue
    return None


def _state_path() -> Path:
    return handoff.root() / "recovery-state.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # A recovery that cannot persist its attempt count must not therefore skip the
        # count; the caller treats an unwritable state file as "already at the limit".
        raise


def _attempts_for(snapshot) -> int:
    state = _load_state()
    if state.get("session_id") != snapshot.session_id or state.get("epoch") != snapshot.epoch:
        return 0
    try:
        return int(state.get("attempts", 0))
    except (TypeError, ValueError):
        return 0


def _bank_attempt(snapshot, attempts: int) -> None:
    """Record the attempt BEFORE acting, so an interrupted run still counts."""
    _save_state({
        "session_id": snapshot.session_id,
        "epoch": snapshot.epoch,
        "attempts": attempts,
        "last_attempt_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


def _clear_state() -> None:
    try:
        _state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _sentinel_is_ours(snapshot) -> bool:
    """True when the ArcServe maintenance sentinel is absent or names this session.

    restart-arc.cmd honours this file as a UAC-free stop-only control, so a human
    maintenance window looks exactly like an image session holding ArcServe down. Deleting
    it blind would end that window silently. _start_transition stamps it with
    "owned by <session_id> epoch <n>", which is what we match on.
    """
    try:
        text = ARC_SENTINEL.read_text(encoding="utf-8")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return snapshot.session_id in text


def _probe_preconditions() -> Optional[str]:
    """Return a refusal reason when the repair could not possibly verify itself."""
    if not os.environ.get("OMEN_ARC_TOKEN"):
        return ("OMEN_ARC_TOKEN is not set; every ArcServe probe would fail closed and the "
                "verify loop could never pass")
    return None


def recover(*, now: Optional[float] = None,
            verify_timeout: float = DEFAULT_VERIFY_TIMEOUT) -> dict:
    current = time.time() if now is None else now
    store = GpuTenancyStore()
    snapshot = store.get(POOL)
    if snapshot is None or snapshot.owner != "imagegen":
        _clear_state()
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
            "ok": False, "action": "deferred", "severity": "warn",
            "reason": "stale session still has claimed jobs",
            "claims": [path.stem for path in claims],
        }
    listening = _backend_listening()
    if listening is not None:
        return {
            "ok": False, "action": "deferred", "severity": "warn",
            "reason": "image backend still owns a localhost port", "port": listening,
        }
    if not _sentinel_is_ours(snapshot):
        return {
            "ok": True, "action": "none",
            "reason": "ArcServe maintenance sentinel is not owned by this image session",
        }
    blocked = _probe_preconditions()
    if blocked is not None:
        return {"ok": False, "action": "blocked", "severity": "error", "reason": blocked}

    attempts = _attempts_for(snapshot)
    if attempts >= MAX_ATTEMPTS:
        return {
            "ok": False, "action": "escalated", "severity": "error",
            "reason": "recovery failed %d times for this session; refusing to bounce "
                      "ArcServe again -- the fence stays held so the rung is out of "
                      "rotation, and this needs a human" % attempts,
            "session_id": snapshot.session_id, "epoch": snapshot.epoch,
            "attempts": attempts,
        }

    controller = ImageSessionController(store=store, autostart=False)
    try:
        # Bank the attempt first. If this run is cut off by the SSH timeout after the
        # force-kill but before the release, the NEXT tick must see that it already spent
        # one -- otherwise the counter never advances and the loop is unbounded.
        _bank_attempt(snapshot, attempts + 1)
        controller.record_event("session.recovery_started", snapshot,
                                reason="stale or expired image session (attempt %d/%d)"
                                       % (attempts + 1, MAX_ATTEMPTS))
        ARC_SENTINEL.unlink(missing_ok=True)
        controller.restart_arcserve()
        deadline = time.monotonic() + verify_timeout
        while time.monotonic() < deadline:
            if controller.verify_arcserve():
                if not store.release(
                    resource=POOL, session_id=snapshot.session_id,
                    epoch=snapshot.epoch, reason="scheduled stale-session recovery",
                ):
                    return {"ok": False, "action": "failed", "severity": "error",
                            "reason": "lost session fence"}
                _clear_state()
                released = store.get(POOL)
                if released is not None:
                    controller.record_event("session.recovered", released,
                                            reason="scheduled stale-session recovery")
                return {"ok": True, "action": "restored", "session_id": snapshot.session_id,
                        "epoch": snapshot.epoch, "attempts": attempts + 1}
            time.sleep(5)
        controller.record_event("session.recovery_failed", snapshot,
                                reason="ArcServe failed its warm serviceability probe")
        return {"ok": False, "action": "failed", "severity": "error",
                "reason": "ArcServe failed its warm serviceability probe",
                "attempts": attempts + 1, "attempts_remaining": MAX_ATTEMPTS - attempts - 1}
    finally:
        controller.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-timeout", type=float, default=DEFAULT_VERIFY_TIMEOUT)
    args = parser.parse_args()
    result = recover(verify_timeout=args.verify_timeout)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
