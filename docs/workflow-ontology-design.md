# Workflow Ontology Design

## Executive Summary

The current `commandcenter` vocabulary is fragmented across three layers:

- operator-facing workflow language in docs and board UI: `plan`, `build`, `assay`, `signed off`, `blocked`, `promotion`, `retro`
- run/artifact language: `run_id`, `events.jsonl`, `state.json`, `result.json`, `risk_score`, `lap1`, `draft PR`
- infrastructure/control language: `run_plan`, `get_progress`, `watch_progress`, `node_status`, `mcp.tool_call.*`

The first ontology should not replace any of those. It should define a stable business/operator vocabulary that survives changes in MCP, OTel, Jaeger, MAF, or dashboard implementation.

Supporting implementation artifacts added alongside this design:

- constitutional glossary: [laboratory-language.md](/C:/work/commandcenter/docs/laboratory-language.md)
- future abstraction evidence log: [future-capability-ontology-notes.md](/C:/work/commandcenter/docs/future-capability-ontology-notes.md)
- machine-readable event envelope: [workflow-event.schema.json](/C:/work/commandcenter/contracts/workflow-event.schema.json)
- reducer and validator tooling: [tools/workflow](/C:/work/commandcenter/tools/workflow)
- golden fixtures: [fixtures/workflow](/C:/work/commandcenter/fixtures/workflow)
- append-only durable emitter: [append_event.py](/C:/work/commandcenter/tools/workflow/append_event.py)
- run materializer for `events.jsonl -> state.json`: [materialize_run.py](/C:/work/commandcenter/tools/workflow/materialize_run.py)
- reference run-boundary emitter: [reference_runner.py](/C:/work/commandcenter/tools/workflow/reference_runner.py)
- L2 OTel event mapping adapter: [otel_adapter.py](/C:/work/commandcenter/tools/workflow/otel_adapter.py)
- deterministic projection helpers for board and OTel mirror: [projections.py](/C:/work/commandcenter/tools/workflow/projections.py)

Recommended shape:

- use machine-safe event names such as `work.accepted`, `planning.started`, `candidate.produced`
- treat ontology events as L2 semantic events
- treat major workflow phases as L1 semantic spans
- keep L0 infrastructure spans unchanged
- make durable run artifacts and `events.jsonl` the source of truth; traces are drilldown

The most important design decision is to separate:

- commands: what the system was asked to do
- events: what happened
- states: what is currently true
- artifacts: what was durably produced

That separation is the difference between a noisy execution log and an intelligible engineering-organization story.

## Current Workflow Vocabulary

This checkout still expresses most workflow vocabulary in docs and artifacts rather than executable workflow code. The strongest references are below.

### Intake / work request / inbox

- `Work items` dropped into `inbox/`:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:35)
- filename becomes `run id`:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:36)
- doc uses `idea-pipeline.service` and `idea-pipeline/capture.ndjson`:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:22)
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:67)
- external convergence docs use `idea`, `work request`, `idea.json`:
  - [MAF.png](/C:/work/commandcenter/MAF.png)
  - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:248)

Current terms:

- `work item`
- `idea`
- `work request`
- `inbox`
- `run id`

### Planning / refine / critic

- daemon runs `plan -> build -> assay`:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:53)
- architecture docs use planning-loop terms:
  - `analysis`
  - `decomposition`
  - `refine`
  - [MAF.png](/C:/work/commandcenter/MAF.png)
- external docs describe planner/critic loop and frozen plan:
  - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:34)
  - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:248)
- MCP/control terms include `run_plan`:
  - [fleet-worker-node/README.md](/C:/work/commandcenter/fleet-worker-node/README.md:18)
  - [fleet-worker-node/node.json](/C:/work/commandcenter/fleet-worker-node/node.json:23)
  - [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:250)

Current terms:

- `plan`
- `planning loop`
- `analysis`
- `decomposition`
- `refine`
- `planner`
- `critic`
- `run_plan`
- `.ember/PLAN.md` (external reference)

### Backlog / WIP / capacity

