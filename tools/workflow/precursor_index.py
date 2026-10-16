"""Precursor index, arms and joins (Track D1, plan tender-gathering-fox §5; CONTRACT.md §1–§2).

This module is imported LAZILY by tools/workflow/project_precursor.py, and only when
``HEARTH_PRECURSOR`` is ``shadow`` or ``display``. In ``off`` mode it is never imported, so the
materializer opens no index, corpus, run or knowledge file (test_project_precursor proves it).

Everything here is advisory and off the door's request path: it reads the corpus export and the
work directories, writes ``knowledge/precursor_index.json``, ``runs/operator/work_<id>/precursor/
advice.json`` and ``knowledge/precursor_accuracy.json``. It never records a verdict, never picks a
seat and never changes a routing decision (ADR-0070, proposed).

Arms in this increment: ``table`` and ``bm25``. The JEPA arms stay in the replay.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from tools.workflow.corpus_guard import guard_write, make_extractor

INDEX_SCHEMA = "precursor-index.v1"
ADVICE_SCHEMA = "precursor-advice.v1"
ACCURACY_SCHEMA = "precursor-accuracy.v1"
RUN_SCHEMA = "precursor-run.v2"

INDEX_FILE = "precursor_index.json"
ACCURACY_FILE = "precursor_accuracy.json"
ADVICE_DIR = "precursor"
ADVICE_FILE = "advice.json"

ARMS = ("table", "bm25")
BINARY_DECISIONS = {"accepted": True, "rejected": False}
VERDICT_DECISIONS = ("accepted", "rejected", "superseded", "failed")
LATE_DIAGNOSTIC = "late:advice_after_verdict"
STALE_AFTER = timedelta(hours=24)

# Frozen defaults (CONTRACT §2 arm rules). A frozen replay config at
# <research dir>/results/*/config.json overrides these key by key; config_id hashes the result.
DEFAULT_CONFIG = {
    "k": 5,
    "min_pool": 3,
    "smoothing": "laplace:(acc+1)/(n+2)",
    "bm25": {"k1": 1.5, "b": 0.75, "tokenizer": "lower_word"},
    "table_match": ["backend", "task_family"],
    "table_fallback": ["backend"],
    "neighbour_condition_fields": ["backend", "task_family"],
    "neighbour_condition_min": 2,
    "pool_requires_binary_label": True,
    "timeout_ms": 200,
    "arms": list(ARMS),
}

_TOKEN = re.compile(r"[a-z0-9_]+")


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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(doc) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def atomic_write_json(path: Path, doc) -> None:
    """Temp file in the same directory, fsync, os.replace, fsync the directory."""
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


def write_knowledge(path: Path, doc: dict, count_key: str) -> None:
    """Knowledge files go through the corpus regression guard like every other knowledge/*.json
    writer (tmp + os.replace via fsio); the file and its directory are fsynced afterwards."""
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


# --------------------------------------------------------------------------- config

def load_config(research_dir: Optional[Path]) -> tuple[dict, Optional[str]]:
    """Defaults, overridden by the newest frozen replay config if one exists."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    source = None
    if research_dir is not None:
        candidates = sorted((Path(research_dir) / "results").glob("*/config.json"))
        if candidates:
            frozen = candidates[-1]
            try:
                overrides = read_json(frozen)
                if isinstance(overrides, dict):
                    for key, value in overrides.items():
                        if key in config:
                            config[key] = value
                    source = str(frozen)
            except (OSError, ValueError):
                source = None
    return config, source


def config_id_of(config: dict) -> str:
    return sha256_text(canonical(config))[:12]


# --------------------------------------------------------------------------- live work dirs

def work_dirs(runs_root: Path) -> list[Path]:
    root = Path(runs_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("work_"))


