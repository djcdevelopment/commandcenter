# `flow-top` — Maintainer, Support & Extension Guide

Companion read-only terminal dashboard alongside `fleet-top` (`am4-top`).
While `fleet-top` monitors hardware and model serving (GPUs, NPU, vLLM endpoints, power/VRAM), **`flow-top`** monitors the application workflow, broker, router/context packing, ontology, DuckDB intake, and delivery telemetry around one core question:

> **“What is happening, where is it happening, and does anything need me?”**

---

## 1. Where Things Live

- **Worktree & Branch**:
  - Worktree: `/home/derek/work/worktrees/flow-top-20261104`
  - Branch: `agent/flow-top-20261104` (branched from `/home/derek/work/commandcenter-linux-flash`)
- **CLI Entrypoint**:
  - `/home/derek/.local/bin/flow-top` (sets `PYTHONPATH="/home/derek/work/worktrees/flow-top-20261104:/home/derek/work/listing"` and invokes `python3 -m tools.flow_top.cli "$@"`)
- **Package Modules (`tools/flow_top/`)**:
  - `contract.py`: Dataclass schemas (`FlowSnapshot`, `CorrelatedTask`, `PathHop`, `ServiceAvailability`, `ServiceBusPanel`, `OntologyPanel`, `DuckDBPanel`, `LifetimeCacheSummary`, `MonitorResourceUsage`) and `/dev/shm/flow-top-snapshot.json` cache helpers.
  - `collectors.py`: Read-only, bounded, incremental collectors with `ThreadPoolExecutor` fault isolation and `(inode, byte_offset, mtime_ns)` seek-and-append file caching.
  - `renderer.py`: 256-color ANSI HUD renderer reusing `tools.fleet_monitor.renderer` visual primitives (`grad_bar`, `sparkline`, borders, colors, truncation).
  - `fixtures.py`: Deterministic offline fixtures (`orland_live`, `orland_completed`, `degraded_collector`).
  - `cli.py`: CLI flags and non-blocking interactive terminal loop (`o`/`p`/`s`/`c`/`d` views, `1..9,0` and `j/k` task selection, `r` refresh, `q` quit).

---

## 2. Non-Negotiable Safety & Evidence Invariants

When maintaining or extending `flow-top`, preserve these rules without exception:

1. **Strictly Read-Only Monitoring**:
   - **Zero model calls**: Never dispatch to HEARTH (`:8710`) or vLLM (`:18091`, `:18092`, `10.44.0.2:18094`).
   - **Zero broker consumption**: Query Service Bus Explorer (`http://127.0.0.1:18080`) exclusively via `GET /api/queues/` and `GET /api/topics/`. Never call `POST`/`PUT`/`DELETE` and never consume, settle, or purge AMQP messages on `:5672`.
   - **Zero IRC socket connections**: Never connect to Ergo IRC (`:6667`) or reuse an active agent nickname (`cx-adhoc`, `bh2-observer`, `bh2-board`). Read local `IRC-RECEIPTS.jsonl` files only.
   - **Zero DuckDB writer contention**: Prefer immutable `0444` snapshot files (`snapshot-manifest.json`, `seed-*.duckdb`, `hopper-*.duckdb`) and JSONL receipts (`deliveries.jsonl`) plus `/proc/[pid]/fd` writer inspection. Never hold open or contend with a live writer-locked `.duckdb` file on every poll.
   - **Read-only run directories**: Treat `/home/derek/work/bh2/ops1/*` and `/home/derek/work/washplant/*` as read-only evidence.

2. **Service Availability vs. Job Participation**:
   - A service being online (`● available`) does **not** mean the selected task used it.
   - Every component tile and detail panel must distinguish:
     - `active` (`▶`): currently executing for this job/service
     - `idle` / `completed` (`✓` / `●`): available or completed with receipt evidence
     - `not_used_by_job` (`· unused by selected task`): service is online, but this task did not use it (e.g., MAF is unused on direct Orland UI runs; JEPA is unused when `packed_files=[]`; Ontology is unused unless a lookup or relationship pack receipt exists for the job)
     - `unknown` (`? unknown`): measurement is not exposed by the producer (never render an empty bar implying zero)
     - `unavailable` (`✗ unavailable`): collector/endpoint unreachable or timed out

3. **Current-Run vs. Lifetime Cache Separation**:
   - Never present engine-uptime vLLM KV prefix cache rates or NPU `:18097` daemon-uptime AST hit rates as job-level cache hits.
   - On the `overview` screen, `Job Cache` shows only job-attributed cache evidence (`PACK-CACHE-RECEIPTS.json` for context pack caching; `? unknown (not recorded by HEARTH)` for vLLM prompt cached tokens until `hearth/toolsurface/inference.py` preserves `prompt_tokens_details.cached_tokens`).
   - Lifetime/engine-uptime cache metrics live in the `[c]aches` view.

