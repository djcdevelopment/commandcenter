"""Harvest research-seat logs into ``seat.physical-attempt.v1`` receipts.

``python -m hearth.seats.harvest [--logs DIR ...] [--ledger FILE] [--cursor FILE]
[--dry-run] [--json]``

Eligibility is the disjointness rule of ADR-0047: a log is harvested only when
it belongs to a bespoke research seat the door could not have reached. The
fingerprint is read from the server's own load report, never from the label:

- no ``api_keys:`` line (llama-swap-managed seats carry the production key);
- ``listening on`` a port that is not a gateway-reachable rung (the door's
  backend ports, the friend gate, the retired Ollama port, and llama-swap's
  ephemeral range are all excluded);
- a per-slot context in the load report and at least ``PREFIX_LINES`` stamped
  lines, so the epoch fingerprint is computed on a complete header. A build
  line and a model name are recorded when the verbosity printed them (``-lv 5``)
  and recorded as ``unrecorded`` when it did not (``-lv 3`` campaign seats): the
  public aggregate never carries either, and a receipt that says less than the
  log is still a receipt.

Everything else is listed with a reason and never harvested. Production logs
(``arc-serve.log``, ``arc-swap*.log``) live outside every seat root and are
never offered to this module at all.

Log roots: the door's own seat directory (relative, sandbox-resolved) plus the
historical campaign roots on the model drive, which are read-only inputs and
are read as plain paths. Nothing is ever written under a log root.

Idempotency: a receipt is identified by ``(basename, prefix hash, task)``. A
launcher that reuses a label opens the log fresh, so its first 64 stamped lines
change and the file becomes a new seat epoch; rows from the old epoch stay in
the ledger. Re-running over an unchanged tree writes nothing. A task with a
launch line but no timing block is written as ``unknown`` only once it has
settled (a release line, a later launch on the same slot, or a quiescent log);
an in-flight task is left pending because receipts are immutable.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from hearth.seats import serverlog
from hearth.seats.receipts import (
    DERIVATION_METHOD, ERROR_BOUND_S, SCHEMA, SOURCE, SeatReceiptsLedger,
    attempt_identity, provider_identity, seat_identity,
)

#: Ports a gateway-brokered or gate-brokered call could have reached on OMEN:
#: the door's backend pool, the banked-fire rung, the friend gate, the retired
#: Ollama port, and the gateway itself. A seat on any of these is not disjoint.
DOOR_PORTS = frozenset({8081, 8082, 8083, 8084, 8710, 8791, 11434})
#: llama-swap allocates upstream ports here (``startPort`` 18300 / 18400); such
#: seats also carry the production API key, so this is belt and braces.
SWAP_EPHEMERAL_PORTS = range(18300, 18500)
DEFAULT_LOGS = "hearth/var/swap-logs"
#: Historical campaign seats on the model drive (ADR-0047 decision 9 amended by
#: the 2026-09-19 inventory): the same timing-line receipts at ``-lv 3``.
HISTORICAL_LOG_ROOTS = (
    r"E:\work\battlemage\burnin-2026-08\serverlogs",
    r"E:\work\battlemage\burnin-2026-08\results",
    r"E:\work\battlemage\qwen38-bench-2026-08\results\serverlogs",
    r"E:\work\battlemage\ff-probes",
    r"E:\work\battlemage\rotation-phase1",
    r"E:\work\battlemage\lz-probes",
)
DEFAULT_LEDGER = "hearth/var/seats/receipts.ndjson"
DEFAULT_CURSOR = "hearth/var/seats/harvest-cursor.json"
CURSOR_SCHEMA = "seat.harvest-cursor.v1"
REPORT_SCHEMA = "seat.harvest-report.v1"
#: A log whose mtime is older than this is quiescent: its untimed tasks settle.
QUIESCENT_S = 1800.0
UNKNOWN_REASON = "server log has no timing block for this task"
UNRECORDED = "unrecorded"

SKIP_DOOR_REACHABLE = "door-reachable: llama-swap api key"
SKIP_PORT = "port is a gateway-reachable rung"
SKIP_NO_LISTEN = "no listening line"
SKIP_SHORT = "short log"
SKIP_NO_MODEL = "no context size in load report"

COUNTERS = ("launched", "timed", "unknown", "pending", "probe_shaped",
            "tokens_in", "tokens_out", "new_receipts")


def eligibility(result: serverlog.ScanResult) -> Optional[str]:
    """None when the log is a harvestable research seat, else the skip reason."""
    if result.prefix_sha256 is None:
        return SKIP_SHORT
    if result.api_keyed:
        return SKIP_DOOR_REACHABLE
    if result.port is None:
        return SKIP_NO_LISTEN
    if result.port in DOOR_PORTS or result.port in SWAP_EPHEMERAL_PORTS:
        return SKIP_PORT
    if result.n_ctx_seq is None and result.n_ctx_slot is None:
        return SKIP_NO_MODEL
    return None


def _settled(result: serverlog.ScanResult, record: serverlog.TaskRecord, now: datetime) -> bool:
    if record.released_s is not None or result.later_launch_on_slot(record):
        return True
    age_s = now.timestamp() - result.mtime
    return age_s > QUIESCENT_S


def build_receipts(result: serverlog.ScanResult, seat_id: str, epoch: datetime,
                   now: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Receipts for every settled task of one eligible scan, plus the tally."""
    tally = {key: 0 for key in COUNTERS}
    receipts: list[dict[str, Any]] = []
    model_name = result.model_name or UNRECORDED
    build = result.build or UNRECORDED
    n_ctx = result.n_ctx_seq if result.n_ctx_seq is not None else result.n_ctx_slot
    provider = {
        "execution_class": "local",
        "identity_sha256": provider_identity(model_name, build, n_ctx),
        "model_name": model_name,
        "build": build,
        "n_ctx_seq": n_ctx,
    }
    derivation = {
        "method": DERIVATION_METHOD,
        "epoch_start": serverlog.rfc3339(epoch),
        "log_mtime": serverlog.rfc3339(datetime.fromtimestamp(result.mtime, tz=timezone.utc)),
        "error_bound_s": ERROR_BOUND_S,
    }
    harvested_at = serverlog.rfc3339(now)
    for key in sorted(result.tasks):
        record = result.tasks[key]
        tally["launched"] += 1
        common = {
            "schema": SCHEMA,
            "seat_id": seat_id,
            "attempt_id": attempt_identity(seat_id, record.task),
            "task": record.task,
            "slot": record.slot,
            "log_basename": result.basename,
            "seat_epoch_sha256": result.prefix_sha256,
            "provider": dict(provider),
            "started_at": serverlog.rfc3339(serverlog.wall_clock(epoch, record.launched_s)),
            "timestamp_derivation": dict(derivation),
            "source": dict(SOURCE),
            "harvested_at": harvested_at,
        }
        if record.timed:
            tally["timed"] += 1
            tally["tokens_in"] += record.prompt_n
            tally["tokens_out"] += record.predicted_n
            if record.prompt_n == 1 and record.predicted_n == 1:
                tally["probe_shaped"] += 1
            receipts.append({
                **common,
                "finished_at": serverlog.rfc3339(serverlog.wall_clock(epoch, record.timed_s)),
                "usage": {"tokens_in": record.prompt_n, "tokens_out": record.predicted_n},
                "usage_unknown_reason": None,
                "timing": {"prompt_ms": record.prompt_ms, "predicted_ms": record.eval_ms},
                "outcome": "succeeded",
            })
        elif _settled(result, record, now):
            tally["unknown"] += 1
            finished_s = record.released_s if record.released_s is not None else record.launched_s
            receipts.append({
                **common,
                "finished_at": serverlog.rfc3339(serverlog.wall_clock(epoch, finished_s)),
                "usage": None,
                "usage_unknown_reason": UNKNOWN_REASON,
                "timing": None,
                "outcome": "unknown",
            })
        else:
            tally["pending"] += 1
    return receipts, tally