- board/status docs use `in progress`, `blocked`, `signed off`, `NOW horizon`:
  - [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:145)
  - [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:146)
  - [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:148)
- external docs use `WIP`, `capacity planning`, `backlog brief`:
  - [MAF.png](/C:/work/commandcenter/MAF.png)
  - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:34)
  - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:52)
- AM4 planning docs use `capacity`, `queue`, `risk class`:
  - [am4-fleet-node/results/2026-06-29-next-layer-tooling-plan.md](/C:/work/commandcenter/am4-fleet-node/results/2026-06-29-next-layer-tooling-plan.md:30)
  - [am4-fleet-node/results/2026-06-29-next-layer-tooling-plan.md](/C:/work/commandcenter/am4-fleet-node/results/2026-06-29-next-layer-tooling-plan.md:44)

Current terms:

- `WIP`
- `capacity`
- `blocked`
- `in progress`
- `signed off`
- `queue`
- `backlog brief`

### Builder / worker / segment / lap

- worker vocabulary:
  - `worker`, `claude-agent-worker`
  - [fleet-worker-node/README.md](/C:/work/commandcenter/fleet-worker-node/README.md:3)
  - [fleet-worker-node/node.json](/C:/work/commandcenter/fleet-worker-node/node.json:5)
- run topology:
  - parallel workers
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:53)
- branch/lap vocabulary:
  - `ccfarm/<id>/<worker>/lap1`
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:68)
- architecture image uses:
  - `builders grooming`
  - `builders planning`
  - `build segments`
  - `build segment status`
  - [MAF.png](/C:/work/commandcenter/MAF.png)

Current terms:

- `builder`
- `worker`
- `agent worker`
- `segment`
- `build segment`
- `lap1`
- `parallel workers`

### Grooming / question / answer / resume

- architecture image explicitly includes:
  - `builders grooming`
  - `questions, permission`
  - `progress history, questions asked`
  - [MAF.png](/C:/work/commandcenter/MAF.png)
- ADDP docs include `open questions`:
  - [CONSTELLATION-WAVE2-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-WAVE2-2026-06-28.md:267)
- this repo does not contain concrete `answer_question` or `resume_builder` code paths.

Current terms:

- `grooming`
- `question`
- `permission`
- `open questions`
- `resume` is mostly missing as a first-class term

### Candidate / branch / diff

- candidate branch as produced output:
  - `draft PR`
  - competing build branches named `ccfarm/<id>/<worker>/lap1`
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:68)
  - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:34)
- `winner` term appears in result and logs:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:60)
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:61)

Current terms:

- `candidate` is mostly implied, not standardized
- `branch`
- `draft PR`
- `winner`
- `diff` is not surfaced as a first-class ontology term yet

### Assay / grade / tiebreak

- `assay` is the current evaluation verb:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:53)
- `grade`, `risk_score`, `winner` in `result.json`:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:61)
- architecture image includes `quality grade, risk score`:
  - [MAF.png](/C:/work/commandcenter/MAF.png)
- explicit `tiebreak` term is absent from this checkout's code, but present in the larger semantic-tracing design direction.

Current terms:

- `assay`
- `grade`
- `risk_score`
- `winner`
- `tiebreak` not yet grounded in local code

### Risk / hold / promotion

- `promotion` is currently manual/open:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:72)
- `risk_score` is present in result vocabulary:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:61)
- AM4 docs use `risk class`, `danger`, `admission`:
  - [am4-fleet-node/results/2026-06-29-next-layer-tooling-plan.md](/C:/work/commandcenter/am4-fleet-node/results/2026-06-29-next-layer-tooling-plan.md:44)
  - [am4-fleet-node/scripts/am4-mcp-server.py](/C:/work/commandcenter/am4-fleet-node/scripts/am4-mcp-server.py:246)

Current terms:

- `risk_score`
- `risk class`
- `promotion`
- `manual/open`
- `hold` is not yet first-class in local artifacts

### Retro / replan / history

