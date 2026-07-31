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
from pathlib import Path
from typing import Iterator, Optional


class CapacityUnavailable(RuntimeError):
    """Raised when a global execution scope has no free slots."""


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
