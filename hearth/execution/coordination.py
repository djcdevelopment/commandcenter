"""Mutable global admission-control leases.

Leases are disposable coordination state, deliberately separate from the
append-only Execution Ledger. All gateway workers sharing the SQLite file see
the same provider/model capacity budget.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


class CapacityUnavailable(RuntimeError):
    """Raised when a global execution scope has no free slots."""


class TenancyConflict(RuntimeError):
    """Raised when a fenced resource is owned by another live session."""


@dataclass(frozen=True)
class TenancySnapshot:
    resource: str
    owner: str
    session_id: str
    epoch: int
    state: str
    reason: Optional[str]
    acquired_at: float
    updated_at: float
    expires_at: float

    def active(self, *, now: Optional[float] = None) -> bool:
        current = time.time() if now is None else now
        return self.owner == "imagegen" and self.expires_at > current

    def to_dict(self, *, now: Optional[float] = None) -> dict[str, Any]:
        value = asdict(self)
        value["active"] = self.active(now=now)
        return value


def default_coordination_path() -> Path:
    configured = os.environ.get("HEARTH_COORDINATION_DB")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "var" / "execution" / "coordination.sqlite"


class CapacityLeaseStore:
    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path).resolve() if path is not None else default_coordination_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS capacity_leases (
                    lease_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS capacity_leases_scope "
                "ON capacity_leases(scope, expires_at)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def acquire(
        self,
        *,
        scope: str,
        job_id: str,
        invocation_id: str,
        limit: int,
        ttl_seconds: float,
        now: Optional[float] = None,
    ) -> str:
        if not scope:
            raise ValueError("scope must not be empty")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        acquired_at = time.time() if now is None else now
        expires_at = acquired_at + ttl_seconds
        lease_id = f"lease_{secrets.token_hex(16)}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM capacity_leases WHERE expires_at <= ?", (acquired_at,)
                )
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM capacity_leases WHERE scope = ?",
                    (scope,),
                ).fetchone()
                if int(row["count"]) >= limit:
                    raise CapacityUnavailable(
                        f"global capacity exhausted for {scope}: limit={limit}"
                    )
                connection.execute(
                    """
                    INSERT INTO capacity_leases (
                        lease_id, scope, job_id, invocation_id, acquired_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (lease_id, scope, job_id, invocation_id, acquired_at, expires_at),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return lease_id

    def renew(
        self, lease_id: str, *, ttl_seconds: float, now: Optional[float] = None
    ) -> bool:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        current = time.time() if now is None else now
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE capacity_leases
                SET expires_at = ?
                WHERE lease_id = ? AND expires_at > ?
                """,
                (current + ttl_seconds, lease_id, current),
            )
        return cursor.rowcount == 1

    def release(self, lease_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM capacity_leases WHERE lease_id = ?", (lease_id,)
            )
        return cursor.rowcount == 1

    def active_count(self, scope: str, *, now: Optional[float] = None) -> int:
        current = time.time() if now is None else now
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM capacity_leases WHERE expires_at <= ?", (current,)
            )
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM capacity_leases WHERE scope = ?",
                (scope,),
            ).fetchone()
        return int(row["count"])

    def reap(self, *, now: Optional[float] = None) -> int:
        current = time.time() if now is None else now
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM capacity_leases WHERE expires_at <= ?", (current,)
            )
        return cursor.rowcount


class GpuTenancyStore:
    """Fenced ownership for a whole GPU pool.

    Capacity leases protect individual lanes. This store protects the larger
    operating mode: ArcServe or image generation may own the two-B70 pool, but
    never both. Rows are retained after release so ``epoch`` remains monotonic
    across crashes and restarts; an agent must present the current epoch before
    every claim and renewal.
    """

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path).resolve() if path is not None else default_coordination_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gpu_tenancy (
                    resource TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT,
                    acquired_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> TenancySnapshot:
        return TenancySnapshot(
            resource=str(row["resource"]), owner=str(row["owner"]),
            session_id=str(row["session_id"]), epoch=int(row["epoch"]),
            state=str(row["state"]), reason=row["reason"],
            acquired_at=float(row["acquired_at"]),
            updated_at=float(row["updated_at"]),
            expires_at=float(row["expires_at"]),
        )

    def acquire(
        self, *, resource: str, session_id: str, ttl_seconds: float,
        state: str = "draining_llm", reason: Optional[str] = None,
        now: Optional[float] = None,
    ) -> TenancySnapshot:
        if not resource or not session_id or not state:
            raise ValueError("resource, session_id, and state must not be empty")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        current = time.time() if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM gpu_tenancy WHERE resource = ?", (resource,)
                ).fetchone()
                if row is not None and float(row["expires_at"]) > current:
                    if str(row["owner"]) == "imagegen" and str(row["session_id"]) == session_id:
                        connection.commit()
                        return self._snapshot(row)
                    raise TenancyConflict(
                        "%s is owned by %s session %s at epoch %s" % (
                            resource, row["owner"], row["session_id"], row["epoch"]
                        )
                    )
                epoch = (int(row["epoch"]) + 1) if row is not None else 1
                connection.execute(
                    """
                    INSERT INTO gpu_tenancy (
                        resource, owner, session_id, epoch, state, reason,
                        acquired_at, updated_at, expires_at
                    ) VALUES (?, 'imagegen', ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(resource) DO UPDATE SET
                        owner='imagegen', session_id=excluded.session_id,
                        epoch=excluded.epoch, state=excluded.state,
                        reason=excluded.reason, acquired_at=excluded.acquired_at,
                        updated_at=excluded.updated_at, expires_at=excluded.expires_at
                    """,
                    (resource, session_id, epoch, state, reason, current, current,
                     current + ttl_seconds),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        snapshot = self.get(resource)
        assert snapshot is not None
        return snapshot

    def transition(
        self, *, resource: str, session_id: str, epoch: int, state: str,
        ttl_seconds: float, reason: Optional[str] = None,
        now: Optional[float] = None,
    ) -> TenancySnapshot:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        current = time.time() if now is None else now
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE gpu_tenancy
                SET state = ?, reason = ?, updated_at = ?, expires_at = ?
                WHERE resource = ? AND owner = 'imagegen'
                  AND session_id = ? AND epoch = ? AND expires_at > ?
                """,
                (state, reason, current, current + ttl_seconds, resource,
                 session_id, epoch, current),
            )
        if cursor.rowcount != 1:
            raise TenancyConflict("stale or expired imagegen tenancy fence")
        snapshot = self.get(resource)
        assert snapshot is not None
        return snapshot

    def renew(
        self, *, resource: str, session_id: str, epoch: int,
        ttl_seconds: float, now: Optional[float] = None,
    ) -> bool:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        current = time.time() if now is None else now
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE gpu_tenancy SET updated_at = ?, expires_at = ?
                WHERE resource = ? AND owner = 'imagegen'
                  AND session_id = ? AND epoch = ? AND expires_at > ?
                """,
                (current, current + ttl_seconds, resource, session_id, epoch, current),
            )
        return cursor.rowcount == 1

    def release(
        self, *, resource: str, session_id: str, epoch: int,
        reason: Optional[str] = None, now: Optional[float] = None,
    ) -> bool:
        current = time.time() if now is None else now
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE gpu_tenancy
                SET owner = 'arcserve', state = 'llm', reason = ?,
                    updated_at = ?, expires_at = 0
                WHERE resource = ? AND owner = 'imagegen'
                  AND session_id = ? AND epoch = ?
                """,
                (reason, current, resource, session_id, epoch),
            )
        return cursor.rowcount == 1

    def get(self, resource: str) -> Optional[TenancySnapshot]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM gpu_tenancy WHERE resource = ?", (resource,)
            ).fetchone()
        return self._snapshot(row) if row is not None else None

    def active_image_session(
        self, resource: str = "omen-b70-pool", *, now: Optional[float] = None
    ) -> Optional[TenancySnapshot]:
        snapshot = self.get(resource)
        return snapshot if snapshot is not None and snapshot.active(now=now) else None