- AM4 docs already use `retrospective`:
  - [am4-fleet-node/results/README.md](/C:/work/commandcenter/am4-fleet-node/results/README.md:11)
  - [am4-fleet-node/results/2026-06-29-collaboration-retrospective.md](/C:/work/commandcenter/am4-fleet-node/results/2026-06-29-collaboration-retrospective.md:1)
- run/artifact docs use `events.jsonl`, `state.json`, `result.json` and immutable run dirs:
  - [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:92)
  - [CONSTELLATION-WAVE2-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-WAVE2-2026-06-28.md:196)
- `progress.md`, `conductor.log`, `capture.ndjson`, `history` language:
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:48)
  - [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:60)
  - [MAF.png](/C:/work/commandcenter/MAF.png)

Current terms:

- `retrospective`
- `retro`
- `history`
- `progress`
- `events.jsonl`
- `capture.ndjson`
- `replan` not yet formalized

### OTel / trace vocabulary already in use

- `fleet.node_status_call`
- `mcp.tool_call.node_status`
- `am4.node_status`
- [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:385)

This is useful debug vocabulary, but it is not yet business/operator vocabulary.

## Proposed Ontology

Conventions for all ontology events:

- canonical event names use lowercase dotted names
- event payloads should prefer stable IDs over display strings
- ontology events are L2 semantic events
- if a major phase lasts measurable time, it should also have an L1 parent span
- durable event log entries are authoritative; trace events mirror them when possible

### 1. `work.accepted`

- Human label: `Work Accepted`
- Description: A new unit of work entered the system and was accepted as a tracked workflow.
- Required IDs: `workflow_id`, `run_id`
- Optional IDs: `parent_run_id`, `artifact_id`, `trace_id`, `span_id`
- Required attributes: `source`, `input_ref`, `status=accepted`
- Optional attributes: `requester`, `priority`, `repo`, `title`
- Produced artifacts: inbox file, `idea.json`, initial run directory
- Triggering code path: inbox ingestion is documented at [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:35)
- Consuming surfaces: board, run history, intake metrics, learning corpus
- Type: event
- Operator attention: no
- Suggested OTel mapping: span event on `workflow.intake` or `workflow.plan`
- Durable event-log mapping: first event in `runs/<run_id>/events.jsonl`

### 2. `planning.started`

- Human label: `Planning Started`
- Description: Planning began for an accepted work item.
- Required IDs: `workflow_id`, `run_id`
- Optional IDs: `model_id`, `trace_id`, `span_id`
- Required attributes: `semantic.phase=planning`, `status=in_progress`
- Optional attributes: `planner_role`, `plan_strategy`, `input_artifact_ref`
- Produced artifacts: working plan draft
- Triggering code path: current equivalent is `run_plan`; documented in [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:256)
- Consuming surfaces: board, trace view, run history
- Type: event
- Operator attention: no
- Suggested OTel mapping: L1 span start `workflow.plan` plus span event
- Durable event-log mapping: `event_type=planning.started`

### 3. `planning.completed`

- Human label: `Planning Completed`
- Description: Planning ended with a stable plan suitable for routing/build.
- Required IDs: `workflow_id`, `run_id`
- Optional IDs: `artifact_id`, `model_id`, `trace_id`, `span_id`
- Required attributes: `outcome`, `plan_ref`
- Optional attributes: `critic_verdict`, `estimated_segments`, `estimated_builders`
- Produced artifacts: plan markdown/json such as `.ember/PLAN.md` or equivalent
- Triggering code path: external references describe frozen plan production; see [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:248)
- Consuming surfaces: board, run history, builder dispatch
- Type: event
- Operator attention: maybe
- Suggested OTel mapping: end of `workflow.plan`
- Durable event-log mapping: `event_type=planning.completed`

### 4. `backlog.entry_created`

