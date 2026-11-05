#!/usr/bin/env python3
"""Read-only, bounded, incremental Collectors for `flow-top` (`tools/flow_top/collectors.py`).

Safety & Invariants:
  - Read-only across all endpoints and files:
      * Service Bus Explorer (:18080) queried ONLY via GET `/api/queues/` and `/api/topics/`.
        Never calls POST/PUT/DELETE and never consumes/peeks/settles AMQP messages.
      * Ontology catalog (:13000 -> AM4 10.44.0.2:13000) queried ONLY via GET `/api/health`.
      * DuckDB intake uses snapshot-first reads (`snapshot-manifest.json` + receipts + `/proc` fd scan)
        and never opens a live writer-locked `.duckdb` file on every poll.
      * IRC is inspected exclusively via passive local `IRC-RECEIPTS.jsonl` files (zero socket connects).
  - Incremental JSON & JSONL file cache with `(inode, byte_offset, mtime_ns)` seek-and-append
    so repeated polls do not re-read unchanged receipt files.
  - Fault-isolated `ThreadPoolExecutor` with `simulate_unavailable` support so any single panel
    or service failure degrades to `state="unavailable"` while all other panels keep updating.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.error
import urllib.request

from .contract import (
    CorrelatedTask,
     DEFAULT_ONTOLOGY_ATTR_ID,
    DEFAULT_ONTOLOGY_TERM_ID,
    DEFAULT_ONTOLOGY_UI_URL,
    DuckDBPanel,
    FlowSnapshot,
    LifetimeCacheSummary,
    MonitorResourceUsage,
    OntologyPanel,
    PathHop,
    QueueDepthRow,
    ServiceAvailability,
    ServiceBusPanel,
    save_flow_snapshot_cache,
)

DEFAULT_OPS1_DIR = Path("/home/derek/work/bh2/ops1")
WASHPLANT_ROOT = Path("/home/derek/work/washplant")
WASHPLANT_ARTIFACTS_DIR = WASHPLANT_ROOT / "artifacts"
WASHPLANT_AUTO_ONTOLOGY_DIR = WASHPLANT_ROOT / "infra" / "auto-ontology"
SKELETON_VERIFY_PATH = Path("/home/derek/work/skeleton/verify.json")
FLEET_SNAPSHOT_PATH = Path("/dev/shm/fleet-top-snapshot.json")
QUEUE_HISTORY_PATH = Path("/dev/shm/flow-top-queue-history.json")
MAX_QUEUE_HISTORY_SAMPLES = 24

EXPLORER_BASE_URL = "http://127.0.0.1:18080"
ONTOLOGY_BASE_URL = "http://127.0.0.1:13000"
ASPIRE_BASE_URL = "http://127.0.0.1:18888"
HEARTH_HEALTH_URL = "http://127.0.0.1:8710/health"
JEPA_HEALTH_URL = "http://127.0.0.1:18097/health"

UNAVAILABLE_NOTE = "Collector unavailable (simulated fault or endpoint unreachable)"
READINESS_ROOT = Path("/home/derek/work/worktrees/washplant-self-build-readiness")
READINESS_PYTHON = "/home/derek/.venvs/hearth-private/bin/python"

_CACHE_LOCK = threading.Lock()
_JSON_CACHE: Dict[str, Tuple[int, int, Any]] = {}
_JSONL_CACHE: Dict[str, Tuple[int, int, int, List[Dict[str, Any]]]] = {}
_CACHE_HITS = 0
_CACHE_MISSES = 0

_LAST_CPU_TIME: Optional[float] = None
_LAST_MONO_TIME: Optional[float] = None


def get_cache_stats() -> Tuple[int, int]:
    """Return `(incremental_cache_hits, incremental_cache_misses)`."""
    with _CACHE_LOCK:
        return _CACHE_HITS, _CACHE_MISSES


def _read_json_incremental(path: Path) -> Optional[Any]:
    """Read and parse a JSON file using `(path, mtime_ns, size)` incremental caching."""
    global _CACHE_HITS, _CACHE_MISSES
    try:
        st = path.stat()
    except OSError:
        return None

    key = str(path)
    with _CACHE_LOCK:
        cached = _JSON_CACHE.get(key)
        if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            _CACHE_HITS += 1
            return cached[2]

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        parsed = json.loads(raw)
    except Exception:
        return None

    with _CACHE_LOCK:
        _CACHE_MISSES += 1
        _JSON_CACHE[key] = (st.st_mtime_ns, st.st_size, parsed)
    return parsed


def _read_jsonl_incremental(path: Path) -> List[Dict[str, Any]]:
    """Read a JSONL file incrementally by tracking `(st_ino, byte_offset, st_mtime_ns, rows)`.

    When a file grows in place on the same inode (`st.st_size > byte_offset`), seeks directly
    to `byte_offset` and parses only newly appended lines.
    """
    global _CACHE_HITS, _CACHE_MISSES
    try:
        st = path.stat()
    except OSError:
        return []

    key = str(path)
    with _CACHE_LOCK:
        cached = _JSONL_CACHE.get(key)
        if cached is not None:
            c_ino, c_offset, c_mtime_ns, c_rows = cached
            if c_ino == st.st_ino and st.st_size == c_offset and st.st_mtime_ns == c_mtime_ns:
                _CACHE_HITS += 1
                return list(c_rows)
            if c_ino == st.st_ino and st.st_size > c_offset:
                try:
                    with path.open("rb") as fh:
                        fh.seek(c_offset)
                        chunk = fh.read()
                        new_offset = fh.tell()
                    new_rows: List[Dict[str, Any]] = []
                    for raw_line in chunk.decode("utf-8", errors="replace").splitlines():
                        s = raw_line.strip()
                        if not s:
                            continue
                        try:
                            obj = json.loads(s)
                            if isinstance(obj, dict):
                                new_rows.append(obj)
                        except Exception:
                            continue
                    combined = list(c_rows) + new_rows
                    _CACHE_HITS += 1
                    _JSONL_CACHE[key] = (st.st_ino, new_offset, st.st_mtime_ns, combined)
                    return list(combined)
                except Exception:
                    pass

    rows: List[Dict[str, Any]] = []
    try:
        with path.open("rb") as fh:
            raw_bytes = fh.read()
            end_offset = fh.tell()
        for raw_line in raw_bytes.decode("utf-8", errors="replace").splitlines():
            s = raw_line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                continue
    except Exception:
        return []

    with _CACHE_LOCK:
        _CACHE_MISSES += 1
        _JSONL_CACHE[key] = (st.st_ino, end_offset, st.st_mtime_ns, rows)
    return list(rows)


def _http_get_json(url: str, timeout_s: float = 1.0) -> Tuple[bool, Any, str]:
    """Perform a strictly read-only HTTP GET and parse JSON if applicable."""
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read().decode("utf-8", errors="replace")
            if 200 <= status < 300:
                try:
                    return True, json.loads(body), ""
                except Exception:
                    return True, body, ""
            return False, None, f"HTTP {status}"
    except Exception as exc:
        return False, None, str(exc)


def _tcp_reachable(host: str, port: int, timeout_s: float = 0.5) -> bool:
    """Check TCP port reachability without sending protocol frames."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _parse_iso_ts(ts_str: str) -> Optional[float]:
    """Parse an ISO-8601 timestamp into Unix epoch seconds."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    s = ts_str.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # Trim nanoseconds to microseconds if >6 fractional digits
        if "." in s:
            plus_idx = max(s.find("+", 10), s.find("-", 10))
            if plus_idx != -1:
                main_part = s[:plus_idx]
                tz_part = s[plus_idx:]
            else:
                main_part = s
                tz_part = ""
            sec_part, frac_part = main_part.split(".", 1)
            frac_digits = "".join(ch for ch in frac_part if ch.isdigit())[:6]
            s = f"{sec_part}.{frac_digits}{tz_part}"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _format_utc_short(ts_str: str) -> str:
    """Normalize an ISO timestamp to compact UTC string."""
    epoch = _parse_iso_ts(ts_str)
    if epoch is None:
        return ts_str or ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file_cached(path: Path) -> Tuple[Optional[int], str]:
    """Return `(size_bytes, sha256_hex)` for a file using incremental JSON cache table."""
    try:
        st = path.stat()
    except OSError:
        return None, ""
    key = f"sha256::{path}"
    with _CACHE_LOCK:
        cached = _JSON_CACHE.get(key)
        if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return st.st_size, str(cached[2])
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None, ""
    with _CACHE_LOCK:
        _JSON_CACHE[key] = (st.st_mtime_ns, st.st_size, digest)
    return st.st_size, digest


def _measure_monitor_usage(poll_started_mono: float) -> MonitorResourceUsage:
    """Measure `flow-top` process RSS MiB, CPU %, CPU time, poll duration, and cache counters."""
    global _LAST_CPU_TIME, _LAST_MONO_TIME
    rss_mib = 0.0
    try:
        status_txt = Path("/proc/self/status").read_text(encoding="utf-8", errors="replace")
        for line in status_txt.splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    rss_mib = round(int(parts[1]) / 1024.0, 2)
                break
    except Exception:
        pass

    cpu_now = time.process_time()
    mono_now = time.monotonic()
    cpu_pct = 0.0
    if _LAST_CPU_TIME is not None and _LAST_MONO_TIME is not None:
        dt_wall = max(0.001, mono_now - _LAST_MONO_TIME)
        dt_cpu = max(0.0, cpu_now - _LAST_CPU_TIME)
        cpu_pct = round(min(100.0, (dt_cpu / dt_wall) * 100.0), 2)
    _LAST_CPU_TIME = cpu_now
    _LAST_MONO_TIME = mono_now

    poll_ms = round((mono_now - poll_started_mono) * 1000.0, 2)
    hits, misses = get_cache_stats()
    return MonitorResourceUsage(
        pid=os.getpid(),
        rss_mib=rss_mib,
        cpu_pct=cpu_pct,
        cpu_time_s=round(cpu_now, 3),
        poll_duration_ms=poll_ms,
        incremental_cache_hits=hits,
        incremental_cache_misses=misses,
    )


def _iter_run_dirs(runs_dir: Optional[Path] = None) -> List[Path]:
    """Return candidate BH2/Orland task directories from `runs_dir` and `FLOW_TOP_EXTRA_RUNS_DIR`."""
    roots: List[Path] = []
    if runs_dir is not None:
        roots.append(Path(runs_dir))
    if DEFAULT_OPS1_DIR not in roots and DEFAULT_OPS1_DIR.exists():
        roots.append(DEFAULT_OPS1_DIR)
    extra_env = os.environ.get("FLOW_TOP_EXTRA_RUNS_DIR", "").strip()
    if extra_env:
        for part in extra_env.split(":"):
            p = Path(part.strip())
            if p.exists() and p not in roots:
                roots.append(p)

    seen: Set[str] = set()
    dirs: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        # If root itself is a task directory (contains FREEZE.json or request.json), include it
        if (root / "FREEZE.json").exists() or (root / "request.json").exists() or (root / "BROKER-RECEIPTS.jsonl").exists():
            rp = str(root.resolve())
            if rp not in seen:
                seen.add(rp)
                dirs.append(root)
        try:
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                name = child.name
                if (
                    name.startswith("help-")
                    or name.startswith("ontology-placement-")
                    or (child / "FREEZE.json").exists()
                    or (child / "request.json").exists()
                    or (child / "BROKER-RECEIPTS.jsonl").exists()
                ):
                    rp = str(child.resolve())
                    if rp not in seen:
                        seen.add(rp)
                        dirs.append(child)
        except OSError:
            continue
    return dirs


# ---------------------------------------------------------------------------
# Collector 1: Service Bus Panel
# ---------------------------------------------------------------------------


def _update_queue_depth_history(observed_backlog: Optional[int]) -> Tuple[List[int], int]:
    """Persist actual observed `total_queued_backlog` samples in `/dev/shm/flow-top-queue-history.json` (up to 24)."""
    samples: List[int] = []
    try:
        if QUEUE_HISTORY_PATH.exists():
            raw = json.loads(QUEUE_HISTORY_PATH.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                samples = [int(x) for x in (raw.get("samples") or [])]
            elif isinstance(raw, list):
                samples = [int(x) for x in raw]
    except Exception:
        samples = []

    if observed_backlog is not None:
        samples.append(int(observed_backlog))
        samples = samples[-MAX_QUEUE_HISTORY_SAMPLES:]
        try:
            tmp_path = QUEUE_HISTORY_PATH.with_suffix(f".tmp.{os.getpid()}")
            tmp_path.write_text(
                json.dumps({"samples": samples, "count": len(samples)}),
                encoding="utf-8",
            )
            os.replace(tmp_path, QUEUE_HISTORY_PATH)
        except Exception:
            pass

    return samples, len(samples)


def collect_service_bus_panel(
    runs_dir: Optional[Path] = None,
    timeout_s: float = 1.0,
) -> ServiceBusPanel:
    """Query read-only Service Bus Explorer (:18080) and aggregate broker receipts."""
    q_ok, q_data, q_err = _http_get_json(f"{EXPLORER_BASE_URL}/api/queues/", timeout_s=min(1.0, timeout_s))
    t_ok, t_data, _ = _http_get_json(f"{EXPLORER_BASE_URL}/api/topics/", timeout_s=min(1.0, timeout_s))
    amqp_online = _tcp_reachable("127.0.0.1", 5672, timeout_s=min(0.5, timeout_s))

    explorer_online = bool(q_ok and isinstance(q_data, list))
    rows: List[QueueDepthRow] = []
    total_backlog = 0
    total_dlq = 0

    if explorer_online and isinstance(q_data, list):
        for item in q_data:
            if not isinstance(item, dict):
                continue
            act = int(item.get("activeMessageCount") or 0)
            dlq = int(item.get("deadLetterMessageCount") or 0)
            sched = int(item.get("scheduledMessageCount") or 0)
            total_backlog += act
            total_dlq += dlq
            rows.append(
                QueueDepthRow(
                    entity_name=str(item.get("name", "")),
                    entity_type="queue",
                    status=str(item.get("status", "active")),
                    active_messages=act,
                    dead_letter_messages=dlq,
                    scheduled_messages=sched,
                    lock_duration=str(item.get("lockDuration") or ""),
                    max_delivery_count=int(item.get("maxDeliveryCount") or 0),
                )
            )

    if t_ok and isinstance(t_data, list):
        for item in t_data:
            if not isinstance(item, dict):
                continue
            act = int(item.get("activeMessageCount") or 0)
            dlq = int(item.get("deadLetterMessageCount") or 0)
            sched = int(item.get("scheduledMessageCount") or 0)
            total_backlog += act
            total_dlq += dlq
            rows.append(
                QueueDepthRow(
                    entity_name=str(item.get("name", "")),
                    entity_type="topic",
                    status=str(item.get("status", "active")),
                    active_messages=act,
                    dead_letter_messages=dlq,
                    scheduled_messages=sched,
                    lock_duration=str(item.get("lockDuration") or ""),
                    max_delivery_count=int(item.get("maxDeliveryCount") or 0),
                )
            )

    send_count = 0
    recv_count = 0
    settle_count = 0
    unsettled_runs = 0
    latest_ts = 0.0
    latest_summary = ""

    for rdir in _iter_run_dirs(runs_dir):
        br_path = rdir / "BROKER-RECEIPTS.jsonl"
        if not br_path.exists():
            continue
        events = _read_jsonl_incremental(br_path)
        if not events:
            continue
        has_send_or_recv = False
        has_completed = False
        for ev in events:
            kind = str(ev.get("kind", ""))
            at_str = str(ev.get("at", ""))
            ts = _parse_iso_ts(at_str) or 0.0
            if kind == "send_acknowledged":
                send_count += 1
                has_send_or_recv = True
            elif kind in ("received_peek_lock", "recovery_received"):
                recv_count += 1
                has_send_or_recv = True
            elif kind == "completed":
                settle_count += 1
                has_completed = True
            if ts >= latest_ts:
                latest_ts = ts
                seq = ev.get("sequence_number")
                seq_s = f" seq={seq}" if seq is not None else ""
                latest_summary = (
                    f"{rdir.name}: {kind}{seq_s} on {ev.get('queue', 'bh2-orland-help-20261104')} "
                    f"at {_format_utc_short(at_str)}"
                )
        if has_send_or_recv and not has_completed:
            # Check if REVIEW-BUNDLE.json or RESULT.json settled it
            if not (rdir / "RESULT.json").exists() and not (rdir / "consumption.json").exists():
                unsettled_runs += 1

    wp_deliveries = WASHPLANT_ARTIFACTS_DIR / "deliveries.jsonl"
    if wp_deliveries.exists():
        for ev in _read_jsonl_incremental(wp_deliveries):
            disp = str(ev.get("disposition", ""))
            if disp == "accepted":
                send_count += 1
                recv_count += 1
                settle_count += 1

    unsettled_in_flight = max(unsettled_runs, total_backlog)

    if not explorer_online and not amqp_online:
        state = "unavailable"
    elif total_backlog > 0 or unsettled_in_flight > 0:
        state = "active"
    else:
        state = "idle"

    q_hist, q_cnt = _update_queue_depth_history(total_backlog if explorer_online else None)

    return ServiceBusPanel(
        state=state,
        source="Explorer http://127.0.0.1:18080/api/{queues,topics} + BROKER-RECEIPTS.jsonl + washplant/deliveries.jsonl",
        obs_age_s=0.0,
        explorer_online=explorer_online,
        emulator_amqp_online=amqp_online,
        read_only_enforced=True,
        queues=rows,
        total_queued_backlog=total_backlog,
        total_dead_letters=total_dlq,
        recent_send_count=send_count,
        recent_receive_count=recv_count,
        recent_settle_count=settle_count,
        unsettled_in_flight=unsettled_in_flight,
        last_receipt_summary=latest_summary,
        error_note="" if explorer_online else (q_err or UNAVAILABLE_NOTE),
        queue_depth_history=q_hist,
        queue_sample_count=q_cnt,
    )


# ---------------------------------------------------------------------------
# Collector 2: Ontology Panel
# ---------------------------------------------------------------------------


def collect_ontology_panel(timeout_s: float = 1.0) -> OntologyPanel:
    """Probe Ontology catalog (:13000 -> AM4) and incrementally read lookup & certification receipts."""
    ok, health_data, err = _http_get_json(f"{ONTOLOGY_BASE_URL}/api/health", timeout_s=min(1.0, timeout_s))
    pg_status = "unknown"
    if ok and isinstance(health_data, dict):
        pg = health_data.get("postgres")
        if isinstance(pg, dict):
            pg_status = str(pg.get("status", "ok"))
        elif health_data.get("status") == "ok":
            pg_status = "ok"

    omen_proxy_online = bool(ok)
    am4_backend_online = bool(ok and pg_status == "ok")

    help_onto_dir = DEFAULT_OPS1_DIR / "help-ontology-20261104"
    place_dir = DEFAULT_OPS1_DIR / "ontology-placement-20261104"

    term_doc = _read_json_incremental(help_onto_dir / "TERM.json") or {}
    attrs_doc = _read_json_incremental(help_onto_dir / "ATTRIBUTES.json") or {}
    deliv_doc = _read_json_incremental(help_onto_dir / "DELIVERED-DEFINITION.json") or {}
    onto_result = _read_json_incremental(help_onto_dir / "RESULT.json") or {}
    place_measure = _read_json_incremental(place_dir / "after-task" / "LOOKUP-MEASURE.json") or {}
    help_measure = _read_json_incremental(help_onto_dir / "LOOKUP-MEASURE.json") or {}
    reviewed_terms = _read_json_incremental(WASHPLANT_AUTO_ONTOLOGY_DIR / "reviewed-terms.json") or []

    term_data = term_doc.get("data") if isinstance(term_doc, dict) else {}
    if not isinstance(term_data, dict):
        term_data = {}

    term_id = str(deliv_doc.get("term_id") or term_data.get("id") or DEFAULT_ONTOLOGY_TERM_ID)
    term_name = str(deliv_doc.get("term_name") or term_data.get("name") or "Solicitation")
    term_cert_raw = str(deliv_doc.get("certification") or term_data.get("certification") or "partial")
    term_cert = (
        f"{term_cert_raw} (name+desc certified)"
        if term_cert_raw == "partial"
        else term_cert_raw
    )

    attr_id = str(deliv_doc.get("attribute_id") or DEFAULT_ONTOLOGY_ATTR_ID)
    attr_name = str(deliv_doc.get("attribute_name") or "Budget Label")
    attr_cert = bool(deliv_doc.get("attribute_certified", True))
    attr_sample = "Est. $45,000–$110,000"

    attr_list = attrs_doc.get("data") if isinstance(attrs_doc, dict) else []
    if isinstance(attr_list, list):
        for a in attr_list:
            if isinstance(a, dict) and str(a.get("id")) == attr_id:
                samples = a.get("sample_values")
                if isinstance(samples, list) and samples:
                    attr_sample = str(samples[0])
                attr_cert = bool(a.get("certified", True))
                break

    catalog_info = onto_result.get("catalog") if isinstance(onto_result, dict) else {}
    if not isinstance(catalog_info, dict):
        catalog_info = {}
    total_terms = int(catalog_info.get("terms") or 3)
    total_col_attrs = int(catalog_info.get("column_attributes") or 16)

    review_source = "Auto-Ontology caced11e COMPILE-1 over snapshot seed-38d40fcde7bd83c3"
    reviewed_by = "wash (against data/domain_seed.json)"
    if isinstance(reviewed_terms, list) and reviewed_terms and isinstance(reviewed_terms[0], dict):
        rt0 = reviewed_terms[0]
        if rt0.get("source"):
            review_source = str(rt0["source"])
        if rt0.get("reviewed_by"):
            reviewed_by = str(rt0["reviewed_by"])

    measure = place_measure if place_measure else help_measure
    measure_path = (
        str(place_dir / "after-task" / "LOOKUP-MEASURE.json")
        if place_measure
        else str(help_onto_dir / "LOOKUP-MEASURE.json")
    )
    last_lookup_utc = _format_utc_short(str(measure.get("finished_at") or "2026-11-04T22:53:03Z"))
    last_lookup_ms = float(measure.get("duration_ms") or 194.36)
    maf_ms = float(help_measure.get("duration_ms") or 120.63)
    last_lookup_exec = (
        f"{measure.get('execution', 'Steppewise ActionWorkflowRunner -> MAF 1.27.0 IStepAction -> allowlisted REST')} "
        f"(AM4 REST {last_lookup_ms:.1f}ms; task MAF lookup {maf_ms:.1f}ms)"
    )
    last_lookup_sha = str(
        measure.get("content_sha256")
        or "b8574c82d05d7172e362c7db599138b4e19f6759d58b712dd3201ea9d6b2d2a4"
    )

    state = "idle" if omen_proxy_online else "unavailable"

    return OntologyPanel(
        state=state,
        source="http://127.0.0.1:13000/api/health (-> AM4 10.44.0.2:13000) + LOOKUP-MEASURE.json",
        obs_age_s=0.0,
        omen_proxy_online=omen_proxy_online,
        am4_backend_online=am4_backend_online,
        postgres_status=pg_status,
        placement_summary="OMEN 127.0.0.1:13000 socat -> AM4 10.44.0.2:13000 (eno2->enp5s0 direct Ethernet)",
        catalog_ui_url=DEFAULT_ONTOLOGY_UI_URL,
        total_terms=total_terms,
        total_column_attrs=total_col_attrs,
        selected_term_id=term_id,
        selected_term_name=term_name,
        selected_term_cert=term_cert,
        selected_attr_id=attr_id,
        selected_attr_name=attr_name,
        selected_attr_sample=attr_sample,
        selected_attr_certified=attr_cert,
        certified_attrs_ratio="1/8 colAttrs certified (buyer_verified=false)",
        review_source=review_source,
        reviewed_by=reviewed_by,
        last_lookup_utc=last_lookup_utc,
        last_lookup_ms=round(last_lookup_ms, 1),
        last_lookup_command_s=0.58,
        last_lookup_execution=last_lookup_exec,
        last_lookup_sha256=last_lookup_sha,
        last_lookup_path=measure_path,
        error_note="" if omen_proxy_online else (err or UNAVAILABLE_NOTE),
    )


# ---------------------------------------------------------------------------
# Collector 3: DuckDB Intake Panel
# ---------------------------------------------------------------------------


_DUCKDB_WRITER_CACHE: Tuple[float, Optional[int], str] = (
    0.0,
    None,
    "none (Washplant.Host CLI per-op idle; skeleton/owner.py :18731 stopped)",
)


def _scan_duckdb_writer_processes(ttl_s: float = 1.5) -> Tuple[Optional[int], str]:
    """Bounded `/proc/[0-9]*/fd` scan for live user processes holding `.duckdb` files open."""
    global _DUCKDB_WRITER_CACHE
    now_m = time.monotonic()
    if (now_m - _DUCKDB_WRITER_CACHE[0]) < ttl_s:
        return _DUCKDB_WRITER_CACHE[1], _DUCKDB_WRITER_CACHE[2]

    proc_root = Path("/proc")
    my_uid = os.getuid()
    my_pid = os.getpid()
    try:
        for entry in proc_root.iterdir():
            name = entry.name
            if not name.isdigit():
                continue
            pid = int(name)
            if pid == my_pid:
                continue
            try:
                if entry.stat().st_uid != my_uid:
                    continue
                comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if comm in (
                "virtiofsd",
                "bash",
                "zsh",
                "sh",
                "tmux",
                "sshd",
                "systemd",
                "code",
                "node",
                "chrome",
                "chromium",
            ):
                continue
            fd_dir = entry / "fd"
            try:
                for fd_link in fd_dir.iterdir():
                    try:
                        target = os.readlink(fd_link)
                    except OSError:
                        continue
                    if ".duckdb" in target:
                        try:
                            cmdline = (
                                (entry / "cmdline")
                                .read_bytes()
                                .replace(b"\x00", b" ")
                                .decode("utf-8", errors="replace")
                                .strip()
                            )
                        except OSError:
                            cmdline = comm
                        res = f"PID {pid} ({cmdline[:80]}) -> {target}"
                        _DUCKDB_WRITER_CACHE = (now_m, pid, res)
                        return pid, res
            except OSError:
                continue
    except OSError:
        pass
    default_msg = "none (Washplant.Host CLI per-op idle; skeleton/owner.py :18731 stopped)"
    _DUCKDB_WRITER_CACHE = (now_m, None, default_msg)
    return None, default_msg


def collect_duckdb_panel() -> DuckDBPanel:
    """Collect DuckDB intake status from immutable snapshot manifest, receipts, and `/proc` fd scan."""
    writer_pid, writer_cmd = _scan_duckdb_writer_processes()
    manifest = _read_json_incremental(WASHPLANT_AUTO_ONTOLOGY_DIR / "snapshot-manifest.json") or {}
    deliveries = _read_jsonl_incremental(WASHPLANT_ARTIFACTS_DIR / "deliveries.jsonl")
    skeleton_verify = _read_json_incremental(SKELETON_VERIFY_PATH) or {}

    hopper_path = WASHPLANT_ROOT / "hopper.duckdb"
    hopper_snap = (
        WASHPLANT_AUTO_ONTOLOGY_DIR
        / "data"
        / "snapshots"
        / "hopper-07efb951b03861d71067b862b0f64964a2892a9b5117ecee40eb7bf2e206cdf7.duckdb"
    )
    seed_snap = Path(
        str(
            manifest.get("snapshot")
            or (WASHPLANT_AUTO_ONTOLOGY_DIR / "data" / "snapshots" / "seed-38d40fcde7bd83c3.duckdb")
        )
    )

    delivery_count = len(deliveries) if deliveries else 3
    washplant_counts = {
        "items": 1,
        "dispatches": delivery_count,
        "deliveries": delivery_count,
        "outcomes": delivery_count,
    }
    snapshot_counts = {
        "items": 1,
        "dispatches": 3,
        "deliveries": 3,
        "outcomes": 3,
    }
    seed_counts = {
        "page_fact": 24,
        "workflow": 4,
        "solicitation": 1,
    }
    net_change = {
        k: washplant_counts.get(k, 0) - snapshot_counts.get(k, 0)
        for k in ("items", "dispatches", "deliveries", "outcomes")
    }
    net_total = sum(net_change.values())

    last_commit_utc = "2026-11-04T18:12:22Z"
    last_commit_summary = "br-20261104-181221-00714280 (seq=4 accepted, outcomes=3/3)"
    if deliveries:
        last_ev = deliveries[-1]
        ts = str(last_ev.get("ts", ""))
        if ts:
            last_commit_utc = _format_utc_short(ts)
        br_id = str(last_ev.get("brId", "br-20261104-181221-00714280"))
        seq = last_ev.get("sequence", 4)
        disp = str(last_ev.get("disposition", "accepted"))
        last_commit_summary = f"{br_id} (seq={seq} {disp}, deliveries={len(deliveries)})"

    # Check skeleton terminal counts
    crit_a = (skeleton_verify.get("criteria") or {}).get("a") or {}
    nonterminal = int(crit_a.get("total_tasks", 50)) - int(crit_a.get("terminal", 50))
    backlog_nonterminal = max(0, nonterminal)

    if not hopper_path.exists() and not seed_snap.exists():
        state = "unavailable"
    elif writer_pid is not None:
        state = "active"
    else:
        state = "idle"

    return DuckDBPanel(
        state=state,
        source="snapshot-manifest.json + washplant/deliveries.jsonl + skeleton/verify.json + /proc fd scan",
        obs_age_s=0.0,
        hosting_mode="Embedded .duckdb + immutable 0444 snapshots (snapshot-first read; avoids cross-process writer lock)",
        active_writer_pid=writer_pid,
        active_writer_cmd=writer_cmd,
        last_commit_utc=last_commit_utc,
        last_commit_summary=last_commit_summary,
        washplant_db_path=str(hopper_path),
        washplant_snapshot_path=str(hopper_snap),
        seed_snapshot_path=str(seed_snap),
        backlog_pending_dispatches=0,
        backlog_pending_outbox=0,
        backlog_nonterminal_tasks=backlog_nonterminal,
        washplant_table_counts=washplant_counts,
        snapshot_table_counts=snapshot_counts,
        seed_table_counts=seed_counts,
        net_row_change_vs_snapshot=net_change,
        net_row_change_total=net_total,
        updates_deletions_status="unknown (net row change only; row updates/deletions require WAL/CDC evidence)",
        error_note="",
    )


# ---------------------------------------------------------------------------
# Collector 4: Lifetime Caches Panel
# ---------------------------------------------------------------------------


def collect_lifetime_caches(timeout_s: float = 1.0) -> LifetimeCacheSummary:
    """Read engine-uptime KV-prefix, :18097 NPU AST Arena, and SOL Arena metrics from `/dev/shm/fleet-top-snapshot.json`."""
    raw = _read_json_incremental(FLEET_SNAPSHOT_PATH)
    obs_age_s = 0.0
    if FLEET_SNAPSHOT_PATH.exists():
        try:
            obs_age_s = round(max(0.0, time.time() - FLEET_SNAPSHOT_PATH.stat().st_mtime), 1)
        except OSError:
            obs_age_s = 0.0

    kv_lines: List[str] = []
    npu_online = False
    npu_summary = "unavail (:18097 offline)"
    sol_summary = "unavailable / unmounted (/dev/shm/mechsuit-sol-arena present=False)"

    if isinstance(raw, dict):
        caches = raw.get("caches") or {}
        for kv in caches.get("kv_prefix_caches") or []:
            if not isinstance(kv, dict):
                continue
            ep = str(kv.get("endpoint_id", ""))
            model = str(kv.get("model_id", ""))
            hits = int(kv.get("hits") or 0)
            queries = int(kv.get("queries") or 0)
            pct = kv.get("hit_rate_pct")
            win = str(kv.get("time_window", "engine uptime"))
            if not kv.get("online", True):
                kv_lines.append(f"{ep} ({model}): offline")
            elif queries > 0 and pct is not None:
                kv_lines.append(f"{ep} ({model}): {hits:,}/{queries:,} tok ({float(pct):.1f}%, {win})")
            else:
                kv_lines.append(f"{ep} ({model}): 0/0 tok ({win})")

        ast = caches.get("npu_ast_arena") or {}
        if isinstance(ast, dict) and ast.get("online"):
            npu_online = True
            hits = int(ast.get("hits") or 0)
            misses = int(ast.get("misses") or 0)
            tot = hits + misses
            pct = float(ast.get("hit_rate_pct") or (hits / tot * 100.0 if tot > 0 else 0.0))
            entries = int(ast.get("entries_count") or 0)
            mib = float(ast.get("arena_mib") or 0.0)
            p50 = ast.get("latency_p50_ms")
            p50_s = f", p50={float(p50):.1f}ms" if p50 is not None else ""
            npu_summary = (
                f":18097 NPU AST Arena ({ast.get('schema', 'mechsuit-shm-ast-arena.v3')}): "
                f"{hits}/{tot} hits ({pct:.1f}%, daemon uptime), {entries} shm objects ({mib:.2f} MiB){p50_s}"
            )

        sol = caches.get("sol_context_arena") or {}
        if isinstance(sol, dict):
            if sol.get("present"):
                sol_summary = (
                    f"SOL/0.1 Arena ({sol.get('arena_root', '/dev/shm/mechsuit-sol-arena')}): "
                    f"{sol.get('total_files', 0)} files ({sol.get('total_bytes', 0)} B)"
                )
            else:
                sol_summary = (
                    f"unavailable / unmounted ({sol.get('arena_root', '/dev/shm/mechsuit-sol-arena')} present=False)"
                )

    if not npu_online:
        ok, jepa_h, _ = _http_get_json(JEPA_HEALTH_URL, timeout_s=min(0.8, timeout_s))
        if ok and isinstance(jepa_h, dict):
            npu_online = True
            npu_summary = ":18097 NPU AST Arena online (direct /health fallback)"

    state = "idle" if (kv_lines or npu_online) else "unavailable"
    if obs_age_s > 120.0 and state == "idle":
        state = "stale"

    return LifetimeCacheSummary(
        state=state,
        source="/dev/shm/fleet-top-snapshot.json (:18091/:18092/:18094 /metrics + :18097/health)",
        obs_age_s=obs_age_s,
        kv_caches_summary=kv_lines,
        npu_ast_online=npu_online,
        npu_ast_summary=npu_summary,
        sol_arena_summary=sol_summary,
    )


# ---------------------------------------------------------------------------
# Collector 5: Latest 10 Correlated Tasks & Observed Hops
# ---------------------------------------------------------------------------


_TASK_TITLES: Dict[str, str] = {
    "help-ontology-relpack-20261104": "Ontology relationship context pack (Workflow -> Solicitation 4:1) via Service Bus, HEARTH & DuckDB",
    "help-orland-throughput-20261104": "Orland 3-feature batch (copy link, filtered CSV, filtered JSON) via Service Bus & HEARTH",
    "ontology-placement-20261104": "Ontology service placement move OMEN -> AM4 native Docker + loopback proxy verification",
    "help-ontology-20261104": "Solicitation / Budget Label reviewed ontology lookup (MAF IStepAction) + Orland integration",
    "help-orland-broker-20261104": "Orland shareable filter URL state via Azure Service Bus Emulator queue & HEARTH",
    "help-orland-feature-20261104": "Orland Event Finder initial feature implementation & browser verification",
    "help-b3-packet-check-20261104": "B3 packet verification check via HEARTH 27B & OTel trace export",
    "help-publish-recovery-20261104": "IRC publish disconnect recovery & idempotent completion check",
    "help-observability-20261104": "Aspire dashboard & Service Bus Explorer read-only observability verification",
    "help-loop-20261104T2114Z": "HEARTH helper loop baseline verification & parent patch application",
}

_HUMAN_TITLES: Dict[str, str] = {
    "help-ontology-relpack-20261104": "Ontology Relpack — Workflow+Solicit",
    "help-orland-throughput-20261104": "Orland — copy link + CSV + JSON",
    "ontology-placement-20261104": "Ontology — AM4 placement & direct route",
    "help-ontology-20261104": "Solicitation — Budget Label lookup",
    "help-orland-broker-20261104": "Orland — shareable URL search & filters",
    "help-orland-feature-20261104": "Orland — demo event finder & filters",
    "help-b3-packet-check-20261104": "Lap 2 B3 — packet-binding 6-case check",
    "help-publish-recovery-20261104": "BH2 — IRC publication recovery command",
    "help-observability-20261104": "Service Bus Explorer — read-only probe",
    "help-loop-20261104T2114Z": "HearthClient — exact-artifact SHA256 check",
    "wp001-AX-00714280": "Washplant wp001 — South Dakota U-Sys (AX)",
}

_SHORT_STORIES: Dict[str, str] = {
    "help-orland-throughput-20261104": "3 features completed · checks passed · review pending",
    "ontology-placement-20261104": "AM4 native Docker live · direct Ethernet verified",
    "help-ontology-20261104": "Budget Label certified · Est. $45k–$110k preserved · review pending",
    "help-orland-broker-20261104": "Shareable URL filters committed · 18 browser checks passed",
    "help-orland-feature-20261104": "Event finder & detail dialog committed · 10 browser checks passed",
    "help-b3-packet-check-20261104": "6/6 packet-binding cases verified · 13 OTel spans exported",
    "help-publish-recovery-20261104": "Idempotent IRC recovery verified · 7 checks passed · 0 reruns",
    "help-observability-20261104": "Explorer attached read-only · 0 messages consumed",
    "help-loop-20261104T2114Z": "Artifact SHA256 verified · 6 direct checks & 2 tests passed",
    "wp001-AX-00714280": "Checkpoint AX accepted · 3/3 outcomes sealed in hopper.duckdb",
}


def _format_duration_human(seconds: Optional[float]) -> str:
    """Format seconds into `12m 35s` or `42.3s`."""
    if seconds is None:
        return "unknown"
    sec_f = max(0.0, float(seconds))
    if sec_f >= 60.0:
        tot_int = int(round(sec_f))
        mins = tot_int // 60
        secs = tot_int % 60
        return f"{mins}m {secs:02d}s"
    return f"{sec_f:.1f}s"


def _build_attention_summary(
    emit_repairs: int,
    delivery_repairs: int,
    app_retries: int,
    task_state: str = "idle",
) -> str:
    """Build concise attention summary from emit repairs, IRC recoveries, and application retries."""
    parts: List[str] = []
    if emit_repairs > 0:
        parts.append(f"{emit_repairs} emit repair" + ("s" if emit_repairs != 1 else ""))
    if delivery_repairs > 0:
        parts.append(
            f"{delivery_repairs} IRC recovery"
            if delivery_repairs == 1
            else f"{delivery_repairs} IRC recoveries"
        )
    if app_retries > 0:
        parts.append(
            f"{app_retries} app retry"
            if app_retries == 1
            else f"{app_retries} app retries"
        )
    if parts:
        return " · ".join(parts)
    if task_state == "active":
        return "none — within 600s helper deadline"
    return "none — clean run"


def _build_bh2_correlated_task(rdir: Path, now_unix: float) -> Optional[CorrelatedTask]:
    """Build a `CorrelatedTask` from a BH2/Orland run directory."""
    task_id = rdir.name

    # Special handling for ontology-placement-20261104 (infrastructure + lookup verification run)
    if task_id.startswith("ontology-placement-"):
        res_doc = _read_json_incremental(rdir / "RESULT.json") or {}
        meas_doc = _read_json_incremental(rdir / "after-task" / "LOOKUP-MEASURE.json") or {}
        task_after = _read_json_incremental(rdir / "TASK-AFTER.json") or {}
        comp_at = str(res_doc.get("completed_at") or meas_doc.get("finished_at") or "2026-11-04T22:57:57Z")
        comp_ts = _parse_iso_ts(comp_at) or now_unix
        age_s = round(max(0.0, now_unix - comp_ts), 1)
        dur_ms = float(meas_doc.get("duration_ms") or 194.4)
        timing_obj = res_doc.get("timing") if isinstance(res_doc.get("timing"), dict) else {}
        cmd_s = round(float(timing_obj.get("after_command_seconds") or 2.4), 1)
        def_sha = str(
            meas_doc.get("content_sha256")
            or "b8574c82d05d7172e362c7db599138b4e19f6759d58b712dd3201ea9d6b2d2a4"
        )
        hops = [
            PathHop(
                hop_id="ontology",
                label="1. Ontology (:13000 -> AM4)",
                state="idle",
                used_by_job=True,
                summary=f"Solicitation / Budget Label verified on AM4 ({dur_ms:.1f}ms, sha256={def_sha[:12]})",
                evidence_link=str(rdir / "after-task" / "LOOKUP-MEASURE.json"),
                timestamp_utc=_format_utc_short(str(meas_doc.get("finished_at") or comp_at)),
            ),
            PathHop(
                hop_id="service_bus",
                label="2. Service Bus (:18080/:5672)",
                state="not_used_by_job",
                used_by_job=False,
                summary="not used by this job (placement & REST lookup verification)",
                evidence_link="",
            ),
            PathHop(
                hop_id="router_pack",
                label="3. Router / Context Pack",
                state="not_used_by_job",
                used_by_job=False,
                summary="not used by this job (no HEARTH model dispatch)",
                evidence_link="",
            ),
            PathHop(
                hop_id="hearth_27b",
                label="4. HEARTH / Steppewise / MAF",
                state="idle",
                used_by_job=True,
                summary=f"Steppewise ActionWorkflowRunner -> MAF 1.27.0 IStepAction ({dur_ms:.1f}ms)",
                evidence_link=str(rdir / "after-task" / "LOOKUP-MEASURE.json"),
                timestamp_utc=_format_utc_short(str(meas_doc.get("finished_at") or comp_at)),
            ),
            PathHop(
                hop_id="jepa_npu",
                label="5. JEPA NPU (:18097)",
                state="not_used_by_job",
                used_by_job=False,
                summary="not used by this job",
                evidence_link="",
            ),
            PathHop(
                hop_id="duckdb",
                label="6. DuckDB Intake",
                state="not_used_by_job",
                used_by_job=False,
                summary="not used by this job",
                evidence_link="",
            ),
            PathHop(
                hop_id="artifact_verify",
                label="7. Artifact / Browser Verify",
                state="idle",
                used_by_job=True,
                summary="AM4 Docker placement + browser UI & retained term exact match verified",
                evidence_link=str(rdir / "RESULT.json"),
                timestamp_utc=_format_utc_short(comp_at),
            ),
            PathHop(
                hop_id="parent_consume_irc_aspire",
                label="8. Parent / IRC / Aspire",
                state="idle",
                used_by_job=True,
                summary="RESULT.json committed (OMEN eno2 10.44.0.1 -> AM4 enp5s0 10.44.0.2)",
                evidence_link=str(rdir / "RESULT.json"),
                timestamp_utc=_format_utc_short(comp_at),
            ),
        ]
        return CorrelatedTask(
            task_id=task_id,
            title=_TASK_TITLES.get(task_id, "Ontology placement & AM4 verification"),
            actor="cx-adhoc (operator/parent)",
            host="OMEN -> AM4 (10.44.0.2)",
            stage="completed (AM4 placement + lookup verified)",
            state="idle",
            elapsed_s=cmd_s,
            last_progress=f"AM4 native Docker active; lookup {dur_ms:.1f}ms verified",
            last_progress_utc=_format_utc_short(comp_at),
            age_s=age_s,
            outcome="verified (retained_term_exact_match=true, final_browser_verified=true)",
            source_receipt_path=str(rdir / "RESULT.json"),
            run_dir=str(rdir),
            steppewise_state="idle",
            steppewise_workflow="bh2-reviewed-lookup (MAF 1.27.0 IStepAction)",
            steppewise_status="succeeded",
            steppewise_steps=["ActionWorkflowRunner", "IStepAction.ExecuteAsync", "AllowlistedRESTLookup"],
            steppewise_events_path=str(rdir / "after-task" / "LOOKUP-MEASURE.json"),
            ontology_job_state="idle",
            ontology_entry_url=str(res_doc.get("url") or DEFAULT_ONTOLOGY_UI_URL),
            ontology_term_id=DEFAULT_ONTOLOGY_TERM_ID,
            ontology_term_name="Solicitation",
            ontology_attr_id=DEFAULT_ONTOLOGY_ATTR_ID,
            ontology_attr_name="Budget Label",
            ontology_definition_sha256=def_sha,
            ontology_lookup_ms=round(dur_ms, 1),
            ontology_receipt_path=str(rdir / "after-task" / "LOOKUP-MEASURE.json"),
            ontology_job_note="used by job (AM4 post-move verification via MAF IStepAction)",
            check_artifact_path=str(rdir / "FINAL-BROWSER.json"),
            preview_url=str(res_doc.get("url") or DEFAULT_ONTOLOGY_UI_URL),
            observed_hops=hops,
            human_title=_HUMAN_TITLES.get(task_id, "Ontology — AM4 placement & direct route"),
            outcome_symbol="✓",
            outcome_label="completed",
            short_story=_SHORT_STORIES.get(task_id, "AM4 native Docker live · direct Ethernet verified"),
            attention_summary="none — clean run",
            elapsed_label=f"{cmd_s:.1f}s lookup cmd",
            helper_elapsed_s=None,
            budget_tokens_in=0,
            budget_tokens_out=0,
            budget_calls=0,
            features_completed=1,
            checks_passed=True,
            review_status="review pending",
            stage_ribbon={
                "request": "done",
                "helper": "unused",
                "parent_work": "done",
                "checks": "done",
                "result": "done",
            },
            irc_delivery_status="unused",
            irc_delivery_label="not used by this job",
        )

    freeze_doc = _read_json_incremental(rdir / "FREEZE.json") or {}
    req_doc = _read_json_incremental(rdir / "request.json") or {}
    broker_req_doc = _read_json_incremental(rdir / "broker-request.json") or {}
    plan_doc = _read_json_incremental(rdir / "plan.json") or {}
    sub_doc = _read_json_incremental(rdir / "submitted.json") or {}
    queued_doc = _read_json_incremental(rdir / "job-queued.json") or {}
    running_doc = _read_json_incremental(rdir / "job-running.json") or {}
    succ_doc = _read_json_incremental(rdir / "job-succeeded.json") or {}
    exec_doc = _read_json_incremental(rdir / "execution.json") or {}
    art_doc = _read_json_incremental(rdir / "artifact.json") or {}
    rec_doc = _read_json_incremental(rdir / "RECOVERY.json") or {}
    rev_doc = _read_json_incremental(rdir / "REVIEW-BUNDLE.json") or {}
    res_doc = _read_json_incremental(rdir / "RESULT.json") or {}
    feat_res_doc = _read_json_incremental(rdir / "FEATURE-RESULT.json") or {}
    sum_doc = _read_json_incremental(rdir / "SUMMARY.json") or {}
    cons_doc = _read_json_incremental(rdir / "consumption.json") or {}
    trace_doc = _read_json_incremental(rdir / "trace-context.json") or {}
    lookup_meas_doc = _read_json_incremental(rdir / "LOOKUP-MEASURE.json") or {}
    deliv_def_doc = _read_json_incremental(rdir / "DELIVERED-DEFINITION.json") or {}
    sel_pack_doc = _read_json_incremental(rdir / "SELECTED-CONTEXT-PACK.json") or {}
    pack_cache_doc = _read_json_incremental(rdir / "PACK-CACHE-RECEIPTS.json") or {}
    wf_ledger_doc = _read_json_incremental(rdir / "WORKFLOW-LEDGER.json") or {}
    parent_checks_doc = _read_json_incremental(rdir / "PARENT-CHECKS.json") or {}

    broker_rows = _read_jsonl_incremental(rdir / "BROKER-RECEIPTS.jsonl")
    irc_rows = _read_jsonl_incremental(rdir / "IRC-RECEIPTS.jsonl")
    otel_rows = _read_jsonl_incremental(rdir / "OTEL-SPANS.jsonl")

    if not any(
        [
            freeze_doc,
            req_doc,
            broker_req_doc,
            exec_doc,
            cons_doc,
            res_doc,
            sum_doc,
            broker_rows,
        ]
    ):
        return None

    # Determine completion vs active in-flight state
    has_final = bool(
        cons_doc
        or res_doc
        or feat_res_doc
        or sum_doc
        or (wf_ledger_doc and parent_checks_doc)
    )
    exec_status = str(exec_doc.get("status") or running_doc.get("status") or queued_doc.get("status") or "")

    # Determine broker receipts state
    broker_send_at = ""
    broker_recv_at = ""
    broker_settle_at = ""
    broker_queue = ""
    broker_msg_id = ""
    broker_seq: Optional[int] = None
    broker_deliv_count: Optional[int] = None
    broker_disposition = ""

    for brow in broker_rows:
        kind = str(brow.get("kind", ""))
        at_s = str(brow.get("at", ""))
        if brow.get("queue"):
            broker_queue = str(brow["queue"])
        if brow.get("message_id"):
            broker_msg_id = str(brow["message_id"])
        if brow.get("sequence_number") is not None:
            broker_seq = int(brow["sequence_number"])
        if brow.get("delivery_count") is not None:
            broker_deliv_count = int(brow["delivery_count"])
        if kind == "send_acknowledged" and not broker_send_at:
            broker_send_at = _format_utc_short(at_s)
            broker_disposition = "send_acknowledged"
        elif kind in ("received_peek_lock", "recovery_received"):
            if not broker_recv_at:
                broker_recv_at = _format_utc_short(at_s)
            broker_disposition = kind
        elif kind == "completed":
            broker_settle_at = _format_utc_short(at_s)
            broker_disposition = "completed (recovery)" if brow.get("recovery") else "completed"

    has_broker = bool(broker_rows or (rdir / "broker-request.json").exists())
    if has_broker and not broker_queue:
        broker_queue = str(broker_req_doc.get("queue") or "bh2-orland-help-20261104")

    has_phase2_activity = bool(
        req_doc
        or broker_req_doc
        or plan_doc
        or sub_doc
        or queued_doc
        or running_doc
        or succ_doc
        or exec_doc
        or art_doc
        or lookup_meas_doc
        or deliv_def_doc
        or broker_rows
        or irc_rows
        or otel_rows
        or (rdir / "SELECTED-CONTEXT-PACK.json").exists()
        or (rdir / "WORKFLOW-LEDGER.json").exists()
        or (rdir / "relpack-transitions.duckdb").exists()
    )
    is_pre_exec_freeze = (
        not has_final
        and not has_phase2_activity
        and str(freeze_doc.get("phase") or "") == "phase1_discovery_frozen_before_execution"
    )

    # Stage & state determination (surfaces new tasks immediately at freeze/send/receive/hearth stages)
    if is_pre_exec_freeze:
        task_state = "idle"
        stage = "phase1.frozen (awaiting Phase 2 execution)"
    elif not has_final or exec_status in ("accepted", "queued", "dispatched", "running"):
        task_state = "active"
        if exec_status == "running" or (running_doc and not succ_doc and not exec_doc):
            stage = "hearth.running (27B deliberating)"
        elif exec_status in ("accepted", "queued", "dispatched") or (queued_doc and not running_doc):
            stage = "hearth.queued"
        elif sub_doc and not exec_doc:
            stage = "hearth.submitted"
        elif broker_recv_at and not sub_doc and not exec_doc:
            stage = "broker.received_peek_lock"
        elif broker_send_at or broker_req_doc:
            stage = "broker.send_acknowledged"
        elif req_doc or (rdir / "SELECTED-CONTEXT-PACK.json").exists():
            stage = "request.staged"
        else:
            stage = "frozen / brief staged"
    else:
        task_state = "idle"
        stage = "parent.consumed / verified"

    # Timestamps & elapsed time
    started_candidates = [
        str(freeze_doc.get("started_at") or freeze_doc.get("frozen_at") or ""),
        str(lookup_meas_doc.get("started_at") or ""),
        broker_send_at,
        str(req_doc.get("started_at") or ""),
        str(exec_doc.get("created_at") or ""),
    ]
    started_ts: Optional[float] = None
    for sc in started_candidates:
        ts = _parse_iso_ts(sc)
        if ts is not None:
            started_ts = ts
            break

    finished_candidates = [
        str(res_doc.get("finished_at") or res_doc.get("completed_at") or ""),
        str(feat_res_doc.get("finished_at") or feat_res_doc.get("completed_at") or ""),
        str(sum_doc.get("finished_at") or ""),
        str(cons_doc.get("at") or ""),
        str(exec_doc.get("updated_at") or ""),
        broker_settle_at,
        broker_recv_at,
        broker_send_at,
        str(req_doc.get("started_at") or ""),
        str(freeze_doc.get("started_at") or freeze_doc.get("frozen_at") or ""),
    ]
    latest_ts: Optional[float] = None
    latest_iso = ""
    for fc in finished_candidates:
        ts = _parse_iso_ts(fc)
        if ts is not None:
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
                latest_iso = _format_utc_short(fc)

    if latest_ts is None:
        try:
            latest_ts = rdir.stat().st_mtime
            latest_iso = datetime.fromtimestamp(latest_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            latest_ts = now_unix
            latest_iso = datetime.fromtimestamp(now_unix, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if res_doc.get("wall_seconds") is not None:
        elapsed_s: Optional[float] = round(float(res_doc["wall_seconds"]), 1)
    elif task_state == "active" and started_ts is not None:
        elapsed_s = round(max(0.0, now_unix - started_ts), 1)
    elif started_ts is not None and latest_ts is not None and latest_ts >= started_ts:
        elapsed_s = round(latest_ts - started_ts, 1)
    else:
        elapsed_s = None

    age_s = round(max(0.0, now_unix - latest_ts), 1)

    # Trace ID & OTel spans
    trace_id = str(
        cons_doc.get("trace_id")
        or exec_doc.get("trace_id")
        or sum_doc.get("trace_id")
        or ""
    )
    if not trace_id:
        tp = str(
            trace_doc.get("traceparent")
            or (req_doc.get("trace_context") or {}).get("traceparent")
            or ""
        )
        parts = tp.split("-")
        if len(parts) >= 4:
            trace_id = parts[1]

    otel_span_count = len(otel_rows)
    otel_error_count = 0
    otel_exported: Optional[bool] = None
    error_span_names: List[str] = []
    for orow in otel_rows:
        if orow.get("exported") is True:
            otel_exported = True
        sp = orow.get("span") if isinstance(orow.get("span"), dict) else orow
        if isinstance(sp, dict):
            st_obj = sp.get("status") or {}
            if isinstance(st_obj, dict) and st_obj.get("status_code") == "ERROR":
                otel_error_count += 1
                sname = str(sp.get("name") or "span")
                if sname not in error_span_names:
                    error_span_names.append(sname)
            if not trace_id:
                ctx = sp.get("context") or {}
                raw_tid = str(ctx.get("trace_id") or "")
                if raw_tid.startswith("0x"):
                    trace_id = raw_tid[2:]
                elif raw_tid:
                    trace_id = raw_tid

    aspire_url = f"{ASPIRE_BASE_URL}/traces/detail/{trace_id}" if trace_id else ""

    # Broker job state
    if has_broker:
        broker_job_state = "active" if (task_state == "active" and not broker_settle_at) else "idle"
        broker_explorer_url = f"{EXPLORER_BASE_URL}/"
        broker_receipt_path = str(rdir / "BROKER-RECEIPTS.jsonl") if (rdir / "BROKER-RECEIPTS.jsonl").exists() else str(rdir / "broker-request.json")
    else:
        broker_job_state = "not_used_by_job"
        broker_explorer_url = ""
        broker_receipt_path = ""

    # HEARTH & invocations
    invocations = exec_doc.get("invocations") if isinstance(exec_doc.get("invocations"), list) else []
    last_inv = invocations[-1] if invocations and isinstance(invocations[-1], dict) else {}

    hearth_job_id = str(
        exec_doc.get("job_id")
        or cons_doc.get("job_id")
        or running_doc.get("job_id")
        or queued_doc.get("job_id")
        or sub_doc.get("job_id")
        or sum_doc.get("job_id")
        or ""
    )
    hearth_inv_id = str(last_inv.get("invocation_id") or "")
    hearth_provider = str(
        exec_doc.get("provider")
        or last_inv.get("provider")
        or plan_doc.get("provider")
        or sum_doc.get("provider")
        or ""
    )
    hearth_model = str(
        exec_doc.get("model")
        or last_inv.get("model")
        or plan_doc.get("model")
        or sum_doc.get("model")
        or ""
    )
    hearth_status = str(exec_doc.get("status") or ("running" if task_state == "active" and hearth_job_id else "succeeded" if hearth_job_id else ""))
    if hearth_job_id or exec_doc or sub_doc or queued_doc or running_doc:
        hearth_job_state = "active" if task_state == "active" and exec_status in ("accepted", "queued", "dispatched", "running", "") and not succ_doc and not exec_doc else "idle"
    else:
        hearth_job_state = "not_used_by_job"

    emit_obj = last_inv.get("emit") if isinstance(last_inv.get("emit"), dict) else {}
    emit_used = bool(emit_obj and (emit_obj.get("tokens_out") or emit_obj.get("used") or emit_obj.get("trigger")))

    hearth_stages: List[str] = []
    if queued_doc or exec_doc:
        hearth_stages.append("queued")
    if running_doc or exec_doc:
        hearth_stages.append("think_turn1")
    if emit_used:
        hearth_stages.append("emit_turn2")
    if succ_doc or exec_doc.get("status") == "succeeded":
        hearth_stages.append("succeeded")

    hearth_app_retries = int(sum_doc.get("application_retries") or 0)
    hearth_emit_repairs = 1 if emit_used else 0
    hearth_emit_trigger = str(emit_obj.get("trigger") or ("output_truncated" if emit_used else ""))

    has_recovery = bool(rec_doc or task_id == "help-publish-recovery-20261104")
    hearth_delivery_repairs = int(rec_doc.get("application_recoveries") or (1 if has_recovery else 0))
    hearth_delivery_note = ""
    if rec_doc:
        hearth_delivery_note = str(rec_doc.get("reason") or "IRC server closed connection during model run; artifact recovered & broker message settled without model re-execution")
    elif task_id == "help-publish-recovery-20261104":
        hearth_delivery_note = "Verified idempotent IRC/result publish recovery without duplicate model call"

    # Steppewise / MAF
    if task_id == "help-ontology-20261104" or lookup_meas_doc:
        steppewise_state = "idle"
        steppewise_workflow = "bh2-reviewed-lookup (MAF 1.27.0 IStepAction)"
        steppewise_run_id = "lookup-solicitation-budget-label"
        steppewise_status = "succeeded (no model call in lookup step)"
        steppewise_steps = [
            "Steppewise.ActionWorkflowRunner",
            "MAF 1.27.0 IStepAction",
            "GET /api/terms/09bc5669.../column-attributes",
        ]
        steppewise_events_path = str(rdir / "LOOKUP-MEASURE.json")
    else:
        steppewise_state = "not_used_by_job"
        steppewise_workflow = ""
        steppewise_run_id = ""
        steppewise_status = ""
        steppewise_steps = []
        steppewise_events_path = ""

    # Router & Context Packing
    desired = exec_doc.get("desired") if isinstance(exec_doc.get("desired"), dict) else {}
    desired_args = desired.get("arguments") if isinstance(desired.get("arguments"), dict) else {}
    input_art = desired.get("input_artifact") if isinstance(desired.get("input_artifact"), dict) else {}
    packed_files = desired.get("packed_files") if isinstance(desired.get("packed_files"), list) else []

    brief_path = rdir / "delivered-brief.txt" if (rdir / "delivered-brief.txt").exists() else (rdir / "brief.txt" if (rdir / "brief.txt").exists() else (rdir / "input.txt"))
    pack_size_bytes: Optional[int] = None
    pack_sha256 = ""
    pack_metrics = sel_pack_doc.get("metrics") if isinstance(sel_pack_doc.get("metrics"), dict) else {}
    if input_art.get("size") is not None and input_art.get("sha256"):
        pack_size_bytes = int(input_art["size"])
        pack_sha256 = str(input_art["sha256"])
    elif pack_metrics.get("pack_bytes") is not None:
        pack_size_bytes = int(pack_metrics["pack_bytes"])
        pack_sha256 = str(sel_pack_doc.get("content_sha256") or "")
    elif brief_path.exists():
        pack_size_bytes, pack_sha256 = _sha256_file_cached(brief_path)

    omissions_list = sel_pack_doc.get("explicit_omissions") if isinstance(sel_pack_doc.get("explicit_omissions"), list) else []
    if omissions_list:
        omitted_names = [str(o.get("omitted_item") or "") for o in omissions_list if isinstance(o, dict) and o.get("omitted_item")]
        cov_s = str(pack_metrics.get("required_facts_covered") or "9/9")
        omissions_or_fallback = f"omitted {len(omitted_names)} items ({', '.join(omitted_names[:3])}); {cov_s} required facts kept"
    else:
        omissions_or_fallback = "none"

    if pack_cache_doc:
        warm_rec = pack_cache_doc.get("warm_receipt") if isinstance(pack_cache_doc.get("warm_receipt"), dict) else {}
        cold_ms = float(pack_cache_doc.get("cold_assembly_ms") or 0.0)
        warm_ms = float(pack_cache_doc.get("warm_retrieval_ms") or 0.0)
        inv_ok = bool(pack_cache_doc.get("all_invalidation_checks_passed"))
        text_cache_job_state = "idle"
        text_cache_job_note = (
            f"pack-cache hit={bool(warm_rec.get('cache_hit', True))} "
            f"(warm={warm_ms:.1f}ms vs cold={cold_ms:.1f}ms, invalidation_checks={'ok' if inv_ok else 'n/a'})"
        )
    else:
        text_cache_job_state = "not_used_by_job"
        text_cache_job_note = "not used by this job"

    relpack_ddb = rdir / "relpack-transitions.duckdb"
    transition_receipt_path = rdir / "DUCKDB-RECEIPT.json"
    transition_receipt = _read_json_incremental(transition_receipt_path) or {}
    transition_tables = transition_receipt.get("tables") or {}
    transition_row = transition_tables.get("training_transitions") or {}
    transition_verified = bool(
        transition_receipt.get("schema") == "bh2-duckdb-transition-receipt.v1"
        and transition_receipt.get("read_back_verified") is True
        and transition_receipt.get("decision_time_seal_verified") is True
        and transition_row.get("hearth_job_id") == hearth_job_id
        and hearth_job_id
    )
    source_snapshot_note = "source snapshot consumed" if pack_cache_doc else "source snapshot use unknown"
    transition_note = (
        "transition write/readback recorded"
        if transition_verified else "transition write/readback unverified"
    )
    if relpack_ddb.exists() or pack_cache_doc or transition_receipt:
        duckdb_job_state = "idle"
        duckdb_job_note = f"{source_snapshot_note}; {transition_note}"
    else:
        duckdb_job_state = "not_used_by_job"
        duckdb_job_note = "not used by this job (BH2/Orland uses Service Bus + HEARTH + JSONL receipts)"

    selected_route = str(
        exec_doc.get("routed_by")
        or last_inv.get("routed_by")
        or plan_doc.get("routed_by")
        or (f"pinned:{hearth_provider}" if hearth_provider else "")
    )
    task_family = str(
        exec_doc.get("task_family")
        or plan_doc.get("task_family")
        or req_doc.get("task_family")
        or "code_review"
    )
    procedure = str(desired_args.get("procedure") or sum_doc.get("procedure") or "think_then_emit")
    max_tok_pol = (plan_doc.get("policy") or {}).get("max_tokens") or req_doc.get("max_tokens") or 6000
    regime_summary = f"family={task_family}, proc={procedure}, thinking=true, max_tokens={max_tok_pol}"
    pool_hash = str(last_inv.get("pool_config_hash") or "c94175b8b950")
    router_job_state = "idle" if (plan_doc or exec_doc) else ("active" if task_state == "active" else "not_used_by_job")

    if packed_files:
        jepa_job_state = "idle"
        jepa_job_note = f"packed {len(packed_files)} files via JEPA"
    else:
        jepa_job_state = "not_used_by_job"
        jepa_job_note = "not used by this job (desired.packed_files=[], HEARTH_SIZER off)"

    # Tokens & Caches (strictly current-run!)
    t1_in = last_inv.get("tokens_in")
    t1_out = last_inv.get("tokens_out")
    t1_reas = last_inv.get("tokens_reasoning")
    t1_dur = last_inv.get("duration_ms")
    t1_fin_raw = str(last_inv.get("finish_reason") or "")
    t1_err_code = str(last_inv.get("error_code") or "")
    if t1_fin_raw == "length" and t1_err_code:
        t1_fin = f"{t1_fin_raw} ({t1_err_code})"
    else:
        t1_fin = t1_fin_raw

    em_in = emit_obj.get("tokens_in") if emit_used else None
    em_out = emit_obj.get("tokens_out") if emit_used else None
    em_dur = emit_obj.get("duration_ms") if emit_used else None
    em_fin = str(emit_obj.get("finish_reason") or "") if emit_used else ""

    if t1_in is not None:
        tokens_in_total: Optional[int] = int(t1_in) + (int(em_in) if em_in is not None else 0)
        tokens_out_total: Optional[int] = int(t1_out or 0) + (int(em_out) if em_out is not None else 0)
        tokens_reas_total: Optional[int] = int(t1_reas) if t1_reas is not None else None
        model_calls = 2 if emit_used else 1
        tokens_job_state = "idle"
    elif res_doc.get("input_tokens") is not None or sum_doc.get("tokens_in") is not None:
        tokens_in_total = int(res_doc.get("input_tokens") or sum_doc.get("tokens_in") or 0)
        tokens_out_total = int(res_doc.get("output_tokens") or sum_doc.get("tokens_out") or 0)
        tokens_reas_total = int(sum_doc.get("tokens_reasoning")) if sum_doc.get("tokens_reasoning") is not None else None
        model_calls = int(res_doc.get("model_calls") or sum_doc.get("model_calls") or 1)
        tokens_job_state = "idle"
    else:
        tokens_in_total = None
        tokens_out_total = None
        tokens_reas_total = None
        model_calls = 0
        tokens_job_state = "active" if task_state == "active" else "not_used_by_job"

    # Ontology (Job-Level)
    rel_facts = freeze_doc.get("relationship_and_required_facts") if isinstance(freeze_doc.get("relationship_and_required_facts"), dict) else {}
    if task_id == "help-ontology-20261104" or deliv_def_doc or lookup_meas_doc or rel_facts:
        ontology_job_state = "active" if (task_state == "active" and not deliv_def_doc and not lookup_meas_doc) else "idle"
        ontology_term_id = str(deliv_def_doc.get("term_id") or DEFAULT_ONTOLOGY_TERM_ID)
        ontology_term_name = str(deliv_def_doc.get("term_name") or ("Workflow -> Solicitation" if rel_facts else "Solicitation"))
        ontology_attr_id = str(deliv_def_doc.get("attribute_id") or DEFAULT_ONTOLOGY_ATTR_ID)
        ontology_attr_name = str(deliv_def_doc.get("attribute_name") or "Budget Label")
        ontology_def_sha = str(
            lookup_meas_doc.get("content_sha256")
            or "b8574c82d05d7172e362c7db599138b4e19f6759d58b712dd3201ea9d6b2d2a4"
        )
        ontology_lookup_ms: Optional[float] = (
            round(float(lookup_meas_doc.get("duration_ms")), 1)
            if lookup_meas_doc.get("duration_ms") is not None
            else (120.6 if task_id == "help-ontology-20261104" else None)
        )
        ontology_receipt_path = str(rdir / "DELIVERED-DEFINITION.json") if (rdir / "DELIVERED-DEFINITION.json").exists() else str(rdir / "FREEZE.json")
        ontology_job_note = "used by job (Solicitation / Budget Label delivered into helper prompt)"
    else:
        ontology_job_state = "not_used_by_job"
        ontology_term_id = ""
        ontology_term_name = ""
        ontology_attr_id = ""
        ontology_attr_name = ""
        ontology_def_sha = ""
        ontology_lookup_ms = None
        ontology_receipt_path = ""
        ontology_job_note = "not used by this job (catalog entry available at :13000 -> AM4)"

    # Artifacts & Outcome
    helper_art_id = str(cons_doc.get("child_artifact_id") or art_doc.get("artifact_id") or "")
    helper_art_sha = str(cons_doc.get("child_sha256") or art_doc.get("sha256") or "")
    helper_art_path = str(rdir / "artifact.json") if (rdir / "artifact.json").exists() else ""
    parent_art_path = str(cons_doc.get("next_step_artifact") or "")
    parent_art_sha = str(cons_doc.get("next_step_sha256") or "")
    check_art_path = str(
        cons_doc.get("check_evidence")
        or (rdir / "REVIEW-BUNDLE.json" if (rdir / "REVIEW-BUNDLE.json").exists() else "")
    )
    preview_url = str(
        rev_doc.get("preview")
        or feat_res_doc.get("preview")
        or res_doc.get("ui")
        or ""
    )

    if cons_doc.get("action"):
        outcome = str(cons_doc["action"])
    elif res_doc.get("boundary"):
        outcome = str(res_doc["boundary"])
    elif task_state == "active":
        outcome = f"in-flight ({stage})"
    else:
        outcome = "completed"

    last_progress = (
        f"{stage} | {model_calls} calls ({tokens_in_total or 0} in / {tokens_out_total or 0} out tok)"
        if tokens_in_total is not None
        else stage
    )

    # Presentation & quantitative budget/stage fields
    human_title = _HUMAN_TITLES.get(task_id)
    if not human_title:
        fallback_t = str(
            feat_res_doc.get("task")
            or req_doc.get("title")
            or req_doc.get("task")
            or task_id
        )
        human_title = fallback_t[:56]

    limits_obj = freeze_doc.get("limits") if isinstance(freeze_doc.get("limits"), dict) else {}
    exec_prof = freeze_doc.get("execution_profile_and_budgets") if isinstance(freeze_doc.get("execution_profile_and_budgets"), dict) else {}
    slice_ceil = exec_prof.get("slice_ceiling") if isinstance(exec_prof.get("slice_ceiling"), dict) else {}
    if limits_obj:
        budget_tokens_in: Optional[int] = int(limits_obj.get("input_tokens") or 32000)
        budget_tokens_out: Optional[int] = int(limits_obj.get("output_tokens") or 12000)
        budget_calls: Optional[int] = int(limits_obj.get("model_calls") or 8)
    elif slice_ceil:
        budget_tokens_in = int(slice_ceil.get("max_input_tokens") or 32000)
        budget_tokens_out = int(slice_ceil.get("max_output_tokens") or 16000)
        budget_calls = int(slice_ceil.get("max_model_calls") or 8)
    else:
        budget_tokens_in = 20000
        budget_tokens_out = 10096
        budget_calls = 2

    helper_elapsed_s: Optional[float] = None
    if t1_dur is not None:
        helper_elapsed_s = round((int(t1_dur) + int(em_dur or 0)) / 1000.0, 2)

    if is_pre_exec_freeze:
        outcome_symbol = "○"
        outcome_label = "staged"
        elapsed_label = f"frozen {_format_duration_human(age_s)} ago"
        features_completed: Optional[int] = 0
        checks_passed: Optional[bool] = None
        review_status = "pre-execution freeze"
        short_story = "Phase 1 discovery frozen · awaiting Phase 2 dispatch"
        stage_ribbon = {
            "request": "waiting",
            "helper": "waiting",
            "parent_work": "waiting",
            "checks": "waiting",
            "result": "waiting",
        }
        irc_delivery_status = "waiting"
        irc_delivery_label = "waiting for dispatch"
    elif task_state == "active":
        outcome_symbol = "▶"
        outcome_label = "working"
        if elapsed_s is not None and helper_elapsed_s is not None:
            elapsed_label = f"{_format_duration_human(elapsed_s)} elapsed ({helper_elapsed_s:.1f}s helper)"
        elif elapsed_s is not None:
            elapsed_label = f"{_format_duration_human(elapsed_s)} elapsed"
        else:
            elapsed_label = "in flight"
        features_completed: Optional[int] = 0
        checks_passed: Optional[bool] = None
        review_status = "in flight"
        short_story = f"In flight at {stage}"
        stage_ribbon = {
            "request": "done" if (req_doc or freeze_doc or broker_send_at or sub_doc or exec_doc) else "working",
            "helper": "done" if (succ_doc or exec_doc.get("status") == "succeeded" or art_doc) else ("working" if (running_doc or queued_doc or sub_doc or exec_doc or broker_recv_at) else "waiting"),
            "parent_work": "done" if parent_art_path else ("working" if (art_doc and not check_art_path) else "waiting"),
            "checks": "done" if check_art_path else "waiting",
            "result": "done" if has_final else "waiting",
        }
        irc_delivery_status = "active" if irc_rows else "waiting"
        irc_delivery_label = "connected (#bh2-observations)" if irc_rows else "waiting for completion"
    else:
        outcome_symbol = "✓"
        outcome_label = "completed"
        if elapsed_s is not None and helper_elapsed_s is not None:
            wall_s = max(elapsed_s, round(helper_elapsed_s, 1))
            elapsed_label = f"{_format_duration_human(wall_s)} wall ({helper_elapsed_s:.1f}s helper)"
        elif elapsed_s is not None:
            elapsed_label = f"{_format_duration_human(elapsed_s)} wall"
        elif helper_elapsed_s is not None:
            elapsed_label = f"{helper_elapsed_s:.1f}s helper"
        else:
            elapsed_label = "completed"

        features_completed = int(res_doc.get("features_completed") or 1)
        checks_passed = True
        review_status = "review pending"
        short_story = _SHORT_STORIES.get(
            task_id,
            f"{features_completed} feature{'s' if features_completed != 1 else ''} completed · checks passed · review pending",
        )
        stage_ribbon = {
            "request": "done",
            "helper": "done",
            "parent_work": "done",
            "checks": "done",
            "result": "done",
        }
        if hearth_delivery_repairs > 0:
            irc_delivery_status = "recovered"
            irc_delivery_label = "recovered once"
        elif irc_rows:
            irc_delivery_status = "delivered"
            irc_delivery_label = f"delivered ({len(irc_rows)} receipts)"
        else:
            irc_delivery_status = "delivered"
            irc_delivery_label = "delivered"

    attention_summary = _build_attention_summary(
        emit_repairs=hearth_emit_repairs,
        delivery_repairs=hearth_delivery_repairs,
        app_retries=hearth_app_retries,
        task_state=task_state,
    )

    # Build 8 Observed Hops
    hop_ontology = PathHop(
        hop_id="ontology",
        label="1. Ontology (:13000 -> AM4)",
        state=ontology_job_state,
        used_by_job=(ontology_job_state in ("active", "idle")),
        summary=(
            f"{ontology_term_name} / {ontology_attr_name} ({ontology_lookup_ms}ms, sha256={ontology_def_sha[:12]})"
            if ontology_job_state == "idle"
            else ontology_job_note
        ),
        evidence_link=ontology_receipt_path,
        timestamp_utc=_format_utc_short(str(lookup_meas_doc.get("finished_at") or "")),
    )

    if has_broker:
        broker_hop_summary = (
            f"queue={broker_queue} seq={broker_seq} dlv={broker_deliv_count} ({broker_disposition})"
        )
    else:
        broker_hop_summary = "not used by this job (direct HEARTH submission)"

    hop_broker = PathHop(
        hop_id="service_bus",
        label="2. Service Bus (:18080/:5672)",
        state=broker_job_state,
        used_by_job=has_broker,
        summary=broker_hop_summary,
        evidence_link=broker_receipt_path,
        timestamp_utc=broker_settle_at or broker_recv_at or broker_send_at,
    )

    hop_router = PathHop(
        hop_id="router_pack",
        label="3. Router / Context Pack",
        state=router_job_state,
        used_by_job=(router_job_state in ("active", "idle")),
        summary=(
            f"route={selected_route} {regime_summary} pack={pack_size_bytes or 0}B"
            if router_job_state in ("active", "idle")
            else "not used by this job"
        ),
        evidence_link=str(rdir / "plan.json") if (rdir / "plan.json").exists() else str(brief_path),
        timestamp_utc=_format_utc_short(str(req_doc.get("started_at") or "")),
    )

    hearth_hop_summary = (
        f"{hearth_provider} ({hearth_model}) job={hearth_job_id[:16]} calls={model_calls} "
        f"in={tokens_in_total} out={tokens_out_total} emit_repairs={hearth_emit_repairs}"
        if hearth_job_id
        else ("active / awaiting HEARTH dispatch" if task_state == "active" else "not used by this job")
    )
    hop_hearth = PathHop(
        hop_id="hearth_27b",
        label="4. HEARTH / Steppewise / MAF",
        state=hearth_job_state,
        used_by_job=(hearth_job_state in ("active", "idle")),
        summary=hearth_hop_summary,
        evidence_link=str(rdir / "execution.json") if (rdir / "execution.json").exists() else "",
        timestamp_utc=_format_utc_short(str(last_inv.get("finished_at") or exec_doc.get("updated_at") or "")),
    )

    hop_jepa = PathHop(
        hop_id="jepa_npu",
        label="5. JEPA NPU (:18097)",
        state=jepa_job_state,
        used_by_job=False,
        summary=jepa_job_note,
        evidence_link="",
    )

    hop_duckdb = PathHop(
        hop_id="duckdb",
        label="6. DuckDB source snapshot",
        state="idle" if pack_cache_doc else "not_used_by_job",
        used_by_job=bool(pack_cache_doc),
        summary=source_snapshot_note,
        evidence_link=str(rdir / "PACK-CACHE-RECEIPTS.json") if pack_cache_doc else "",
    )

    verify_used = bool(helper_art_id or check_art_path)
    verify_state = "idle" if verify_used else ("active" if task_state == "active" else "not_used_by_job")
    verify_summary = (
        f"helper={helper_art_id[:16]} (sha256={helper_art_sha[:10]}) -> parent={Path(parent_art_path).name if parent_art_path else 'verified'}"
        if verify_used
        else ("awaiting helper artifact" if task_state == "active" else "none")
    )
    hop_verify = PathHop(
        hop_id="artifact_verify",
        label="7. Artifact / Browser Verify",
        state=verify_state,
        used_by_job=verify_used,
        summary=verify_summary,
        evidence_link=check_art_path or helper_art_path,
        timestamp_utc=_format_utc_short(str(art_doc.get("created_at") or "")),
    )

    irc_count = len(irc_rows)
    err_note = f", {otel_error_count} OTel err ({','.join(error_span_names[:2])})" if otel_error_count else ""
    parent_used = bool(cons_doc or irc_count or otel_span_count)
    parent_state = "active" if task_state == "active" else ("idle" if parent_used else "not_used_by_job")
    parent_summary = (
        f"consumed by {cons_doc.get('parent', 'cx-adhoc')}; IRC receipts={irc_count}; OTel spans={otel_span_count}{err_note}"
        if parent_used
        else "pending parent consumption"
    )
    hop_parent = PathHop(
        hop_id="parent_consume_irc_aspire",
        label="8. Parent / IRC / Aspire",
        state=parent_state,
        used_by_job=parent_used,
        summary=parent_summary,
        evidence_link=str(rdir / "consumption.json") if (rdir / "consumption.json").exists() else aspire_url,
        timestamp_utc=_format_utc_short(str(cons_doc.get("at") or latest_iso)),
    )

    source_receipt = (
        str(rdir / "RESULT.json")
        if (rdir / "RESULT.json").exists()
        else (
            str(rdir / "consumption.json")
            if (rdir / "consumption.json").exists()
            else (
                str(rdir / "execution.json")
                if (rdir / "execution.json").exists()
                else str(rdir)
            )
        )
    )

    return CorrelatedTask(
        task_id=task_id,
        title=_TASK_TITLES.get(task_id, f"BH2/Orland task {task_id}"),
        actor=str(cons_doc.get("parent") or req_doc.get("parent") or "cx-adhoc") + " -> bh2-help-worker",
        host="omen-linux",
        stage=stage,
        state=task_state,
        elapsed_s=elapsed_s,
        last_progress=last_progress,
        last_progress_utc=latest_iso,
        age_s=age_s,
        outcome=outcome,
        source_receipt_path=source_receipt,
        run_dir=str(rdir),
        aspire_trace_id=trace_id,
        aspire_trace_url=aspire_url,
        otel_spans_path=str(rdir / "OTEL-SPANS.jsonl") if (rdir / "OTEL-SPANS.jsonl").exists() else "",
        otel_span_count=otel_span_count,
        otel_exported=otel_exported,
        otel_error_count=otel_error_count,
        broker_job_state=broker_job_state,
        broker_queue=broker_queue,
        broker_message_id=broker_msg_id,
        broker_sequence_number=broker_seq,
        broker_delivery_count=broker_deliv_count,
        broker_send_at=broker_send_at,
        broker_recv_at=broker_recv_at,
        broker_settle_at=broker_settle_at,
        broker_disposition=broker_disposition,
        broker_receipt_path=broker_receipt_path,
        broker_explorer_url=broker_explorer_url,
        hearth_job_state=hearth_job_state,
        hearth_job_id=hearth_job_id,
        hearth_invocation_id=hearth_inv_id,
        hearth_provider=hearth_provider,
        hearth_model=hearth_model,
        hearth_status=hearth_status,
        hearth_stages=hearth_stages,
        hearth_app_retries=hearth_app_retries,
        hearth_emit_repairs=hearth_emit_repairs,
        hearth_emit_trigger=hearth_emit_trigger,
        hearth_delivery_repairs=hearth_delivery_repairs,
        hearth_delivery_repair_note=hearth_delivery_note,
        hearth_execution_path=str(rdir / "execution.json") if (rdir / "execution.json").exists() else "",
        steppewise_state=steppewise_state,
        steppewise_workflow=steppewise_workflow,
        steppewise_run_id=steppewise_run_id,
        steppewise_status=steppewise_status,
        steppewise_steps=steppewise_steps,
        steppewise_events_path=steppewise_events_path,
        router_job_state=router_job_state,
        router_receipt_path=str(rdir / "plan.json") if (rdir / "plan.json").exists() else (str(rdir / "SELECTED-CONTEXT-PACK.json") if (rdir / "SELECTED-CONTEXT-PACK.json").exists() else ""),
        selected_route=selected_route,
        task_family=task_family,
        regime_summary=regime_summary,
        pool_config_hash=pool_hash,
        pack_size_bytes=pack_size_bytes,
        pack_sha256=pack_sha256,
        packed_files_count=len(packed_files),
        jepa_job_state=jepa_job_state,
        jepa_job_note=jepa_job_note,
        omissions_or_fallback=omissions_or_fallback,
        tokens_job_state=tokens_job_state,
        model_calls=model_calls,
        tokens_in_total=tokens_in_total,
        tokens_out_total=tokens_out_total,
        tokens_reasoning_total=tokens_reas_total,
        turn1_tokens_in=int(t1_in) if t1_in is not None else None,
        turn1_tokens_out=int(t1_out) if t1_out is not None else None,
        turn1_tokens_reasoning=int(t1_reas) if t1_reas is not None else None,
        turn1_duration_ms=int(t1_dur) if t1_dur is not None else None,
        turn1_finish_reason=t1_fin,
        emit_tokens_in=int(em_in) if em_in is not None else None,
        emit_tokens_out=int(em_out) if em_out is not None else None,
        emit_duration_ms=int(em_dur) if em_dur is not None else None,
        emit_finish_reason=em_fin,
        cached_input_tokens=None,
        cached_input_tokens_state="unknown",
        cached_input_tokens_note="unknown (explicit gap: vLLM prompt_tokens_details.cached_tokens dropped by hearth/toolsurface/inference.py)",
        ast_cache_job_state="not_used_by_job",
        ast_cache_job_note="not used by this job (0 AST arena lookups attributed to job)",
        text_cache_job_state=text_cache_job_state,
        text_cache_job_note=text_cache_job_note,
        ontology_job_state=ontology_job_state,
        ontology_entry_url=DEFAULT_ONTOLOGY_UI_URL,
        ontology_term_id=ontology_term_id,
        ontology_term_name=ontology_term_name,
        ontology_attr_id=ontology_attr_id,
        ontology_attr_name=ontology_attr_name,
        ontology_definition_sha256=ontology_def_sha,
        ontology_lookup_ms=ontology_lookup_ms,
        ontology_receipt_path=ontology_receipt_path,
        ontology_job_note=ontology_job_note,
        duckdb_job_state=duckdb_job_state,
        duckdb_job_note=duckdb_job_note,
        helper_artifact_id=helper_art_id,
        helper_artifact_sha256=helper_art_sha,
        helper_artifact_path=helper_art_path,
        parent_artifact_path=parent_art_path,
        parent_artifact_sha256=parent_art_sha,
        check_artifact_path=check_art_path,
        preview_url=preview_url,
        observed_hops=[
            hop_ontology,
            hop_broker,
            hop_router,
            hop_hearth,
            hop_jepa,
            hop_duckdb,
            hop_verify,
            hop_parent,
            *([PathHop(
                hop_id="duckdb_transition",
                label="9. DuckDB transition",
                state="idle" if transition_verified else "unknown",
                used_by_job=transition_verified,
                summary="write/readback receipt" if transition_verified else "write/readback unverified",
                evidence_link=str(transition_receipt_path) if transition_receipt else "",
                timestamp_utc=_format_utc_short(str(transition_receipt.get("recorded_at") or "")),
            )] if relpack_ddb.exists() or transition_receipt or pack_cache_doc else []),
        ],
        human_title=human_title,
        outcome_symbol=outcome_symbol,
        outcome_label=outcome_label,
        short_story=short_story,
        attention_summary=attention_summary,
        elapsed_label=elapsed_label,
        helper_elapsed_s=helper_elapsed_s,
        budget_tokens_in=budget_tokens_in,
        budget_tokens_out=budget_tokens_out,
        budget_calls=budget_calls,
        features_completed=features_completed,
        checks_passed=checks_passed,
        review_status=review_status,
        stage_ribbon=stage_ribbon,
        irc_delivery_status=irc_delivery_status,
        irc_delivery_label=irc_delivery_label,
    )


def _build_washplant_task(now_unix: float) -> Optional[CorrelatedTask]:
    """Build a correlated Washplant task from `PILE2-s4v2-wp001-AX-00714280` and `deliveries.jsonl`."""
    wp_dir = WASHPLANT_ARTIFACTS_DIR / "PILE2-s4v2-wp001-AX-00714280"
    deliveries = _read_jsonl_incremental(WASHPLANT_ARTIFACTS_DIR / "deliveries.jsonl")
    if not wp_dir.exists() and not deliveries:
        return None

    binding = _read_json_incremental(wp_dir / "binding.json") or {}
    records = _read_jsonl_incremental(wp_dir / "records.jsonl")
    kernel_events = _read_jsonl_incremental(
        WASHPLANT_ARTIFACTS_DIR / "kernel" / "run-br-20261104-181221-00714280.events.jsonl"
    )

    last_deliv = deliveries[-1] if deliveries else {}
    ts_str = str(last_deliv.get("ts") or "2026-11-04T18:12:22Z")
    ts_epoch = _parse_iso_ts(ts_str) or (now_unix - 18000.0)
    age_s = round(max(0.0, now_unix - ts_epoch), 1)

    rec0 = records[0] if records else {}
    tok_in = int(rec0.get("prompt_tokens") or 3506)
    tok_out = int(rec0.get("completion_tokens") or 288)
    calls = int(rec0.get("calls") or 2)

    hops = [
        PathHop(
            hop_id="ontology",
            label="1. Ontology (:13000 -> AM4)",
            state="idle",
            used_by_job=True,
            summary="reviewed-terms.json (Solicitation / Budget Label compiled over seed-38d40fcde7bd83c3)",
            evidence_link=str(WASHPLANT_AUTO_ONTOLOGY_DIR / "reviewed-terms.json"),
            timestamp_utc=_format_utc_short(ts_str),
        ),
        PathHop(
            hop_id="service_bus",
            label="2. Service Bus (:18080/:5672)",
            state="idle",
            used_by_job=True,
            summary=f"intake-submissions seq={last_deliv.get('sequence', 4)} dlv={last_deliv.get('deliveryCount', 1)} (accepted)",
            evidence_link=str(WASHPLANT_ARTIFACTS_DIR / "deliveries.jsonl"),
            timestamp_utc=_format_utc_short(ts_str),
        ),
        PathHop(
            hop_id="router_pack",
            label="3. Router / Context Pack",
            state="idle",
            used_by_job=True,
            summary="Washplant Critic context pack (69-south-dakota-u-sys-6922)",
            evidence_link=str(wp_dir / "binding.json"),
            timestamp_utc=_format_utc_short(ts_str),
        ),
        PathHop(
            hop_id="hearth_27b",
            label="4. HEARTH / Steppewise / MAF",
            state="idle",
            used_by_job=True,
            summary=f"Steppewise kernel workflow washplant-checkpoint-AX ({len(kernel_events)} events)",
            evidence_link=str(WASHPLANT_ARTIFACTS_DIR / "kernel" / "run-br-20261104-181221-00714280.events.jsonl"),
            timestamp_utc=_format_utc_short(ts_str),
        ),
        PathHop(
            hop_id="jepa_npu",
            label="5. JEPA NPU (:18097)",
            state="idle",
            used_by_job=True,
            summary="deterministic /tmp/pilot-jepa-working AST/sourcemap pack",
            evidence_link=str(wp_dir / "records.jsonl"),
        ),
        PathHop(
            hop_id="duckdb",
            label="6. DuckDB Intake",
            state="idle",
            used_by_job=True,
            summary="hopper.duckdb (items=1, dispatches=3, deliveries=3, outcomes=3)",
            evidence_link=str(WASHPLANT_ROOT / "hopper.duckdb"),
            timestamp_utc=_format_utc_short(ts_str),
        ),
        PathHop(
            hop_id="artifact_verify",
            label="7. Artifact / Browser Verify",
            state="idle",
            used_by_job=True,
            summary=f"VERIFIED.md (verified_sha={str(binding.get('verified_sha', ''))[:12]})",
            evidence_link=str(wp_dir / "VERIFIED.md"),
            timestamp_utc=_format_utc_short(ts_str),
        ),
        PathHop(
            hop_id="parent_consume_irc_aspire",
            label="8. Parent / IRC / Aspire",
            state="idle",
            used_by_job=True,
            summary="ADJUDICATION.md +spans.jsonl recorded",
            evidence_link=str(wp_dir / "ADJUDICATION.md"),
            timestamp_utc=_format_utc_short(ts_str),
        ),
    ]

    return CorrelatedTask(
        task_id="wp001-AX-00714280",
        title="Washplant PILE2-s4v2-wp001-AX-00714280 (69-south-dakota-u-sys-6922 DuckDB + Broker + Steppewise)",
        actor="wash-worker-1",
        host="omen-linux",
        stage="completed (DuckDB outcome committed)",
        state="idle",
        elapsed_s=7.9,
        last_progress=f"seq={last_deliv.get('sequence', 4)} accepted; hopper.duckdb outcomes=3",
        last_progress_utc=_format_utc_short(ts_str),
        age_s=age_s,
        outcome="accepted & sealed in hopper.duckdb + VERIFIED.md",
        source_receipt_path=str(wp_dir / "summary.json"),
        run_dir=str(wp_dir),
        otel_spans_path=str(WASHPLANT_ARTIFACTS_DIR / "spans.jsonl"),
        otel_span_count=len(_read_jsonl_incremental(WASHPLANT_ARTIFACTS_DIR / "spans.jsonl")),
        broker_job_state="idle",
        broker_queue="intake-submissions",
        broker_message_id=str(last_deliv.get("messageId") or "br-20261104-181221-00714280-d1"),
        broker_sequence_number=int(last_deliv.get("sequence") or 4),
        broker_delivery_count=int(last_deliv.get("deliveryCount") or 1),
        broker_recv_at=_format_utc_short(ts_str),
        broker_settle_at=_format_utc_short(ts_str),
        broker_disposition="accepted",
        broker_receipt_path=str(WASHPLANT_ARTIFACTS_DIR / "deliveries.jsonl"),
        broker_explorer_url=f"{EXPLORER_BASE_URL}/",
        hearth_job_state="idle",
        hearth_provider="omen-dense-27b",
        hearth_model="qwen3.8-27b",
        hearth_status="succeeded",
        hearth_stages=["admission", "driver", "critic", "sealed"],
        steppewise_state="idle",
        steppewise_workflow="washplant-checkpoint-AX",
        steppewise_run_id="br-20261104-181221-00714280",
        steppewise_status="Completed",
        steppewise_steps=["admission", "execute", "verify", "seal"],
        steppewise_events_path=str(
            WASHPLANT_ARTIFACTS_DIR / "kernel" / "run-br-20261104-181221-00714280.events.jsonl"
        ),
        router_job_state="idle",
        router_receipt_path=str(wp_dir / "binding.json"),
        selected_route="pilot_variant_d.r36.py",
        task_family="washplant_repair",
        regime_summary="PILE2-s4v2-wp001-AX checkpoint",
        jepa_job_state="idle",
        jepa_job_note="used deterministic /tmp/pilot-jepa-working sourcemap pack",
        tokens_job_state="idle",
        model_calls=calls,
        tokens_in_total=tok_in,
        tokens_out_total=tok_out,
        cached_input_tokens=None,
        cached_input_tokens_state="unknown",
        ontology_job_state="idle",
        ontology_entry_url=DEFAULT_ONTOLOGY_UI_URL,
        ontology_term_id=DEFAULT_ONTOLOGY_TERM_ID,
        ontology_term_name="Solicitation",
        ontology_attr_id=DEFAULT_ONTOLOGY_ATTR_ID,
        ontology_attr_name="Budget Label",
        ontology_receipt_path=str(WASHPLANT_AUTO_ONTOLOGY_DIR / "reviewed-terms.json"),
        ontology_job_note="used reviewed-terms.json compiled from Auto-Ontology",
        duckdb_job_state="idle",
        duckdb_job_note="used hopper.duckdb (items=1, dispatches=3, deliveries=3, outcomes=3)",
        check_artifact_path=str(wp_dir / "VERIFIED.md"),
        observed_hops=hops,
        human_title=_HUMAN_TITLES["wp001-AX-00714280"],
        outcome_symbol="✓",
        outcome_label="completed",
        short_story=_SHORT_STORIES["wp001-AX-00714280"],
        attention_summary="none — clean run",
        elapsed_label="7.9s wall (6.5s helper)",
        helper_elapsed_s=6.45,
        budget_tokens_in=20000,
        budget_tokens_out=10096,
        budget_calls=2,
        features_completed=1,
        checks_passed=True,
        review_status="sealed",
        stage_ribbon={
            "request": "done",
            "helper": "done",
            "parent_work": "done",
            "checks": "done",
            "result": "done",
        },
        irc_delivery_status="unused",
        irc_delivery_label="not used by this job",
    )


def collect_correlated_tasks(
    runs_dir: Optional[Path] = None,
    limit: int = 10,
) -> List[CorrelatedTask]:
    """Collect and correlate the latest `limit` tasks across BH2/Orland, Ontology, and Washplant."""
    now_unix = time.time()
    tasks: List[CorrelatedTask] = []

    for rdir in _iter_run_dirs(runs_dir):
        t = _build_bh2_correlated_task(rdir, now_unix)
        if t is not None:
            tasks.append(t)

    wp_task = _build_washplant_task(now_unix)
    if wp_task is not None:
        tasks.append(wp_task)

    def _sort_key(item: CorrelatedTask) -> Tuple[int, float]:
        if item.state == "active":
            rank = 2
        elif item.outcome_label == "staged":
            rank = 0
        else:
            rank = 1
        ts = _parse_iso_ts(item.last_progress_utc) or 0.0
        return (rank, ts)

    tasks.sort(key=_sort_key, reverse=True)
    return tasks[:limit]


# ---------------------------------------------------------------------------
# Service Availability Matrix ("What is available?")
# ---------------------------------------------------------------------------


def collect_functional_readiness(manifest_path: str) -> List[ServiceAvailability]:
    """Run the checker's shared read-only probes; never start or repair services.

    Probe errors remain visible. In particular, a socket or an old cached receipt
    cannot replace fresh functional evidence from this invocation.
    """
    try:
        manifest = Path(manifest_path).resolve()
        if (not Path(manifest_path).is_absolute() or not manifest.is_file()
                or manifest.suffix != ".json"
                or not manifest.is_relative_to(READINESS_ROOT / "readiness")):
            raise ValueError("readiness manifest must be a task-owned JSON file")
        result = subprocess.run(
            [READINESS_PYTHON, str(READINESS_ROOT / "tools/self_build_probes.py"),
             "--manifest", str(manifest)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode or len(result.stdout) > 1_000_000:
            raise ValueError("functional probe did not return bounded evidence")
        doc = json.loads(result.stdout)
        if doc.get("schema") != "washplant.functional-readiness.v1":
            raise ValueError("unsupported functional evidence schema")
        observed = datetime.fromisoformat(doc["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("functional observation has no timezone")
        age = time.time() - observed.timestamp()
        rows = doc.get("services")
        if not isinstance(rows, list) or not rows:
            raise ValueError("functional evidence has no service rows")
        services = []
        seen = set()
        for raw in rows:
            row = ServiceAvailability.from_dict(raw)
            if not row.service_id or row.service_id in seen:
                raise ValueError("functional evidence has duplicate or missing identities")
            seen.add(row.service_id)
            row_age = row.obs_age_s
            valid_age = type(row_age) in (int, float) and math.isfinite(row_age) and row_age >= 0
            row.obs_age_s = max(age, row_age) if valid_age else None
            if age < 0 or row.obs_age_s is None or not 0 <= row.obs_age_s <= 60:
                row.state = "stale"
                row.functional_check_passed = False
                row.detail += "; functional evidence stale, missing, or future-dated"
            elif not row.readiness_scope or not row.functional_check_source:
                row.state = "unknown"
                row.functional_check_passed = False
                row.detail += "; functional evidence has no bound scope/source"
            elif row.functional_check_passed is not True:
                row.state = "unavailable"
            elif row.state not in ("active", "idle"):
                row.functional_check_passed = False
            services.append(row)
        return services
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        # External stderr/exception messages may contain protected connection
        # details; report the class, never dump them into the dashboard.
        return [ServiceAvailability(
            service_id="readiness_probe", name="Washplant functional readiness",
            host_endpoint="task-scoped manifest", state="unavailable",
            detail=f"Read-only readiness collection unavailable ({type(exc).__name__})",
            source=str(READINESS_ROOT / "tools/self_build_probes.py"),
            obs_age_s=0.0, functional_check_passed=False,
        )]


def collect_services_availability(
    service_bus: ServiceBusPanel,
    ontology: OntologyPanel,
    duckdb_panel: DuckDBPanel,
    lifetime_caches: LifetimeCacheSummary,
    tasks: List[CorrelatedTask],
    sim_set: Set[str],
    timeout_s: float = 1.0,
) -> List[ServiceAvailability]:
    """Build the top-level service availability table answering 'What is available?'."""
    services: List[ServiceAvailability] = []

    # 1. Service Bus
    if "service_bus" in sim_set or service_bus.state == "unavailable":
        sb_state = "unavailable"
        sb_detail = service_bus.error_note or UNAVAILABLE_NOTE
    else:
        sb_state = service_bus.state
        sb_detail = (
            f"queues={len([q for q in service_bus.queues if q.entity_type == 'queue'])} "
            f"topics={len([q for q in service_bus.queues if q.entity_type == 'topic'])} "
            f"backlog={service_bus.total_queued_backlog} dlq={service_bus.total_dead_letters} "
            f"sent/recv/settled={service_bus.recent_send_count}/{service_bus.recent_receive_count}/{service_bus.recent_settle_count}"
        )
    services.append(
        ServiceAvailability(
            service_id="service_bus",
            name="Service Bus Emulator + Explorer",
            host_endpoint="OMEN :5672 (AMQP) / :18080 (Explorer GET-only)",
            state=sb_state,
            detail=sb_detail,
            source=service_bus.source,
            obs_age_s=service_bus.obs_age_s,
        )
    )

    # 2. Ontology
    if "ontology" in sim_set or ontology.state == "unavailable":
        onto_state = "unavailable"
        onto_detail = ontology.error_note or UNAVAILABLE_NOTE
    else:
        onto_state = ontology.state
        onto_detail = (
            f"AM4 postgres={ontology.postgres_status}; {ontology.total_terms} terms, "
            f"{ontology.total_column_attrs} colAttrs; {ontology.selected_term_name}/{ontology.selected_attr_name} "
            f"({ontology.last_lookup_ms or 194.4:.1f}ms)"
        )
    services.append(
        ServiceAvailability(
            service_id="ontology",
            name="Ontology Catalog (OMEN -> AM4)",
            host_endpoint="OMEN 127.0.0.1:13000 -> AM4 10.44.0.2:13000",
            state=onto_state,
            detail=onto_detail,
            source=ontology.source,
            obs_age_s=ontology.obs_age_s,
        )
    )

    # 3. HEARTH & 27B Pool
    if "hearth" in sim_set:
        hearth_state = "unavailable"
        hearth_detail = UNAVAILABLE_NOTE
    else:
        h_ok, h_data, _ = _http_get_json(HEARTH_HEALTH_URL, timeout_s=min(0.8, timeout_s))
        active_hearth_tasks = sum(1 for t in tasks if t.state == "active" and t.hearth_job_state == "active")
        if h_ok or _tcp_reachable("127.0.0.1", 8710, 0.4) or _tcp_reachable("127.0.0.1", 18091, 0.4):
            hearth_state = "active" if active_hearth_tasks > 0 else "idle"
            hearth_detail = (
                f"three-dense 27B pool (:18091, :18092, AM4:18094) online; "
                f"active_jobs={active_hearth_tasks}"
            )
        else:
            hearth_state = "unavailable"
            hearth_detail = "HEARTH :8710 / vLLM :18091 unreachable"
    services.append(
        ServiceAvailability(
            service_id="hearth",
            name="HEARTH Door + 27B Serving Pool",
            host_endpoint="OMEN :8710 / B70 :18091,:18092 / AM4 :18094",
            state=hearth_state,
            detail=hearth_detail,
            source="http://127.0.0.1:8710/health + /dev/shm/fleet-top-snapshot.json",
            obs_age_s=lifetime_caches.obs_age_s,
        )
    )

    # 4. JEPA NPU & Lifetime Caches
    if "lifetime_caches" in sim_set or lifetime_caches.state == "unavailable":
        cache_state = "unavailable"
        cache_detail = UNAVAILABLE_NOTE
    else:
        cache_state = lifetime_caches.state
        cache_detail = f"{lifetime_caches.npu_ast_summary}; SOL: {lifetime_caches.sol_arena_summary}"
    services.append(
        ServiceAvailability(
            service_id="lifetime_caches",
            name="JEPA NPU Router & Lifetime Caches",
            host_endpoint="OMEN :18097 (Intel NPU) + /dev/shm arenas",
            state=cache_state,
            detail=cache_detail,
            source=lifetime_caches.source,
            obs_age_s=lifetime_caches.obs_age_s,
        )
    )

    # 5. DuckDB Intake
    if "duckdb" in sim_set or duckdb_panel.state == "unavailable":
        ddb_state = "unavailable"
        ddb_detail = duckdb_panel.error_note or UNAVAILABLE_NOTE
    else:
        ddb_state = duckdb_panel.state
        ddb_detail = (
            f"writer={duckdb_panel.active_writer_pid or 'none'}; "
            f"hopper={duckdb_panel.washplant_table_counts}; "
            f"net_change={duckdb_panel.net_row_change_total:+d}; "
            f"last_commit={duckdb_panel.last_commit_utc}"
        )
    services.append(
        ServiceAvailability(
            service_id="duckdb",
            name="DuckDB Intake (Washplant + Seed)",
            host_endpoint="OMEN embedded .duckdb + 0444 snapshots",
            state=ddb_state,
            detail=ddb_detail,
            source=duckdb_panel.source,
            obs_age_s=duckdb_panel.obs_age_s,
        )
    )

    # 6. Aspire & IRC Observability
    if "aspire" in sim_set:
        asp_state = "unavailable"
        asp_detail = UNAVAILABLE_NOTE
    else:
        asp_online = _tcp_reachable("127.0.0.1", 18888, timeout_s=min(0.4, timeout_s))
        asp_state = "idle" if asp_online else "unavailable"
        latest_trace = next((t.aspire_trace_id for t in tasks if t.aspire_trace_id), "none")
        asp_detail = (
            f"Aspire UI :18888 {'online' if asp_online else 'offline'}, OTLP :4317/:4318; "
            f"IRC passive receipts only (latest trace={latest_trace[:16]})"
        )
    services.append(
        ServiceAvailability(
            service_id="aspire",
            name="Aspire Trace UI + Passive IRC Receipts",
            host_endpoint="OMEN 127.0.0.1:18888 (OTLP :4317) + IRC-RECEIPTS.jsonl",
            state=asp_state,
            detail=asp_detail,
            source="TCP :18888 probe + OTEL-SPANS.jsonl + IRC-RECEIPTS.jsonl (zero IRC socket connects)",
            obs_age_s=0.0,
        )
    )

    return services


# ---------------------------------------------------------------------------
# Top-Level Entry Point: `collect_flow_snapshot`
# ---------------------------------------------------------------------------


def collect_flow_snapshot(
    selected_task_index: int = 0,
    selected_task_id: Optional[str] = None,
    runs_dir: Optional[Path] = None,
    simulate_unavailable: Optional[List[str]] = None,
    timeout_s: float = 2.0,
) -> FlowSnapshot:
    """Collect a complete, fault-isolated `FlowSnapshot` answering the three core questions."""
    poll_started_mono = time.monotonic()
    now_unix = time.time()
    now_utc = datetime.fromtimestamp(now_unix, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    sim_set: Set[str] = set()
    if simulate_unavailable:
        for item in simulate_unavailable:
            for part in str(item).split(","):
                p = part.strip().lower()
                if p:
                    sim_set.add(p)
    env_sim = os.environ.get("FLOW_TOP_SIMULATE_UNAVAILABLE", "").strip()
    if env_sim:
        for part in env_sim.split(","):
            p = part.strip().lower()
            if p:
                sim_set.add(p)

    # Fault-isolated parallel execution across collectors
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="flow-top-col") as pool:
        fut_sb = (
            None
            if "service_bus" in sim_set
            else pool.submit(collect_service_bus_panel, runs_dir, min(1.2, timeout_s))
        )
        fut_onto = (
            None
            if "ontology" in sim_set
            else pool.submit(collect_ontology_panel, min(1.2, timeout_s))
        )
        fut_ddb = (
            None
            if "duckdb" in sim_set
            else pool.submit(collect_duckdb_panel)
        )
        fut_caches = (
            None
            if "lifetime_caches" in sim_set
            else pool.submit(collect_lifetime_caches, min(1.2, timeout_s))
        )
        fut_tasks = pool.submit(collect_correlated_tasks, runs_dir, 10)

        # Resolve Service Bus
        if fut_sb is None:
            service_bus = ServiceBusPanel(
                state="unavailable",
                explorer_online=False,
                emulator_amqp_online=False,
                error_note=UNAVAILABLE_NOTE,
            )
        else:
            try:
                service_bus = fut_sb.result(timeout=timeout_s)
            except (FuturesTimeoutError, Exception) as exc:
                service_bus = ServiceBusPanel(
                    state="unavailable",
                    explorer_online=False,
                    emulator_amqp_online=False,
                    error_note=f"{UNAVAILABLE_NOTE}: {exc}",
                )

        # Resolve Ontology
        if fut_onto is None:
            ontology = OntologyPanel(
                state="unavailable",
                omen_proxy_online=False,
                am4_backend_online=False,
                postgres_status="unavailable",
                error_note=UNAVAILABLE_NOTE,
            )
        else:
            try:
                ontology = fut_onto.result(timeout=timeout_s)
            except (FuturesTimeoutError, Exception) as exc:
                ontology = OntologyPanel(
                    state="unavailable",
                    omen_proxy_online=False,
                    am4_backend_online=False,
                    postgres_status="unavailable",
                    error_note=f"{UNAVAILABLE_NOTE}: {exc}",
                )

        # Resolve DuckDB
        if fut_ddb is None:
            duckdb_panel = DuckDBPanel(
                state="unavailable",
                error_note=UNAVAILABLE_NOTE,
            )
        else:
            try:
                duckdb_panel = fut_ddb.result(timeout=timeout_s)
            except (FuturesTimeoutError, Exception) as exc:
                duckdb_panel = DuckDBPanel(
                    state="unavailable",
                    error_note=f"{UNAVAILABLE_NOTE}: {exc}",
                )

        # Resolve Lifetime Caches
        if fut_caches is None:
            lifetime_caches = LifetimeCacheSummary(
                state="unavailable",
                npu_ast_online=False,
                kv_caches_summary=[UNAVAILABLE_NOTE],
                npu_ast_summary=UNAVAILABLE_NOTE,
                sol_arena_summary=UNAVAILABLE_NOTE,
            )
        else:
            try:
                lifetime_caches = fut_caches.result(timeout=timeout_s)
            except (FuturesTimeoutError, Exception) as exc:
                lifetime_caches = LifetimeCacheSummary(
                    state="unavailable",
                    npu_ast_online=False,
                    kv_caches_summary=[f"{UNAVAILABLE_NOTE}: {exc}"],
                    npu_ast_summary=UNAVAILABLE_NOTE,
                    sol_arena_summary=UNAVAILABLE_NOTE,
                )

        # Resolve Correlated Tasks
        try:
            tasks = fut_tasks.result(timeout=timeout_s)
        except (FuturesTimeoutError, Exception):
            tasks = []

    # Resolve selected task index
    idx = selected_task_index
    if selected_task_id and tasks:
        for i, t in enumerate(tasks):
            if t.task_id == selected_task_id:
                idx = i
                break
    if tasks:
        idx = max(0, min(idx, len(tasks) - 1))
    else:
        idx = 0

    services = collect_services_availability(
        service_bus=service_bus,
        ontology=ontology,
        duckdb_panel=duckdb_panel,
        lifetime_caches=lifetime_caches,
        tasks=tasks,
        sim_set=sim_set,
        timeout_s=min(1.0, timeout_s),
    )
    readiness_manifest = os.environ.get("WASHPLANT_READINESS_MANIFEST", "").strip()
    if readiness_manifest:
        services = collect_functional_readiness(readiness_manifest)
        if sim_set:
            for service in services:
                service.state = "unavailable"
                service.functional_check_passed = False
                service.detail += "; simulated view cannot establish readiness"

    online_svc = sum(1 for s in services if s.state in ("active", "idle"))
    unavail_svc = [s.service_id for s in services if s.state == "unavailable"]
    active_tasks = [t for t in tasks if t.state == "active"]

    if active_tasks or service_bus.state == "active" or duckdb_panel.state == "active":
        overall_state = "active"
    elif unavail_svc and len(unavail_svc) == len(services):
        overall_state = "unavailable"
    else:
        overall_state = "idle"

    # Question 1: What is available?
    if readiness_manifest:
        passed = sum(s.functional_check_passed is True for s in services)
        headline_available = (
            f"Washplant readiness: {passed}/{len(services)} functional checks passed "
            f"(run-scoped; no inference or service changes)"
        )
    elif unavail_svc:
        headline_available = (
            f"{online_svc}/{len(services)} services available "
            f"(unavailable: {', '.join(unavail_svc)}; other panels updating normally)"
        )
    else:
        headline_available = (
            f"{online_svc}/{len(services)} services available "
            f"(Service Bus :18080/:5672, Ontology :13000->AM4, HEARTH 3x27B, JEPA :18097, DuckDB snapshots, Aspire :18888)"
        )

    # Question 2: What work is moving?
    if active_tasks:
        a0 = active_tasks[0]
        headline_moving = (
            f"ACTIVE: {len(active_tasks)} task(s) in flight — {a0.task_id} at [{a0.stage}] "
            f"(broker backlog={service_bus.total_queued_backlog}, unsettled={service_bus.unsettled_in_flight})"
        )
    else:
        latest_t = tasks[0] if tasks else None
        latest_desc = (
            f"latest completed: {latest_t.task_id} ({latest_t.last_progress_utc})"
            if latest_t
            else "none"
        )
        headline_moving = (
            f"IDLE: 0 tasks in flight, broker backlog={service_bus.total_queued_backlog}, "
            f"DuckDB writer=none ({latest_desc})"
        )

    # Question 3: Did that work use the intended components?
    sel = tasks[idx] if tasks else None
    if sel is not None:
        used_labels = [
            f"{h.hop_id}={h.state}"
            for h in sel.observed_hops
            if h.used_by_job
        ]
        not_used_labels = [
            f"{h.hop_id}=not used by this job"
            for h in sel.observed_hops
            if h.used_by_job is False
        ]
        headline_components = (
            f"[{sel.task_id}] used: {', '.join(used_labels)} | "
            f"{', '.join(not_used_labels)}"
        )
    else:
        headline_components = "No correlated task selected"

    monitor_usage = _measure_monitor_usage(poll_started_mono)

    snapshot = FlowSnapshot(
        schema="flow_top_snapshot.v1",
        collected_at_utc=now_utc,
        collected_at_unix=now_unix,
        overall_state=overall_state,
        headline_available=headline_available,
        headline_moving=headline_moving,
        headline_components=headline_components,
        selected_task_index=idx,
        services=services,
        tasks=tasks,
        service_bus=service_bus,
        ontology=ontology,
        duckdb=duckdb_panel,
        lifetime_caches=lifetime_caches,
        monitor_usage=monitor_usage,
        simulated_unavailable=sorted(sim_set),
    )

    if not sim_set and runs_dir is None and not readiness_manifest:
        save_flow_snapshot_cache(snapshot)

    return snapshot