def read_work_dir(path: Path) -> Optional[dict]:
    """Everything the index and the advisor need from one work directory; None without an
    envelope. Manifest and history are optional (a run may have been received only)."""
    path = Path(path)
    envelope_path = path / "refs" / "envelope.json"
    if not envelope_path.is_file():
        return None
    envelope = read_json(envelope_path)
    manifest: dict = {}
    manifest_path = path / "work-manifest.json"
    if manifest_path.is_file():
        try:
            manifest = read_json(manifest_path) or {}
        except ValueError:
            manifest = {}
    history_path = path / "history.ndjson"
    events = read_ndjson(history_path) if history_path.is_file() else []
    events.sort(key=lambda e: (e.get("timestamp") or "", e.get("sequence") or 0))

    def first(kind: str) -> Optional[dict]:
        for event in events:
            if event.get("event_type") == kind:
                return event
        return None

    received = first("task.received")
    dispatched = first("step.dispatched")
    artifact = first("artifact.produced")
    final = first("outcome.final")

    verdict_events = []
    for event in events:
        kind = event.get("event_type")
        payload = event.get("payload") or {}
        if kind == "outcome.final":
            decision = payload.get("status")
        elif kind == "verification.recorded":
            decision = payload.get("decision")
        else:
            continue
        if decision not in VERDICT_DECISIONS:
            continue
        verdict_events.append({
            "at": event.get("timestamp"),
            "decision": decision,
            "bucket": payload.get("bucket") or ((manifest.get("verdict") or {}).get("bucket")),
            "by": payload.get("validator") or ((manifest.get("verdict") or {}).get("caller_id")),
            "receipt_id": (event.get("refs") or {}).get("receipt_id"),
            "event": kind,
        })

    route = manifest.get("route") or {}
    dispatch_payload = (dispatched or {}).get("payload") or {}
    run_id = manifest.get("work_id") or path.name
    intent = envelope.get("intent") or ""
    return {
        "run_id": run_id,
        "path": str(path),
        "intent_text": intent,
        "intent_sha256": sha256_text(intent),
        "criteria": list(envelope.get("acceptance_criteria") or []),
        "submitted_by": envelope.get("submitted_by") or ((received or {}).get("payload") or {}).get("submitted_by"),
        "submitted_at": envelope.get("submitted_at") or (received or {}).get("timestamp"),
        "received_at": (received or {}).get("timestamp"),
        "dispatched_at": (dispatched or {}).get("timestamp"),
        "artifact_at": (artifact or {}).get("timestamp"),
        "first_final_at": (final or {}).get("timestamp"),
        "first_final_decision": ((final or {}).get("payload") or {}).get("status"),
        "verdict_events": verdict_events,
        "backend": route.get("backend") or route.get("provider") or dispatch_payload.get("provider"),
        "task_family": route.get("task_family"),
        "serving_profile_sha": route.get("serving_profile_sha256"),
        "status": manifest.get("status"),
        "has_advice": (path / ADVICE_DIR / ADVICE_FILE).is_file(),
    }


# --------------------------------------------------------------------------- index

INDEX_ROW_FIELDS = ("run_id", "group_id", "intent_text", "criteria", "backend", "task_family",
                    "serving_profile_sha", "dispatched_at", "artifact_at", "first_final_at",
                    "verdict_events", "source")


def _index_row(source: dict, origin: str, group_id: str) -> dict:
    row = {field: source.get(field) for field in INDEX_ROW_FIELDS}
    row["group_id"] = group_id
    row["source"] = origin
    row["criteria"] = list(row.get("criteria") or [])
    row["verdict_events"] = [
        {k: ev.get(k) for k in ("at", "decision", "bucket", "by", "receipt_id")}
        for ev in (row.get("verdict_events") or []) if isinstance(ev, dict)
    ]
    return row


def load_corpus(corpus_path: Path) -> tuple[list[dict], dict, int]:
    """Rows of precursor-run.v2 plus the MANIFEST beside it (if any) and the malformed count."""
    corpus_path = Path(corpus_path)
    rows: list[dict] = []
    skipped = 0
    if corpus_path.is_file():
        for line in corpus_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if not isinstance(doc, dict) or doc.get("schema") != RUN_SCHEMA or not doc.get("run_id"):
                skipped += 1
                continue
            rows.append(doc)
    manifest: dict = {}
    manifest_path = corpus_path.parent / "MANIFEST.json"
    if manifest_path.is_file():
        try:
            manifest = read_json(manifest_path) or {}
        except ValueError:
            manifest = {}
    return rows, manifest, skipped