- Human label: `Backlog Entry Created`
- Description: A plan or sub-plan was materialized into an operator-visible backlog item.
- Required IDs: `workflow_id`, `run_id`, `artifact_id`
- Optional IDs: `segment_id`, `trace_id`, `span_id`
- Required attributes: `title`, `status=queued`
- Optional attributes: `wip_bucket`, `capacity_hint`, `priority`
- Produced artifacts: backlog JSON/markdown/card
- Triggering code path: not found in local code; conceptually grounded by board and backlog docs
- Consuming surfaces: board, operator UI, run queue
- Type: artifact + event
- Operator attention: no
- Suggested OTel mapping: span event on `workflow.route`
- Durable event-log mapping: `event_type=backlog.entry_created`

### 5. `builder.assigned`

- Human label: `Builder Assigned`
- Description: A builder/worker/container/VM was assigned to a plan, segment, or candidate lane.
- Required IDs: `workflow_id`, `run_id`, `builder_id`
- Optional IDs: `segment_id`, `lap_id`, `model_id`, `trace_id`, `span_id`
- Required attributes: `assignment_scope`, `status=assigned`
- Optional attributes: `host`, `node`, `placement`, `role`
- Produced artifacts: dispatch record, route/assignment record
- Triggering code path: current routing is described in [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:308)
- Consuming surfaces: board, trace view, fleet status
- Type: event
- Operator attention: no
- Suggested OTel mapping: span event under `workflow.dispatch`
- Durable event-log mapping: `event_type=builder.assigned`

### 6. `builder.grooming_started`

- Human label: `Builder Grooming Started`
- Description: The builder began local preparation, context assembly, or segment grooming before coding.
- Required IDs: `workflow_id`, `run_id`, `builder_id`
- Optional IDs: `segment_id`, `lap_id`, `trace_id`, `span_id`
- Required attributes: `status=in_progress`
- Optional attributes: `context_refs`, `repo`, `branch`
- Produced artifacts: local grooming notes, prepared segment packet
- Triggering code path: concept present in [MAF.png](/C:/work/commandcenter/MAF.png)
- Consuming surfaces: trace view, operator drilldown
- Type: event
- Operator attention: no
- Suggested OTel mapping: L1 `workflow.builder` child phase or span event
- Durable event-log mapping: `event_type=builder.grooming_started`

### 7. `builder.grooming_completed`

- Human label: `Builder Grooming Completed`
- Description: Builder preparation finished and active implementation can begin.
- Required IDs: `workflow_id`, `run_id`, `builder_id`
- Optional IDs: `segment_id`, `lap_id`, `artifact_id`, `trace_id`, `span_id`
- Required attributes: `outcome`
- Optional attributes: `prepared_context_size`, `segment_count`
- Produced artifacts: groomed packet, local segment plan
- Triggering code path: not found in local code
- Consuming surfaces: trace view, run history
- Type: event
- Operator attention: no
- Suggested OTel mapping: event within `workflow.builder`
- Durable event-log mapping: `event_type=builder.grooming_completed`

### 8. `question.raised`

- Human label: `Question Raised`
- Description: A builder or planner raised a blocking question requiring clarification or permission.
- Required IDs: `workflow_id`, `run_id`, `question_id`
- Optional IDs: `builder_id`, `segment_id`, `lap_id`, `artifact_id`, `trace_id`, `span_id`
- Required attributes: `status=waiting_on_operator`, `operator.action_required=true`, `question_kind`
- Optional attributes: `severity`, `blocking=true`, `artifact.refs`
- Produced artifacts: `QUESTION.md` or question JSON record
- Triggering code path: concept present in [MAF.png](/C:/work/commandcenter/MAF.png) and ADDP `open questions` in [CONSTELLATION-WAVE2-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-WAVE2-2026-06-28.md:267)
- Consuming surfaces: board, notifications, run history, learning corpus
- Type: event
- Operator attention: yes
- Suggested OTel mapping: span event plus `operator.action_required=true`
- Durable event-log mapping: `event_type=question.raised`

### 9. `question.answered`

