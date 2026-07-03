# Semantic Tracing Discovery

## Executive Summary

This workspace is not the full `commandcenter` implementation. The strongest fact in this repo is in [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:8): the canonical code lives on `claude@100.74.110.91:~/work/commandcenter`, and `C:\work\commandcenter` is a deprecated stub. That matters because most of the requested MAF workflow engine paths, dashboard API code, and OpenTelemetry instrumentation are not present here.

What is present:

- MCP node-control surfaces for a generic worker and the AM4 services node:
  - [fleet-worker-node/scripts/worker-mcp-server.py](/C:/work/commandcenter/fleet-worker-node/scripts/worker-mcp-server.py:1)
  - [am4-fleet-node/scripts/am4-mcp-server.py](/C:/work/commandcenter/am4-fleet-node/scripts/am4-mcp-server.py:1)
- A simple MCP client used by the conductor-side demo:
  - [fleet-worker-node/scripts/mcp_call.py](/C:/work/commandcenter/fleet-worker-node/scripts/mcp_call.py:1)
- Architecture and operational docs that describe the intended workflow, traces, board, and artifact model:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:1)
  - [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:1)
  - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:90)
  - [CONSTELLATION-WAVE2-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-WAVE2-2026-06-28.md:255)
  - [MAF.png](/C:/work/commandcenter/MAF.png)

Bottom line:

- The current checkout shows the control-plane seam and some trace naming intent.
- It does not contain the MAF orchestration implementation needed to inventory `workflow.run`, `executor.process`, `route_node`, `edge_group.process`, `FanOutEdgeGroup`, `FanInEdgeGroup`, `tiebreak`, `promote`, or the board/backend code that computes operator-facing status.
- The safest plan is to keep raw spans, add semantic parent spans and MAF-native events, and build operator views from durable run artifacts/events rather than making Jaeger the source of truth.

## Current OTel Instrumentation

Direct code evidence in this workspace is thin.

1. No actual OTel SDK initialization code is present here.
   - Repo-wide search in this checkout found no implementation file defining `OpenTelemetry`, `trace.get_tracer`, `start_span`, `span.set_attribute`, or `span.add_event`.
   - The only explicit trace implementation references are in docs, not source.

2. Documented instrumentation shape:
   - [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:383) says OTel is initialized via a shared `otel_setup.py`, gated by `ENABLE_OTEL=true`, exporting OTLP to `http://am4.tail8e749c.ts.net:4318`.
   - The same document records one verified trace shape:
     - `cc-conductor::fleet.node_status_call`
     - `mcp.tool_call.node_status`
     - `am4-mcp-server::am4.node_status`
     - with propagation via `params._meta`
     - see [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:385) and [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:407)
   - [CONSTELLATION-WAVE2-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-WAVE2-2026-06-28.md:257) states the intended rule: every run is traced per-stage with GenAI semantic conventions and fixed-order middleware.
   - [CONSTELLATION-WAVE2-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-WAVE2-2026-06-28.md:305) describes explicit async context propagation as a known requirement: consumer handlers must recover parent context explicitly.

3. Span naming conventions that are evidenced here:
   - `fleet.node_status_call` for the conductor-side wrapper
   - `mcp.tool_call.<tool_name>` for the transport/tool seam
   - `am4.node_status` or `am4-mcp-server::am4.node_status` for the server-side tool body

4. Metrics:
   - [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:387) references `MeterProvider` and `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` counters in `ensemble_structure.py`, but that file is not in this repo.

Assessment:

- Current naming is execution-centric and transport-centric.
- The docs imply there is already enough infrastructure to add semantic spans without changing the export pipeline.

## Current Workflow Execution Model

The real workflow executor is not in this checkout. What is present is an architectural description plus a thin node-control slice.

What this repo directly shows:

1. High-level runtime model:
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:53) says the daemon runs `plan -> build (parallel workers) -> assay (scored winner)`.
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:72) says promotion is manual/open, not automatic.
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:77) marks `scripts/conductor.py` and `scripts/conductor_workflow.py` as deprecated, but those files are not present in this stub.

2. Workflow architecture from the diagram:
   - [MAF.png](/C:/work/commandcenter/MAF.png) shows:
     - planning loop: `analysis -> decomposition -> refine`
     - operator/scrum loop: `scrum master input -> builders grooming -> builders planning`
     - builder farm with multiple VM workers
     - artifacts like `handoff.md build segments`, `progress/history/questions asked`, `quality grade, risk score`, `retro`
     - QA feedback into risk/grade and operator loop