def build_index(corpus_path: Path, runs_root: Path, out_dir: Path, *, research_dir: Optional[Path],
                now: Optional[datetime] = None) -> dict:
    t0 = time.perf_counter()
    now = now or utc_now()
    corpus_rows, corpus_manifest, skipped = load_corpus(corpus_path)
    corpus_version = corpus_manifest.get("corpus_version") or (
        Path(corpus_path).parent.name if Path(corpus_path).is_file() else "none")

    rows: list[dict] = []
    seen: set[str] = set()
    group_by_intent: dict[str, str] = {}
    for src in corpus_rows:
        group_id = src.get("group_id") or ("g_" + sha256_text(src["run_id"])[:12])
        rows.append(_index_row(src, "corpus", group_id))
        seen.add(src["run_id"])
        sha = src.get("intent_sha256") or sha256_text(src.get("intent_text") or "")
        group_by_intent.setdefault(sha, group_id)

    live_rows = 0
    for path in work_dirs(runs_root):
        try:
            info = read_work_dir(path)
        except (OSError, ValueError):
            skipped += 1
            continue
        if info is None or info["run_id"] in seen:
            continue
        # Lineage (lineage.py) is Track A's; live rows newer than the corpus share a group only
        # when their intent text is identical, else they are singleton groups.
        group_id = group_by_intent.get(info["intent_sha256"])
        if group_id is None:
            group_id = "g_" + sha256_text(info["run_id"])[:12]
            group_by_intent[info["intent_sha256"]] = group_id
        rows.append(_index_row(info, "live", group_id))
        seen.add(info["run_id"])
        live_rows += 1

    rows.sort(key=lambda r: r["run_id"])
    config, config_source = load_config(research_dir)
    finals = [r["first_final_at"] for r in rows if r.get("first_final_at")]
    doc = {
        "schema": INDEX_SCHEMA,
        "corpus_version": corpus_version,
        "corpus_path": str(corpus_path),
        "built_at": iso_z(now),
        "row_count": len(rows),
        "corpus_rows": len(corpus_rows),
        "live_rows": live_rows,
        "skipped": skipped,
        "evidence_watermark": max(finals) if finals else None,
        "config": config,
        "config_source": config_source,
        "config_id": config_id_of(config),
        "sha256": sha256_text(canonical(rows)),
        "rows": rows,
    }
    doc["build_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    write_knowledge(Path(out_dir) / INDEX_FILE, doc, "row_count")
    return doc


def load_index(out_dir: Path) -> tuple[Optional[dict], Optional[str]]:
    """(index, diagnostic): the diagnostic names why the index is unusable."""
    path = Path(out_dir) / INDEX_FILE
    if not path.is_file():
        return None, f"index missing: {path}"
    try:
        doc = read_json(path)
    except (OSError, ValueError) as exc:
        return None, f"index unreadable: {type(exc).__name__}: {str(exc)[:200]}"
    if not isinstance(doc, dict) or doc.get("schema") != INDEX_SCHEMA or not isinstance(doc.get("rows"), list):
        return None, f"index schema stale: {doc.get('schema') if isinstance(doc, dict) else type(doc).__name__}"
    return doc, None


# --------------------------------------------------------------------------- labels and pools

def label_at(row: dict, at: datetime) -> Optional[str]:
    """Latest verdict event with at <= T (CONTRACT §1)."""
    latest_ts = None
    latest = None
    for ev in row.get("verdict_events") or []:
        ts = parse_ts(ev.get("at"))
        if ts is None or ts > at:
            continue
        if latest_ts is None or ts >= latest_ts:
            latest_ts, latest = ts, ev.get("decision")
    return latest


def eligible_rows(rows: Iterable[dict], query_run_id: str, at: datetime, config: dict) -> list[dict]:
    """Temporal eligibility: first_final_at <= T, artifact_at <= T, not the query's own run;
    with pool_requires_binary_label the label at T must be accepted or rejected."""
    pool = []
    for row in rows:
        if row.get("run_id") == query_run_id:
            continue
        final = parse_ts(row.get("first_final_at"))
        artifact = parse_ts(row.get("artifact_at"))
        if final is None or artifact is None or final > at or artifact > at:
            continue
        label = label_at(row, at)
        if config.get("pool_requires_binary_label", True) and label not in BINARY_DECISIONS:
            continue
        pool.append({**row, "_label": label})
    return pool


def laplace(acc: int, n: int) -> float:
    return (acc + 1) / (n + 2)


def candidate_set_sha256(pool: list[dict]) -> str:
    return sha256_text("\n".join(sorted(r["run_id"] for r in pool)))


# --------------------------------------------------------------------------- BM25 (own)

class BM25:
    """Okapi BM25 over lower-cased word tokens; k1 = 1.5, b = 0.75 by default."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.tfs = [self._tf(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avgdl = (sum(self.lens) / self.n) if self.n else 0.0
        self.df: dict[str, int] = {}
        for tf in self.tfs:
            for term in tf:
                self.df[term] = self.df.get(term, 0) + 1

    @staticmethod
    def _tf(doc: list[str]) -> dict[str, int]:
        tf: dict[str, int] = {}
        for term in doc:
            tf[term] = tf.get(term, 0) + 1
        return tf

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))

    def scores(self, query: list[str]) -> list[float]:
        out = []
        q_terms = sorted(set(query))
        for tf, dl in zip(self.tfs, self.lens):
            score = 0.0
            norm = self.k1 * (1.0 - self.b + self.b * (dl / self.avgdl if self.avgdl else 0.0))
            for term in q_terms:
                f = tf.get(term)
                if not f:
                    continue
                score += self.idf(term) * (f * (self.k1 + 1.0)) / (f + norm)
            out.append(score)
        return out


def row_text(row: dict) -> str:
    return " ".join([row.get("intent_text") or ""] + list(row.get("criteria") or []))


# --------------------------------------------------------------------------- arms

def _aggregate(neighbours: list[dict], query_conditions: dict, config: dict) -> tuple[int, int]:
    fields = config.get("neighbour_condition_fields") or ["backend", "task_family"]
    matching = [r for r in neighbours
                if all(r.get(f) == query_conditions.get(f) for f in fields)]
    chosen = matching if len(matching) >= int(config.get("neighbour_condition_min", 2)) else neighbours
    n = len(chosen)
    acc = sum(1 for r in chosen if r["_label"] == "accepted")
    return acc, n


def arm_table(pool: list[dict], query_conditions: dict, config: dict) -> dict:
    for fields in (config.get("table_match") or ["backend", "task_family"],
                   config.get("table_fallback") or ["backend"]):
        if any(query_conditions.get(f) is None for f in fields):
            continue
        matching = [r for r in pool if all(r.get(f) == query_conditions.get(f) for f in fields)]
        if matching:
            acc = sum(1 for r in matching if r["_label"] == "accepted")
            return {"state": "ok", "p_accept": laplace(acc, len(matching)), "n_support": len(matching),
                    "n_accepted": acc, "neighbours": [], "diagnostic": f"table:{'+'.join(fields)}"}
    return {"state": "abstain", "p_accept": None, "n_support": 0, "n_accepted": 0, "neighbours": [],
            "diagnostic": "abstain:no_matching_rows_at_T"}


def arm_bm25(pool: list[dict], query_text: str, query_conditions: dict, config: dict, at: datetime) -> dict:
    params = config.get("bm25") or {}
    engine = BM25([tokenize(row_text(r)) for r in pool],
                  k1=float(params.get("k1", 1.5)), b=float(params.get("b", 0.75)))
    scores = engine.scores(tokenize(query_text))
    k = int(config.get("k", 5))
    order = sorted(range(len(pool)), key=lambda i: (-scores[i], pool[i]["run_id"]))[:k]
    top = [pool[i] for i in order]
    acc, n = _aggregate(top, query_conditions, config)
    neighbours = [{"run_id": pool[i]["run_id"], "score": round(scores[i], 4),
                   "decision_at_T": pool[i]["_label"],
                   "bucket_at_T": next((ev.get("bucket") for ev in reversed(pool[i].get("verdict_events") or [])
                                        if (parse_ts(ev.get("at")) or at) <= at), None)}
                  for i in order]
    return {"state": "ok", "p_accept": laplace(acc, n), "n_support": n, "n_accepted": acc,
            "neighbours": neighbours, "diagnostic": None}


# --------------------------------------------------------------------------- advice

def _request_id(run_id: str, arm: str, corpus_version: str, config_id: str) -> str:
    return "adv_" + sha256_text(f"{run_id}|{arm}|{corpus_version}|{config_id}")[:12]


def _advice_row(run_id: str, arm: str, mode: str, at: Optional[datetime], generated_at: datetime,
                corpus_version: str, config_id: str, state: str, diagnostic: Optional[str],
                conditions: dict, result: Optional[dict], pool: Optional[list[dict]], latency_ms: float) -> dict:
    result = result or {}
    return {
        "schema": ADVICE_SCHEMA,
        "request_id": _request_id(run_id, arm, corpus_version, config_id),
        "run_id": run_id,
        "arm": arm,
        "mode": mode,
        "view": "operational",
        "as_of": iso_z(at) if at else None,
        "generated_at": iso_z(generated_at),
        "corpus_version": corpus_version,
        "config_id": config_id,
        "state": state,
        "diagnostic": diagnostic,
        "conditions": {k: conditions.get(k) for k in ("backend", "task_family", "serving_profile_sha")},
        "p_accept": result.get("p_accept") if state == "ok" else None,
        "n_support": int(result.get("n_support") or 0),
        "n_accepted": int(result.get("n_accepted") or 0),
        "neighbours": list(result.get("neighbours") or []),
        "candidate_set_sha256": candidate_set_sha256(pool) if pool is not None else None,
        "latency_ms": round(latency_ms, 2),
        "displayed": None,
    }


def _join_diag(*parts: Optional[str]) -> Optional[str]:
    kept = [p for p in parts if p]
    return "; ".join(kept) if kept else None


def advise_run(info: dict, index: Optional[dict], index_diag: Optional[str], *, mode: str,
               now: datetime, stale_reason: Optional[str]) -> list[dict]:
    """Advice rows for one run: one per arm, never raises."""
    t0 = time.perf_counter()
    run_id = info["run_id"]
    at = parse_ts(info.get("dispatched_at")) or parse_ts(info.get("received_at"))
    late = LATE_DIAGNOSTIC if info.get("first_final_at") else None
    corpus_version = (index or {}).get("corpus_version") or "none"
    config = (index or {}).get("config") or DEFAULT_CONFIG
    config_id = (index or {}).get("config_id") or config_id_of(config)
    conditions = {k: info.get(k) for k in ("backend", "task_family", "serving_profile_sha")}
    arms = list(config.get("arms") or ARMS)
    budget_ms = float(config.get("timeout_ms", 200))

    def rows_with(state: str, diagnostic: Optional[str], results: Optional[dict] = None,
                  pool: Optional[list[dict]] = None) -> list[dict]:
        latency = (time.perf_counter() - t0) * 1000
        return [_advice_row(run_id, arm, mode, at, now, corpus_version, config_id, state,
                            diagnostic, conditions, (results or {}).get(arm), pool, latency)
                for arm in arms]

    try:
        if index is None:
            return rows_with("unavailable", _join_diag(index_diag, late))
        if at is None:
            return rows_with("unavailable", _join_diag("no dispatched_at or received_at", late))
        pool = eligible_rows(index["rows"], run_id, at, config)
        if len(pool) < int(config.get("min_pool", 3)):
            return rows_with("abstain", _join_diag(f"abstain:pool={len(pool)}<{config.get('min_pool', 3)}", late, stale_reason), None, pool)
        results = {}
        for arm in arms:
            if arm == "table":
                results[arm] = arm_table(pool, conditions, config)
            elif arm == "bm25":
                results[arm] = arm_bm25(pool, row_text(info), conditions, config, at)
            else:
                results[arm] = {"state": "unavailable", "diagnostic": f"arm not in this increment: {arm}"}
        elapsed = (time.perf_counter() - t0) * 1000
        if elapsed > budget_ms:
            return rows_with("timeout", _join_diag(f"timeout:{elapsed:.1f}ms>{budget_ms:.0f}ms", late, stale_reason), results, pool)
        out = []
        for arm in arms:
            res = results[arm]
            state = res.get("state", "ok")
            diagnostic = _join_diag(res.get("diagnostic") if state != "ok" else None, late)
            if state == "ok" and stale_reason:
                state, diagnostic = "stale", _join_diag(stale_reason, late)
            if state == "ok" and res.get("diagnostic"):
                diagnostic = _join_diag(res.get("diagnostic"), late)
            out.append(_advice_row(run_id, arm, mode, at, now, corpus_version, config_id, state,
                                   diagnostic, conditions, res, pool, (time.perf_counter() - t0) * 1000))
        return out
    except Exception as exc:  # noqa: BLE001 -- an advisor fault is a signal, never a failure
        diag = f"{type(exc).__name__}: {str(exc)[:200]}"
        return rows_with("unavailable", _join_diag(diag, late))


def stale_reason_for(index: dict, newest_final_in_runs: Optional[str], now: datetime) -> Optional[str]:
    built = parse_ts(index.get("built_at"))
    if built is None:
        return "stale:index_built_at_unparseable"
    if now - built > STALE_AFTER:
        return f"stale:index_age>{int(STALE_AFTER.total_seconds() // 3600)}h (built_at={index.get('built_at')})"
    watermark = index.get("evidence_watermark")
    if newest_final_in_runs and (watermark is None or newest_final_in_runs > watermark):
        return f"stale:newest_final {newest_final_in_runs} not in index (watermark={watermark})"
    return None


def advise(runs_root: Path, out_dir: Path, *, mode: str, now: Optional[datetime] = None) -> dict:
    """Sweep: advice for every run with an envelope and a dispatch/receive event but no advice."""
    now = now or utc_now()
    index, index_diag = load_index(out_dir)
    infos: list[dict] = []
    errors: list[dict] = []
    for path in work_dirs(runs_root):
        try:
            info = read_work_dir(path)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            continue
        if info is not None:
            infos.append(info)
    finals = [i["first_final_at"] for i in infos if i.get("first_final_at")]
    stale_reason = stale_reason_for(index, max(finals) if finals else None, now) if index else None

    counts = {"swept": len(infos), "advised": 0, "skipped_has_advice": 0, "skipped_no_event": 0,
              "late": 0, "by_state": {}, "errors": errors, "stale": bool(stale_reason), "latency_ms": []}
    for info in infos:
        if info["has_advice"]:
            counts["skipped_has_advice"] += 1
            continue
        if not (info.get("dispatched_at") or info.get("received_at")):
            counts["skipped_no_event"] += 1
            continue
        rows = advise_run(info, index, index_diag, mode=mode, now=now, stale_reason=stale_reason)
        target = Path(info["path"]) / ADVICE_DIR / ADVICE_FILE
        try:
            atomic_write_json(target, rows)
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(target), "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            continue
        counts["advised"] += 1
        if any((r.get("diagnostic") or "").find(LATE_DIAGNOSTIC) >= 0 for r in rows):
            counts["late"] += 1
        for r in rows:
            counts["by_state"][r["state"]] = counts["by_state"].get(r["state"], 0) + 1
        counts["latency_ms"].append(max(r["latency_ms"] for r in rows))
    lat = sorted(counts.pop("latency_ms"))
    counts["latency_ms_p50"] = lat[len(lat) // 2] if lat else None
    counts["latency_ms_p95"] = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None
    counts["latency_ms_max"] = lat[-1] if lat else None
    return counts


# --------------------------------------------------------------------------- join

def _periods(corpus_manifest: dict) -> list[tuple[str, Optional[datetime], Optional[datetime]]]:
    out = []
    for name, span in (corpus_manifest.get("periods") or {}).items():
        if isinstance(span, dict):
            out.append((name, parse_ts(span.get("from")), parse_ts(span.get("to"))))
    return out


def _period_of(at: Optional[datetime], periods) -> str:
    if at is not None:
        for name, start, end in periods:
            if (start is None or at >= start) and (end is None or at <= end):
                return name
    return "live"


def _metrics(rows: list[dict]) -> dict:
    """rows: joined advice with state ok and a binary label."""
    n = len(rows)
    if n == 0:
        return {"n": 0, "brier": None, "balanced_accuracy": None, "accuracy": None, "prevalence": None}
    brier = sum((r["p"] - (1.0 if r["label"] else 0.0)) ** 2 for r in rows) / n
    pos = [r for r in rows if r["label"]]
    neg = [r for r in rows if not r["label"]]
    tpr = (sum(1 for r in pos if r["p"] >= 0.5) / len(pos)) if pos else None
    tnr = (sum(1 for r in neg if r["p"] < 0.5) / len(neg)) if neg else None
    balanced = None if tpr is None or tnr is None else (tpr + tnr) / 2
    accuracy = sum(1 for r in rows if (r["p"] >= 0.5) == r["label"]) / n
    return {"n": n, "brier": round(brier, 4), "balanced_accuracy": None if balanced is None else round(balanced, 4),
            "accuracy": round(accuracy, 4), "prevalence": round(len(pos) / n, 4)}


def join(runs_root: Path, out_dir: Path, corpus_path: Path, *, now: Optional[datetime] = None) -> dict:
    now = now or utc_now()
    index, _ = load_index(out_dir)
    _, corpus_manifest, _ = load_corpus(corpus_path)
    periods = _periods(corpus_manifest)

    advice_count = 0
    files = 0
    joined = 0
    late_excluded = 0
    not_yet = 0
    nonbinary = 0
    unreadable = 0
    per: dict[str, dict[str, dict]] = {}
    watermark = None

    for path in work_dirs(runs_root):
        advice_path = path / ADVICE_DIR / ADVICE_FILE
        if not advice_path.is_file():
            continue
        files += 1
        try:
            rows = read_json(advice_path)
            info = read_work_dir(path)
        except Exception:  # noqa: BLE001
            unreadable += 1
            continue
        if not isinstance(rows, list) or info is None:
            unreadable += 1
            continue
        final_at = parse_ts(info.get("first_final_at"))
        decision = info.get("first_final_decision")
        for row in rows:
            if not isinstance(row, dict) or row.get("schema") != ADVICE_SCHEMA:
                continue
            advice_count += 1
            if LATE_DIAGNOSTIC in (row.get("diagnostic") or ""):
                late_excluded += 1
                continue
            generated = parse_ts(row.get("generated_at"))
            if final_at is None or generated is None or final_at <= generated:
                not_yet += 1
                continue
            if decision not in BINARY_DECISIONS:
                nonbinary += 1
                continue
            joined += 1
            watermark = max(watermark or "", info["first_final_at"])
            arm = row.get("arm") or "?"
            period = _period_of(parse_ts(row.get("as_of")), periods)
            bucket = per.setdefault(arm, {}).setdefault(period, {"joined": 0, "by_state": {}, "ok_rows": []})
            bucket["joined"] += 1
            state = row.get("state") or "?"
            bucket["by_state"][state] = bucket["by_state"].get(state, 0) + 1
            if state == "ok" and isinstance(row.get("p_accept"), (int, float)):
                bucket["ok_rows"].append({"p": float(row["p_accept"]), "label": BINARY_DECISIONS[decision]})

    arms_out: dict[str, dict[str, dict]] = {}
    for arm, by_period in per.items():
        arms_out[arm] = {}
        all_rows: list[dict] = []
        total = 0
        for period, bucket in by_period.items():
            metrics = _metrics(bucket["ok_rows"])
            metrics["coverage"] = round(len(bucket["ok_rows"]) / bucket["joined"], 4) if bucket["joined"] else None
            metrics["joined"] = bucket["joined"]
            metrics["by_state"] = bucket["by_state"]
            arms_out[arm][period] = metrics
            all_rows.extend(bucket["ok_rows"])
            total += bucket["joined"]
        overall = _metrics(all_rows)
        overall["coverage"] = round(len(all_rows) / total, 4) if total else None
        overall["joined"] = total
        arms_out[arm]["all"] = overall

    doc = {
        "schema": ACCURACY_SCHEMA,
        "contract_version": ACCURACY_SCHEMA,
        "built_at": iso_z(now),
        "corpus_version": (index or {}).get("corpus_version") or corpus_manifest.get("corpus_version") or "none",
        "config_id": (index or {}).get("config_id"),
        "index_built_at": (index or {}).get("built_at"),
        "advice_files": files,
        "advice_count": advice_count,
        "joined_count": joined,
        "late_excluded": late_excluded,
        "awaiting_verdict": not_yet,
        "nonbinary_excluded": nonbinary,
        "unreadable": unreadable,
        "evidence_watermark": watermark,
        "arms": arms_out,
        "note": "advisory record only; no verdict is recorded and no routing decision is read or changed (ADR-0070)",
    }
    write_knowledge(Path(out_dir) / ACCURACY_FILE, doc, "advice_count")
    return doc


__all__ = ["INDEX_SCHEMA", "ADVICE_SCHEMA", "ACCURACY_SCHEMA", "INDEX_FILE", "ACCURACY_FILE", "ADVICE_DIR",
           "ADVICE_FILE", "LATE_DIAGNOSTIC", "DEFAULT_CONFIG", "BM25", "tokenize", "label_at", "eligible_rows",
           "arm_table", "arm_bm25", "build_index", "load_index", "advise", "advise_run", "join", "read_work_dir",
           "atomic_write_json", "config_id_of", "parse_ts", "iso_z", "traceback"]