- Human label: `Question Answered`
- Description: Operator or system answered a previously raised question.
- Required IDs: `workflow_id`, `run_id`, `question_id`
- Optional IDs: `builder_id`, `artifact_id`, `trace_id`, `span_id`
- Required attributes: `answered_by`, `outcome`
- Optional attributes: `answer_ref`, `decision`
- Produced artifacts: answer artifact, updated question record
- Triggering code path: not found in local code
- Consuming surfaces: board, run history, builder resume logic
- Type: event
- Operator attention: no
- Suggested OTel mapping: span event
- Durable event-log mapping: `event_type=question.answered`

### 10. `builder.resumed`

- Human label: `Builder Resumed`
- Description: Builder resumed after an answer, retry, or hold release.
- Required IDs: `workflow_id`, `run_id`, `builder_id`
- Optional IDs: `question_id`, `segment_id`, `lap_id`, `trace_id`, `span_id`
- Required attributes: `resume_reason`
- Optional attributes: `resumed_from_state`
- Produced artifacts: updated progress state
- Triggering code path: not found in local code
- Consuming surfaces: board, trace view
- Type: event
- Operator attention: no
- Suggested OTel mapping: event within `workflow.builder`
- Durable event-log mapping: `event_type=builder.resumed`

### 11. `candidate.produced`

- Human label: `Candidate Produced`
- Description: A build candidate was produced and is ready for assay.
- Required IDs: `workflow_id`, `run_id`, `candidate_id`, `builder_id`
- Optional IDs: `lap_id`, `segment_id`, `artifact_id`, `trace_id`, `span_id`
- Required attributes: `candidate_ref`, `status=produced`
- Optional attributes: `branch`, `pr_ref`, `diff_stats`, `file_count`, `has_tests`
- Produced artifacts: branch, draft PR, manifest/result packet
- Triggering code path: branch and winner vocabulary are documented at [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:68)
- Consuming surfaces: assay, board, run history
- Type: event
- Operator attention: no
- Suggested OTel mapping: end event on `workflow.builder`
- Durable event-log mapping: `event_type=candidate.produced`

### 12. `assay.started`

- Human label: `Assay Started`
- Description: Candidate evaluation started.
- Required IDs: `workflow_id`, `run_id`, `assay_id`
- Optional IDs: `candidate_id`, `trace_id`, `span_id`
- Required attributes: `status=in_progress`
- Optional attributes: `assay_strategy`, `candidate_count`
- Produced artifacts: assay working state
- Triggering code path: current high-level workflow says `build -> assay`; [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:53)
- Consuming surfaces: board, traces, run history
- Type: event
- Operator attention: no
- Suggested OTel mapping: L1 span `workflow.assay`
- Durable event-log mapping: `event_type=assay.started`

### 13. `assay.passed`

- Human label: `Assay Passed`
- Description: Assay completed successfully for at least one candidate.
- Required IDs: `workflow_id`, `run_id`, `assay_id`, `candidate_id`
- Optional IDs: `risk_report_id`, `trace_id`, `span_id`
- Required attributes: `outcome=passed`
- Optional attributes: `grade`, `score`, `winner=true`
- Produced artifacts: assay result, updated `result.json`
- Triggering code path: `winner + full scoreboard` in [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:61)
- Consuming surfaces: board, promotion flow, history
- Type: event
- Operator attention: maybe
- Suggested OTel mapping: event on `workflow.assay`
- Durable event-log mapping: `event_type=assay.passed`

### 14. `assay.failed`

- Human label: `Assay Failed`
- Description: Assay completed without an acceptable candidate.
- Required IDs: `workflow_id`, `run_id`, `assay_id`
- Optional IDs: `candidate_id`, `trace_id`, `span_id`
- Required attributes: `outcome=failed`
- Optional attributes: `grade`, `failure_reason`, `retry_recommended`
- Produced artifacts: assay result, failure record
- Triggering code path: failure vocabulary exists in board and Discord notes at [MECHSUIT-MODERNIZATION-PLAN.html](/C:/work/commandcenter/MECHSUIT-MODERNIZATION-PLAN.html:346)
- Consuming surfaces: board, retry logic, operator review
- Type: event
- Operator attention: yes
- Suggested OTel mapping: event on `workflow.assay`
- Durable event-log mapping: `event_type=assay.failed`

