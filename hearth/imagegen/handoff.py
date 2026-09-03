"""Atomic sidecars joining the session-0 gateway to the interactive .NET agent."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

AGENT_STALE_SECONDS = 120.0


def root() -> Path:
    configured = os.environ.get("HEARTH_IMAGEGEN_HANDOFF")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "var" / "imagegen"


def _dirs() -> dict[str, Path]:
    base = root()
    return {
        "queue": base / "queue",
        "claims": base / "claims",
        "results": base / "results",
        "cancels": base / "cancels",
    }


def ensure_dirs() -> None:
    for path in _dirs().values():
        path.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".imagegen-", suffix=".tmp", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def enqueue_job(job_id: str, spec: dict, *, deadline_s: Optional[int], principal: Optional[str],
                traceparent: Optional[str] = None) -> None:
    ensure_dirs()
    path = _dirs()["queue"] / (job_id + ".json")
    if path.exists() or (_dirs()["claims"] / path.name).exists():
        return
    payload = {
        "schema": "imagegen.handoff.v1", "job_id": job_id, "spec": spec,
        "deadline_s": deadline_s, "principal": principal, "queued_at": utc_now(),
    }
    if traceparent:
        payload["traceparent"] = traceparent
    _write(path, payload)


def list_queued() -> list[Path]:
    ensure_dirs()
    return sorted(_dirs()["queue"].glob("job_*.json"), key=lambda p: p.stat().st_mtime)


def list_claims() -> list[Path]:
    ensure_dirs()
    return sorted(_dirs()["claims"].glob("job_*.json"))


def list_results() -> list[Path]:
    ensure_dirs()
    return sorted(_dirs()["results"].glob("job_*.json"))


def read_claim(job_id: str) -> Optional[dict]:
    return _read(_dirs()["claims"] / (job_id + ".json"))


def dequeue_job(job_id: str) -> None:
    (_dirs()["queue"] / (job_id + ".json")).unlink(missing_ok=True)


def request_cancel(job_id: str, *, reason: str) -> None:
    _write(_dirs()["cancels"] / (job_id + ".json"), {
        "schema": "imagegen.cancel.v1", "job_id": job_id,
        "reason": reason, "requested_at": utc_now(),
    })


def clear_cancel(job_id: str) -> None:
    (_dirs()["cancels"] / (job_id + ".json")).unlink(missing_ok=True)


def clear_claim(job_id: str) -> None:
    (_dirs()["claims"] / (job_id + ".json")).unlink(missing_ok=True)


def clear_result(job_id: str) -> None:
    (_dirs()["results"] / (job_id + ".json")).unlink(missing_ok=True)


def active_count() -> int:
    return len(list_queued()) + len(list_claims())


def abandoned_claims(*, now: Optional[float] = None) -> list:
    """Claims no live worker can still be advancing.

    A claim file protects ArcServe from being restarted under a running job -- but only
    while a worker could actually still be running it. When the agent heartbeat is gone AND
    the claim itself has not been touched for longer than the heartbeat window, nothing is
    going to finish it, and treating it as protective wedges ArcServe down forever with no
    automated escape. That is the failure this distinguishes.

    Returns the abandoned subset; an empty list means every claim is still plausibly live.
    """
    claims = list_claims()
    if not claims:
        return []
    if agent_status(now=now).available:
        return []
    current = time.time() if now is None else now
    abandoned = []
    for path in claims:
        try:
            age = current - path.stat().st_mtime
        except OSError:
            # The file vanished mid-scan: it is not holding anything down.
            abandoned.append(path)
            continue
        if age > AGENT_STALE_SECONDS:
            abandoned.append(path)
    return abandoned


def claims_are_abandoned(*, now: Optional[float] = None) -> bool:
    """True when there ARE claims and every one of them is abandoned."""
    claims = list_claims()
    return bool(claims) and len(abandoned_claims(now=now)) == len(claims)


@dataclass(frozen=True)
class AgentStatus:
    available: bool
    age_seconds: Optional[float]
    detail: str
    record: Optional[dict]

    def to_dict(self) -> dict:
        return {
            "available": self.available, "age_seconds": self.age_seconds,
            "detail": self.detail, "record": self.record,
        }


def _pid_running(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        # Query only: never signal a process while deciding whether a heartbeat
        # is still authoritative.
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, value)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(value, 0)
    except (OSError, ValueError):
        return False
    return True


def agent_status(*, now: Optional[float] = None) -> AgentStatus:
    path = root() / "agent-heartbeat.json"
    record = _read(path)
    if record is None:
        return AgentStatus(False, None, "no interactive agent heartbeat", None)
    current = time.time() if now is None else now
    try:
        age = max(0.0, current - path.stat().st_mtime)
    except OSError:
        return AgentStatus(False, None, "heartbeat disappeared", None)
    process_live = _pid_running(record.get("pid"))
    ready = bool(record.get("ready")) and age <= AGENT_STALE_SECONDS and process_live
    detail = "ready" if ready else (
        "heartbeat process is no longer running" if not process_live
        else "heartbeat stale or agent not ready"
    )
    return AgentStatus(ready, age, detail, record)