4. **Queue Depth Is Backlog, Not Throughput**:
   - Label broker queue counts as `queued` / `Backlog` (`msg`). Rolling samples in `/dev/shm/flow-top-queue-history.json` only accumulate from actual observed polls (never synthesize prior history).

5. **DuckDB Net Row Change**:
   - Report table count deltas strictly as `Net Δ` (`net row change`). Row-level `Updates` and `Deletions` must remain `? unknown` unless WAL/CDC or transition-table evidence is present.

---

## 3. Visual Architecture & Layout Contract (`overview`)

Target dimensions for `overview`: **120 columns × 33–36 lines** (adapting cleanly down to 100 columns with zero `"…"` clipping).

1. **Section 1 — Quiet 1-Line Headline (3 lines)**:
   - `■ IDLE` (calm muted border) / `▶ WORKING` (Cyan) / `! ATTENTION` (Amber when a collector is unavailable) · `active tasks` · `queued requests` · `last completion age` · `Clock`.
2. **Section 2 — Visual Center: Selected Task Card (9 lines)**:
   - Header: `SELECTED TASK [#1]  <human_title>` + right-aligned outcome badge (`✓ COMPLETED · 12m 35s wall` or `▶ WORKING · 1m 10s elapsed`). Never label completed work as `IDLE`.
   - `Stages`: 5 receipt-backed stages (`request ─── helper ─── parent work ─── checks ─── result`) using shape+color (`✓` completed, `▶` working, `○` waiting, `!` attention/recovery, `✗` failed, `?` unknown, `·` unused).
   - `IRC delivery`: Shown separately on the right so a publication recovery (`! recovered once`) never makes completed application work look failed.
   - `Story` & `Attention`: Immediate human-readable outcome summary and repair/recovery counts (`0 model reruns`).
   - Quantitative Bars:
     - `Input Tok`: `grad_bar` against `FREEZE.json` `limits.input_tokens` (or `slice_ceiling.max_input_tokens`).
     - `Output`: `grad_bar` against `limits.output_tokens` + `Out Split` 2-segment bar (`█` Gold reasoning vs `▓` Green emit tokens).
     - `Helper`: 2-segment phase bar (`█` Cyan Turn 1 think + `▓` Green Turn 2 emit seconds) + `Calls` against `limits.model_calls`.
3. **Section 3 — Six Compact Component Tiles (2×3 Grid, 12 lines)**:
   - `BROKER (:5672 / :18080)`, `HEARTH (:8710 -> B70 :18091)`, `ROUTING / CONTEXT (:18097)`, `ONTOLOGY (:13000 -> AM4)`, `DUCKDB INTAKE`, `DELIVERY / TELEMETRY`.
   - Each tile has: status badge + observation age in header, 1 main availability + job-participation line, and 1 compact metric/freshness line.
4. **Section 4 — Recent Work List (3 Tasks by Default, 5 lines)**:
   - Sliding 3-task window around `selected_task_index`: selector + outcome symbol (`>1 ✓`), human title, labeled wall + helper time, and result phrase.
5. **Section 5 — Compact 1-Line Footer**:
   - `Views: [o]verview [p]ath [s]ervices [c]aches [d]ata │ Task: [1-9,0] [j/k] │ [r]efresh [q]uit`

---

## 4. How to Extend `flow-top`

### Run-scoped functional readiness

Set `WASHPLANT_READINESS_MANIFEST` to an absolute JSON manifest under
`/home/derek/work/worktrees/washplant-self-build-readiness/readiness/` to display
only that run's required service/provider rows in the services view:

```bash
WASHPLANT_READINESS_MANIFEST=/home/derek/work/worktrees/washplant-self-build-readiness/readiness/self-build-readiness.json \
  flow-top --view services --render --color --width 156
```

Without this variable the ordinary dashboard remains unchanged. The scoped view
invokes the checker's shared `tools/self_build_probes.py --manifest PATH` through
the existing HEARTH Python environment. It does not invoke the bring-up command.
The shared probes perform functional read-only checks, including a non-consuming
peek of the task's own broker queue; they never receive/settle work, submit model
calls, or start/restart/reconfigure services. This explicit readiness mode is the
only exception to the default collector's HTTP-only broker observation.