### 15. `risk.scored`

- Human label: `Risk Scored`
- Description: A candidate or workflow received explicit risk evaluation.
- Required IDs: `workflow_id`, `run_id`, `risk_report_id`
- Optional IDs: `candidate_id`, `assay_id`, `trace_id`, `span_id`
- Required attributes: `risk.level`
- Optional attributes: `risk_score`, `risk_factors`, `recommended_action`
- Produced artifacts: risk report
- Triggering code path: `risk_score` exists in `result.json` vocabulary at [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:61)
- Consuming surfaces: board, promotion decisions, learning corpus
- Type: event
- Operator attention: maybe
- Suggested OTel mapping: event under `workflow.risk`
- Durable event-log mapping: `event_type=risk.scored`

### 16. `promotion.held`

- Human label: `Promotion Held`
- Description: Promotion was intentionally paused pending operator review or policy gate.
- Required IDs: `workflow_id`, `run_id`, `promotion_id`
- Optional IDs: `candidate_id`, `risk_report_id`, `trace_id`, `span_id`
- Required attributes: `status=waiting_on_operator`, `operator.action_required=true`
- Optional attributes: `hold_reason`, `risk.level`
- Produced artifacts: promotion hold record
- Triggering code path: current system says promotion is manual/open at [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:72)
- Consuming surfaces: board, notifications, run history
- Type: state transition event
- Operator attention: yes
- Suggested OTel mapping: event under `workflow.promote`
- Durable event-log mapping: `event_type=promotion.held`

### 17. `promotion.approved`

- Human label: `Promotion Approved`
- Description: A candidate was approved to move past the human gate.
- Required IDs: `workflow_id`, `run_id`, `promotion_id`, `candidate_id`
- Optional IDs: `trace_id`, `span_id`
- Required attributes: `promotion.decision=approved`
- Optional attributes: `approved_by`, `target_ref`
- Produced artifacts: approval record
- Triggering code path: not found in local code
- Consuming surfaces: board, history, release/publish surfaces
- Type: event
- Operator attention: no
- Suggested OTel mapping: event under `workflow.promote`
- Durable event-log mapping: `event_type=promotion.approved`

### 18. `promotion.rejected`

- Human label: `Promotion Rejected`
- Description: A candidate failed or was denied at the promotion gate.
- Required IDs: `workflow_id`, `run_id`, `promotion_id`
- Optional IDs: `candidate_id`, `risk_report_id`, `trace_id`, `span_id`
- Required attributes: `promotion.decision=rejected`
- Optional attributes: `rejected_by`, `reason`, `retry_recommended`
- Produced artifacts: rejection record
- Triggering code path: not found in local code
- Consuming surfaces: board, history, retry/replan logic
- Type: event
- Operator attention: yes
- Suggested OTel mapping: event under `workflow.promote`
- Durable event-log mapping: `event_type=promotion.rejected`

### 19. `retrospective.created`

- Human label: `Retrospective Created`
- Description: The run produced a retrospective or learning artifact.
- Required IDs: `workflow_id`, `run_id`, `retrospective_id`
- Optional IDs: `artifact_id`, `trace_id`, `span_id`
- Required attributes: `artifact.refs`
- Optional attributes: `scope`, `author_role`, `summary`
- Produced artifacts: retrospective markdown, ADDP artifacts, learning corpus entry
- Triggering code path: retros are part of the current artifact model in [CONSTELLATION-BASELINE-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-BASELINE-2026-06-28.md:92) and local AM4 retrospective artifacts such as [am4-fleet-node/results/2026-06-29-collaboration-retrospective.md](/C:/work/commandcenter/am4-fleet-node/results/2026-06-29-collaboration-retrospective.md:1)
- Consuming surfaces: run history, learning corpus, future planning
- Type: artifact + event
- Operator attention: no
- Suggested OTel mapping: event on `workflow.retro`
- Durable event-log mapping: `event_type=retrospective.created`

