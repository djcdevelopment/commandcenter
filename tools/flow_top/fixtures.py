#!/usr/bin/env python3
"""Deterministic Offline Fixtures for `flow-top` (`tools/flow_top/fixtures.py`).

Provides three canonical fixtures:
  - `orland_live`: Active in-flight Orland task + 9 completed correlated tasks.
  - `orland_completed`: Completed `help-orland-throughput-20261104` + `help-ontology-20261104` + 8 prior tasks.
  - `degraded_collector`: Demonstrates `service_bus` and `ontology` collectors unavailable
    (`state="unavailable"`) while all other panels remain `"idle"`/`"active"` and updating.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Callable, Dict, List, Optional

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
)


def _base_services(
    sb_state: str = "idle",
    onto_state: str = "idle",
    hearth_state: str = "idle",
) -> List[ServiceAvailability]:
    return [
        ServiceAvailability(
            service_id="service_bus",
            name="Service Bus Emulator + Explorer",
            host_endpoint="OMEN :5672 (AMQP) / :18080 (Explorer GET-only)",
            state=sb_state,
            detail=(
                "Collector unavailable (simulated fault or endpoint unreachable)"
                if sb_state == "unavailable"
                else (
                    "queues=2 topics=1 backlog=1 dlq=0 sent/recv/settled=7/8/6"
                    if sb_state == "active"
                    else "queues=2 topics=1 backlog=0 dlq=0 sent/recv/settled=6/7/6"
                )
            ),
            source="Explorer http://127.0.0.1:18080/api/{queues,topics} + BROKER-RECEIPTS.jsonl",
            obs_age_s=0.0,
        ),
        ServiceAvailability(
            service_id="ontology",
            name="Ontology Catalog (OMEN -> AM4)",
            host_endpoint="OMEN 127.0.0.1:13000 -> AM4 10.44.0.2:13000",
            state=onto_state,
            detail=(
                "Collector unavailable (simulated fault or endpoint unreachable)"
                if onto_state == "unavailable"
                else "AM4 postgres=ok; 3 terms, 16 colAttrs; Solicitation/Budget Label (194.4ms)"
            ),
            source="http://127.0.0.1:13000/api/health (-> AM4 10.44.0.2:13000) + LOOKUP-MEASURE.json",
            obs_age_s=0.0,
        ),
        ServiceAvailability(
            service_id="hearth",
            name="HEARTH Door + 27B Serving Pool",
            host_endpoint="OMEN :8710 / B70 :18091,:18092 / AM4 :18094",
            state=hearth_state,
            detail=(
                "three-dense 27B pool (:18091, :18092, AM4:18094) online; active_jobs=1"
                if hearth_state == "active"
                else "three-dense 27B pool (:18091, :18092, AM4:18094) online; active_jobs=0"
            ),
            source="http://127.0.0.1:8710/health + /dev/shm/fleet-top-snapshot.json",
            obs_age_s=0.0,
        ),
        ServiceAvailability(
            service_id="lifetime_caches",
            name="JEPA NPU Router & Lifetime Caches",
            host_endpoint="OMEN :18097 (Intel NPU) + /dev/shm arenas",
            state="idle",
            detail=":18097 NPU AST Arena (mechsuit-shm-ast-arena.v3): 43/45 hits (95.6%, daemon uptime), 19 shm objects (4.73 MiB); SOL: unavailable / unmounted (/dev/shm/mechsuit-sol-arena present=False)",
            source="/dev/shm/fleet-top-snapshot.json (:18091/:18092/:18094 /metrics + :18097/health)",
            obs_age_s=0.5,
        ),
        ServiceAvailability(
            service_id="duckdb",
            name="DuckDB Intake (Washplant + Seed)",
            host_endpoint="OMEN embedded .duckdb + 0444 snapshots",
            state="idle",
            detail="writer=none; hopper={'items': 1, 'dispatches': 3, 'deliveries': 3, 'outcomes': 3}; net_change=+0; last_commit=2026-11-04T18:12:22Z",
            source="snapshot-manifest.json + washplant/deliveries.jsonl + skeleton/verify.json + /proc fd scan",
            obs_age_s=0.0,
        ),
        ServiceAvailability(
            service_id="aspire",
            name="Aspire Trace UI + Passive IRC Receipts",
            host_endpoint="OMEN 127.0.0.1:18888 (OTLP :4317) + IRC-RECEIPTS.jsonl",
            state="idle",
            detail="Aspire UI :18888 online, OTLP :4317/:4318; IRC passive receipts only (latest trace=8749b272e9492e1c)",
            source="TCP :18888 probe + OTEL-SPANS.jsonl + IRC-RECEIPTS.jsonl (zero IRC socket connects)",
            obs_age_s=0.0,
        ),
    ]


def _make_completed_tasks() -> List[CorrelatedTask]:
    """Return the 10 completed correlated tasks across BH2/Orland, Ontology, and Washplant."""
    t_throughput = CorrelatedTask(
        task_id="help-orland-throughput-20261104",
        title="Orland 3-feature batch (copy link, filtered CSV, filtered JSON) via Service Bus & HEARTH",
        actor="cx-adhoc -> bh2-help-worker",
        host="omen-linux",
        stage="parent.consumed / verified",
        state="idle",
        elapsed_s=755.0,
        last_progress="parent.consumed / verified | 2 calls (9094 in / 7482 out tok)",
        last_progress_utc="2026-11-04T23:15:44Z",
        age_s=120.0,
        outcome="Orland 7f125b3: copy view and filtered CSV/JSON implemented; native clipboard, exports, empty results, mobile and 18 existing checks passed. Helper copy/JSON accepted; CSV repaired. 2 calls 9094 input/7482 output, zero Derek interventions.",
        source_receipt_path="/home/derek/work/bh2/ops1/help-orland-throughput-20261104/RESULT.json",
        run_dir="/home/derek/work/bh2/ops1/help-orland-throughput-20261104",
        aspire_trace_id="8749b272e9492e1c58cfd76ee461782b",
        aspire_trace_url="http://127.0.0.1:18888/traces/detail/8749b272e9492e1c58cfd76ee461782b",
        otel_spans_path="/home/derek/work/bh2/ops1/help-orland-throughput-20261104/OTEL-SPANS.jsonl",
        otel_span_count=29,
        otel_exported=True,
        otel_error_count=5,
        broker_job_state="idle",
        broker_queue="bh2-orland-help-20261104",
        broker_message_id="help-orland-throughput-20261104",
        broker_sequence_number=3,
        broker_delivery_count=1,
        broker_send_at="2026-11-04T23:05:06Z",
        broker_recv_at="2026-11-04T23:05:07Z",
        broker_settle_at="2026-11-04T23:11:26Z",
        broker_disposition="completed (recovery)",
        broker_receipt_path="/home/derek/work/bh2/ops1/help-orland-throughput-20261104/BROKER-RECEIPTS.jsonl",
        broker_explorer_url="http://127.0.0.1:18080/",
        hearth_job_state="idle",
        hearth_job_id="job_dd0ce1ee249e2ed2fc4fc35f9f4bbf6e",
        hearth_invocation_id="inv_aede99cda569a52fc1d2e00e80a5c338",
        hearth_provider="omen-dense-27b",
        hearth_model="qwen3.8-27b",
        hearth_status="succeeded",
        hearth_stages=["queued", "think_turn1", "emit_turn2", "succeeded"],
        hearth_app_retries=0,
        hearth_emit_repairs=1,
        hearth_emit_trigger="output_truncated",
        hearth_delivery_repairs=1,
        hearth_delivery_repair_note="IRC server closed observer connection while model job ran; result retained",
        hearth_execution_path="/home/derek/work/bh2/ops1/help-orland-throughput-20261104/execution.json",
        steppewise_state="not_used_by_job",
        router_job_state="idle",
        router_receipt_path="/home/derek/work/bh2/ops1/help-orland-throughput-20261104/plan.json",
        selected_route="pinned:omen-dense-27b",
        task_family="code_review",
        regime_summary="family=code_review, proc=think_then_emit, thinking=true, max_tokens=6000",
        pool_config_hash="c94175b8b950",
        pack_size_bytes=7051,
        pack_sha256="ae40fa1f43c16136d6ed947e791908f87734a1362b3e28dbe7c5e5fb2584a72b",
        packed_files_count=0,
        jepa_job_state="not_used_by_job",
        jepa_job_note="not used by this job (desired.packed_files=[], HEARTH_SIZER off)",
        omissions_or_fallback="none",
        tokens_job_state="idle",
        model_calls=2,
        tokens_in_total=9094,
        tokens_out_total=7482,
        tokens_reasoning_total=6000,
        turn1_tokens_in=1532,
        turn1_tokens_out=6000,
        turn1_tokens_reasoning=6000,
        turn1_duration_ms=152201,
        turn1_finish_reason="length (output_truncated)",
        emit_tokens_in=7562,
        emit_tokens_out=1482,
        emit_duration_ms=31509,
        emit_finish_reason="stop",
        cached_input_tokens=None,
        cached_input_tokens_state="unknown",
        cached_input_tokens_note="unknown (explicit gap: vLLM prompt_tokens_details.cached_tokens dropped by hearth/toolsurface/inference.py)",
        ast_cache_job_state="not_used_by_job",
        ast_cache_job_note="not used by this job (0 AST arena lookups attributed to job)",
        text_cache_job_state="not_used_by_job",
        text_cache_job_note="not used by this job",
        ontology_job_state="not_used_by_job",
        ontology_job_note="not used by this job (catalog entry available at :13000 -> AM4)",
        duckdb_job_state="not_used_by_job",
        duckdb_job_note="not used by this job (BH2/Orland uses Service Bus + HEARTH + JSONL receipts)",
        helper_artifact_id="art_61cab68c296b690c644e3ed1b0583bdb",
        helper_artifact_sha256="cf9193801a12a18d116845d01dc6a53c88c81664a35672dd306e0d07767a4028",
        helper_artifact_path="/home/derek/work/bh2/ops1/help-orland-throughput-20261104/artifact.json",
        parent_artifact_path="/home/derek/work/worktrees/bh2-orland-event-finder/web/index.html",
        parent_artifact_sha256="5c39613df7bcdbeb0f79e26a87b06a95b3a809845d65bdd176e41ed4a4781702",
        check_artifact_path="/home/derek/work/bh2/ops1/help-orland-throughput-20261104/REVIEW-BUNDLE.json",
        preview_url="http://127.0.0.1:8896/#event-finder",
        observed_hops=[
            PathHop("ontology", "1. Ontology (:13000 -> AM4)", "not_used_by_job", False, "not used by this job (catalog entry available at :13000 -> AM4)", ""),
            PathHop("service_bus", "2. Service Bus (:18080/:5672)", "idle", True, "queue=bh2-orland-help-20261104 seq=3 dlv=1 (completed (recovery))", "/home/derek/work/bh2/ops1/help-orland-throughput-20261104/BROKER-RECEIPTS.jsonl", "2026-11-04T23:11:26Z"),
            PathHop("router_pack", "3. Router / Context Pack", "idle", True, "route=pinned:omen-dense-27b family=code_review, proc=think_then_emit, thinking=true, max_tokens=6000 pack=7051B", "/home/derek/work/bh2/ops1/help-orland-throughput-20261104/plan.json", "2026-11-04T23:05:08Z"),
            PathHop("hearth_27b", "4. HEARTH / Steppewise / MAF", "idle", True, "omen-dense-27b (qwen3.8-27b) job=job_dd0ce1ee249e calls=2 in=9094 out=7482 emit_repairs=1", "/home/derek/work/bh2/ops1/help-orland-throughput-20261104/execution.json", "2026-11-04T23:08:13Z"),
            PathHop("jepa_npu", "5. JEPA NPU (:18097)", "not_used_by_job", False, "not used by this job (desired.packed_files=[], HEARTH_SIZER off)", ""),
            PathHop("duckdb", "6. DuckDB Intake", "not_used_by_job", False, "not used by this job (BH2/Orland uses Service Bus + HEARTH + JSONL receipts)", ""),
            PathHop("artifact_verify", "7. Artifact / Browser Verify", "idle", True, "helper=art_61cab68c296b (sha256=cf9193801a) -> parent=index.html", "/home/derek/work/bh2/ops1/help-orland-throughput-20261104/REVIEW-BUNDLE.json", "2026-11-04T23:08:13Z"),
            PathHop("parent_consume_irc_aspire", "8. Parent / IRC / Aspire", "idle", True, "consumed by cx-adhoc; IRC receipts=9; OTel spans=29, 5 OTel err (irc.publish,helper.execution)", "/home/derek/work/bh2/ops1/help-orland-throughput-20261104/consumption.json", "2026-11-04T23:15:20Z"),
        ],
    )

    t_onto_place = CorrelatedTask(
        task_id="ontology-placement-20261104",
        title="Ontology service placement move OMEN -> AM4 native Docker + loopback proxy verification",
        actor="cx-adhoc (operator/parent)",
        host="OMEN -> AM4 (10.44.0.2)",
        stage="completed (AM4 placement + lookup verified)",
        state="idle",
        elapsed_s=0.19,
        last_progress="AM4 native Docker active; lookup 194.4ms verified",
        last_progress_utc="2026-11-04T22:57:57Z",
        age_s=1180.0,
        outcome="verified (retained_term_exact_match=true, final_browser_verified=true)",
        source_receipt_path="/home/derek/work/bh2/ops1/ontology-placement-20261104/RESULT.json",
        run_dir="/home/derek/work/bh2/ops1/ontology-placement-20261104",
        steppewise_state="idle",
        steppewise_workflow="bh2-reviewed-lookup (MAF 1.27.0 IStepAction)",
        steppewise_status="succeeded",
        steppewise_steps=["ActionWorkflowRunner", "IStepAction.ExecuteAsync", "AllowlistedRESTLookup"],
        steppewise_events_path="/home/derek/work/bh2/ops1/ontology-placement-20261104/after-task/LOOKUP-MEASURE.json",
        ontology_job_state="idle",
        ontology_entry_url=DEFAULT_ONTOLOGY_UI_URL,
        ontology_term_id=DEFAULT_ONTOLOGY_TERM_ID,
        ontology_term_name="Solicitation",
        ontology_attr_id=DEFAULT_ONTOLOGY_ATTR_ID,
        ontology_attr_name="Budget Label",
        ontology_definition_sha256="b8574c82d05d7172e362c7db599138b4e19f6759d58b712dd3201ea9d6b2d2a4",
        ontology_lookup_ms=194.4,
        ontology_receipt_path="/home/derek/work/bh2/ops1/ontology-placement-20261104/after-task/LOOKUP-MEASURE.json",
        ontology_job_note="used by job (AM4 post-move verification via MAF IStepAction)",
        check_artifact_path="/home/derek/work/bh2/ops1/ontology-placement-20261104/FINAL-BROWSER.json",
        preview_url=DEFAULT_ONTOLOGY_UI_URL,
        observed_hops=[
            PathHop("ontology", "1. Ontology (:13000 -> AM4)", "idle", True, "Solicitation / Budget Label verified on AM4 (194.4ms, sha256=b8574c82d05d)", "/home/derek/work/bh2/ops1/ontology-placement-20261104/after-task/LOOKUP-MEASURE.json", "2026-11-04T22:53:03Z"),
            PathHop("service_bus", "2. Service Bus (:18080/:5672)", "not_used_by_job", False, "not used by this job (placement & REST lookup verification)", ""),
            PathHop("router_pack", "3. Router / Context Pack", "not_used_by_job", False, "not used by this job (no HEARTH model dispatch)", ""),
            PathHop("hearth_27b", "4. HEARTH / Steppewise / MAF", "idle", True, "Steppewise ActionWorkflowRunner -> MAF 1.27.0 IStepAction (194.4ms)", "/home/derek/work/bh2/ops1/ontology-placement-20261104/after-task/LOOKUP-MEASURE.json", "2026-11-04T22:53:03Z"),
            PathHop("jepa_npu", "5. JEPA NPU (:18097)", "not_used_by_job", False, "not used by this job", ""),
            PathHop("duckdb", "6. DuckDB Intake", "not_used_by_job", False, "not used by this job", ""),
            PathHop("artifact_verify", "7. Artifact / Browser Verify", "idle", True, "AM4 Docker placement + browser UI & retained term exact match verified", "/home/derek/work/bh2/ops1/ontology-placement-20261104/RESULT.json", "2026-11-04T22:57:57Z"),
            PathHop("parent_consume_irc_aspire", "8. Parent / IRC / Aspire", "idle", True, "RESULT.json committed (OMEN eno2 10.44.0.1 -> AM4 enp5s0 10.44.0.2)", "/home/derek/work/bh2/ops1/ontology-placement-20261104/RESULT.json", "2026-11-04T22:57:57Z"),
        ],
    )

    t_onto = CorrelatedTask(
        task_id="help-ontology-20261104",
        title="Solicitation / Budget Label reviewed ontology lookup (MAF IStepAction) + Orland integration",
        actor="cx-adhoc -> bh2-help-worker",
        host="omen-linux",
        stage="parent.consumed / verified",
        state="idle",
        elapsed_s=339.8,
        last_progress="parent.consumed / verified | 2 calls (8136 in / 6376 out tok)",
        last_progress_utc="2026-11-04T22:45:33Z",
        age_s=1920.0,
        outcome="Delivered Solicitation / Budget Label definition via Steppewise + MAF IStepAction (120.6ms) and integrated budget display into Orland.",
        source_receipt_path="/home/derek/work/bh2/ops1/help-ontology-20261104/RESULT.json",
        run_dir="/home/derek/work/bh2/ops1/help-ontology-20261104",
        aspire_trace_id="ac2e983dba7f55f99c2f233963fe639b",
        aspire_trace_url="http://127.0.0.1:18888/traces/detail/ac2e983dba7f55f99c2f233963fe639b",
        otel_spans_path="/home/derek/work/bh2/ops1/help-ontology-20261104/OTEL-SPANS.jsonl",
        otel_span_count=20,
        otel_exported=True,
        otel_error_count=0,
        broker_job_state="idle",
        broker_queue="bh2-orland-help-20261104",
        broker_message_id="help-ontology-20261104",
        broker_sequence_number=2,
        broker_delivery_count=0,
        broker_send_at="2026-11-04T22:40:44Z",
        broker_recv_at="2026-11-04T22:40:46Z",
        broker_settle_at="2026-11-04T22:43:14Z",
        broker_disposition="completed",
        broker_receipt_path="/home/derek/work/bh2/ops1/help-ontology-20261104/BROKER-RECEIPTS.jsonl",
        broker_explorer_url="http://127.0.0.1:18080/",
        hearth_job_state="idle",
        hearth_job_id="job_c661bc6aa15bb6ec3090c04c291b0346",
        hearth_provider="omen-dense-27b",
        hearth_model="qwen3.8-27b",
        hearth_status="succeeded",
        hearth_stages=["queued", "think_turn1", "emit_turn2", "succeeded"],
        hearth_emit_repairs=1,
        hearth_emit_trigger="output_truncated",
        steppewise_state="idle",
        steppewise_workflow="bh2-reviewed-lookup (MAF 1.27.0 IStepAction)",
        steppewise_run_id="lookup-solicitation-budget-label",
        steppewise_status="succeeded (no model call in lookup step)",
        steppewise_steps=["Steppewise.ActionWorkflowRunner", "MAF 1.27.0 IStepAction", "GET /api/terms/09bc5669.../column-attributes"],
        steppewise_events_path="/home/derek/work/bh2/ops1/help-ontology-20261104/LOOKUP-MEASURE.json",
        router_job_state="idle",
        router_receipt_path="/home/derek/work/bh2/ops1/help-ontology-20261104/plan.json",
        selected_route="pinned:omen-dense-27b",
        task_family="code_review",
        regime_summary="family=code_review, proc=think_then_emit, thinking=true, max_tokens=6000",
        pack_size_bytes=5214,
        jepa_job_state="not_used_by_job",
        jepa_job_note="not used by this job (desired.packed_files=[], HEARTH_SIZER off)",
        tokens_job_state="idle",
        model_calls=2,
        tokens_in_total=8136,
        tokens_out_total=6376,
        turn1_tokens_in=856,
        turn1_tokens_out=6000,
        turn1_tokens_reasoning=6000,
        turn1_finish_reason="length (output_truncated)",
        emit_tokens_in=7280,
        emit_tokens_out=376,
        emit_finish_reason="stop",
        ontology_job_state="idle",
        ontology_entry_url=DEFAULT_ONTOLOGY_UI_URL,
        ontology_term_id=DEFAULT_ONTOLOGY_TERM_ID,
        ontology_term_name="Solicitation",
        ontology_attr_id=DEFAULT_ONTOLOGY_ATTR_ID,
        ontology_attr_name="Budget Label",
        ontology_definition_sha256="b8574c82d05d7172e362c7db599138b4e19f6759d58b712dd3201ea9d6b2d2a4",
        ontology_lookup_ms=120.6,
        ontology_receipt_path="/home/derek/work/bh2/ops1/help-ontology-20261104/DELIVERED-DEFINITION.json",
        ontology_job_note="used by job (Solicitation / Budget Label delivered into helper prompt)",
        duckdb_job_state="not_used_by_job",
        helper_artifact_id="art_7fadfccbc444d0b11b545a21c220f0b2",
        helper_artifact_sha256="113121d3c2d007ddf305e631a533de5151c8626a2076932eff177daf23845719",
        preview_url=DEFAULT_ONTOLOGY_UI_URL,
        observed_hops=[
            PathHop("ontology", "1. Ontology (:13000 -> AM4)", "idle", True, "Solicitation / Budget Label (120.6ms, sha256=b8574c82d05d)", "/home/derek/work/bh2/ops1/help-ontology-20261104/DELIVERED-DEFINITION.json", "2026-11-04T22:39:53Z"),
            PathHop("service_bus", "2. Service Bus (:18080/:5672)", "idle", True, "queue=bh2-orland-help-20261104 seq=2 dlv=0 (completed)", "/home/derek/work/bh2/ops1/help-ontology-20261104/BROKER-RECEIPTS.jsonl", "2026-11-04T22:43:14Z"),
            PathHop("router_pack", "3. Router / Context Pack", "idle", True, "route=pinned:omen-dense-27b family=code_review, proc=think_then_emit, thinking=true, max_tokens=6000 pack=5214B", "/home/derek/work/bh2/ops1/help-ontology-20261104/plan.json", "2026-11-04T22:40:47Z"),
            PathHop("hearth_27b", "4. HEARTH / Steppewise / MAF", "idle", True, "omen-dense-27b (qwen3.8-27b) job=job_c661bc6aa15b calls=2 in=8534 out=6347 emit_repairs=1", "/home/derek/work/bh2/ops1/help-ontology-20261104/execution.json", "2026-11-04T22:43:14Z"),
            PathHop("jepa_npu", "5. JEPA NPU (:18097)", "not_used_by_job", False, "not used by this job (desired.packed_files=[], HEARTH_SIZER off)", ""),
            PathHop("duckdb", "6. DuckDB Intake", "not_used_by_job", False, "not used by this job (BH2/Orland uses Service Bus + HEARTH + JSONL receipts)", ""),
            PathHop("artifact_verify", "7. Artifact / Browser Verify", "idle", True, "helper=art_7fadfccbc444 (sha256=113121d3c2) -> parent=index.html", "/home/derek/work/bh2/ops1/help-ontology-20261104/RESULT.json", "2026-11-04T22:44:37Z"),
            PathHop("parent_consume_irc_aspire", "8. Parent / IRC / Aspire", "idle", True, "consumed by cx-adhoc; IRC receipts=7; OTel spans=20", "/home/derek/work/bh2/ops1/help-ontology-20261104/consumption.json", "2026-11-04T22:44:37Z"),
        ],
    )

    other_ids = [
        ("help-orland-broker-20261104", "Orland shareable filter URL state via Azure Service Bus Emulator queue & HEARTH", "2026-11-04T22:31:10Z", 1, "idle"),
        ("help-orland-feature-20261104", "Orland Event Finder initial feature implementation & browser verification", "2026-11-04T22:12:45Z", None, "not_used_by_job"),
        ("help-b3-packet-check-20261104", "B3 packet verification check via HEARTH 27B & OTel trace export", "2026-11-04T21:58:12Z", None, "not_used_by_job"),
        ("help-publish-recovery-20261104", "IRC publish disconnect recovery & idempotent completion check", "2026-11-04T21:51:04Z", None, "not_used_by_job"),
        ("help-observability-20261104", "Aspire dashboard & Service Bus Explorer read-only observability verification", "2026-11-04T21:46:24Z", None, "not_used_by_job"),
        ("help-loop-20261104T2114Z", "HEARTH helper loop baseline verification & parent patch application", "2026-11-04T21:18:35Z", None, "not_used_by_job"),
        ("wp001-AX-00714280", "Washplant PILE2-s4v2-wp001-AX-00714280 (69-south-dakota-u-sys-6922 DuckDB + Broker + Steppewise)", "2026-11-04T18:12:22Z", 4, "idle"),
    ]
    tasks = [t_throughput, t_onto_place, t_onto]
    for tid, title, ts, seq, b_state in other_ids:
        is_wp = tid.startswith("wp001")
        tasks.append(
            CorrelatedTask(
                task_id=tid,
                title=title,
                actor="wash-worker-1" if is_wp else "cx-adhoc -> bh2-help-worker",
                host="omen-linux",
                stage="completed (DuckDB outcome committed)" if is_wp else "parent.consumed / verified",
                state="idle",
                elapsed_s=7.9 if is_wp else 145.0,
                last_progress=f"completed at {ts}",
                last_progress_utc=ts,
                age_s=3600.0,
                outcome="completed & verified",
                source_receipt_path=f"/home/derek/work/bh2/ops1/{tid}/consumption.json",
                run_dir=f"/home/derek/work/bh2/ops1/{tid}",
                broker_job_state=b_state,
                broker_queue="intake-submissions" if is_wp else ("bh2-orland-help-20261104" if b_state == "idle" else ""),
                broker_sequence_number=seq,
                hearth_job_state="idle",
                hearth_provider="omen-dense-27b",
                hearth_model="qwen3.8-27b",
                router_job_state="idle",
                jepa_job_state="idle" if is_wp else "not_used_by_job",
                tokens_job_state="idle",
                model_calls=2,
                tokens_in_total=3506 if is_wp else 8342,
                tokens_out_total=288 if is_wp else 6331,
                ontology_job_state="idle" if is_wp else "not_used_by_job",
                duckdb_job_state="idle" if is_wp else "not_used_by_job",
                observed_hops=deepcopy(t_throughput.observed_hops),
            )
        )
    return tasks


def _build_orland_completed_fixture() -> FlowSnapshot:
    tasks = _make_completed_tasks()
    return FlowSnapshot(
        schema="flow_top_snapshot.v1",
        collected_at_utc="2026-11-04T23:20:00Z",
        collected_at_unix=1762298400.0,
        overall_state="idle",
        headline_available="6/6 services available (Service Bus :18080/:5672, Ontology :13000->AM4, HEARTH 3x27B, JEPA :18097, DuckDB snapshots, Aspire :18888)",
        headline_moving="IDLE: 0 tasks in flight, broker backlog=0, DuckDB writer=none (latest completed: help-orland-throughput-20261104 (2026-11-04T23:15:44Z))",
        headline_components="[help-orland-throughput-20261104] used: service_bus=idle, router_pack=idle, hearth_27b=idle, artifact_verify=idle, parent_consume_irc_aspire=idle | ontology=not used by this job, jepa_npu=not used by this job, duckdb=not used by this job",
        selected_task_index=0,
        services=_base_services("idle", "idle", "idle"),
        tasks=tasks,
        service_bus=ServiceBusPanel(
            state="idle",
            explorer_online=True,
            emulator_amqp_online=True,
            read_only_enforced=True,
            queues=[
                QueueDepthRow("bh2-orland-help-20261104", "queue", "active", 0, 0, 0, "00:05:00", 3),
                QueueDepthRow("intake-submissions", "queue", "active", 0, 0, 0, "00:01:00", 5),
                QueueDepthRow("run-events", "topic", "active", 0, 0, 0, "", 0),
            ],
            total_queued_backlog=0,
            total_dead_letters=0,
            recent_send_count=6,
            recent_receive_count=7,
            recent_settle_count=6,
            unsettled_in_flight=0,
            last_receipt_summary="help-orland-throughput-20261104: completed seq=3 on bh2-orland-help-20261104 at 2026-11-04T23:11:26Z",
        ),
        ontology=OntologyPanel(
            state="idle",
            omen_proxy_online=True,
            am4_backend_online=True,
            postgres_status="ok",
            last_lookup_utc="2026-11-04T22:53:03Z",
            last_lookup_ms=194.4,
            last_lookup_command_s=0.58,
            last_lookup_execution="Steppewise ActionWorkflowRunner -> MAF 1.27.0 executor -> IStepAction -> allowlisted REST (AM4 REST 194.4ms; task MAF lookup 120.6ms)",
            last_lookup_sha256="b8574c82d05d7172e362c7db599138b4e19f6759d58b712dd3201ea9d6b2d2a4",
            last_lookup_path="/home/derek/work/bh2/ops1/ontology-placement-20261104/after-task/LOOKUP-MEASURE.json",
        ),
        duckdb=DuckDBPanel(
            state="idle",
            active_writer_pid=None,
            last_commit_utc="2026-11-04T18:12:22Z",
            last_commit_summary="br-20261104-181221-00714280 (seq=4 accepted, deliveries=3)",
            washplant_table_counts={"items": 1, "dispatches": 3, "deliveries": 3, "outcomes": 3},
            snapshot_table_counts={"items": 1, "dispatches": 3, "deliveries": 3, "outcomes": 3},
            seed_table_counts={"page_fact": 24, "workflow": 4, "solicitation": 1},
            net_row_change_vs_snapshot={"items": 0, "dispatches": 0, "deliveries": 0, "outcomes": 0},
            net_row_change_total=0,
        ),
        lifetime_caches=LifetimeCacheSummary(
            state="idle",
            obs_age_s=0.5,
            kv_caches_summary=[
                "omen:18091 (qwen3.8-27b): 1,134,272/1,300,923 tok (87.2%, engine uptime)",
                "omen:18092 (qwen3.8-27b): 117,312/330,122 tok (35.5%, engine uptime)",
                "am4:18094 (qwen3-27b): 0/2,816,554 tok (0.0%, engine uptime)",
            ],
            npu_ast_online=True,
            npu_ast_summary=":18097 NPU AST Arena (mechsuit-shm-ast-arena.v3): 43/45 hits (95.6%, daemon uptime), 19 shm objects (4.73 MiB), p50=52.1ms",
            sol_arena_summary="unavailable / unmounted (/dev/shm/mechsuit-sol-arena present=False)",
        ),
        monitor_usage=MonitorResourceUsage(
            pid=12345,
            rss_mib=28.4,
            cpu_pct=0.4,
            cpu_time_s=0.12,
            poll_duration_ms=14.2,
            incremental_cache_hits=42,
            incremental_cache_misses=0,
        ),
    )


def _build_orland_live_fixture() -> FlowSnapshot:
    snap = _build_orland_completed_fixture()
    snap.overall_state = "active"
    live_task = deepcopy(snap.tasks[0])
    live_task.task_id = "help-orland-live-20261104"
    live_task.title = "In-flight Orland helper task (peek-locked on Service Bus, running on HEARTH 27B)"
    live_task.stage = "hearth.running (27B deliberating)"
    live_task.state = "active"
    live_task.elapsed_s = 42.3
    live_task.last_progress = "hearth.running (27B deliberating) | Turn 1 streaming on omen-dense-27b"
    live_task.last_progress_utc = "2026-11-04T23:20:00Z"
    live_task.age_s = 0.8
    live_task.outcome = "in-flight (hearth.running (27B deliberating))"
    live_task.broker_job_state = "active"
    live_task.broker_settle_at = ""
    live_task.broker_disposition = "received_peek_lock"
    live_task.hearth_job_state = "active"
    live_task.hearth_status = "running"
    live_task.hearth_stages = ["queued", "think_turn1"]
    live_task.tokens_job_state = "active"
    for hop in live_task.observed_hops:
        if hop.hop_id in ("service_bus", "hearth_27b", "parent_consume_irc_aspire"):
            hop.state = "active"

    snap.tasks = [live_task] + snap.tasks[:9]
    snap.services = _base_services("active", "idle", "active")
    snap.service_bus.state = "active"
    snap.service_bus.total_queued_backlog = 1
    snap.service_bus.unsettled_in_flight = 1
    snap.service_bus.queues[0].active_messages = 1
    snap.headline_moving = (
        "ACTIVE: 1 task(s) in flight — help-orland-live-20261104 at [hearth.running (27B deliberating)] "
        "(broker backlog=1, unsettled=1)"
    )
    snap.headline_components = (
        "[help-orland-live-20261104] used: service_bus=active, router_pack=idle, hearth_27b=active, "
        "artifact_verify=idle, parent_consume_irc_aspire=active | ontology=not used by this job, "
        "jepa_npu=not used by this job, duckdb=not used by this job"
    )
    return snap


def _build_degraded_collector_fixture() -> FlowSnapshot:
    snap = _build_orland_completed_fixture()
    snap.simulated_unavailable = ["ontology", "service_bus"]
    snap.services = _base_services("unavailable", "unavailable", "idle")
    snap.service_bus = ServiceBusPanel(
        state="unavailable",
        explorer_online=False,
        emulator_amqp_online=False,
        error_note="Collector unavailable (simulated fault or endpoint unreachable)",
    )
    snap.ontology = OntologyPanel(
        state="unavailable",
        omen_proxy_online=False,
        am4_backend_online=False,
        postgres_status="unavailable",
        error_note="Collector unavailable (simulated fault or endpoint unreachable)",
    )
    snap.headline_available = (
        "4/6 services available (unavailable: service_bus, ontology; other panels updating normally)"
    )
    return snap


FIXTURES: Dict[str, Callable[[], FlowSnapshot]] = {
    "orland_live": _build_orland_live_fixture,
    "orland_completed": _build_orland_completed_fixture,
    "degraded_collector": _build_degraded_collector_fixture,
}


def list_fixtures() -> List[str]:
    """Return available fixture names."""
    return list(FIXTURES.keys())


def get_fixture_snapshot(
    name: str,
    selected_task_index: int = 0,
    selected_task_id: Optional[str] = None,
) -> FlowSnapshot:
    """Return a deterministic `FlowSnapshot` for `name`."""
    builder = FIXTURES.get(name)
    if builder is None:
        raise ValueError(f"Unknown fixture {name!r}. Valid fixtures: {list_fixtures()}")
    snap = builder()
    if selected_task_id and snap.tasks:
        for i, t in enumerate(snap.tasks):
            if t.task_id == selected_task_id:
                selected_task_index = i
                break
    if snap.tasks:
        snap.selected_task_index = max(0, min(selected_task_index, len(snap.tasks) - 1))
    return snap