3. Canonical external implementation reference:
   - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:90) describes the inherited `planning/Farmer` system as a `.NET 9` control plane with a `7-stage pipeline per HTTP /trigger`, MAF, NATS, ObjectStore artifacts, and OpenTelemetry traces.

What I could not locate in this repo:

- `workflow.run`
- `executor.process`
- `route_node`
- `edge_group.process`
- `FanOutEdgeGroup`
- `FanInEdgeGroup`
- `finalize`
- `assay`
- `tiebreak`
- `promote`

Conclusion:

- The workflow model requested in items 2 and 7 exists conceptually in the docs and diagram, but the executable code paths are absent from this workspace.

## Current MCP / Tool Span Model

The MCP wrapper surface is the clearest implemented layer in this repo.

1. Conductor-side client
   - [fleet-worker-node/scripts/mcp_call.py](/C:/work/commandcenter/fleet-worker-node/scripts/mcp_call.py:27)
   - `call(session, tool)` does:
     - `session.initialize()`
     - `session.list_tools()`
     - `session.call_tool(tool, {})`
   - `main()` can connect over:
     - streamable HTTP
     - stdio
     - SSH-launched stdio via `node.json`

2. Generic worker MCP server
   - [fleet-worker-node/scripts/worker-mcp-server.py](/C:/work/commandcenter/fleet-worker-node/scripts/worker-mcp-server.py:33)
   - `FastMCP("fleet-worker-node")`
   - resources:
     - `worker://node` via `node_resource()`
   - tools:
     - `node_status()`
     - `ping()`
   - The module docstring and README say `run_plan`, `agent_status`, and `get_progress` arrive in S1, but those tools are not implemented in this checkout.

3. AM4 MCP server
   - [am4-fleet-node/scripts/am4-mcp-server.py](/C:/work/commandcenter/am4-fleet-node/scripts/am4-mcp-server.py:28)
   - `FastMCP("am4-fleet-node")`
   - resources:
     - `am4://node`
     - `am4://hermes/env`
     - `am4://gpu/render-owners`
   - tools include:
     - `node_status`
     - `render_owners`
     - `hermes_facade_health`
     - `hermes_models`
     - `hermes_ready`
     - `hermes_backend_status`
     - `start_hermes_backend`
     - `stop_hermes_backend`
     - `long_context_memory_plan`
     - `denning_bounds`
     - `llama_cpp_placement_modes`
     - `am4_operating_posture`
     - `accelerator_capabilities`
   - prompt:
     - `safe_hermes_long_context_run`

4. How these likely become spans
   - Docs show a 3-span pattern:
     - conductor wrapper span
     - MCP transport/tool-call span
     - server-side tool body span
   - Explicit evidence: [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:385)

5. Requested MCP tool names not present here
   - Not found in this checkout:
     - `initialize`
     - `tools/list`
     - `prompts/list`
     - `tools/call`
     - `message.send`
     - `get_progress`
     - `git_setup_branch`
     - `git_commit_push`
     - `assay_compare_branches`
     - `critique_tiebreak`

Assessment:

- The current MCP span story is low-level and mechanically useful.
- It needs semantic parents above it, not replacement of it.

## Current MAF Artifact Model

This repo contains artifact conventions in docs, not the full artifact-contract implementation.

What is evidenced:

1. Run-level durable artifacts
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:60) references `runs/<id>/result.json`
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:67) references durable event log `idea-pipeline/capture.ndjson`
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:68) references build branches named `ccfarm/<id>/<worker>/lap1`

2. External canonical artifact model
   - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:92) lists typed contracts:
     - `RunRequest`
     - `TaskPacket`
     - `Manifest` / `OutputArtifact`
     - `ReviewVerdict { Accept | Retry | Reject }`
     - `DirectiveSuggestion`
     - `RetryPolicy`
     - `RunStatus`
   - Same section describes immutable run dirs with:
     - `events.jsonl`
     - `state.json`
     - `result.json`
   - [CONSTELLATION-WAVE2-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-WAVE2-2026-06-28.md:196) reiterates `runs/{run_id}/` as immutable and reconstructable.

3. MAF diagram artifact nouns
   - [MAF.png](/C:/work/commandcenter/MAF.png) shows:
     - work request
     - product spec
     - structured epic/feature/story
     - handoff/build segments
     - build segment status
     - questions/permission
     - progress/history/questions asked
     - quality grade/risk score
     - retro

4. Requested concrete contract types not found here
   - No definitions in this repo for:
     - `QuestionRecord`
     - `RiskReport`
     - backlog item model
     - promotion record
     - assay result contract
     - builder status contract
     - workflow ID / artifact ID schema

Assessment:

- The durable artifact philosophy is clear.
- The actual schema definitions live elsewhere.
- That is a good sign for projection work: semantic views should be artifact-first, not trace-first.

## Current Dashboard / Board Model

The dashboard implementation is also absent from this checkout; only references and one static HTML planning board are present.

What is present:

1. External runtime surfaces
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:21) references `commandcenter-api.service`
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:31) and [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:65) reference `http://100.74.110.91:8080/fleet-dashboard.html`
   - There is no API or dashboard source file for that endpoint in this repo.

2. Polling/status model
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:53) says the daemon polls `inbox/` every ~3s.
   - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:60) points operators at `conductor.log` and `runs/<id>/result.json`.
   - The same document uses `trace_id` as a manual correlation handle into Jaeger.

3. Existing board semantics in static HTML
   - [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:150) contains a status board with semantic sections like:
     - signed off
     - in progress
     - now horizon
     - blocked
   - [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:400) explicitly states two surfaces:
     - detailed dashboard
     - Discord ambient/mobile view
     - both fed from the same event stream

Assessment:

- Semantic state is already being computed somewhere conceptually: signed off, blocked, now horizon, completion, failure.
- The projection should reuse those business states instead of asking Jaeger to become the board.

## Current Span Attribute Inventory

Direct attribute inventory is limited because no span-construction code is present here.

Observed in code/docs:

- `trace_id`
  - operator correlation field mentioned in [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:66)
- propagated parent context via `params._meta`
  - documented in [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:385)
- service identity by span/service naming:
  - `cc-conductor`
  - `am4-mcp-server`
- tool identity embedded in name:
  - `mcp.tool_call.node_status`
- some run-ish fields exist in artifacts/scripts, not spans:
  - `run_id` in AM4 benchmark scripts such as [am4-fleet-node/scripts/service-path-benchmark.py](/C:/work/commandcenter/am4-fleet-node/scripts/service-path-benchmark.py:326)
  - branch convention `ccfarm/<id>/<worker>/lap1` in [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:68)
  - `risk_score` in `result.json` per [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:61)

Requested attribute check:

- `workflow_id`: not found in this repo
- `run_id`: present in artifact/docs and AM4 benchmark scripts; not evidenced on spans
- `lap_id`: implied by branch naming `lap1`; not evidenced on spans
- `segment_id`: not found
- `builder_id`: not found
- `model_id`: not found on spans here; model aliases exist in [am4-fleet-node/node.json](/C:/work/commandcenter/am4-fleet-node/node.json:1)
- `role`: node roles exist in node manifests; not evidenced on spans
- `artifact_refs`: not found
- `maf_message_type`: not found
- `semantic_layer`: not found
- `semantic_event`: not found
- `operator_action_required`: not found
- `risk_level`: not found; only `risk_score` is referenced
- `promotion_decision`: not found
- `status/outcome`: some result payloads and service statuses exist, but not as known span attributes
- `error_class`: not found

## Gaps

1. Source gap
   - The canonical conductor/workflow repo is not this repo.

2. Instrumentation gap
   - No direct span builder code here, so current attribute practice cannot be fully audited from source.

3. Semantic gap
   - The evidenced span names are transport-centric and tool-centric.
   - The desired business phases exist in docs, board language, and diagrams, but not in trace vocabulary.

4. Contract gap
   - Artifact philosophy is clear; concrete schema definitions for questions, risk, promotion, builder state, and assay are absent here.

5. UI gap
   - Dashboard source/API is missing, so current semantic-state computation cannot be inspected directly.

## Proposed Semantic Convention

Use three layers simultaneously. Do not replace L0.

Span naming:

- L0 raw spans: keep existing names
  - `mcp.tool_call.node_status`
  - `message.send`
  - `tools/list`
  - `tools/call`
- L1 workflow spans: stable phase names
  - `workflow.plan`
  - `workflow.route`
  - `workflow.dispatch`
  - `workflow.builder`
  - `workflow.fan_in`
  - `workflow.assay`
  - `workflow.risk`
  - `workflow.tiebreak`
  - `workflow.promote`
- L2 business/operator events: emit as span events or parallel durable events
  - `work.accepted`
  - `plan.produced`
  - `builders.dispatched`
  - `builder.blocked`
  - `question.opened`
  - `operator.answered`
  - `assay.completed`
  - `risk.scored`
  - `promotion.held`
  - `promotion.approved`

Recommended attributes on all L1 spans:

- `workflow_id`
- `run_id`
- `parent_run_id` when applicable
- `lap_id`
- `builder_id` when applicable
- `model_id`
- `role`
- `status`
- `outcome`
- `semantic_layer` = `workflow`
- `semantic_phase`
- `operator_action_required` = boolean
- `artifact_refs` = array/stringified refs