## Identity Model

Recommended first-class IDs:

- `workflow_id`: stable logical work item across retries, laps, and promotions
- `run_id`: one concrete execution attempt
- `parent_run_id`: links retries, replays, or resumed runs
- `lap_id`: one competitive builder lane or iteration, already implied by `lap1`
- `segment_id`: one decomposed unit of build work
- `builder_id`: stable worker/builder identity
- `model_id`: planner/judge/builder model identity
- `artifact_id`: stable artifact handle, not just path
- `question_id`: one blocking clarification thread
- `candidate_id`: one build candidate
- `assay_id`: one evaluation pass
- `risk_report_id`: one risk assessment record
- `promotion_id`: one promotion decision cycle
- `retrospective_id`: one retrospective artifact
- `trace_id`: trace correlation
- `span_id`: drilldown correlation

What already exists or is implied:

- `run_id`: explicit in onboarding docs and benchmark scripts
- `parent_run_id`: referenced in external convergence docs; [CONSTELLATION-WAVE2-2026-06-28.md](/C:/work/commandcenter/CONSTELLATION-WAVE2-2026-06-28.md:261)
- `lap_id`: implied by branch naming `lap1`; [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:68)
- `builder_id`: partly implied by worker/node identity such as `claudefarm1`; [fleet-worker-node/node.json](/C:/work/commandcenter/fleet-worker-node/node.json:2)
- `model_id`: present in AM4 model aliases like `vllama-planner`; [am4-fleet-node/node.json](/C:/work/commandcenter/am4-fleet-node/node.json:39)
- `trace_id`: explicit in onboarding docs; [ONBOARDING-QUICKSTART.md](/C:/work/commandcenter/ONBOARDING-QUICKSTART.md:66)

What is missing and should become first-class:

- `workflow_id`
- `segment_id`
- `artifact_id`
- `question_id`
- `candidate_id`
- `assay_id`
- `risk_report_id`
- `promotion_id`
- `retrospective_id`

Recommendation:

- `workflow_id` should be the primary long-lived business key
- `run_id` should remain execution-attempt scoped
- every ontology event should carry `workflow_id` and `run_id`
- any event involving human interruption or evaluation should also carry its domain ID (`question_id`, `assay_id`, `promotion_id`, etc.)

## Event vs State vs Artifact

Definitions:

- Event: something happened at a point in time
  - example: `question.raised`
- State: something is currently true until changed
  - example: `waiting_on_operator`
- Artifact: a durable object created or updated by the workflow
  - example: `QUESTION.md`, `result.json`, draft PR
- Command: an instruction issued to the system
  - example: `answer_question`, `run_plan`, `promote_candidate`

Decision note:

- `Decision` behaves like a cross-cutting class rather than a peer event family.
- Current decision-bearing events include:
  - `question.answered`
  - `promotion.held`
  - `promotion.approved`
  - `promotion.rejected`
- The durable event envelope should therefore carry decision metadata even when the event type itself remains workflow-specific.

Recommended conventions:

- events:
  - dotted past-tense nouns/verbs
  - examples: `planning.completed`, `candidate.produced`
- states:
  - snake_case adjectives/conditions
  - examples: `queued`, `in_progress`, `waiting_on_operator`, `held`, `approved`
- artifacts:
  - explicit `artifact_type`
  - examples: `plan`, `question`, `candidate`, `assay_result`, `risk_report`, `promotion_record`, `retro`
- commands:
  - imperative verbs
  - examples: `accept_work`, `start_planning`, `assign_builder`, `answer_question`, `approve_promotion`

Storage conventions:

- events:
  - append-only JSONL in `runs/<run_id>/events.jsonl`
- state:
  - latest projection in `runs/<run_id>/state.json`
- artifact:
  - immutable or versioned files under `runs/<run_id>/artifacts/`
- command:
  - request log or input envelope; not the source of truth for what happened

## OTel Mapping

Recommended trace model:

- L0 infrastructure/debug spans:
  - keep existing span names like `mcp.tool_call.node_status`
  - keep transport and tool detail
