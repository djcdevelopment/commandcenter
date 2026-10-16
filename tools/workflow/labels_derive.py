"""Label derivations for the labels materializer (Track C; CONTRACT.md §1, plan tender-gathering-fox Lap 2).

This module is imported LAZILY by tools/workflow/project_labels.py and only when ``HEARTH_LABELS=on``.
In ``off`` mode it is never imported, so the materializer opens no run, ledger, transcript, git or
knowledge file (test_project_labels proves it).

One ``labels.json`` per run (schema ``labels.v1``): every label is ``{value, method, source, at, note?}``;
``value`` is null when unknown and never imputed; ``method`` names how the value was obtained.

Sources, in the order each label prefers them:

  L1 verdict      work-manifest.json + history.ndjson (``verification.recorded``, ``outcome.final``; the
                  Track B keys ``bucket, attended, task_id, validator`` when present; the manifest's verdict
                  as the fallback, marked ``manifest_post_outcome``)
  L2 route        ``step.dispatched`` payload keys when present (``event``), else manifest.route /
                  manifest.conditions (``manifest_post_outcome``)
  L3 review time  ``artifact.fetched`` (``event``) else the HEARTH ledger row for
                  ``get_local_work_artifact`` (``derived_ledger``); elapsed and queue delay from those plus
                  ``artifact.produced`` and the verdict (``derived_events``); the Claude transcript proxy
                  (``derived_transcript``) and the reviewer's own estimate (``self_report``)
  L4 downstream   ``Local-Work: work_<id>`` commit trailers across the continuity repos (``git_trailer``);
                  rework from lineage groups (corpus ``group_id`` when a precursor-run.v2 corpus is given,
                  else identical ``intent_sha256``); submit-to-accept minutes
  L5 delivery     delivery.json + manifest.route / manifest.attempts

Transcript rule (binding, CONTRACT.md preamble): from a transcript record only ``timestamp``, ``type`` and
``sessionId`` are read, and from a ``tool_use`` item only ``name`` and ``input.work_id``. No prompt or output
text is read, kept or hashed. ``scan_transcripts`` is the only function that touches a transcript.

Nothing here records a verdict, picks a seat, or reads or changes a routing decision. Every scan that
could fail (ledger, transcripts, git, corpus) degrades to a diagnostic; ``derive_run`` never raises.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from tools.workflow.corpus_guard import check_fixture_taint, guard_write, make_extractor

SCHEMA = "labels.v1"
ROLLUP_SCHEMA = "labels-rollup.v1"
GENERATOR_VERSION = "labels_derive.v1"
LABELS_FILE = "labels.json"
ROLLUP_FILE = "labels.json"
CORPUS_RUN_SCHEMA = "precursor-run.v2"

VERDICT_TOOL = "mcp__hearth__record_local_work_verdict"
FETCH_TOOL = "get_local_work_artifact"
UNATTENDED_SUBMITTERS = ("bankedfire-drain",)
VERDICT_DECISIONS = ("accepted", "rejected", "superseded", "failed")
BUCKETS = ("form", "setup", "substance")

# Transcript proxy constants (CONTRACT §1, review_active_minutes_transcript).
ACTIVE_GAP_MAX_S = 120.0
TICK_PERIOD_S = 60.0
TICK_TOLERANCE_S = 5.0
TICK_MIN_RUN = 3

PER_RUN_BUDGET_MS = 250.0

METHODS = ("event", "principal", "derived_events", "derived_transcript", "derived_ledger", "self_report",
           "git_trailer", "board", "manifest_post_outcome", "manual")

L1 = ("decision", "bucket", "attended", "validator", "verdict_task_id", "verdict_at", "criteria", "reversal")
L2 = ("task_family", "lane", "selected_lane", "serving_profile_sha256", "route_profile_sha256", "procedure",
      "lab_configuration", "temperature", "provider", "model", "submitted_by", "unattended_submission")
L3 = ("first_fetch_at", "first_fetch_by", "queue_delay_minutes", "review_elapsed_minutes",
      "review_active_minutes_transcript", "concurrent_reviews", "review_minutes_self_report")
L4 = ("applied", "applied_at", "applied_commit", "rework", "submit_to_accept_minutes")
L5 = ("repairs", "claims", "resolved", "unsupported", "aids_used", "deterministic", "suspected_incomplete",
      "structured_output_repairs", "tokens_in", "tokens_out", "duration_ms")
LABEL_NAMES = L1 + L2 + L3 + L4 + L5

# step.dispatched payload key -> (manifest section, manifest key) for the L2 fallback.
L2_FALLBACK = {
    "task_family": ("route", "task_family"),
    "lane": ("route", "requested_lane"),
    "selected_lane": ("route", "selected_lane"),
    "serving_profile_sha256": ("route", "serving_profile_sha256"),
    "route_profile_sha256": ("route", "profile_sha256"),
    "procedure": ("route", "procedure"),
    "lab_configuration": ("conditions", "lab_configuration"),
    "temperature": ("conditions", "temperature"),
    "provider": ("route", "provider"),
    "model": ("route", "model"),
}

PASSED = {"passed", "pass", "met", "ok", "true", "yes"}
FAILED = {"failed", "fail", "unmet", "not_met", "false", "no"}

_SELF_REPORT_KV = re.compile(r"review_minutes_active\s*=\s*(\d+(?:\.\d+)?)")
_SELF_REPORT_PROSE = re.compile(r"Review minutes:\s*(\d+(?:\.\d+)?)")
_WORK_ID_IN_PREVIEW = re.compile(r'"work_id"\s*:\s*"(work_[0-9a-f]+)"')
_WORK_ID_IN_BYTES = re.compile(rb'\\?"work_id\\?"\s*:\s*\\?"(work_[0-9a-f]+)')
_WORK_ID = re.compile(r"work_[0-9a-f]{6,}")


# --------------------------------------------------------------------------- small helpers

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def minutes_between(start, end) -> Optional[float]:
    a, b = parse_ts(start), parse_ts(end)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 60.0, 2)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(doc) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def mtime_iso(path: Path) -> Optional[str]:
    try:
        return iso_z(datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc))
    except OSError:
        return None


def atomic_write_json(path: Path, doc) -> None:
    """Temp file in the same directory, fsync, os.replace, fsync the directory (precursor convention)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def write_knowledge(path: Path, doc: dict, count_key: str, sources: Iterable[Path] = ()) -> None:
    """knowledge/*.json goes through the corpus regression guard and the fixture-taint check like every
    other knowledge writer; the file and its directory are fsynced afterwards."""
    check_fixture_taint(list(sources), Path(path).parent)
    guard_write(Path(path), doc, make_extractor(count_key))
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        dfd = os.open(str(Path(path).parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_ndjson(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict):
            events.append(doc)
    return events


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def label(value, method: str, source: Optional[str], at: Optional[str], note: Optional[str] = None) -> dict:
    out = {"value": value, "method": method, "source": source, "at": at}
    if note:
        out["note"] = note
    return out


# --------------------------------------------------------------------------- work dirs

def work_dirs(runs_root: Path) -> list[Path]:
    root = Path(runs_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("work_"))


def input_files(path: Path) -> list[Path]:
    path = Path(path)
    return [path / "work-manifest.json", path / "history.ndjson", path / "delivery.json", path / "refs" / "envelope.json"]


def inputs_mtime_ns(path: Path) -> Optional[int]:
    newest = None
    for f in input_files(path):
        try:
            ns = f.stat().st_mtime_ns
        except OSError:
            continue
        newest = ns if newest is None else max(newest, ns)
    return newest


def read_work_dir(path: Path) -> Optional[dict]:
    """Everything the derivations need from one work directory; None when neither a manifest nor a
    history exists (nothing to label)."""
    path = Path(path)
    manifest_path = path / "work-manifest.json"
    history_path = path / "history.ndjson"
    if not manifest_path.is_file() and not history_path.is_file():
        return None
    manifest: dict = {}
    if manifest_path.is_file():
        try:
            doc = read_json(manifest_path)
            manifest = doc if isinstance(doc, dict) else {}
        except ValueError:
            manifest = {}
    events = read_ndjson(history_path) if history_path.is_file() else []
    events.sort(key=lambda e: (e.get("timestamp") or "", e.get("sequence") or 0))
    envelope: dict = {}
    envelope_path = path / "refs" / "envelope.json"
    if envelope_path.is_file():
        try:
            doc = read_json(envelope_path)
            envelope = doc if isinstance(doc, dict) else {}
        except ValueError:
            envelope = {}
    delivery: Optional[dict] = None
    delivery_path = path / "delivery.json"
    if delivery_path.is_file():
        try:
            doc = read_json(delivery_path)
            delivery = doc if isinstance(doc, dict) else None
        except ValueError:
            delivery = None

    def first(kind: str) -> Optional[dict]:
        for event in events:
            if event.get("event_type") == kind:
                return event
        return None

    received = first("task.received")
    intent = envelope.get("intent") or ""
    intent_sha = sha256_text(intent) if intent else ((received or {}).get("payload") or {}).get("intent_sha256")
    return {
        "run_id": manifest.get("work_id") or path.name,
        "path": path,
        "manifest": manifest,
        "events": events,
        "envelope": envelope,
        "delivery": delivery,
        "intent_sha256": intent_sha,
        "submitted_by": envelope.get("submitted_by") or ((received or {}).get("payload") or {}).get("submitted_by"),
        "submitted_at": envelope.get("submitted_at") or (received or {}).get("timestamp"),
        "manifest_at": mtime_iso(manifest_path) if manifest_path.is_file() else None,
        "delivery_at": mtime_iso(delivery_path) if delivery_path.is_file() else None,
    }


def _events_of(info: dict, kind: str) -> list[dict]:
    return [e for e in info["events"] if e.get("event_type") == kind]


def _event_src(event: dict) -> str:
    return f"history.ndjson#{event.get('event_type')}@seq{event.get('sequence')}"


# --------------------------------------------------------------------------- one-time scans (per sweep)

def scan_ledger(ledger_dir: Path) -> dict:
    """First ``get_local_work_artifact`` row per work_id from the HEARTH ledger, read-only.
    Rows come from ``index.sqlite``; each body is read at ``offset/length`` from ``events.ndjson`` and the
    work id taken from ``args_preview``."""
    t0 = time.perf_counter()
    out = {"by_work_id": {}, "rows": 0, "matched": 0, "diagnostic": None, "ms": 0.0}
    ledger_dir = Path(ledger_dir)
    index = ledger_dir / "index.sqlite"
    ndjson = ledger_dir / "events.ndjson"
    if not index.is_file() or not ndjson.is_file():
        out["diagnostic"] = f"ledger absent: {ledger_dir}"
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out
    try:
        conn = sqlite3.connect(f"file:{index}?mode=ro", uri=True, timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT event_id, ts, caller_id, offset, length FROM events WHERE tool = ? ORDER BY ts",
                (FETCH_TOOL,)).fetchall()
        finally:
            conn.close()
        with ndjson.open("rb") as fh:
            for event_id, ts, caller_id, offset, length in rows:
                out["rows"] += 1
                try:
                    fh.seek(int(offset))
                    body = fh.read(int(length))
                except (OSError, ValueError):
                    continue
                work_id = None
                try:
                    doc = json.loads(body)
                    preview = doc.get("args_preview") if isinstance(doc, dict) else None
                    if isinstance(preview, str):
                        m = _WORK_ID_IN_PREVIEW.search(preview)
                        work_id = m.group(1) if m else None
                    if not caller_id and isinstance(doc, dict) and isinstance(doc.get("caller"), dict):
                        caller_id = doc["caller"].get("id")
                except ValueError:
                    m = _WORK_ID_IN_BYTES.search(body)
                    work_id = m.group(1).decode("ascii") if m else None
                if not work_id:
                    continue
                out["matched"] += 1
                at = parse_ts(ts)
                row = {"at": iso_z(at) if at else ts, "by": caller_id, "event_id": event_id}
                prev = out["by_work_id"].get(work_id)
                if prev is None or (row["at"] or "") < (prev["at"] or ""):
                    out["by_work_id"][work_id] = row
    except (sqlite3.Error, OSError) as exc:
        out["diagnostic"] = f"ledger unreadable: {type(exc).__name__}: {str(exc)[:200]}"
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def _line_has_verdict_call(line: bytes) -> bool:
    """Cheap pre-filter, then a confirming parse of that one line: a tool_use item named VERDICT_TOOL."""
    if b'"work_id"' not in line or b"tool_use" not in line or VERDICT_TOOL.encode() not in line:
        return False
    try:
        rec = json.loads(line)
    except ValueError:
        return False
    msg = rec.get("message") if isinstance(rec, dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return False
    return any(isinstance(it, dict) and it.get("type") == "tool_use" and it.get("name") == VERDICT_TOOL
               for it in content)


def scan_transcripts(root: Path) -> dict:
    """Sessions that recorded a verdict, as timestamps and work-id tool calls only.

    Returns {"sessions": {sessionId: {"records": [epoch...], "tool_uses": [(epoch, name, work_id)...]}},
    "files", "parsed_files", "diagnostic", "ms"}. Pass 1 streams each file and keeps it only if a line holds
    a tool_use named VERDICT_TOOL; pass 2 streams that file again and takes, per record, ``timestamp``,
    ``type`` and ``sessionId`` and, for ``tool_use`` items, ``name`` and ``input.work_id``. Nothing else is
    read into memory beyond the one line being parsed."""
    t0 = time.perf_counter()
    out = {"sessions": {}, "files": 0, "parsed_files": 0, "diagnostic": None, "ms": 0.0}
    root = Path(root).expanduser()
    if not root.is_dir():
        out["diagnostic"] = f"transcripts absent: {root}"
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out
    for path in sorted(root.rglob("*.jsonl")):
        out["files"] += 1
        try:
            hit = False
            with path.open("rb") as fh:
                for line in fh:
                    if _line_has_verdict_call(line):
                        hit = True
                        break
            if not hit:
                continue
            out["parsed_files"] += 1
            with path.open("rb") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    ts = parse_ts(rec.get("timestamp"))
                    if ts is None:
                        continue
                    sid = rec.get("sessionId") or path.stem
                    sess = out["sessions"].setdefault(sid, {"records": [], "tool_uses": []})
                    epoch = ts.timestamp()
                    sess["records"].append(epoch)
                    if rec.get("type") != "assistant":
                        continue
                    msg = rec.get("message")
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if not isinstance(content, list):
                        continue
                    for item in content:
                        if not isinstance(item, dict) or item.get("type") != "tool_use":
                            continue
                        inp = item.get("input")
                        work_id = inp.get("work_id") if isinstance(inp, dict) else None
                        if isinstance(work_id, str) and work_id.startswith("work_"):
                            sess["tool_uses"].append((epoch, str(item.get("name")), work_id))
        except OSError as exc:
            out["diagnostic"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
    for sess in out["sessions"].values():
        sess["records"] = sorted(set(sess["records"]))
        sess["tool_uses"].sort()
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def tick_gap_mask(gaps: list[float]) -> list[bool]:
    """True for gaps that belong to a Monitor-tick burst: a run of >= TICK_MIN_RUN consecutive gaps each
    within TICK_TOLERANCE_S of TICK_PERIOD_S. Excluded by cadence only (CONTRACT §1)."""
    mask = [False] * len(gaps)
    i = 0
    while i < len(gaps):
        if abs(gaps[i] - TICK_PERIOD_S) <= TICK_TOLERANCE_S:
            j = i
            while j < len(gaps) and abs(gaps[j] - TICK_PERIOD_S) <= TICK_TOLERANCE_S:
                j += 1
            if j - i >= TICK_MIN_RUN:
                for k in range(i, j):
                    mask[k] = True
            i = j
        else:
            i += 1
    return mask


def session_spans(sess: dict) -> list[dict]:
    """One span per work_id that received a verdict in this session: [first tool_use for the work_id,
    that verdict tool_use]. The first verdict per work_id in the session is the one that counts."""
    spans: list[dict] = []
    seen: set[str] = set()
    for ts, name, work_id in sess["tool_uses"]:
        if name != VERDICT_TOOL or work_id in seen:
            continue
        seen.add(work_id)
        start = min((t for t, _n, w in sess["tool_uses"] if w == work_id and t <= ts), default=ts)
        spans.append({"work_id": work_id, "start": start, "end": ts})
    return spans


def span_activity(records: list[float], spans: list[dict], span: dict) -> dict:
    recs = [r for r in records if span["start"] <= r <= span["end"]]
    gaps = [b - a for a, b in zip(recs, recs[1:])]
    mask = tick_gap_mask(gaps)
    active = sum(g for g, tick in zip(gaps, mask) if g <= ACTIVE_GAP_MAX_S and not tick)
    concurrent = sum(1 for s in spans if s["start"] <= span["end"] and s["end"] >= span["start"])
    return {
        "active_seconds": round(active, 3),
        "tick_gaps_excluded": sum(mask),
        "records_in_span": len(recs),
        "concurrent_reviews": max(concurrent, 1),
        "span_start": iso_z(datetime.fromtimestamp(span["start"], tz=timezone.utc)),
        "span_end": iso_z(datetime.fromtimestamp(span["end"], tz=timezone.utc)),
    }


def transcript_proxies(scan: dict) -> dict:
    """work_id -> proxy row (the session whose verdict call came first wins)."""
    out: dict[str, dict] = {}
    for sid, sess in scan.get("sessions", {}).items():
        spans = session_spans(sess)
        for span in spans:
            row = span_activity(sess["records"], spans, span)
            row["session_id"] = sid
            row["active_minutes_shared"] = round(row["active_seconds"] / row["concurrent_reviews"] / 60.0, 2)
            prev = out.get(span["work_id"])
            if prev is None or row["span_end"] < prev["span_end"]:
                out[span["work_id"]] = row
    return out


def scan_git_trailers(config_path: Path) -> dict:
    """Earliest commit per work_id carrying a ``Local-Work: work_<id>`` trailer across the repos named in
    ``~/.continuity/config.toml`` ``[repos.*] path``. One ``git log`` per repo per sweep."""
    t0 = time.perf_counter()
    out = {"by_work_id": {}, "repos": [], "diagnostic": None, "ms": 0.0}
    config_path = Path(config_path).expanduser()
    if not config_path.is_file():
        out["diagnostic"] = f"continuity config absent: {config_path}"
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out
    try:
        import tomllib
        with config_path.open("rb") as fh:
            config = tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001
        out["diagnostic"] = f"continuity config unreadable: {type(exc).__name__}: {str(exc)[:120]}"
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out
    repos = config.get("repos") if isinstance(config, dict) else None
    for name, table in sorted((repos or {}).items()):
        path = table.get("path") if isinstance(table, dict) else None
        if not path:
            continue
        repo = Path(path).expanduser()
        entry = {"name": name, "path": str(repo), "commits": 0, "error": None}
        out["repos"].append(entry)
        if not repo.is_dir():
            entry["error"] = "missing"
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo), "log", "--all", "--grep=Local-Work:",
                 "--format=%H%x09%cI%x09%(trailers:key=Local-Work,valueonly,separator=%x2c)"],
                capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
        if proc.returncode != 0:
            entry["error"] = (proc.stderr or "").strip()[:200] or f"git exit {proc.returncode}"
            continue
        for line in proc.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            sha, ciso, trailers = parts
            at = parse_ts(ciso.strip())
            for work_id in _WORK_ID.findall(trailers):
                entry["commits"] += 1
                row = {"commit": sha, "at": iso_z(at) if at else ciso.strip(), "repo": str(repo)}
                prev = out["by_work_id"].get(work_id)
                if prev is None or row["at"] < prev["at"]:
                    out["by_work_id"][work_id] = row
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def load_corpus_groups(corpus_path: Optional[Path]) -> dict:
    """run_id -> group_id and intent_sha256 -> group_id from a precursor-run.v2 corpus, if given."""
    out = {"by_run": {}, "by_intent": {}, "rows": 0, "diagnostic": None}
    if corpus_path is None:
        out["diagnostic"] = "no corpus given: lineage by identical intent_sha256"
        return out
    corpus_path = Path(corpus_path).expanduser()
    if not corpus_path.is_file():
        out["diagnostic"] = f"corpus absent: {corpus_path}; lineage by identical intent_sha256"
        return out
    try:
        for line in corpus_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if not isinstance(doc, dict) or doc.get("schema") != CORPUS_RUN_SCHEMA or not doc.get("run_id"):
                continue
            gid = doc.get("group_id")
            if not gid:
                continue
            out["rows"] += 1
            out["by_run"][doc["run_id"]] = gid
            sha = doc.get("intent_sha256") or (sha256_text(doc["intent_text"]) if doc.get("intent_text") else None)
            if sha:
                out["by_intent"].setdefault(sha, gid)
    except OSError as exc:
        out["diagnostic"] = f"corpus unreadable: {type(exc).__name__}"
    return out


class SweepContext:
    """Per-sweep caches: each scan runs at most once and only when a run asks for it."""

    def __init__(self, *, ledger_dir: Path, transcripts_root: Optional[Path], continuity_config: Optional[Path],
                 corpus_path: Optional[Path], infos: Iterable[dict] = ()):
        self.ledger_dir = Path(ledger_dir)
        self.transcripts_root = Path(transcripts_root) if transcripts_root else None
        self.continuity_config = Path(continuity_config) if continuity_config else None
        self.corpus_path = Path(corpus_path) if corpus_path else None
        self._ledger: Optional[dict] = None
        self._transcripts: Optional[dict] = None
        self._proxies: Optional[dict] = None
        self._git: Optional[dict] = None
        self._lineage: Optional[dict] = None
        self.infos = list(infos)

    @property
    def ledger(self) -> dict:
        if self._ledger is None:
            self._ledger = scan_ledger(self.ledger_dir)
        return self._ledger

    @property
    def transcripts(self) -> dict:
        if self._transcripts is None:
            if self.transcripts_root is None:
                self._transcripts = {"sessions": {}, "files": 0, "parsed_files": 0, "ms": 0.0,
                                     "diagnostic": "transcript proxy disabled"}
            else:
                self._transcripts = scan_transcripts(self.transcripts_root)
        return self._transcripts

    @property
    def proxies(self) -> dict:
        if self._proxies is None:
            self._proxies = transcript_proxies(self.transcripts)
        return self._proxies

    @property
    def git(self) -> dict:
        if self._git is None:
            if self.continuity_config is None:
                self._git = {"by_work_id": {}, "repos": [], "ms": 0.0, "diagnostic": "git trailer scan disabled"}
            else:
                self._git = scan_git_trailers(self.continuity_config)
        return self._git

    @property
    def lineage(self) -> dict:
        """run_id -> {group_id, group_method} and group_id -> [member summaries] over every run in the sweep."""
        if self._lineage is None:
            corpus = load_corpus_groups(self.corpus_path)
            by_intent = dict(corpus["by_intent"])
            runs: dict[str, dict] = {}
            groups: dict[str, list[dict]] = {}
            for info in self.infos:
                rid = info["run_id"]
                gid = corpus["by_run"].get(rid)
                method = "corpus_group"
                if gid is None:
                    sha = info.get("intent_sha256")
                    gid = by_intent.get(sha) if sha else None
                    method = "intent_sha256"
                    if gid is None:
                        gid = "g_" + sha256_text(rid)[:12]
                        if sha:
                            by_intent[sha] = gid
                decision, verdict_at = _first_decision(info)
                member = {"run_id": rid, "submitted_at": info.get("submitted_at"), "decision": decision,
                          "verdict_at": verdict_at}
                runs[rid] = {"group_id": gid, "group_method": method}
                groups.setdefault(gid, []).append(member)
            self._lineage = {"runs": runs, "groups": groups, "corpus_rows": corpus["rows"],
                             "diagnostic": corpus["diagnostic"]}
        return self._lineage

    def scan_summary(self) -> dict:
        out = {}
        if self._ledger is not None:
            out["ledger"] = {k: self._ledger[k] for k in ("rows", "matched", "diagnostic", "ms")}
        if self._transcripts is not None:
            out["transcripts"] = {k: self._transcripts[k] for k in ("files", "parsed_files", "diagnostic", "ms")}
            out["transcripts"]["sessions"] = len(self._transcripts["sessions"])
            out["transcripts"]["work_ids_with_proxy"] = len(self.proxies)
        if self._git is not None:
            out["git"] = {"repos": self._git["repos"], "work_ids": len(self._git["by_work_id"]),
                          "diagnostic": self._git["diagnostic"], "ms": self._git["ms"]}
        if self._lineage is not None:
            out["lineage"] = {"groups": len(self._lineage["groups"]), "corpus_rows": self._lineage["corpus_rows"],
                              "diagnostic": self._lineage["diagnostic"]}
        return out


# --------------------------------------------------------------------------- L1 verdict

def _first_decision(info: dict) -> tuple[Optional[str], Optional[str]]:
    """(decision, verdict_at): first outcome.final, else the manifest verdict (no timestamp)."""
    for event in _events_of(info, "outcome.final"):
        status = (event.get("payload") or {}).get("status")
        if status in VERDICT_DECISIONS:
            return status, event.get("timestamp")
    verdict = _dict(info["manifest"].get("verdict"))
    if verdict.get("decision") in VERDICT_DECISIONS:
        return verdict["decision"], None
    if info["manifest"].get("status") in VERDICT_DECISIONS:
        return info["manifest"]["status"], None
    return None, None


def _verification_event(info: dict) -> Optional[dict]:
    """The first verification.recorded that carries a decision (the verdict call), not the door's
    deterministic checks row."""
    for event in _events_of(info, "verification.recorded"):
        if (event.get("payload") or {}).get("decision") in VERDICT_DECISIONS:
            return event
    return None


def _normalise_status(raw) -> str:
    if isinstance(raw, bool):
        return "passed" if raw else "failed"
    text = str(raw).strip().lower() if raw is not None else ""
    return text or "unknown"


def _criteria_label(info: dict, verification: Optional[dict], manifest_at: Optional[str]) -> dict:
    verdict = _dict(info["manifest"].get("verdict"))
    raw_rows = verdict.get("criteria")
    if not isinstance(raw_rows, list):
        return label(None, "manifest_post_outcome", "work-manifest.json:verdict.criteria", manifest_at,
                     "no criteria rows in the manifest verdict")
    rows = []
    passed = failed = unknown = 0
    for row in raw_rows:
        if isinstance(row, str):
            text, status, evidence = row, None, None
        elif isinstance(row, dict):
            text = row.get("criterion") or row.get("id") or ""
            status = row.get("status", row.get("state"))
            if status is None and "met" in row:
                status = row.get("met")
            evidence = row.get("evidence", row.get("note"))
        else:
            continue
        norm = _normalise_status(status)
        if norm in PASSED:
            passed += 1
        elif norm in FAILED:
            failed += 1
        else:
            unknown += 1
        rows.append({
            "criterion_sha12": sha256_text(str(text))[:12],
            "status": norm,
            "evidence_sha12": sha256_text(canonical(evidence))[:12] if evidence is not None else None,
        })
    value = {"passed": passed, "failed": failed, "unknown": unknown, "rows": rows}
    if verification is not None:
        return label(value, "event", "work-manifest.json:verdict.criteria", verification.get("timestamp"),
                     f"rows recorded with {_event_src(verification)}")
    return label(value, "manifest_post_outcome", "work-manifest.json:verdict.criteria", manifest_at)


def derive_l1(info: dict, ctx: SweepContext) -> dict:
    out: dict[str, dict] = {}
    manifest = info["manifest"]
    verdict = _dict(manifest.get("verdict"))
    manifest_at = info.get("manifest_at")
    finals = _events_of(info, "outcome.final")
    first_final = next((e for e in finals if (e.get("payload") or {}).get("status") in VERDICT_DECISIONS), None)
    verification = _verification_event(info)
    vpayload = (verification or {}).get("payload") or {}
    fpayload = (first_final or {}).get("payload") or {}
    msrc = "work-manifest.json:verdict"

    # decision
    if first_final is not None:
        out["decision"] = label(fpayload.get("status"), "event", _event_src(first_final), first_final.get("timestamp"))
    elif verdict.get("decision") in VERDICT_DECISIONS:
        out["decision"] = label(verdict["decision"], "manifest_post_outcome", msrc, manifest_at,
                                "no outcome.final in history")
    elif manifest.get("status") in VERDICT_DECISIONS:
        out["decision"] = label(manifest["status"], "manifest_post_outcome", "work-manifest.json:status", manifest_at,
                                "no outcome.final and no manifest verdict")
    else:
        out["decision"] = label(None, "event", None, None, "no verdict yet")

    # verdict_at
    if first_final is not None:
        out["verdict_at"] = label(first_final.get("timestamp"), "event", _event_src(first_final), first_final.get("timestamp"))
    elif verification is not None:
        out["verdict_at"] = label(verification.get("timestamp"), "event", _event_src(verification), verification.get("timestamp"),
                                  "verification.recorded carries the decision; no outcome.final")
    else:
        out["verdict_at"] = label(None, "event", None, None, "no verdict event in history")
    verdict_at = out["verdict_at"]["value"]

    # bucket
    bucket = vpayload.get("bucket") or fpayload.get("bucket")
    if bucket in BUCKETS:
        src_event = verification if vpayload.get("bucket") else first_final
        out["bucket"] = label(bucket, "event", _event_src(src_event), src_event.get("timestamp"))
    elif verdict.get("bucket") in BUCKETS:
        out["bucket"] = label(verdict["bucket"], "manifest_post_outcome", msrc, manifest_at)
    else:
        note = "absent from events and manifest" + (" (historic rejection without bucket)"
                                                      if out["decision"]["value"] == "rejected" else "")
        out["bucket"] = label(None, "event", None, None, note)

    # attended
    attended = vpayload.get("attended", fpayload.get("attended"))
    if isinstance(attended, bool):
        src_event = verification if "attended" in vpayload else first_final
        out["attended"] = label(attended, "event", _event_src(src_event), src_event.get("timestamp"))
    else:
        out["attended"] = label(None, "event", None, None, "historic: no attended flag in events")

    # validator
    validator = vpayload.get("validator") or fpayload.get("validator")
    if validator:
        src_event = verification if vpayload.get("validator") else first_final
        out["validator"] = label(validator, "principal", _event_src(src_event), src_event.get("timestamp"))
    elif verdict.get("caller_id"):
        out["validator"] = label(verdict["caller_id"], "manifest_post_outcome", msrc + ".caller_id", manifest_at)
    elif _dict(manifest.get("caller")).get("validated_by"):
        out["validator"] = label(manifest["caller"]["validated_by"], "manifest_post_outcome",
                                 "work-manifest.json:caller.validated_by", manifest_at)
    else:
        out["validator"] = label(None, "principal", None, None)

    # verdict_task_id
    task_id = vpayload.get("task_id") or fpayload.get("task_id")
    if task_id:
        src_event = verification if vpayload.get("task_id") else first_final
        out["verdict_task_id"] = label(task_id, "event", _event_src(src_event), src_event.get("timestamp"))
    else:
        proxy = ctx.proxies.get(info["run_id"]) if ctx.transcripts_root is not None else None
        if proxy is not None:
            out["verdict_task_id"] = label("cc-" + proxy["session_id"][:8], "derived_transcript",
                                           f"transcript:{proxy['session_id']}", proxy["span_end"],
                                           "cc-<sessionId[:8]> of the session that recorded the verdict")
        else:
            out["verdict_task_id"] = label(None, "event", None, None, "no task_id in events; no transcript match")

    out["criteria"] = _criteria_label(info, verification, manifest_at)

    # reversal: history vs manifest disagree, or more than one final status
    statuses = [(e.get("payload") or {}).get("status") for e in finals]
    statuses = [s for s in statuses if s in VERDICT_DECISIONS]
    manifest_decision = verdict.get("decision") if verdict.get("decision") in VERDICT_DECISIONS else (
        manifest.get("status") if manifest.get("status") in VERDICT_DECISIONS else None)
    if statuses and manifest_decision:
        reversed_ = (len(set(statuses)) > 1) or (statuses[-1] != manifest_decision)
        src = f"history.ndjson#outcome.final x{len(statuses)} vs {msrc}"
        out["reversal"] = label(reversed_, "derived_events", src, verdict_at,
                                None if not reversed_ else f"finals={statuses} manifest={manifest_decision}")
    elif len(statuses) > 1:
        out["reversal"] = label(len(set(statuses)) > 1, "derived_events", "history.ndjson#outcome.final", verdict_at)
    else:
        out["reversal"] = label(None, "derived_events", None, None, "nothing to compare")
    return out


# --------------------------------------------------------------------------- L2 route

def derive_l2(info: dict) -> dict:
    out: dict[str, dict] = {}
    manifest = info["manifest"]
    dispatched = next(iter(_events_of(info, "step.dispatched")), None)
    payload = (dispatched or {}).get("payload") or {}
    manifest_at = info.get("manifest_at")
    for name, (section, key) in L2_FALLBACK.items():
        if dispatched is not None and name in payload:
            out[name] = label(payload.get(name), "event", _event_src(dispatched), dispatched.get("timestamp"))
            continue
        table = _dict(manifest.get(section))
        if key in table:
            out[name] = label(table.get(key), "manifest_post_outcome", f"work-manifest.json:{section}.{key}", manifest_at)
        else:
            out[name] = label(None, "manifest_post_outcome", None, None, "absent from dispatch payload and manifest")
    received = next(iter(_events_of(info, "task.received")), None)
    submitted_by = info.get("submitted_by")
    if submitted_by:
        if info["envelope"].get("submitted_by"):
            out["submitted_by"] = label(submitted_by, "event", "refs/envelope.json:submitted_by", info.get("submitted_at"))
        else:
            out["submitted_by"] = label(submitted_by, "event", _event_src(received), received.get("timestamp"))
        out["unattended_submission"] = label(submitted_by in UNATTENDED_SUBMITTERS, "event",
                                             out["submitted_by"]["source"], out["submitted_by"]["at"])
    else:
        out["submitted_by"] = label(None, "event", None, None, "no envelope and no task.received")
        out["unattended_submission"] = label(None, "event", None, None, "submitter unknown")
    return out


# --------------------------------------------------------------------------- L3 review time

def _self_report(info: dict, verdict_at: Optional[str], manifest_at: Optional[str]) -> dict:
    verdict = _dict(info["manifest"].get("verdict"))
    texts: list[str] = []
    for item in _list(verdict.get("evidence")):
        if isinstance(item, str):
            texts.append(item)
    for row in _list(verdict.get("criteria")):
        if isinstance(row, dict):
            for key in ("evidence", "note"):
                if isinstance(row.get(key), str):
                    texts.append(row[key])
    for text in texts:
        m = _SELF_REPORT_KV.search(text)
        if m:
            return label(float(m.group(1)), "self_report", "work-manifest.json:verdict.evidence",
                         verdict_at or manifest_at, "review_minutes_active=<n> (estimate, not the measurement)")
    summary = verdict.get("summary")
    if isinstance(summary, str):
        m = _SELF_REPORT_PROSE.search(summary)
        if m:
            return label(float(m.group(1)), "self_report", "work-manifest.json:verdict.summary",
                         verdict_at or manifest_at, "legacy_prose")
    return label(None, "self_report", None, None, "no review_minutes_active= and no 'Review minutes:' prose")


def derive_l3(info: dict, ctx: SweepContext, verdict_at: Optional[str]) -> dict:
    out: dict[str, dict] = {}
    run_id = info["run_id"]
    fetched = next(iter(_events_of(info, "artifact.fetched")), None)
    fetch_method = None
    if fetched is not None:
        payload = fetched.get("payload") or {}
        out["first_fetch_at"] = label(fetched.get("timestamp"), "event", _event_src(fetched), fetched.get("timestamp"))
        out["first_fetch_by"] = label(payload.get("by"), "event", _event_src(fetched), fetched.get("timestamp"))
        fetch_method = "event"
    else:
        row = ctx.ledger["by_work_id"].get(run_id)
        if row is not None:
            src = f"ledger:events/{row['event_id']}"
            out["first_fetch_at"] = label(row["at"], "derived_ledger", src, row["at"])
            out["first_fetch_by"] = label(row["by"], "derived_ledger", src, row["at"])
            fetch_method = "derived_ledger"
        else:
            note = ctx.ledger.get("diagnostic") or "no artifact.fetched event and no ledger fetch row"
            out["first_fetch_at"] = label(None, "derived_ledger", None, None, note)
            out["first_fetch_by"] = label(None, "derived_ledger", None, None, note)
    first_fetch_at = out["first_fetch_at"]["value"]

    produced = _events_of(info, "artifact.produced")
    produced_at = None
    produced_src = None
    if produced:
        before = [e for e in produced if first_fetch_at is None or (e.get("timestamp") or "") <= first_fetch_at]
        chosen = before[-1] if before else produced[0]
        produced_at, produced_src = chosen.get("timestamp"), _event_src(chosen)
    note = f"fetch:{fetch_method}" if fetch_method else None
    queue = minutes_between(produced_at, first_fetch_at)
    out["queue_delay_minutes"] = label(queue, "derived_events", f"{out['first_fetch_at']['source']} - {produced_src}"
                                       if queue is not None else None, first_fetch_at if queue is not None else None,
                                       note if queue is not None else "needs artifact.produced and a first fetch")
    elapsed = minutes_between(first_fetch_at, verdict_at)
    out["review_elapsed_minutes"] = label(elapsed, "derived_events", f"verdict_at - {out['first_fetch_at']['source']}"
                                          if elapsed is not None else None, verdict_at if elapsed is not None else None,
                                          note if elapsed is not None else "needs a first fetch and a verdict_at")

    proxy = ctx.proxies.get(run_id) if ctx.transcripts_root is not None else None
    if proxy is not None:
        src = f"transcript:{proxy['session_id']}"
        detail = (f"proxy: gap-sum<= {int(ACTIVE_GAP_MAX_S)} s over {proxy['records_in_span']} records in "
                  f"[{proxy['span_start']}, {proxy['span_end']}], raw_active_s={proxy['active_seconds']}, "
                  f"tick_gaps_excluded={proxy['tick_gaps_excluded']}, shared by {proxy['concurrent_reviews']}")
        out["review_active_minutes_transcript"] = label(proxy["active_minutes_shared"], "derived_transcript", src,
                                                        proxy["span_end"], detail)
        out["concurrent_reviews"] = label(proxy["concurrent_reviews"], "derived_transcript", src, proxy["span_end"])
    else:
        note = ctx.transcripts.get("diagnostic") if ctx.transcripts_root is None else "no reviewer session found for this work_id"
        out["review_active_minutes_transcript"] = label(None, "derived_transcript", None, None, note)
        out["concurrent_reviews"] = label(None, "derived_transcript", None, None, note)

    out["review_minutes_self_report"] = _self_report(info, verdict_at, info.get("manifest_at"))
    return out


# --------------------------------------------------------------------------- L4 downstream

def derive_l4(info: dict, ctx: SweepContext, decision: Optional[str], verdict_at: Optional[str]) -> dict:
    out: dict[str, dict] = {}
    run_id = info["run_id"]
    git = ctx.git
    row = git["by_work_id"].get(run_id)
    if row is not None:
        src = f"git:{row['repo']}"
        out["applied"] = label(True, "git_trailer", src, row["at"])
        out["applied_at"] = label(row["at"], "git_trailer", src, row["at"])
        out["applied_commit"] = label(row["commit"], "git_trailer", src, row["at"])
    elif git.get("diagnostic") or not git.get("repos"):
        note = git.get("diagnostic") or "no continuity repos"
        out["applied"] = label(None, "git_trailer", None, None, note)
        out["applied_at"] = label(None, "git_trailer", None, None, note)
        out["applied_commit"] = label(None, "git_trailer", None, None, note)
    else:
        scanned = ", ".join(r["name"] for r in git["repos"] if not r.get("error"))
        note = f"no 'Local-Work: {run_id}' trailer in [{scanned}]"
        out["applied"] = label(False, "git_trailer", "git:continuity repos", None, note)
        out["applied_at"] = label(None, "git_trailer", None, None, note)
        out["applied_commit"] = label(None, "git_trailer", None, None, note)

    lineage = ctx.lineage
    mine = lineage["runs"].get(run_id) or {}
    gid = mine.get("group_id")
    members = [m for m in lineage["groups"].get(gid, []) if m["run_id"] != run_id]
    submitted_at = info.get("submitted_at")
    if submitted_at is None:
        out["rework"] = label(None, "derived_events", None, None, "no submitted_at")
    else:
        earlier_accepted = [m for m in members if m["decision"] == "accepted"
                            and (m["verdict_at"] or m["submitted_at"] or "") < submitted_at
                            and (m["verdict_at"] or m["submitted_at"])]
        src = f"lineage:{gid} ({mine.get('group_method')}, {len(members) + 1} runs)"
        out["rework"] = label(bool(earlier_accepted), "derived_events", src, submitted_at,
                              ("after accepted " + ", ".join(m["run_id"] for m in earlier_accepted)) if earlier_accepted
                              else "no earlier accepted run in the same lineage group")
    if decision == "accepted":
        minutes = minutes_between(submitted_at, verdict_at)
        out["submit_to_accept_minutes"] = label(minutes, "derived_events", "verdict_at - submitted_at" if minutes is not None else None,
                                                verdict_at if minutes is not None else None,
                                                None if minutes is not None else "needs submitted_at and verdict_at")
    else:
        out["submit_to_accept_minutes"] = label(None, "derived_events", None, None, f"decision is {decision}")
    return out


# --------------------------------------------------------------------------- L5 delivery

def derive_l5(info: dict) -> dict:
    out: dict[str, dict] = {}
    delivery = info.get("delivery")
    manifest = info["manifest"]
    route = _dict(manifest.get("route"))
    manifest_at = info.get("manifest_at")
    dsrc = "delivery.json"
    dat = info.get("delivery_at")
    if delivery is not None:
        claims = delivery.get("claims")
        claims = claims if isinstance(claims, list) else []
        unsupported = delivery.get("unsupported")
        out["repairs"] = label(delivery.get("repairs"), "event", dsrc + ":repairs", dat)
        out["claims"] = label(len(claims), "event", dsrc + ":claims", dat)
        out["resolved"] = label(sum(1 for c in claims if isinstance(c, dict) and c.get("resolved")), "event",
                                dsrc + ":claims[].resolved", dat)
        out["unsupported"] = label(len(unsupported) if isinstance(unsupported, list) else unsupported, "event",
                                   dsrc + ":unsupported", dat)
        out["aids_used"] = label(delivery.get("aids_used"), "event", dsrc + ":aids_used", dat)
        det = _dict(_dict(delivery.get("verification")).get("deterministic"))
        out["deterministic"] = label(det.get("state"), "event", dsrc + ":verification.deterministic.state", dat)
        out["suspected_incomplete"] = label(delivery.get("suspected_incomplete"), "event", dsrc + ":suspected_incomplete", dat)
    else:
        for name in ("repairs", "claims", "resolved", "unsupported", "aids_used", "deterministic", "suspected_incomplete"):
            out[name] = label(None, "event", None, None, "no delivery.json")
    repairs: list = []
    for attempt in _list(manifest.get("attempts")):
        if isinstance(attempt, dict) and isinstance(attempt.get("structured_output_repairs"), list):
            repairs.extend(attempt["structured_output_repairs"])
    if "attempts" in manifest:
        out["structured_output_repairs"] = label(repairs, "manifest_post_outcome",
                                                 "work-manifest.json:attempts[].structured_output_repairs", manifest_at)
    else:
        out["structured_output_repairs"] = label(None, "manifest_post_outcome", None, None, "no attempts in manifest")
    for name in ("tokens_in", "tokens_out", "duration_ms"):
        if name in route:
            out[name] = label(route.get(name), "manifest_post_outcome", f"work-manifest.json:route.{name}", manifest_at)
        else:
            out[name] = label(None, "manifest_post_outcome", None, None, "absent from manifest.route")
    return out


# --------------------------------------------------------------------------- per run

def derive_run(info: dict, ctx: SweepContext, *, now: Optional[datetime] = None) -> dict:
    """labels.v1 for one run; never raises (a fault becomes generator_error)."""
    t0 = time.perf_counter()
    now = now or utc_now()
    doc = {
        "schema": SCHEMA,
        "run_id": info["run_id"],
        "generated_at": iso_z(now),
        "generator_version": GENERATOR_VERSION,
        "labels": {},
        "coverage": {},
    }
    try:
        labels: dict[str, dict] = {}
        labels.update(derive_l1(info, ctx))
        labels.update(derive_l2(info))
        verdict_at = labels["verdict_at"]["value"]
        decision = labels["decision"]["value"]
        labels.update(derive_l3(info, ctx, verdict_at))
        labels.update(derive_l4(info, ctx, decision, verdict_at))
        labels.update(derive_l5(info))
        ordered = {name: labels[name] for name in LABEL_NAMES}
        doc["labels"] = ordered
        doc["coverage"] = {name: ordered[name]["value"] is not None for name in LABEL_NAMES}
        doc["status_class"] = info["manifest"].get("status") or ("unknown" if not info["events"] else "history_only")
        doc["submitted_at"] = info.get("submitted_at")
    except Exception as exc:  # noqa: BLE001 -- a derivation fault is a record, never a crash of the sweep
        import traceback
        doc["generator_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        doc["diagnostic"] = traceback.format_exc(limit=4)[-1200:]
        doc["coverage"] = {name: False for name in LABEL_NAMES}
    doc["derive_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    if doc["derive_ms"] > PER_RUN_BUDGET_MS:
        doc["budget_exceeded"] = f"{doc['derive_ms']} ms > {PER_RUN_BUDGET_MS:.0f} ms (scans excluded: they run once per sweep)"
    return doc


def labels_path(work_dir: Path) -> Path:
    return Path(work_dir) / LABELS_FILE


def needs_labels(work_dir: Path, *, force: bool = False) -> tuple[bool, str]:
    """(regenerate?, reason): missing labels, inputs newer than the labels (a late verdict), an older
    generator, or an unreadable labels file."""
    target = labels_path(work_dir)
    if force:
        return True, "forced"
    if not target.is_file():
        return True, "missing"
    try:
        existing = read_json(target)
    except (OSError, ValueError):
        return True, "unreadable"
    if not isinstance(existing, dict) or existing.get("generator_version") != GENERATOR_VERSION:
        return True, "generator_version"
    newest = inputs_mtime_ns(work_dir)
    try:
        labels_ns = target.stat().st_mtime_ns
    except OSError:
        return True, "missing"
    if newest is not None and newest > labels_ns:
        return True, "inputs_newer"
    return False, "fresh"


def sweep(runs_root: Path, ctx_kwargs: dict, *, force: bool = False, now: Optional[datetime] = None) -> dict:
    """Write labels.json for every work dir whose labels are missing or stale. Never raises."""
    t_sweep = time.perf_counter()
    now = now or utc_now()
    infos: list[dict] = []
    errors: list[dict] = []
    skipped_no_inputs = 0
    for path in work_dirs(runs_root):
        try:
            info = read_work_dir(path)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            continue
        if info is None:
            skipped_no_inputs += 1
            continue
        infos.append(info)
    ctx = SweepContext(infos=infos, **ctx_kwargs)
    counts = {"swept": len(infos), "written": 0, "skipped_fresh": 0, "skipped_no_inputs": skipped_no_inputs,
              "generator_errors": 0, "budget_exceeded": 0, "reasons": {}, "errors": errors}
    latencies: list[float] = []
    todo: list[tuple[dict, str]] = []
    for info in infos:
        regenerate, reason = needs_labels(info["path"], force=force)
        if regenerate:
            todo.append((info, reason))
        else:
            counts["skipped_fresh"] += 1
    if todo:
        # The one-time scans run here, outside every run's 250 ms budget (their cost is in counts["scans"]).
        t_scan = time.perf_counter()
        ctx.ledger, ctx.proxies, ctx.git, ctx.lineage  # noqa: B018 -- warm the per-sweep caches
        counts["scan_ms"] = round((time.perf_counter() - t_scan) * 1000, 1)
    for info, reason in todo:
        counts["reasons"][reason] = counts["reasons"].get(reason, 0) + 1
        doc = derive_run(info, ctx, now=now)
        try:
            atomic_write_json(labels_path(info["path"]), doc)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(labels_path(info["path"])), "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            continue
        counts["written"] += 1
        if "generator_error" in doc:
            counts["generator_errors"] += 1
        if "budget_exceeded" in doc:
            counts["budget_exceeded"] += 1
        latencies.append(doc["derive_ms"])
    lat = sorted(latencies)
    counts["derive_ms_p50"] = lat[len(lat) // 2] if lat else None
    counts["derive_ms_p95"] = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None
    counts["derive_ms_max"] = lat[-1] if lat else None
    counts["scans"] = ctx.scan_summary()
    counts["sweep_ms"] = round((time.perf_counter() - t_sweep) * 1000, 1)
    return counts


# --------------------------------------------------------------------------- rollup

def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return round(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2, 2)


def rollup(runs_root: Path, out_dir: Path, *, now: Optional[datetime] = None) -> dict:
    """knowledge/labels.json (labels-rollup.v1): coverage per label with by_method, status-class counts and
    the self-report-vs-elapsed calibration rows, read from every labels.json under runs_root."""
    now = now or utc_now()
    docs: list[dict] = []
    unreadable = 0
    errored = 0
    for path in work_dirs(runs_root):
        target = labels_path(path)
        if not target.is_file():
            continue
        try:
            doc = read_json(target)
        except (OSError, ValueError):
            unreadable += 1
            continue
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            unreadable += 1
            continue
        if doc.get("generator_error"):
            errored += 1
        docs.append(doc)

    coverage: dict[str, dict] = {}
    n = len(docs)
    for name in LABEL_NAMES:
        by_method: dict[str, int] = {}
        count = 0
        for doc in docs:
            lab = (doc.get("labels") or {}).get(name)
            if not isinstance(lab, dict) or lab.get("value") is None:
                continue
            count += 1
            method = lab.get("method") or "?"
            by_method[method] = by_method.get(method, 0) + 1
        coverage[name] = {"n": count, "share": round(count / n, 4) if n else None,
                          "by_method": dict(sorted(by_method.items()))}

    status_classes: dict[str, int] = {}
    decisions: dict[str, int] = {}
    buckets: dict[str, int] = {}
    for doc in docs:
        sc = doc.get("status_class") or "unknown"
        status_classes[sc] = status_classes.get(sc, 0) + 1
        labels = doc.get("labels") or {}
        d = (labels.get("decision") or {}).get("value") or "none"
        decisions[d] = decisions.get(d, 0) + 1
        if d == "rejected":
            b = (labels.get("bucket") or {}).get("value") or "none"
            buckets[b] = buckets.get(b, 0) + 1

    rows = []
    for doc in docs:
        labels = doc.get("labels") or {}
        self_report = (labels.get("review_minutes_self_report") or {}).get("value")
        elapsed = (labels.get("review_elapsed_minutes") or {}).get("value")
        if self_report is None or elapsed is None:
            continue
        rows.append({
            "run_id": doc.get("run_id"),
            "self_report_minutes": self_report,
            "self_report_note": (labels.get("review_minutes_self_report") or {}).get("note"),
            "elapsed_minutes": elapsed,
            "fetch_method": (labels.get("first_fetch_at") or {}).get("method"),
            "active_transcript_minutes": (labels.get("review_active_minutes_transcript") or {}).get("value"),
            "concurrent_reviews": (labels.get("concurrent_reviews") or {}).get("value"),
            "ratio_self_over_elapsed": round(self_report / elapsed, 3) if elapsed else None,
        })
    rows.sort(key=lambda r: r["run_id"] or "")
    ratios = [r["ratio_self_over_elapsed"] for r in rows if r["ratio_self_over_elapsed"] is not None]
    with_proxy = [r for r in rows if r["active_transcript_minutes"] is not None]
    verdicts = [(d.get("labels") or {}).get("verdict_at", {}).get("value") for d in docs]
    verdicts = [v for v in verdicts if isinstance(v, str)]
    doc = {
        "schema": ROLLUP_SCHEMA,
        "built_at": iso_z(now),
        "generator_version": GENERATOR_VERSION,
        "runs_root": str(runs_root),
        "runs": n,
        "unreadable": unreadable,
        "generator_errors": errored,
        "evidence_watermark": max(verdicts) if verdicts else None,
        "coverage": coverage,
        "status_classes": dict(sorted(status_classes.items())),
        "decisions": dict(sorted(decisions.items())),
        "rejection_buckets": dict(sorted(buckets.items())),
        "calibration": {
            "self_report_vs_elapsed": rows,
            "summary": {
                "n": len(rows),
                "median_ratio_self_over_elapsed": _median(ratios),
                "median_self_report_minutes": _median([r["self_report_minutes"] for r in rows]),
                "median_elapsed_minutes": _median([r["elapsed_minutes"] for r in rows]),
                "n_with_transcript_proxy": len(with_proxy),
                "median_active_transcript_minutes": _median([r["active_transcript_minutes"] for r in with_proxy]),
            },
        },
        "note": "labels record; no verdict is recorded and no routing decision is read or changed",
    }
    write_knowledge(Path(out_dir) / ROLLUP_FILE, doc, "runs", sources=[Path(runs_root)])
    return doc


__all__ = ["SCHEMA", "ROLLUP_SCHEMA", "GENERATOR_VERSION", "LABELS_FILE", "ROLLUP_FILE", "LABEL_NAMES", "METHODS",
           "VERDICT_TOOL", "FETCH_TOOL", "SweepContext", "derive_run", "sweep", "rollup", "needs_labels",
           "read_work_dir", "scan_ledger", "scan_transcripts", "transcript_proxies", "tick_gap_mask",
           "scan_git_trailers", "load_corpus_groups", "atomic_write_json", "parse_ts", "iso_z", "labels_path"]
