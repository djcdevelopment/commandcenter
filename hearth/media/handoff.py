"""File handoff between the session-0 gateway and the interactive render agent.

WHY THIS EXISTS -- WINDOWS SESSION ISOLATION
--------------------------------------------
The HEARTH gateway runs under an S4U scheduled task in **session 0**, which has
no GPU adapter access: D3D11 device creation returns 887a0004
(DXGI_ERROR_NOT_FOUND) and every QSV render dies at ``init_hw_device``. Renders
must therefore execute in an interactive session, in a different process.

That is a real OS-level execution-context requirement, not an implementation
detail, so the ownership split is deliberate and explicit:

    GATEWAY  owns authority, admission, job lifecycle, and the ledger.
    AGENT    owns GPU process execution, and nothing else.

WHY NOT JUST LET THE AGENT WRITE THE LEDGER
-------------------------------------------
Because ExecutionLedger is single-process by construction, and this was measured
rather than assumed. Its `append` takes a **threading.Lock**, assigns sequence
via ``SELECT MAX(sequence)+1``, and writes at ``offset = stat().st_size``. Two
processes appending concurrently to one ledger produced, in a direct test:

    duplicate sequence 17 written twice   (canonical stream corrupted)
    sqlite OperationalError, then a cascade of PermissionError
    80 attempted appends -> 18 events written

CapacityLeaseStore being cross-process safe does NOT imply the ledger is. So the
agent never touches the ledger. It communicates through directories, and the
gateway remains the only writer of state transitions.

THE PROTOCOL
------------
Three directories under ``hearth/var/render``, plus a heartbeat::

    queue/<job_id>.json     gateway -> agent   validated job, ready to execute
    claims/<job_id>.json    agent -> gateway   claimed: lane, pid, started_at
    results/<job_id>.json   agent -> gateway   terminal receipt
    agent.heartbeat.json    agent -> gateway   liveness + capability

A claim is an atomic ``os.replace`` out of ``queue/``. Whoever wins the rename
owns the job; a loser sees FileNotFoundError and moves on. No locks, no port, no
second door.

FAILURE SEMANTICS
-----------------
* no agent running -> jobs stay QUEUED, not failed. The work is still valid; the
  executor is merely absent, and it will come back.
* agent dies mid-render -> its claim is recoverable: the pid and start time in
  the claim identify the orphan, and the job returns to ``queue/``.
* gateway restarts -> an in-flight render keeps going. The control plane
  restarting must not kill a GPU process, and the result is ingested when the
  gateway returns.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

# An agent is considered gone if its heartbeat is older than this. Generous: a
# long 4K render must not make a busy agent look dead.
HEARTBEAT_STALE_S = 120.0

# A claim older than this with no result and no live process is an orphan.
CLAIM_ORPHAN_S = 30.0


def render_root() -> Path:
    configured = os.environ.get("HEARTH_RENDER_STATE")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "var" / "render"


def queue_dir() -> Path:
    return render_root() / "queue"


def claims_dir() -> Path:
    return render_root() / "claims"


def results_dir() -> Path:
    return render_root() / "results"


def heartbeat_path() -> Path:
    return render_root() / "agent.heartbeat.json"


def ensure_dirs() -> None:
    for directory in (queue_dir(), claims_dir(), results_dir()):
        directory.mkdir(parents=True, exist_ok=True)


def _write_atomic(target: Path, payload: dict) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------ gateway

def enqueue_job(job_id: str, arguments: dict, *, deadline_s: Optional[int] = None,
                principal: Optional[str] = None) -> Path:
    """Publish a validated job for the agent. Gateway side."""
    ensure_dirs()
    return _write_atomic(queue_dir() / ("%s.json" % job_id), {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "arguments": dict(arguments),
        "deadline_s": deadline_s,
        "principal": principal,
        "queued_at": time.time(),
    })


def dequeue_job(job_id: str) -> None:
    """Remove a queue entry (cancelled, or already terminal). Gateway side."""
    for path in (queue_dir() / ("%s.json" % job_id),):
        try:
            path.unlink()
        except OSError:
            pass


def list_queued() -> list:
    ensure_dirs()
    return sorted(queue_dir().glob("*.json"))


def list_claims() -> list:
    ensure_dirs()
    return sorted(claims_dir().glob("*.json"))


def list_results() -> list:
    ensure_dirs()
    return sorted(results_dir().glob("*.json"))


def read_claim(job_id: str) -> Optional[dict]:
    return _read(claims_dir() / ("%s.json" % job_id))


def read_result(job_id: str) -> Optional[dict]:
    return _read(results_dir() / ("%s.json" % job_id))


def clear_claim(job_id: str) -> None:
    try:
        (claims_dir() / ("%s.json" % job_id)).unlink()
    except OSError:
        pass


def clear_result(job_id: str) -> None:
    try:
        (results_dir() / ("%s.json" % job_id)).unlink()
    except OSError:
        pass


# -------------------------------------------------------------------- agent

def claim_job(path: Path) -> Optional[dict]:
    """Atomically take ownership of a queued job. Agent side.

    The rename IS the lock: exactly one process can move a given file out of
    ``queue/``. A loser gets FileNotFoundError and simply tries the next job --
    no lock file, no coordination protocol, no port.
    """
    ensure_dirs()
    job_id = path.stem
    staged = claims_dir() / ("%s.claiming" % job_id)
    try:
        os.replace(path, staged)
    except OSError:
        return None  # someone else won, or it vanished
    record = _read(staged)
    if record is None:
        try:
            staged.unlink()
        except OSError:
            pass
        return None
    return record


def publish_claim(job_id: str, record: dict, *, lane_id: str, pid: int) -> Path:
    """Record who is executing a job, so an orphan can be identified. Agent side."""
    payload = dict(record)
    payload.update({
        "job_id": job_id,
        "lane_id": lane_id,
        "pid": pid,
        "started_at": time.time(),
        "host_session": os.environ.get("SESSIONNAME", "unknown"),
    })
    path = _write_atomic(claims_dir() / ("%s.json" % job_id), payload)
    staged = claims_dir() / ("%s.claiming" % job_id)
    try:
        staged.unlink()
    except OSError:
        pass
    return path


def publish_result(job_id: str, receipt: dict, *, ok: bool, reason: str = "") -> Path:
    """Hand a terminal outcome back to the gateway. Agent side."""
    return _write_atomic(results_dir() / ("%s.json" % job_id), {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "ok": ok,
        "reason": reason,
        "receipt": receipt,
        "finished_at": time.time(),
    })


def requeue(job_id: str, record: dict) -> Path:
    """Return an unfinished job to the queue. Agent side, on orphan recovery.

    A job whose executor died is not a failed job -- the render simply did not
    happen. Putting it back is how "the interactive session went away" stays
    recoverable instead of becoming a false failure.
    """
    clear_claim(job_id)
    return _write_atomic(queue_dir() / ("%s.json" % job_id), dict(record))


def beat(*, capable: bool, detail: str, lanes: Optional[list] = None) -> Path:
    """Publish agent liveness and capability. Agent side."""
    return _write_atomic(heartbeat_path(), {
        "schema_version": SCHEMA_VERSION,
        "at": time.time(),
        "pid": os.getpid(),
        "capable": bool(capable),
        "detail": detail,
        "lanes": list(lanes or []),
        "session": os.environ.get("SESSIONNAME", "unknown"),
    })


# ------------------------------------------------------------------ shared

@dataclass(frozen=True)
class AgentStatus:
    available: bool
    capable: bool
    detail: str
    age_s: Optional[float]
    lanes: list

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "capable": self.capable,
            "detail": self.detail,
            "age_s": round(self.age_s, 1) if self.age_s is not None else None,
            "lanes": list(self.lanes),
        }


def agent_status(now: Optional[float] = None) -> AgentStatus:
    """Is an interactive render executor alive and able to render?

    Read by the gateway so `list_render_lanes` can distinguish "no lanes" from
    "lanes exist but nothing can currently drive them" -- which is the difference
    between a broken install and nobody being logged in.
    """
    record = _read(heartbeat_path())
    if record is None:
        return AgentStatus(False, False, "no render agent heartbeat found", None, [])
    stamp = record.get("at")
    if not isinstance(stamp, (int, float)):
        return AgentStatus(False, False, "malformed heartbeat", None, [])
    age = (time.time() if now is None else now) - float(stamp)
    if age > HEARTBEAT_STALE_S:
        return AgentStatus(
            False, bool(record.get("capable")),
            "render agent heartbeat is %.0fs old (stale after %.0fs)"
            % (age, HEARTBEAT_STALE_S),
            age, list(record.get("lanes") or []),
        )
    return AgentStatus(
        True, bool(record.get("capable")), str(record.get("detail", "")), age,
        list(record.get("lanes") or []),
    )