def _load_cursor(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": CURSOR_SCHEMA, "updated_at": None, "logs": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != CURSOR_SCHEMA or not isinstance(data.get("logs"), dict):
        raise ValueError("unrecognized harvest cursor")
    return data


def _write_cursor(path: Path, cursor: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cursor, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def iter_logs(roots: Iterable[Path]) -> Iterable[Path]:
    """Every ``*.log`` under the roots, recursively, in a stable order.

    ``.console`` twins and anything that is not a regular file are never
    offered. A root that does not exist is simply empty: the historical roots
    live on a drive that may be absent, and an absence is not an error here.
    """
    seen: set[Path] = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.log")):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def harvest(logs_dirs: Union[Path, str, Iterable[Union[Path, str]]], ledger_path: Path,
            cursor_path: Path, *, dry_run: bool = False,
            now: Optional[datetime] = None) -> dict[str, Any]:
    """Scan the log roots once and append the new receipts. Returns the report."""
    now = now or datetime.now(timezone.utc)
    roots = [Path(logs_dirs)] if isinstance(logs_dirs, (str, Path)) else [Path(p) for p in logs_dirs]
    ledger = SeatReceiptsLedger(Path(ledger_path))
    cursor = _load_cursor(Path(cursor_path))
    entries: dict[str, Any] = cursor["logs"]
    known = ledger.attempt_ids()
    seen_seats: set[str] = set()
    rows: list[dict[str, Any]] = []
    totals = {key: 0 for key in COUNTERS}
    totals.update({"eligible_logs": 0, "skipped_logs": 0, "seats": 0})

    for path in iter_logs(roots):
        key = str(path.resolve())
        basename = path.stem
        previous = entries.get(key)
        stat = path.stat()
        unchanged = (previous is not None and previous.get("size") == stat.st_size
                     and previous.get("mtime") == stat.st_mtime
                     and not previous.get("pending_tasks"))
        if unchanged:
            row = {"basename": basename, "eligible": previous["eligible"],
                   "skip_reason": previous.get("skip_reason"), "changed": False,
                   **{k: previous.get(k, 0) for k in COUNTERS}, "new_receipts": 0}
            if previous.get("seat_id") and previous.get("launched"):
                seen_seats.add(previous["seat_id"])
            rows.append(row)
            continue

        result = serverlog.scan(path)
        reason = eligibility(result)
        entry: dict[str, Any] = {
            "size": result.size, "mtime": result.mtime, "eligible": reason is None,
            "skip_reason": reason, "seat_epoch_sha256": result.prefix_sha256,
            "port": result.port, "model_name": result.model_name,
            **{k: 0 for k in COUNTERS},
        }
        row = {"basename": basename, "eligible": reason is None, "skip_reason": reason,
               "changed": True, **{k: 0 for k in COUNTERS}}
        if reason is None:
            same_epoch = (previous is not None
                          and previous.get("seat_epoch_sha256") == result.prefix_sha256
                          and previous.get("epoch_start"))
            if same_epoch:
                seat_id = previous["seat_id"]
                epoch = datetime.fromisoformat(previous["epoch_start"].replace("Z", "+00:00"))
            else:
                seat_id = seat_identity(basename, result.prefix_sha256)
                epoch = serverlog.epoch_start(result)
            receipts, tally = build_receipts(result, seat_id, epoch, now)
            fresh = [r for r in receipts if r["attempt_id"] not in known]
            tally["new_receipts"] = len(fresh)
            if fresh and not dry_run:
                ledger.append(fresh)
                known.update(r["attempt_id"] for r in fresh)
            entry.update(tally)
            entry.update({"seat_id": seat_id, "epoch_start": serverlog.rfc3339(epoch),
                          "pending_tasks": tally["pending"],
                          "harvested_attempts": len(receipts) - tally["pending"]})
            row.update(tally)
            row["seat_id"] = seat_id
            if tally["launched"]:
                seen_seats.add(seat_id)
        if reason == SKIP_SHORT:
            # Deferred, not classified: the header is incomplete, so no entry yet.
            rows.append(row)
            continue
        entries[key] = entry
        rows.append(row)

    for row in rows:
        if row["eligible"]:
            totals["eligible_logs"] += 1
            for k in COUNTERS:
                totals[k] += row.get(k, 0)
        else:
            totals["skipped_logs"] += 1
    totals["seats"] = len(seen_seats)

    # The cursor is rewritten only when a log changed, so a tick over an
    # unchanged tree leaves both files byte-identical.
    dirty = any(row["changed"] for row in rows) or not Path(cursor_path).exists()
    if dirty and not dry_run:
        cursor["updated_at"] = serverlog.rfc3339(now)
        _write_cursor(Path(cursor_path), cursor)

    return {"schema": REPORT_SCHEMA, "dry_run": dry_run, "at": serverlog.rfc3339(now),
            "logs": rows, "totals": totals}


def render_table(report: dict[str, Any]) -> str:
    header = (f"{'log':56} {'status':36} {'launch':>6} {'timed':>5} {'unkn':>4} {'pend':>4} "
              f"{'probe':>5} {'tok_in':>9} {'tok_out':>8} {'new':>4}")
    lines = [header, "-" * len(header)]
    for row in report["logs"]:
        status = "eligible" if row["eligible"] else f"skip: {row['skip_reason']}"
        lines.append(
            f"{row['basename'][:56]:56} {status[:36]:36} {row['launched']:6} {row['timed']:5} "
            f"{row['unknown']:4} {row['pending']:4} {row['probe_shaped']:5} {row['tokens_in']:9} "
            f"{row['tokens_out']:8} {row['new_receipts']:4}")
    t = report["totals"]
    lines.append("-" * len(header))
    lines.append(
        f"eligible {t['eligible_logs']} / skipped {t['skipped_logs']} logs; seats {t['seats']}; "
        f"launched {t['launched']}, timed {t['timed']}, unknown {t['unknown']}, pending {t['pending']}, "
        f"probe-shaped {t['probe_shaped']}; tokens in {t['tokens_in']:,}, out {t['tokens_out']:,}; "
        f"new receipts {t['new_receipts']}" + ("  [dry run: nothing written]" if report["dry_run"] else ""))
    return "\n".join(lines)


def default_roots(resolve) -> list[Path]:
    """The sandbox-resolved seat directory plus the historical roots as given."""
    return [resolve(DEFAULT_LOGS)] + [Path(root) for root in HISTORICAL_LOG_ROOTS]


def main(argv: Optional[list[str]] = None) -> int:
    from hearth.toolsurface._scope import resolve_in_scope

    parser = argparse.ArgumentParser(
        prog="python -m hearth.seats.harvest",
        description="Harvest research-seat llama-server logs into seat receipts (ADR-0047).")
    parser.add_argument("--logs", action="append", default=None,
                        help="a log root (repeatable); default: the seat directory plus the historical campaign roots")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--cursor", default=DEFAULT_CURSOR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.logs:
            roots = [Path(p) if Path(p).is_absolute() else resolve_in_scope(p) for p in args.logs]
        else:
            roots = default_roots(resolve_in_scope)
        report = harvest(roots, resolve_in_scope(args.ledger), resolve_in_scope(args.cursor),
                         dry_run=args.dry_run)
    except Exception as exc:
        print(f"seat harvest: FAILED {exc}")
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(render_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
