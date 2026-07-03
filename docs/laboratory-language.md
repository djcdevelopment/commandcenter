# Laboratory Language

This document defines the constitutional language of the system. It is not an implementation guide. It is the shared meaning layer that traces, event logs, dashboards, artifacts, tests, and future workflows should all reference.

## Workflow

A `Workflow` is the long-lived business unit of work. It begins when work is accepted and remains stable across retries, builder laps, pauses, questions, assays, and promotion decisions. A workflow is identified by `workflow_id`.

## Run

A `Run` is one concrete execution attempt of a workflow. A workflow can have multiple runs over time due to retries, replans, or resumed execution. A run is identified by `run_id`.

## Lap

A `Lap` is one competitive or iterative execution lane within a run. It usually corresponds to one builder attempt, one branch lane, or one repeated pass over the same work. A lap is identified by `lap_id`.

## Segment

A `Segment` is a decomposed unit of build work inside a workflow or lap. Segments are the pieces builders groom, implement, and report on when work is not treated as one undivided blob. A segment is identified by `segment_id`.

## Builder

A `Builder` is the execution actor responsible for producing a candidate. It may be a worker VM, container, agent process, or future specialized subsystem. A builder is identified by `builder_id`.

## Candidate

A `Candidate` is a produced output that is eligible for assay. In software workflows this is usually a branch, diff, or draft PR. In other workflows it may be an image, report, dataset, or other artifact bundle. A candidate is identified by `candidate_id`.

## Artifact

An `Artifact` is a durable object produced or updated by the workflow. Plans, question records, result summaries, risk reports, retrospectives, and draft outputs are all artifacts. Artifacts are durable truth, not transient execution detail. An artifact is identified by `artifact_id`.

## Question

A `Question` is a blocking or decision-bearing request for clarification, permission, or missing information. Questions are raised by the workflow and answered by an operator or future automated decision layer. A question is identified by `question_id`.

## Decision

A `Decision` is a durable choice that changes workflow direction or state. Promotion approvals, promotion holds, promotion rejections, operator answers, overrides, workflow cancellations, and risk acceptances are all decisions. Decision is a cross-cutting class over multiple event types, not just another event name. Decisions should be queryable across workflows and should usually be representable as both events and durable records.

## Assay

An `Assay` is the evaluation step that measures whether a candidate is acceptable. It produces outcomes such as passed or failed, and often also produces grade, comparison, and supporting evidence. An assay is identified by `assay_id`.

## Risk

A `Risk` assessment is the explicit evaluation of uncertainty, danger, or policy concern around a candidate or workflow. Risk is not just a score; it is a decision input. A risk report is identified by `risk_report_id`.

## Promotion

A `Promotion` is the gate between a candidate being produced and a candidate being approved to move forward. Promotion may be held, approved, or rejected. A promotion cycle is identified by `promotion_id`.

## Retrospective

A `Retrospective` is a durable learning artifact created after or during a run. It records what happened, what was learned, and what should change next. A retrospective is identified by `retrospective_id`.