`ServiceAvailability` preserves `readiness_scope`, `functional_check_passed`, and
`functional_check_source` through JSON serialization and rendering. Service ages
come from fresh probe evidence; cached evidence is never re-dated as current.
Readiness bypasses ordinary dashboard cache reads and writes.
Missing/invalid evidence, scopes, or observations older than 60 seconds cannot be
green. A probe timeout produces an unavailable row, not TCP fallback. The
Washplant :5673 broker is separate from the ordinary dashboard's shared :5672
broker. Provider rows come from this run's live `code_fix` eligibility.

### Adding a New Run Type or Receipt Schema in `collectors.py`
1. **Run Discovery (`_iter_run_dirs`)**:
   - Scans `/home/derek/work/bh2/ops1` plus any colon-separated paths in `FLOW_TOP_EXTRA_RUNS_DIR` or `--runs-dir`.
2. **Task Parsing (`_build_bh2_correlated_task`)**:
   - Reads JSON/JSONL receipts incrementally via `_read_json_incremental` / `_read_jsonl_incremental`.
   - Supported receipt files include:
     - `FREEZE.json` (`limits` or `execution_profile_and_budgets.slice_ceiling`; note that `phase == "phase1_discovery_frozen_before_execution"` without Phase 2 files is classified as `○ staged` so it does not trigger a false-positive `▶ WORKING` headline before execution begins)
     - `request.json`, `broker-request.json`, `BROKER-RECEIPTS.jsonl`
     - `plan.json`, `SELECTED-CONTEXT-PACK.json`, `PACK-CACHE-RECEIPTS.json`
     - `submitted.json`, `job-queued.json`, `job-running.json`, `job-succeeded.json`, `execution.json`, `artifact.json`
     - `LOOKUP-MEASURE.json`, `DELIVERED-DEFINITION.json`, `RELATIONSHIP-EVIDENCE.json`
     - `RECOVERY.json`, `REVIEW-BUNDLE.json`, `RESULT.json`, `FEATURE-RESULT.json`, `SUMMARY.json`, `consumption.json`, `WORKFLOW-LEDGER.json`, `PARENT-CHECKS.json`
     - `IRC-RECEIPTS.jsonl`, `OTEL-SPANS.jsonl`, `trace-context.json`
   - Add human-readable titles in `_HUMAN_TITLES` (keep `<= 36` chars so the 3-row `RECENT WORK` table never truncates at 120 columns) and short stories in `_SHORT_STORIES`.

### Known Producer Gaps (Surfaced Explicitly by `flow-top`)
- **Job-level vLLM cached prompt tokens (`? unknown`)**:
  - `vLLM` returns `usage.prompt_tokens_details.cached_tokens`, but `/home/derek/work/commandcenter-linux-flash/hearth/toolsurface/inference.py` (`_extract_usage`, lines 422–432) currently extracts only `prompt_tokens`, `completion_tokens`, and `reasoning_tokens`. Once `inference.py` forwards `cached_tokens` into `execution.json`, populate `CorrelatedTask.cached_input_tokens` in `collectors.py` and `Job Cache` will automatically render `✓ <N> cached tok`.
- **Auto-Ontology physical FK discovery (`connectors/duckdb.py:215-220`)**:
  - `get_pks()` and `get_fks()` in Auto-Ontology's DuckDB connector are empty-DataFrame stubs (`data-layer /api/exploration/edges` returns 0), whereas semantic-layer `/column-attributes` (`referenced_columns`) and `/api/exploration/terms/.../path/...` return the `SEMANTIC_FK` edge.

---

## 5. Useful CLI Commands & Verification

```bash
# Interactive TUI (defaults to 120-col overview when non-TTY, or terminal width in TTY)
flow-top

# One-shot 120-column ANSI render of live state
flow-top --render --color --width 120

# Inspect a specific task by 1-based index (1..9, 0=10th) or task_id substring
flow-top --task help-orland-throughput-20261104 --render --color --width 120
flow-top --task help-ontology-relpack-20261104 --render --color --width 120

# Detail views (all full paths, SHA256 hashes, ports, schemas, and provenance)
flow-top --view path --render --color --width 156
flow-top --view services --render --color --width 156
flow-top --view caches --render --color --width 156
flow-top --view data --render --color --width 156

# Deterministic offline fixtures (active in-flight, completed, and degraded collector)
flow-top --fixture orland_live --render --color --width 120
flow-top --fixture orland_completed --render --color --width 120
flow-top --fixture degraded_collector --render --color --width 120

# Fault-isolation verification (simulates one or more collectors offline)
flow-top --simulate-unavailable ontology,service_bus --render --color --width 120

# Dump full JSON contract snapshot (includes monitor CPU% and RSS MiB)
flow-top --dump-json
```
