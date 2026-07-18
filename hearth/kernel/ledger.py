"""Append-only HEARTH event ledger (Stream H-A, frozen contract 4).

Every tool call through the gateway becomes exactly one hearth-event.v1 line in
``events.ndjson`` plus one row in ``index.sqlite``. The NDJSON file is the record;
SQLite is only an index into it (byte offset + length per event), so `query` reads
back the full event from disk via the index. There are NO update or delete
operations — append-only is a property of the API surface, not a convention.

Ledger location: ``$HEARTH_ROOT/var/ledger`` when the HEARTH_ROOT env var is set,
default ``<repo>/hearth/var/ledger`` (gitignored via hearth/.gitignore).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "hearth-event.v1.schema.json"
SCHEMA_ID = "hearth-event.v1"
RUNNER_CLASSES = ("frontier", "local", "human")
ARGS_PREVIEW_CHARS = 400

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$")


class LedgerValidationError(ValueError):
    """Raised when an event does not conform to hearth-event.v1."""


def hearth_root() -> Path:
    """The hearth data root: $HEARTH_ROOT if set, else <repo>/hearth."""
    env = os.environ.get("HEARTH_ROOT")
    return Path(env).resolve() if env else REPO_ROOT / "hearth"


def default_ledger_dir() -> Path:
    return hearth_root() / "var" / "ledger"


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with a Z suffix, matching the schema's ts pattern."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_dumps_canonical(value: Any) -> str:
    """Deterministic JSON serialization used for digests and previews."""
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def sha256_digest(value: Any) -> str:
    """`sha256:<hex>` digest of the canonical JSON serialization of `value`."""
    payload = json_dumps_canonical(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def classify_error(error: str) -> str:
    """Taxonomy for gateway/provider errors (S4): first match wins."""
    if not error:
        return "other"
    lower = error.lower()
    if "occupancy" in lower or "busy" in lower:
        return "occupancy_skip"
    if "timed out" in lower or "timeout" in lower:
        return "timeout"
    if any(x in lower for x in ("connection refused", "connect error", "unreachable",
                                "failed to establish", "no connection could be made")):
        return "cold_start"
    if any(x in lower for x in ("401", "403", "unauthorized", "token", "credentials")):
        return "auth_expired"
    if any(x in lower for x in ("429", "quota", "resource exhausted", "rate limit")):
        return "quota"
    if any(x in lower for x in ("json", "parse", "decode", "expecting value")):
        return "parse_error"
    return "other"


def new_event(caller: Mapping[str, str], tool: str, *,
              args: Any = None, result: Any = None, ok: bool = True,
              error: Optional[str] = None, duration_ms: float = 0,
              cost: Optional[Mapping[str, Any]] = None,
              task_id: Optional[str] = None,
              task_class: Optional[str] = None,
              model: Optional[str] = None,
              backend: Optional[str] = None,
              routed_by: Optional[str] = None,
              occupancy: Optional[str] = None,
              error_code: Optional[str] = None) -> dict:
    """Build a schema-complete hearth-event.v1 dict for `append`.

    `args` and `result` are the raw python values; digests and the 400-char args
    preview are computed here so every producer stamps provenance identically.

    `task_class` and `model` are optional additive fields (JS1) so duration
    distributions can later be bucketed by (task_class x node x model); both
    default to None and are omitted from nothing — old readers simply see null.
    """
    cost = dict(cost) if cost else {}
    args_json = json_dumps_canonical(args)
    return {
        "schema": SCHEMA_ID,
        "event_id": str(uuid.uuid4()),
        "ts": utc_now_iso(),
        "caller": {
            "id": caller["id"],
            "runner_class": caller["runner_class"],
            "node": caller["node"],
        },
        "tool": tool,
        "args_digest": sha256_digest(args),
        "args_preview": args_json[:ARGS_PREVIEW_CHARS],
        "result_digest": sha256_digest(result),
        "ok": ok,
        "error": error,
        "duration_ms": round(duration_ms, 3),
        "cost": {
            "tokens_in": cost.get("tokens_in"),
            "tokens_out": cost.get("tokens_out"),
            "watt_s": cost.get("watt_s"),
        },
        "task_id": task_id,
        "task_class": task_class,
        "model": model,
        "backend": backend,
        "routed_by": routed_by,
        "occupancy": occupancy,
        "error_code": error_code,
    }


def _validate_stdlib(event: Any) -> None:
    """Hand-coded structural validation mirroring hearth-event.v1.schema.json,
    used only when the jsonschema package is not importable."""
    if not isinstance(event, dict):
        raise LedgerValidationError("event must be an object")
    required = {"schema", "event_id", "ts", "caller", "tool", "args_digest",
                "args_preview", "result_digest", "ok", "error", "duration_ms",
                "cost", "task_id"}
    optional = {"task_class", "model", "backend", "routed_by", "occupancy", "error_code"}
    keys = set(event)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        missing, extra = sorted(required - keys), sorted(keys - (required | optional))
        raise LedgerValidationError(f"bad event keys: missing={missing} extra={extra}")
    if event["schema"] != SCHEMA_ID:
        raise LedgerValidationError(f"schema must be {SCHEMA_ID!r}")
    if not isinstance(event["event_id"], str) or not _UUID_RE.match(event["event_id"]):
        raise LedgerValidationError("event_id must be a uuid4 string")
    if not isinstance(event["ts"], str) or not _TS_RE.match(event["ts"]):
        raise LedgerValidationError("ts must be ISO-8601 UTC")
    caller = event["caller"]
    if (not isinstance(caller, dict) or set(caller) != {"id", "runner_class", "node"}
            or not isinstance(caller.get("id"), str) or not caller["id"]
            or not isinstance(caller.get("node"), str) or not caller["node"]):
        raise LedgerValidationError("caller must be {id, runner_class, node} with non-empty strings")
    if caller["runner_class"] not in RUNNER_CLASSES:
        raise LedgerValidationError(f"runner_class must be one of {RUNNER_CLASSES}")
    if not isinstance(event["tool"], str) or not event["tool"]:
        raise LedgerValidationError("tool must be a non-empty string")
    for field in ("args_digest", "result_digest"):
        if not isinstance(event[field], str) or not _DIGEST_RE.match(event[field]):
            raise LedgerValidationError(f"{field} must match sha256:<64 hex>")
    if not isinstance(event["args_preview"], str) or len(event["args_preview"]) > ARGS_PREVIEW_CHARS:
        raise LedgerValidationError(f"args_preview must be a string of <= {ARGS_PREVIEW_CHARS} chars")
    if not isinstance(event["ok"], bool):
        raise LedgerValidationError("ok must be a boolean")
    if event["error"] is not None and not isinstance(event["error"], str):
        raise LedgerValidationError("error must be a string or null")
    if not isinstance(event["duration_ms"], (int, float)) or isinstance(event["duration_ms"], bool) \
            or event["duration_ms"] < 0:
        raise LedgerValidationError("duration_ms must be a non-negative number")
    cost = event["cost"]
    if not isinstance(cost, dict) or set(cost) != {"tokens_in", "tokens_out", "watt_s"}:
        raise LedgerValidationError("cost must be {tokens_in, tokens_out, watt_s}")
    for field, value in cost.items():
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise LedgerValidationError(f"cost.{field} must be a number or null")
    if event["task_id"] is not None and not isinstance(event["task_id"], str):
        raise LedgerValidationError("task_id must be a string or null")
    for optional_field in ("task_class", "model", "backend", "routed_by", "occupancy", "error_code"):
        if optional_field in event and event[optional_field] is not None \
                and not isinstance(event[optional_field], str):
            raise LedgerValidationError(f"{optional_field} must be a string or null")


_jsonschema_validator = None


def validate_event(event: Any) -> None:
    """Validate an event against hearth-event.v1, raising LedgerValidationError.

    Prefers the jsonschema package (present in the fleet venv) driven by the
    contract file itself; falls back to the stdlib mirror when unavailable.
    """
    global _jsonschema_validator
    try:
        import jsonschema
    except ImportError:
        _validate_stdlib(event)
        return
    if _jsonschema_validator is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        _jsonschema_validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(_jsonschema_validator.iter_errors(event), key=str)
    if errors:
        raise LedgerValidationError(errors[0].message)


class Ledger:
    """Append-only NDJSON event store with a SQLite index.

    append(event) -> event_id; query(...) -> list of full event dicts read back
    from the NDJSON file via indexed byte offsets. No mutation API exists.
    """

    def __init__(self, ledger_dir: Optional[Path | str] = None) -> None:
        self.dir = Path(ledger_dir) if ledger_dir else default_ledger_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.ndjson"
        self.index_path = self.dir / "index.sqlite"
        self._lock = threading.Lock()
        self._init_index()

    @contextlib.contextmanager
    def _index(self):
        # sqlite3's own context manager commits but never closes; an open handle
        # keeps index.sqlite locked on Windows, so close explicitly every time.
        conn = sqlite3.connect(self.index_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_index(self) -> None:
        with self._index() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                " event_id TEXT PRIMARY KEY,"
                " ts TEXT NOT NULL,"
                " caller_id TEXT NOT NULL,"
                " runner_class TEXT NOT NULL,"
                " tool TEXT NOT NULL,"
                " ok INTEGER NOT NULL,"
                " duration_ms REAL NOT NULL,"
                " offset INTEGER NOT NULL,"
                " length INTEGER NOT NULL,"
                " task_class TEXT,"
                " model TEXT,"
                " backend TEXT,"
                " routed_by TEXT,"
                " occupancy TEXT,"
                " error_code TEXT)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
            # Migration: a DB created before JS1 lacks task_class/model columns.
            existing = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
            if "task_class" not in existing:
                conn.execute("ALTER TABLE events ADD COLUMN task_class TEXT")
            if "model" not in existing:
                conn.execute("ALTER TABLE events ADD COLUMN model TEXT")
            for col in ("backend", "routed_by", "occupancy", "error_code"):
                if col not in existing:
                    conn.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")

    @staticmethod
    def _insert_event_row(conn: sqlite3.Connection, event: dict, offset: int, length: int) -> None:
        """The one INSERT statement used by both `append` and `reindex`, so the
        two paths can never drift apart on which columns are written."""
        conn.execute(
            "INSERT INTO events (event_id, ts, caller_id, runner_class, tool,"
            " ok, duration_ms, offset, length, task_class, model,"
            " backend, routed_by, occupancy, error_code)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event["event_id"], event["ts"], event["caller"]["id"],
             event["caller"]["runner_class"], event["tool"],
             1 if event["ok"] else 0, float(event["duration_ms"]),
             offset, length,
             event.get("task_class"), event.get("model"),
             event.get("backend"), event.get("routed_by"),
             event.get("occupancy"), event.get("error_code")),
        )

    def append(self, event: dict) -> str:
        """Validate `event` against hearth-event.v1, append one NDJSON line, and
        index it. Returns the event_id."""
        validate_event(event)
        line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            offset = self.events_path.stat().st_size if self.events_path.exists() else 0
            with self.events_path.open("ab") as fh:
                fh.write(line)
            with self._index() as conn:
                self._insert_event_row(conn, event, offset, len(line))
        return event["event_id"]

    def reindex(self) -> int:
        """Rebuild index.sqlite from events.ndjson (the source of truth).

        Drops and recreates the events table, then streams the NDJSON file in
        binary mode (byte offsets, not char offsets), re-inserting one row per
        line via the exact same INSERT as `append`. A truncated final line
        (one that fails to parse as JSON or fails schema validation, and is
        not newline-terminated) is treated as an in-progress write that was
        never fully flushed and is skipped with a warning; a torn line
        anywhere else in the file is corruption and raises
        LedgerValidationError. Returns the number of events reindexed.
        """
        with self._lock:
            with self._index() as conn:
                conn.execute("DROP TABLE IF EXISTS events")
            self._init_index()

            if not self.events_path.exists():
                return 0

            with self.events_path.open("rb") as fh:
                raw = fh.read()

            lines = raw.splitlines(keepends=True)
            count = 0
            offset = 0
            with self._index() as conn:
                for i, raw_line in enumerate(lines):
                    length = len(raw_line)
                    is_last = i == len(lines) - 1
                    stripped = raw_line.strip()
                    if not stripped:
                        offset += length
                        continue

                    def _torn_line_error(exc: Exception) -> LedgerValidationError:
                        return LedgerValidationError(
                            f"reindex: corrupt line {i} at offset {offset} "
                            f"(not the last line, does not parse/validate): {exc}"
                        )

                    try:
                        event = json.loads(raw_line.decode("utf-8"))
                        validate_event(event)
                    except (json.JSONDecodeError, UnicodeDecodeError,
                            LedgerValidationError) as exc:
                        if is_last and not raw_line.endswith(b"\n"):
                            print(
                                f"reindex: skipping truncated final line "
                                f"({length} bytes): {exc}"
                            )
                            offset += length
                            continue
                        raise _torn_line_error(exc) from exc

                    self._insert_event_row(conn, event, offset, length)
                    count += 1
                    offset += length
            return count

    def verify(self) -> dict:
        """Read-only consistency check between index.sqlite and events.ndjson.

        Returns {ok, index_rows, ndjson_lines, mismatches}. `ndjson_lines` is
        the count of non-blank lines in the NDJSON file. A mismatch is
        recorded when an indexed (offset, length) slice does not parse as
        JSON, or parses but its event_id disagrees with the index row.
        Performs no mutation of either store.
        """
        mismatches: list[dict] = []

        ndjson_lines = 0
        if self.events_path.exists():
            with self.events_path.open("rb") as fh:
                for raw_line in fh:
                    if raw_line.strip():
                        ndjson_lines += 1

        with self._index() as conn:
            rows = conn.execute("SELECT event_id, offset, length FROM events").fetchall()
        index_rows = len(rows)

        if rows and self.events_path.exists():
            with self.events_path.open("rb") as fh:
                for event_id, offset, length in rows:
                    fh.seek(offset)
                    raw = fh.read(length)
                    try:
                        event = json.loads(raw.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        mismatches.append({
                            "event_id": event_id,
                            "offset": offset,
                            "length": length,
                            "reason": f"slice does not parse as JSON: {exc}",
                        })
                        continue
                    if event.get("event_id") != event_id:
                        mismatches.append({
                            "event_id": event_id,
                            "offset": offset,
                            "length": length,
                            "reason": f"event_id mismatch: index={event_id} "
                                      f"ndjson={event.get('event_id')}",
                        })
        elif rows and not self.events_path.exists():
            for event_id, offset, length in rows:
                mismatches.append({
                    "event_id": event_id,
                    "offset": offset,
                    "length": length,
                    "reason": "events.ndjson does not exist",
                })

        ok = (index_rows == ndjson_lines) and not mismatches
        return {
            "ok": ok,
            "index_rows": index_rows,
            "ndjson_lines": ndjson_lines,
            "mismatches": mismatches,
        }

    def query(self, caller: Optional[str] = None, tool: Optional[str] = None,
              since: Optional[str] = None, ok: Optional[bool] = None) -> list[dict]:
        """Return full events matching the filters, oldest first, via the index.

        `caller` matches caller.id; `since` is an ISO-8601 UTC lower bound
        (inclusive, lexicographic — safe for the schema's Z-suffixed format).
        """
        clauses, params = [], []
        if caller is not None:
            clauses.append("caller_id = ?")
            params.append(caller)
        if tool is not None:
            clauses.append("tool = ?")
            params.append(tool)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if ok is not None:
            clauses.append("ok = ?")
            params.append(1 if ok else 0)
        sql = "SELECT offset, length FROM events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts, offset"
        with self._index() as conn:
            rows = conn.execute(sql, params).fetchall()
        if not rows or not self.events_path.exists():
            return []
        events = []
        with self.events_path.open("rb") as fh:
            for offset, length in rows:
                fh.seek(offset)
                events.append(json.loads(fh.read(length).decode("utf-8")))
        return events


def _cli(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m hearth.kernel.ledger",
        description="Rebuild or verify the HEARTH kernel event-ledger sqlite index "
                     "from events.ndjson (the source of truth). Respects HEARTH_ROOT.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--reindex", action="store_true",
                        help="Rebuild index.sqlite from events.ndjson.")
    group.add_argument("--verify", action="store_true",
                        help="Read-only check that index.sqlite matches events.ndjson.")
    args = parser.parse_args(argv)

    ledger = Ledger()
    print(f"ledger dir: {ledger.dir}")

    if args.reindex:
        count = ledger.reindex()
        print(f"reindex: OK, {count} event(s) reindexed")
        return 0

    result = ledger.verify()
    if result["ok"]:
        print(f"verify: OK, {result['index_rows']} indexed row(s) match "
              f"{result['ndjson_lines']} ndjson line(s)")
        return 0
    print(f"verify: FAILED, index_rows={result['index_rows']} "
          f"ndjson_lines={result['ndjson_lines']} "
          f"mismatches={len(result['mismatches'])}")
    for mismatch in result["mismatches"]:
        print(f"  - {mismatch}")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
