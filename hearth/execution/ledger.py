"""Append-only execution ledger with rebuildable SQLite projections.

The NDJSON event stream is canonical. SQLite accelerates reads and contains
only projections that can be discarded and rebuilt from the stream.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional

from .model import FINAL_JOB_STATUSES, validate_execution_event


class ExecutionLedgerError(RuntimeError):
    """Raised when execution history cannot be appended or projected."""


_JOB_EVENT_STATUSES = {
    "request.accepted": "accepted",
    "request.rejected": "rejected",
    "job.queued": "queued",
    "job.dispatched": "dispatched",
    "job.running": "running",
    "job.waiting_for_input": "waiting_for_input",
    "job.waiting_for_approval": "waiting_for_approval",
    "job.cancellation_requested": "cancellation_requested",
    "job.succeeded": "succeeded",
    "job.failed": "failed",
    "job.cancelled": "cancelled",
    "job.expired": "expired",
}

_INVOCATION_EVENT_STATUSES = {
    "invocation.started": "running",
    "invocation.succeeded": "succeeded",
    "invocation.failed": "failed",
    "invocation.cancelled": "cancelled",
}


def default_execution_dir() -> Path:
    configured = os.environ.get("HEARTH_EXECUTION_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "var" / "execution"


class ExecutionLedger:
    """Own the canonical event stream and its local read projections."""

    def __init__(self, root: Optional[Path | str] = None) -> None:
        self.root = Path(root).resolve() if root is not None else default_execution_dir()
        self.events_path = self.root / "events.ndjson"
        self.projection_path = self.root / "projection.sqlite"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path.touch(exist_ok=True)
        self._initialize_projection()
        if self._projection_is_stale():
            self.rebuild()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.projection_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_projection(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    byte_offset INTEGER NOT NULL,
                    byte_length INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    invocation_id TEXT
                );
                CREATE INDEX IF NOT EXISTS events_job_sequence
                    ON events(job_id, sequence);
                CREATE INDEX IF NOT EXISTS events_request_sequence
                    ON events(request_id, sequence);

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_request_id
                    ON jobs(request_id);

                CREATE TABLE IF NOT EXISTS idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    job_id TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    invocation_id TEXT,
                    metadata_json TEXT NOT NULL,
                    recorded_sequence INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS artifacts_job_id
                    ON artifacts(job_id, recorded_sequence);
                """
            )

    def _projection_is_stale(self) -> bool:
        """Detect an interrupted projection update without trusting file size alone."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT sequence, byte_offset, byte_length FROM events "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        file_size = self.events_path.stat().st_size
        if row is None:
            return file_size != 0
        return int(row["byte_offset"]) + int(row["byte_length"]) != file_size

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Append one immutable fact and update projections.

        If projection application fails after the canonical append, projections
        are rebuilt before the error is surfaced.
        """
        candidate = copy.deepcopy(dict(event))
        validate_execution_event(candidate, allow_unsequenced=True)
        if candidate["sequence"] is not None:
            raise ExecutionLedgerError("callers must not assign event sequences")

        with self._lock:
            if self._projection_is_stale():
                self.rebuild()
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM events"
                ).fetchone()
                sequence = int(row["sequence"]) + 1
                # Reject semantically impossible facts before they can poison
                # the canonical stream. This dry reduction is repeated when
                # applying the durable append.
                self._reduce(
                    self._load_state(connection, candidate["job_id"]),
                    {**candidate, "sequence": sequence},
                )
                if candidate["event_type"] == "request.accepted":
                    idempotency_key = (candidate.get("desired") or {}).get(
                        "idempotency_key"
                    )
                    if idempotency_key:
                        duplicate = connection.execute(
                            "SELECT 1 FROM idempotency WHERE idempotency_key = ?",
                            (idempotency_key,),
                        ).fetchone()
                        if duplicate is not None:
                            raise ExecutionLedgerError(
                                f"idempotency key already exists: {idempotency_key}"
                            )
            candidate["sequence"] = sequence
            validate_execution_event(candidate)
            encoded = (
                json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            ).encode("utf-8")
            offset = self.events_path.stat().st_size
            try:
                with self.events_path.open("ab") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                with self._connect() as connection:
                    self._apply_event(connection, candidate, offset, len(encoded))
            except Exception as exc:
                try:
                    self.rebuild()
                except Exception as rebuild_exc:
                    raise ExecutionLedgerError(
                        f"event appended but projection rebuild failed: {rebuild_exc}"
                    ) from exc
                raise ExecutionLedgerError(f"event appended but projection failed: {exc}") from exc
        return copy.deepcopy(candidate)

    def append_many(self, events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [self.append(event) for event in events]

    def _initial_state(self, event: Mapping[str, Any]) -> dict[str, Any]:
        status = _JOB_EVENT_STATUSES[event["event_type"]]
        return {
            "request_id": event["request_id"],
            "job_id": event["job_id"],
            "operation": event["operation"],
            "principal": copy.deepcopy(event["principal"]),
            "source": copy.deepcopy(event["source"]),
            "desired": copy.deepcopy(event["desired"]) or {},
            "status": status,
            "reason": event["reason"],
            "created_at": event["timestamp"],
            "updated_at": event["timestamp"],
            "invocations": [],
            "artifacts": [],
            "deliveries": [],
            "last_sequence": event["sequence"],
        }

    def _load_state(
        self, connection: sqlite3.Connection, job_id: str
    ) -> Optional[dict[str, Any]]:
        row = connection.execute(
            "SELECT state_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return json.loads(row["state_json"]) if row is not None else None

    @staticmethod
    def _merge_observed(target: dict[str, Any], event: Mapping[str, Any]) -> None:
        observed = event.get("observed")
        if observed:
            target.update(copy.deepcopy(observed))
        if event.get("reason") is not None:
            target["reason"] = event["reason"]

    def _reduce(self, state: Optional[dict[str, Any]], event: Mapping[str, Any]) -> dict[str, Any]:
        event_type = event["event_type"]
        if state is None:
            if event_type not in {"request.accepted", "request.rejected"}:
                raise ExecutionLedgerError(
                    f"{event_type} precedes request event for job {event['job_id']}"
                )
            state = self._initial_state(event)
        else:
            if state["request_id"] != event["request_id"]:
                raise ExecutionLedgerError("job cannot change request_id")
            if state["status"] in FINAL_JOB_STATUSES and event_type not in {
                "artifact.recorded",
                "delivery.projected",
            }:
                raise ExecutionLedgerError(
                    f"cannot apply {event_type} to terminal job {event['job_id']}"
                )

        if event_type in _JOB_EVENT_STATUSES:
            state["status"] = _JOB_EVENT_STATUSES[event_type]
            state["reason"] = event["reason"]
            self._merge_observed(state, event)
        elif event_type in _INVOCATION_EVENT_STATUSES:
            invocation_id = event["invocation_id"]
            invocation = next(
                (
                    item
                    for item in state["invocations"]
                    if item["invocation_id"] == invocation_id
                ),
                None,
            )
            if event_type == "invocation.started":
                if invocation is not None:
                    raise ExecutionLedgerError(f"duplicate invocation start: {invocation_id}")
                invocation = {
                    "invocation_id": invocation_id,
                    "status": "running",
                    "started_at": event["timestamp"],
                    "updated_at": event["timestamp"],
                }
                self._merge_observed(invocation, event)
                state["invocations"].append(invocation)
            else:
                if invocation is None:
                    raise ExecutionLedgerError(
                        f"{event_type} has no matching invocation start: {invocation_id}"
                    )
                if invocation["status"] != "running":
                    raise ExecutionLedgerError(f"invocation is already terminal: {invocation_id}")
                invocation["status"] = _INVOCATION_EVENT_STATUSES[event_type]
                invocation["updated_at"] = event["timestamp"]
                invocation["finished_at"] = event["timestamp"]
                self._merge_observed(invocation, event)
        elif event_type == "artifact.recorded":
            for artifact in event["artifacts"]:
                artifact_id = artifact.get("artifact_id")
                if not isinstance(artifact_id, str) or not artifact_id:
                    raise ExecutionLedgerError("artifact metadata requires artifact_id")
                if not any(
                    item.get("artifact_id") == artifact_id for item in state["artifacts"]
                ):
                    state["artifacts"].append(copy.deepcopy(artifact))
        elif event_type == "delivery.projected":
            delivery = copy.deepcopy(event["observed"]) or {}
            delivery["timestamp"] = event["timestamp"]
            state["deliveries"].append(delivery)

        state["updated_at"] = event["timestamp"]
        state["last_sequence"] = event["sequence"]
        return state

    def _apply_event(
        self,
        connection: sqlite3.Connection,
        event: Mapping[str, Any],
        offset: int,
        length: int,
    ) -> None:
        state = self._load_state(connection, event["job_id"])
        state = self._reduce(state, event)
        connection.execute(
            """
            INSERT INTO events (
                sequence, event_id, byte_offset, byte_length, timestamp,
                event_type, request_id, job_id, invocation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["sequence"],
                event["event_id"],
                offset,
                length,
                event["timestamp"],
                event["event_type"],
                event["request_id"],
                event["job_id"],
                event["invocation_id"],
            ),
        )
        connection.execute(
            """
            INSERT INTO jobs (job_id, request_id, status, state_json, last_sequence)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                state_json = excluded.state_json,
                last_sequence = excluded.last_sequence
            """,
            (
                event["job_id"],
                event["request_id"],
                state["status"],
                json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                event["sequence"],
            ),
        )
        if event["event_type"] == "request.accepted":
            desired = event.get("desired") or {}
            idempotency_key = desired.get("idempotency_key")
            if idempotency_key:
                try:
                    connection.execute(
                        "INSERT INTO idempotency (idempotency_key, request_id, job_id) "
                        "VALUES (?, ?, ?)",
                        (idempotency_key, event["request_id"], event["job_id"]),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ExecutionLedgerError(
                        f"idempotency key already exists: {idempotency_key}"
                    ) from exc
        if event["event_type"] == "artifact.recorded":
            for artifact in event["artifacts"]:
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, job_id, invocation_id, metadata_json, recorded_sequence
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        artifact["artifact_id"],
                        event["job_id"],
                        event["invocation_id"],
                        json.dumps(
                            artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                        ),
                        event["sequence"],
                    ),
                )

    def rebuild(self) -> int:
        """Discard all projections and replay the canonical event stream."""
        with self._lock:
            if self.projection_path.exists():
                self.projection_path.unlink()
            self._initialize_projection()
            count = 0
            offset = 0
            try:
                with self.events_path.open("rb") as stream, self._connect() as connection:
                    for line_number, encoded in enumerate(stream, start=1):
                        length = len(encoded)
                        if not encoded.strip():
                            offset += length
                            continue
                        try:
                            event = json.loads(encoded.decode("utf-8"))
                            validate_execution_event(event)
                        except Exception as exc:
                            raise ExecutionLedgerError(
                                f"invalid canonical event at line {line_number}: {exc}"
                            ) from exc
                        expected_sequence = count + 1
                        if event["sequence"] != expected_sequence:
                            raise ExecutionLedgerError(
                                f"non-contiguous sequence at line {line_number}: "
                                f"expected {expected_sequence}, got {event['sequence']}"
                            )
                        self._apply_event(connection, event, offset, length)
                        count += 1
                        offset += length
            except Exception:
                if self.projection_path.exists():
                    self.projection_path.unlink()
                self._initialize_projection()
                raise
            return count

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return copy.deepcopy(json.loads(row["state_json"])) if row is not None else None

    def find_by_idempotency(self, idempotency_key: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT job_id FROM idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self.get_job(row["job_id"]) if row is not None else None

    def list_jobs(
        self, *, statuses: Optional[set[str]] = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        parameters: list[Any] = []
        where = ""
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where = f"WHERE status IN ({placeholders})"
            parameters.extend(sorted(statuses))
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT state_json FROM jobs {where} ORDER BY last_sequence LIMIT ?",
                parameters,
            ).fetchall()
        return [copy.deepcopy(json.loads(row["state_json"])) for row in rows]

    def get_artifact(self, artifact_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return copy.deepcopy(json.loads(row["metadata_json"])) if row is not None else None

    def artifact_job_id(self, artifact_id: str) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT job_id FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return str(row["job_id"]) if row is not None else None

    def iter_events(
        self,
        *,
        job_id: Optional[str] = None,
        request_id: Optional[str] = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> Iterator[dict[str, Any]]:
        if limit <= 0 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        clauses = ["sequence > ?"]
        parameters: list[Any] = [after_sequence]
        if job_id is not None:
            clauses.append("job_id = ?")
            parameters.append(job_id)
        if request_id is not None:
            clauses.append("request_id = ?")
            parameters.append(request_id)
        parameters.append(limit)
        query = (
            "SELECT byte_offset, byte_length FROM events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        with self.events_path.open("rb") as stream:
            for row in rows:
                stream.seek(int(row["byte_offset"]))
                encoded = stream.read(int(row["byte_length"]))
                yield json.loads(encoded.decode("utf-8"))
