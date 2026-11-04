#!/usr/bin/env python3
"""Shared Read-Only Data Contract for `flow-top` (`tools/flow_top/contract.py`).

Answers three questions at a glance:
  1. What is available?
  2. What work is moving?
  3. Did that work use the intended components?

Invariants:
  - Every panel carries its `source` and `obs_age_s` (observation age in seconds).
  - Status badges strictly distinguish:
      `idle`, `active`, `stale`, `unavailable`, `unknown`, and `not_used_by_job`
    (rendered as `not used by this job`).
  - Lifetime/engine-uptime cache totals remain strictly separated from current-run
    (selected-job) measurements.
  - Queue depth measures backlog; it is never labeled as throughput.
  - DuckDB row-count differences are reported strictly as net row change; updates and
    deletions are reported as `unknown` without dedicated CDC/WAL evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

FLOW_SNAPSHOT_CACHE_PATH = Path("/dev/shm/flow-top-snapshot.json")

VALID_PANEL_STATES = (
    "active",
    "idle",
    "stale",
    "unavailable",
    "unknown",
    "not_used_by_job",
)

VALID_FLOW_VIEWS = ("overview", "path", "services", "caches", "data")

DEFAULT_ONTOLOGY_TERM_ID = "09bc5669-d337-4002-8727-7791e5673989"
DEFAULT_ONTOLOGY_ATTR_ID = "43a30643-971c-41ce-baee-8d7955ea40cb"
DEFAULT_ONTOLOGY_UI_URL = (
    f"http://127.0.0.1:13000/terms?focus={DEFAULT_ONTOLOGY_TERM_ID}"
    f"&colAttr={DEFAULT_ONTOLOGY_ATTR_ID}"
)


def format_state_label(state: str) -> str:
    """Canonical human-readable display string for the 6 required states."""
    s = (state or "unknown").strip().lower()
    if s == "not_used_by_job":
        return "not used by this job"
    if s in ("active", "idle", "stale", "unavailable", "unknown"):
        return s
    return s


@dataclass
class ServiceAvailability:
    """Single service row for 'What is available?'."""

    service_id: str
    name: str
    host_endpoint: str
    state: str = "idle"  # active | idle | stale | unavailable | unknown
    detail: str = ""
    source: str = ""
    obs_age_s: Optional[float] = 0.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ServiceAvailability":
        return cls(
            service_id=str(d.get("service_id", "")),
            name=str(d.get("name", "")),
            host_endpoint=str(d.get("host_endpoint", "")),
            state=str(d.get("state", "unknown")),
            detail=str(d.get("detail", "")),
            source=str(d.get("source", "")),
            obs_age_s=d.get("obs_age_s"),
        )


@dataclass
class PathHop:
    """Single hop in the observed path for a selected task."""

    hop_id: str
    label: str
    state: str  # active | idle | stale | unavailable | unknown | not_used_by_job
    used_by_job: Optional[bool] = None  # True=used, False=not used by this job, None=unknown
    summary: str = ""
    evidence_link: str = ""
    timestamp_utc: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PathHop":
        return cls(
            hop_id=str(d.get("hop_id", "")),
            label=str(d.get("label", "")),
            state=str(d.get("state", "unknown")),
            used_by_job=d.get("used_by_job"),
            summary=str(d.get("summary", "")),
            evidence_link=str(d.get("evidence_link", "")),
            timestamp_utc=str(d.get("timestamp_utc", "")),
        )


@dataclass
class CorrelatedTask:
    """A correlated task across BH2/Orland, Service Bus, HEARTH, Steppewise/MAF, Ontology, and Washplant."""

    task_id: str
    title: str
    actor: str
    host: str
    stage: str
    state: str  # active | idle | stale | unavailable | unknown
    elapsed_s: Optional[float] = None
    last_progress: str = ""
    last_progress_utc: str = ""
    age_s: Optional[float] = 0.0
    outcome: str = ""
    source_receipt_path: str = ""
    run_dir: str = ""

    # Links & correlation identifiers
    aspire_trace_id: str = ""
    aspire_trace_url: str = ""
    otel_spans_path: str = ""
    otel_span_count: int = 0
    otel_exported: Optional[bool] = None
    otel_error_count: int = 0

    # Service Bus job-level evidence
    broker_job_state: str = "not_used_by_job"  # active | idle | not_used_by_job | unknown | unavailable
    broker_queue: str = ""
    broker_message_id: str = ""
    broker_sequence_number: Optional[int] = None
    broker_delivery_count: Optional[int] = None
    broker_send_at: str = ""
    broker_recv_at: str = ""
    broker_settle_at: str = ""
    broker_disposition: str = ""
    broker_receipt_path: str = ""
    broker_explorer_url: str = ""

    # HEARTH / Steppewise / MAF job-level evidence
    hearth_job_state: str = "not_used_by_job"
    hearth_job_id: str = ""
    hearth_invocation_id: str = ""
    hearth_provider: str = ""
    hearth_model: str = ""
    hearth_status: str = ""
    hearth_stages: List[str] = field(default_factory=list)
    hearth_app_retries: int = 0
    hearth_emit_repairs: int = 0
    hearth_emit_trigger: str = ""
    hearth_delivery_repairs: int = 0
    hearth_delivery_repair_note: str = ""
    hearth_execution_path: str = ""
    steppewise_state: str = "not_used_by_job"
    steppewise_workflow: str = ""
    steppewise_run_id: str = ""
    steppewise_status: str = ""
    steppewise_steps: List[str] = field(default_factory=list)
    steppewise_events_path: str = ""

    # Router / Context Packing job-level evidence
    router_job_state: str = "not_used_by_job"
    router_receipt_path: str = ""
    selected_route: str = ""
    task_family: str = ""
    regime_summary: str = ""
    pool_config_hash: str = ""
    pack_size_bytes: Optional[int] = None
    pack_sha256: str = ""
    packed_files_count: int = 0
    jepa_job_state: str = "not_used_by_job"  # active | idle | not_used_by_job | unknown
    jepa_job_note: str = "not used by this job (desired.packed_files=[], HEARTH_SIZER off)"
    omissions_or_fallback: str = "none"

    # Tokens & Caches job-level evidence (strictly current-run)
    tokens_job_state: str = "not_used_by_job"
    model_calls: int = 0
    tokens_in_total: Optional[int] = None
    tokens_out_total: Optional[int] = None
    tokens_reasoning_total: Optional[int] = None
    turn1_tokens_in: Optional[int] = None
    turn1_tokens_out: Optional[int] = None
    turn1_tokens_reasoning: Optional[int] = None
    turn1_duration_ms: Optional[int] = None
    turn1_finish_reason: str = ""
    emit_tokens_in: Optional[int] = None
    emit_tokens_out: Optional[int] = None
    emit_duration_ms: Optional[int] = None
    emit_finish_reason: str = ""
    cached_input_tokens: Optional[int] = None  # None when unknown per job
    cached_input_tokens_state: str = "unknown"
    cached_input_tokens_note: str = (
        "unknown (explicit gap: vLLM prompt_tokens_details.cached_tokens dropped by hearth/toolsurface/inference.py)"
    )
    ast_cache_job_state: str = "not_used_by_job"
    ast_cache_job_note: str = "not used by this job (0 AST arena lookups attributed to job)"
    text_cache_job_state: str = "not_used_by_job"
    text_cache_job_note: str = "not used by this job"

    # Ontology job-level evidence
    ontology_job_state: str = "not_used_by_job"
    ontology_entry_url: str = DEFAULT_ONTOLOGY_UI_URL
    ontology_term_id: str = ""
    ontology_term_name: str = ""
    ontology_attr_id: str = ""
    ontology_attr_name: str = ""
    ontology_definition_sha256: str = ""
    ontology_lookup_ms: Optional[float] = None
    ontology_receipt_path: str = ""
    ontology_job_note: str = "not used by this job"

    # DuckDB job-level evidence
    duckdb_job_state: str = "not_used_by_job"
    duckdb_job_note: str = "not used by this job (BH2/Orland uses Service Bus + HEARTH + JSONL receipts)"

    # Result artifacts
    helper_artifact_id: str = ""
    helper_artifact_sha256: str = ""
    helper_artifact_path: str = ""
    parent_artifact_path: str = ""
    parent_artifact_sha256: str = ""
    check_artifact_path: str = ""
    preview_url: str = ""
    observed_hops: List[PathHop] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CorrelatedTask":
        hops = [PathHop.from_dict(x) for x in (d.get("observed_hops") or [])]
        copy_d = dict(d)
        copy_d["observed_hops"] = hops
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in copy_d.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class QueueDepthRow:
    """Single queue or topic row from Service Bus Explorer (depth = backlog, not throughput)."""

    entity_name: str
    entity_type: str = "queue"  # queue | topic
    status: str = "active"
    active_messages: int = 0
    dead_letter_messages: int = 0
    scheduled_messages: int = 0
    lock_duration: str = ""
    max_delivery_count: int = 0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "QueueDepthRow":
        return cls(
            entity_name=str(d.get("entity_name", "")),
            entity_type=str(d.get("entity_type", "queue")),
            status=str(d.get("status", "active")),
            active_messages=int(d.get("active_messages", 0)),
            dead_letter_messages=int(d.get("dead_letter_messages", 0)),
            scheduled_messages=int(d.get("scheduled_messages", 0)),
            lock_duration=str(d.get("lock_duration", "")),
            max_delivery_count=int(d.get("max_delivery_count", 0)),
        )


@dataclass
class ServiceBusPanel:
    """Service Bus backlog, dead letters, and broker receipt activity."""

    state: str = "idle"  # active | idle | stale | unavailable | unknown
    source: str = "Explorer http://127.0.0.1:18080/api/{queues,topics} + BROKER-RECEIPTS.jsonl"
    obs_age_s: float = 0.0
    explorer_online: bool = False
    emulator_amqp_online: bool = False
    read_only_enforced: bool = True
    queues: List[QueueDepthRow] = field(default_factory=list)
    total_queued_backlog: int = 0
    total_dead_letters: int = 0
    recent_send_count: int = 0
    recent_receive_count: int = 0
    recent_settle_count: int = 0
    unsettled_in_flight: int = 0
    last_receipt_summary: str = ""
    error_note: str = ""

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ServiceBusPanel":
        if not isinstance(d, dict):
            return cls(state="unavailable")
        return cls(
            state=str(d.get("state", "unknown")),
            source=str(d.get("source", "")),
            obs_age_s=float(d.get("obs_age_s", 0.0)),
            explorer_online=bool(d.get("explorer_online", False)),
            emulator_amqp_online=bool(d.get("emulator_amqp_online", False)),
            read_only_enforced=bool(d.get("read_only_enforced", True)),
            queues=[QueueDepthRow.from_dict(x) for x in (d.get("queues") or [])],
            total_queued_backlog=int(d.get("total_queued_backlog", 0)),
            total_dead_letters=int(d.get("total_dead_letters", 0)),
            recent_send_count=int(d.get("recent_send_count", 0)),
            recent_receive_count=int(d.get("recent_receive_count", 0)),
            recent_settle_count=int(d.get("recent_settle_count", 0)),
            unsettled_in_flight=int(d.get("unsettled_in_flight", 0)),
            last_receipt_summary=str(d.get("last_receipt_summary", "")),
            error_note=str(d.get("error_note", "")),
        )


@dataclass
class OntologyPanel:
    """Ontology catalog availability on AM4 (via OMEN :13000), last lookup, term & review status."""

    state: str = "idle"  # active | idle | stale | unavailable | unknown
    source: str = "http://127.0.0.1:13000/api/health (-> AM4 10.44.0.2:13000) + LOOKUP-MEASURE.json"
    obs_age_s: float = 0.0
    omen_proxy_online: bool = False
    am4_backend_online: bool = False
    postgres_status: str = "unknown"
    placement_summary: str = "OMEN 127.0.0.1:13000 socat -> AM4 10.44.0.2:13000 (eno2->enp5s0 direct Ethernet)"
    catalog_ui_url: str = DEFAULT_ONTOLOGY_UI_URL
    total_terms: int = 3
    total_column_attrs: int = 16
    selected_term_id: str = DEFAULT_ONTOLOGY_TERM_ID
    selected_term_name: str = "Solicitation"
    selected_term_cert: str = "partial (name+desc certified)"
    selected_attr_id: str = DEFAULT_ONTOLOGY_ATTR_ID
    selected_attr_name: str = "Budget Label"
    selected_attr_sample: str = "Est. $45,000–$110,000"
    selected_attr_certified: bool = True
    certified_attrs_ratio: str = "1/8 colAttrs certified (buyer_verified=false)"
    review_source: str = "Auto-Ontology caced11e COMPILE-1 over snapshot seed-38d40fcde7bd83c3"
    reviewed_by: str = "wash (against data/domain_seed.json)"
    last_lookup_utc: str = ""
    last_lookup_ms: Optional[float] = None
    last_lookup_command_s: Optional[float] = None
    last_lookup_execution: str = ""
    last_lookup_sha256: str = ""
    last_lookup_path: str = ""
    error_note: str = ""

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "OntologyPanel":
        if not isinstance(d, dict):
            return cls(state="unavailable")
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})


@dataclass
class DuckDBPanel:
    """DuckDB intake hosting, writer process, last commit, backlog, and net row change counters."""

    state: str = "idle"  # active | idle | stale | unavailable | unknown
    source: str = "snapshot-manifest.json + washplant/deliveries.jsonl + skeleton/verify.json + /proc fd scan"
    obs_age_s: float = 0.0
    hosting_mode: str = (
        "Embedded .duckdb + immutable 0444 snapshots (snapshot-first read; avoids cross-process writer lock)"
    )
    active_writer_pid: Optional[int] = None
    active_writer_cmd: str = "none (Washplant.Host CLI per-op idle; skeleton/owner.py :18731 stopped)"
    last_commit_utc: str = ""
    last_commit_summary: str = ""
    washplant_db_path: str = "/home/derek/work/washplant/hopper.duckdb"
    washplant_snapshot_path: str = (
        "/home/derek/work/washplant/infra/auto-ontology/data/snapshots/"
        "hopper-07efb951b03861d71067b862b0f64964a2892a9b5117ecee40eb7bf2e206cdf7.duckdb"
    )
    seed_snapshot_path: str = (
        "/home/derek/work/washplant/infra/auto-ontology/data/snapshots/seed-38d40fcde7bd83c3.duckdb"
    )
    backlog_pending_dispatches: int = 0
    backlog_pending_outbox: int = 0
    backlog_nonterminal_tasks: int = 0
    washplant_table_counts: Dict[str, int] = field(default_factory=dict)
    snapshot_table_counts: Dict[str, int] = field(default_factory=dict)
    seed_table_counts: Dict[str, int] = field(default_factory=dict)
    net_row_change_vs_snapshot: Dict[str, int] = field(default_factory=dict)
    net_row_change_total: int = 0
    updates_deletions_status: str = "unknown (net row change only; row updates/deletions require WAL/CDC evidence)"
    error_note: str = ""

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "DuckDBPanel":
        if not isinstance(d, dict):
            return cls(state="unavailable")
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})


@dataclass
class LifetimeCacheSummary:
    """Global/engine-uptime cache metrics strictly separated from current-run job measurements."""

    state: str = "idle"
    source: str = "/dev/shm/fleet-top-snapshot.json (:18091/:18092/:18094 /metrics + :18097/health)"
    obs_age_s: float = 0.0
    kv_caches_summary: List[str] = field(default_factory=list)
    npu_ast_online: bool = False
    npu_ast_summary: str = ""
    sol_arena_summary: str = ""

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "LifetimeCacheSummary":
        if not isinstance(d, dict):
            return cls(state="unavailable")
        return cls(
            state=str(d.get("state", "unknown")),
            source=str(d.get("source", "")),
            obs_age_s=float(d.get("obs_age_s", 0.0)),
            kv_caches_summary=[str(x) for x in (d.get("kv_caches_summary") or [])],
            npu_ast_online=bool(d.get("npu_ast_online", False)),
            npu_ast_summary=str(d.get("npu_ast_summary", "")),
            sol_arena_summary=str(d.get("sol_arena_summary", "")),
        )


@dataclass
class MonitorResourceUsage:
    """Self-monitoring of `flow-top` CPU, RSS RAM, poll latency, and incremental cache hits."""

    pid: int = 0
    rss_mib: float = 0.0
    cpu_pct: float = 0.0
    cpu_time_s: float = 0.0
    poll_duration_ms: float = 0.0
    incremental_cache_hits: int = 0
    incremental_cache_misses: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "MonitorResourceUsage":
        if not isinstance(d, dict):
            return cls()
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})


@dataclass
class FlowSnapshot:
    """Complete read-only snapshot powering all `flow-top` panels and views."""

    schema: str = "flow_top_snapshot.v1"
    collected_at_utc: str = ""
    collected_at_unix: float = 0.0
    overall_state: str = "idle"  # active | idle | stale | unavailable
    headline_available: str = ""
    headline_moving: str = ""
    headline_components: str = ""
    selected_task_index: int = 0
    services: List[ServiceAvailability] = field(default_factory=list)
    tasks: List[CorrelatedTask] = field(default_factory=list)
    service_bus: ServiceBusPanel = field(default_factory=ServiceBusPanel)
    ontology: OntologyPanel = field(default_factory=OntologyPanel)
    duckdb: DuckDBPanel = field(default_factory=DuckDBPanel)
    lifetime_caches: LifetimeCacheSummary = field(default_factory=LifetimeCacheSummary)
    monitor_usage: MonitorResourceUsage = field(default_factory=MonitorResourceUsage)
    simulated_unavailable: List[str] = field(default_factory=list)

    @property
    def selected_task(self) -> Optional[CorrelatedTask]:
        if not self.tasks:
            return None
        idx = max(0, min(self.selected_task_index, len(self.tasks) - 1))
        return self.tasks[idx]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FlowSnapshot":
        return cls(
            schema=str(d.get("schema", "flow_top_snapshot.v1")),
            collected_at_utc=str(d.get("collected_at_utc", "")),
            collected_at_unix=float(d.get("collected_at_unix", 0.0)),
            overall_state=str(d.get("overall_state", "idle")),
            headline_available=str(d.get("headline_available", "")),
            headline_moving=str(d.get("headline_moving", "")),
            headline_components=str(d.get("headline_components", "")),
            selected_task_index=int(d.get("selected_task_index", 0)),
            services=[ServiceAvailability.from_dict(x) for x in (d.get("services") or [])],
            tasks=[CorrelatedTask.from_dict(x) for x in (d.get("tasks") or [])],
            service_bus=ServiceBusPanel.from_dict(d.get("service_bus")),
            ontology=OntologyPanel.from_dict(d.get("ontology")),
            duckdb=DuckDBPanel.from_dict(d.get("duckdb")),
            lifetime_caches=LifetimeCacheSummary.from_dict(d.get("lifetime_caches")),
            monitor_usage=MonitorResourceUsage.from_dict(d.get("monitor_usage")),
            simulated_unavailable=[str(x) for x in (d.get("simulated_unavailable") or [])],
        )


def save_flow_snapshot_cache(
    snapshot: FlowSnapshot, path: Path = FLOW_SNAPSHOT_CACHE_PATH
) -> None:
    try:
        tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
        tmp_path.write_text(snapshot.to_json(), encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        pass


def load_flow_snapshot_cache(
    path: Path = FLOW_SNAPSHOT_CACHE_PATH, max_age_s: float = 2.0
) -> Optional[FlowSnapshot]:
    try:
        if not path.exists():
            return None
        st = path.stat()
        age = time.time() - st.st_mtime
        if age > max_age_s:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return FlowSnapshot.from_dict(data)
    except Exception:
        return None