- L1 semantic phase spans:
  - `workflow.intake`
  - `workflow.plan`
  - `workflow.route`
  - `workflow.dispatch`
  - `workflow.builder`
  - `workflow.assay`
  - `workflow.risk`
  - `workflow.promote`
  - `workflow.retro`
- L2 ontology events:
  - emit as span events and durable JSONL events

Recommended attributes:

- `semantic.event`
- `semantic.phase`
- `semantic.layer`
- `operator.action_required`
- `artifact.refs`
- `status`
- `outcome`
- `risk.level`
- `promotion.decision`
- `workflow_id`
- `run_id`
- `parent_run_id`
- `lap_id`
- `segment_id`
- `builder_id`
- `model_id`

Recommended semantics:

- L0 spans: `semantic.layer=infra`
- L1 spans: `semantic.layer=workflow`
- L2 events: `semantic.layer=business`

Example:

- `workflow.builder` span
  - span event `question.raised`
  - span event `question.answered`
  - span event `builder.resumed`

Rule:

- Jaeger is not the source of truth
- traces are for drilldown, correlation, and timing
- durable artifacts and event logs are the system of record

## Durable Event Log Mapping

Recommended file:

- `runs/<run_id>/events.jsonl`

Recommended envelope:

```json
{
  "event_id": "evt_01J...",
  "event_type": "question.raised",
  "timestamp": "2026-07-02T10:15:30.123Z",
  "workflow_id": "wf_01J...",
  "run_id": "run_01J...",
  "parent_run_id": null,
  "lap_id": "lap_01",
  "segment_id": "seg_02",
  "question_id": "q_01",
  "decision_id": "dec_01",
  "decision_type": "question_answer",
  "decision_class": "question_answer",
  "decision_maker": {
    "type": "operator",
    "id": "derek"
  },
  "decision_reason": "clarified expected scope",
  "actor": {
    "type": "builder",
    "id": "builder-1",
    "model_id": "claude-opus-4.8"
  },
  "artifact_refs": [
    {
      "artifact_id": "art_01",
      "artifact_type": "question",
      "path": "runs/run_01/artifacts/questions/q_01.md"
    }
  ],
  "trace": {
    "trace_id": "69f0312f...",
    "span_id": "8f91..."
  },
  "operator_action_required": true,
  "status": "waiting_on_operator",
  "outcome": null,
  "payload": {
    "question_kind": "clarification",
    "blocking": true
  }
}
```

Recommended JSONL field rules:

- always required:
  - `event_id`
  - `event_type`
  - `timestamp`
  - `workflow_id`
  - `run_id`
  - `actor`
  - `status`
  - `payload`
- conditionally required by event type:
  - `builder_id`-equivalent in `actor.id` for builder events
  - `question_id` for question events
  - `candidate_id` for candidate events
  - `assay_id` for assay events
  - `promotion_id` for promotion events
- recommended:
  - `artifact_refs`
  - `trace`
  - `operator_action_required`
  - `outcome`

Recommended event log rules:

- append-only
- one row per ontology event
- no synthetic state-only rows
- `state.json` should be a projection over `events.jsonl`
- `result.json` should summarize terminal workflow outcome, not replace the event stream

## Open Design Notes

- `assay.passed` and `assay.failed` should be separate events rather than one overloaded `assay.completed` plus outcome. Operators care about the distinction at a glance.
- `promotion.held`, `promotion.approved`, and `promotion.rejected` should all exist independently. A hold is not a failure; it is a waiting state with business meaning.
- `builder.grooming_started` and `builder.grooming_completed` are worth keeping because the current architecture already distinguishes builder planning/grooming from actual implementation in [MAF.png](/C:/work/commandcenter/MAF.png).
- `question.raised` should be the first ontology event that explicitly sets `operator.action_required=true`. That becomes the clean join point across board UI, notifications, and traces.
- The next implementation step should be schema-first: define one shared event envelope and one ID vocabulary before touching traces or UI projections.