Recommended attributes on L2 events:

- `semantic_layer` = `business`
- `semantic_event`
- `run_id`
- `workflow_id`
- `builder_id` when applicable
- `risk_level`
- `promotion_decision`
- `error_class`
- `question_id` when applicable
- `artifact_refs`

Recommended attributes on L0 spans:

- keep transport/tool details
- add correlation only:
  - `run_id`
  - `workflow_id`
  - `builder_id`
  - `semantic_layer` = `infra`

## Projection Strategy

Recommended approach: combine three techniques.

1. Keep existing raw span names.
   - Best for debugging.
   - Avoids breaking current Jaeger queries and habits.

2. Add semantic parent spans around noisy subtrees.
   - Best ROI.
   - Example:
     - `workflow.dispatch`
       - `mcp.initialize`
       - `tools/list`
       - `tools/call.run_plan`
       - `message.send`
   - This makes the timeline readable without losing detail.

3. Emit MAF-native durable events alongside spans.
   - Questions, hold states, risk decisions, retries, promotion approvals should not live only inside Jaeger.
   - The board should project from durable run artifacts/events first, with trace links as drilldown.

4. Build a derived projection view for operator UI.
   - Use spans plus artifacts to construct:
     - current phase
     - blocked/waiting state
     - active builders
     - last operator action needed
     - risk/promotion outcome
   - Do not rename source spans aggressively.

I would not make Jaeger the operator UI, and I would not rely on source-span renaming alone.

## Incremental Plan

1. Inventory / current-state report
   - Run the same audit against the canonical host repo.
   - Capture actual span creation sites, middleware ordering, and current attributes.
   - Add one golden trace example for a representative run.

2. Semantic attribute schema
   - Define a shared schema doc for:
     - `run_id`, `workflow_id`, `lap_id`, `builder_id`
     - `semantic_layer`, `semantic_phase`, `semantic_event`
     - `status`, `outcome`, `risk_level`, `promotion_decision`, `operator_action_required`
   - Add unit tests for attribute presence on major phase spans.

3. Parent semantic spans for major workflow phases
   - Wrap planning, routing, dispatch, builder execution, assay, risk, tiebreak, promotion.
   - Keep all current child spans.
   - Add trace golden tests that assert the parent/child tree shape.

4. Board projection endpoint
   - Build an endpoint that reads durable run artifacts/events and emits semantic status.
   - Include trace links, but do not require Jaeger to answer core board questions.

5. Collapse/hide infra spans by default
   - In UI or query presets, default to L1/L2 semantic views.
   - Let operators expand L0 only when debugging.

6. MAF event stream alignment
   - Align durable run events and span events with the same IDs and status vocabulary.
   - Ensure questions, retries, holds, and promotion decisions exist as first-class events.

7. Regression tests / trace golden tests
   - Golden traces for:
     - happy path
     - builder blocked / question opened
     - assay failure
     - retry
     - promotion hold
   - Artifact/trace consistency tests:
     - `result.json` agrees with semantic trace outcome
     - question artifacts agree with `question.opened` / `operator.answered`

## Risks / Avoidances

1. Avoid breaking existing traces
   - Keep L0 names and exporters stable.

2. Avoid losing debug detail
   - Add parent spans and attributes; do not flatten the tree into one long semantic span.

3. Avoid over-renaming
   - Renaming every low-level span will create churn without improving operator readability.

4. Avoid making Jaeger the operator UI
   - Jaeger is a drilldown and debugging surface, not the durable workflow record.

5. Avoid making the dashboard depend on Jaeger availability
   - Durable artifacts and event logs should remain the truth source.

6. Avoid semantic drift between board and traces
   - Reuse one shared status vocabulary across artifacts, events, and span attributes.

7. Avoid inferring missing facts from this stub repo
   - The next audit needs to run on the canonical repo before implementation.

## Open Questions

1. Can you provide access to the canonical host repo at `claude@100.74.110.91:~/work/commandcenter`? The missing workflow and board code appears to live there.

2. Where is the current OTel initializer (`otel_setup.py`) and the conductor span wrapper code that produced `fleet.node_status_call`?

3. Where do the current durable run artifacts live in the canonical repo, and what schemas exist today for:
   - result
   - question
   - assay
   - promotion
   - retry
   - builder state

4. Is the board currently artifact-driven, event-driven, or trace-driven?

5. Do you want L2 business/operator events stored only in trace span events, or also emitted into the durable event log / run directory?
