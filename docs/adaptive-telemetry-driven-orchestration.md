# Adaptive Telemetry-Driven Orchestration

Date: 2026-07-02
Status: research design
Scope: current `C:\work\commandcenter` checkout plus source-backed prior art

## Executive Summary

The shortest path to an adaptive execution platform is not a new orchestrator. It is a small evidence layer around the existing ontology:

1. Keep `Workflow`, `Run`, `Lap`, `Segment`, `Builder`, `Candidate`, `Artifact`, `Question`, `Decision`, `Assay`, `Risk`, `Promotion`, and `Retrospective` as the constitutional business vocabulary.
2. Keep ontology events as the canonical business event stream.
3. Add capacity evidence as durable artifacts referenced by existing events, especially `builder.assigned`, `candidate.produced`, `assay.*`, `risk.scored`, and `retrospective.created`.
4. Build deterministic projections from those events and artifacts:
   - current run state
   - fleet inventory state
   - live resource state
   - capacity estimates
   - scheduler decision history
   - dashboards and analytics
5. Use OpenTelemetry for correlation, timing, and infrastructure metrics, not as the source of business truth.
6. Start with an explainable scheduler: compatibility filter, capacity/admission estimator, warm-state/locality scoring, reservation, then evidence collection.

The repository already contains the beginning of this shape:

- the ontology glossary and event envelope in [docs/laboratory-language.md](laboratory-language.md) and [contracts/workflow-event.schema.json](../contracts/workflow-event.schema.json);
- event validation, append, projection, and materialization tooling in [tools/workflow](../tools/workflow);
- durable workflow fixtures in [fixtures/workflow](../fixtures/workflow);
- MCP node-control surfaces in [fleet-worker-node](../fleet-worker-node) and [am4-fleet-node](../am4-fleet-node);
- AM4 benchmark artifacts that already behave like capacity evidence in [am4-fleet-node/results](../am4-fleet-node/results);
- design notes that explicitly say `events.jsonl` and run artifacts are truth, while traces are drilldown.

The missing piece is normalization. AM4 has measured service-path data, but that data is not yet represented as capacity evidence attached to workflow runs. The workflow reducer can project operator state, but there is no capacity reducer. The OTel story has correlation intent, but the current checkout has no executable OTel SDK initialization or semantic instrumentation code.

Recommended near-term target:

- define `MachineProfile`, `AcceleratorProfile`, `BuilderCapabilityProfile`, `ModelCapabilityProfile`, `WorkloadShape`, `SchedulerDecision`, and `CapacityObservation` contracts;
- do not add new ontology event types yet;
- attach scheduler decisions and capacity observations through `artifact_refs` and event `payload`;
- add a `capacity_knowledge` projection that reads event logs plus referenced evidence artifacts;
- extend AM4's existing request-cost/admission plan into a node-local policy surface exposed through MCP;
- let the global scheduler consume only explainable projected estimates until enough evidence exists to justify more adaptive policy.

## Current-State Assessment

### Ontology And Event Stream

The current ontology is intentionally business-facing. [docs/laboratory-language.md](laboratory-language.md) defines the stable meanings for `Workflow`, `Run`, `Lap`, `Segment`, `Builder`, `Candidate`, `Artifact`, `Question`, `Decision`, `Assay`, `Risk`, `Promotion`, and `Retrospective`. `Workflow` is long-lived business work, while `Run` is one concrete execution attempt. That distinction is essential for capacity learning because a single workflow may produce several empirical attempts.

[contracts/workflow-event.schema.json](../contracts/workflow-event.schema.json) defines the durable event envelope. It already has the fields needed to correlate future capacity evidence:

- stable event identity: `event_id`, `event_type`, `timestamp`;
- business identity: `workflow_id`, `run_id`, `parent_run_id`, `lap_id`, `segment_id`;
- domain identities: `question_id`, `candidate_id`, `assay_id`, `risk_report_id`, `promotion_id`, `retrospective_id`, `decision_id`;
- actor identity, including `actor.model_id`;
- `artifact_refs`;
- `trace.trace_id` and `trace.span_id`;
- `status`, `outcome`, and free-form `payload`.

[tools/workflow/ontology.py](../tools/workflow/ontology.py) enumerates current event types and maps them to phases. [tools/workflow/validate_events.py](../tools/workflow/validate_events.py) enforces required fields for business events. [tools/workflow/project_state.py](../tools/workflow/project_state.py) projects the latest run state from events, including decisions, artifact refs, active domain IDs, and per-builder last event. [tools/workflow/materialize_run.py](../tools/workflow/materialize_run.py) materializes `events.jsonl` into `state.json`.

Assessment:

- The workflow projection mechanism is real and should be extended, not bypassed.
- The current event types are enough for scheduling and capacity evidence references.
- The current state reducer is run-state focused; it does not yet project fleet, capacity, or scheduler knowledge.
- `artifact_refs` is the right immediate extension point.

### Existing Telemetry Integration

[docs/semantic-tracing-discovery.md](semantic-tracing-discovery.md) says the current checkout does not contain executable OTel SDK initialization code. The OTel initializer, if present, is in the canonical host repo, not in this Windows checkout.

The same document records the intended shape:

- keep raw infrastructure spans;
- add semantic parent spans;
- emit MAF-native durable events;
- build operator views from durable run artifacts and events, not from Jaeger.

[MECHSUIT-MODERNIZATION-PLAN.html](../MECHSUIT-MODERNIZATION-PLAN.html) records one verified trace shape:

- `cc-conductor::fleet.node_status_call`
- `mcp.tool_call.node_status`
- `am4-mcp-server::am4.node_status`

It also records OTel Collector, Jaeger, Prometheus, Grafana, and GenAI token counters as part of the intended observability track.

Assessment:

- OTel is already positioned correctly as middleware and drilldown.
- The repo lacks the code needed to enforce semantic attributes today.
- Semantic tracing should be added as correlation around the durable events, not a second business event stream.

### Fleet And Capacity Surfaces

[fleet-worker-node](../fleet-worker-node) is a reusable GPU-agnostic worker template. It exposes MCP over SSH stdio, a `worker://node` resource, and `node_status` / `ping` tools. `node_status` reports OS, Python, CPU count, load average, disk free/total, and tool availability.

[am4-fleet-node](../am4-fleet-node) is more developed. [am4-fleet-node/node.json](../am4-fleet-node/node.json) already contains:

- host identity and roles;
- two Intel Battlemage / Arc Pro B70 devices;
- observed per-device memory;
- endpoints for SSH, MCP, Jaeger, OTLP, optional NATS, and Hermes;
- model aliases such as `vllama-planner`;
- placement, context, and KV cache settings.

[am4-fleet-node/scripts/am4-mcp-server.py](../am4-fleet-node/scripts/am4-mcp-server.py) exposes capacity-relevant tools:

- `render_owners`;
- `hermes_ready`;
- `hermes_backend_status`;
- `long_context_memory_plan`;
- `denning_bounds`;
- `llama_cpp_placement_modes`;
- `am4_operating_posture`;
- `accelerator_capabilities`.

The benchmark artifacts in [am4-fleet-node/results](../am4-fleet-node/results) are already evidence:

- [2026-06-29-sycl-context-ladder.md](../am4-fleet-node/results/2026-06-29-sycl-context-ladder.md) measures prompt and generation throughput across context depths and placements.
- [2026-06-29-service-path-ladder.md](../am4-fleet-node/results/2026-06-29-service-path-ladder.md) measures served-path TTFT, prompt tok/s, generation tok/s, and total latency.
- [2026-06-29-service-path-concurrency-ladder.md](../am4-fleet-node/results/2026-06-29-service-path-concurrency-ladder.md) shows concurrent prompt ingest dominating and streaming usefulness collapsing under load.
- [2026-06-29-next-layer-tooling-plan.md](../am4-fleet-node/results/2026-06-29-next-layer-tooling-plan.md) already proposes a request cost estimator, admission control, queue policy, prompt-cache-aware scheduling, and MCP scheduler visibility.

Assessment:

- AM4 is the strongest existing foundation for capacity learning.
- The data is evidence-rich but manually summarized.
- The scheduler should first automate the evidence shape already present, not invent a broader platform.

### Repository Boundary

[ONBOARDING-QUICKSTART.md](../ONBOARDING-QUICKSTART.md) states that this checkout is a deprecated stub and that the canonical implementation lives on `claude@100.74.110.91:~/work/commandcenter`. That limits direct implementation conclusions:

- the conductor, dashboard API, and current OTel initializer are not present here;
- current NATS/ObjectStore contracts are only described in docs;
- actual workflow execution code paths must be audited in the canonical repo before code-level changes.

The design below therefore treats this checkout as the constitutional and experimental seed, not the complete runtime.

## Architectural Principle

Do not put everything into the ontology. Put every scheduling-relevant fact behind a durable, replayable reference that the ontology can point at.

| Concern | Recommended representation | Reason |
|---|---|---|
| Business lifecycle | Ontology event | Canonical operator truth. |
| Durable run output | Artifact referenced by event | Keeps event rows small and replayable. |
| Static hardware facts | Inventory metadata artifact or MCP resource | Changes slowly; not a workflow event by itself. |
| Raw high-frequency telemetry | OTel metrics/logs or local telemetry files | Too high-cardinality and too frequent for business events. |
| Scheduling-relevant measurement summary | `CapacityObservation` artifact referenced by event | Evidence needs replay, provenance, and confidence. |
| Current availability | Projection | Derived from inventory, telemetry snapshots, reservations, and events. |
| Learned capacity estimate | Projection | Derived from accumulated evidence; can be rebuilt. |
| Scheduler choice | `SchedulerDecision` artifact referenced by `builder.assigned` | Assignment is already the ontology event; the decision explanation belongs beside it. |

This keeps the event stream canonical without turning it into a GPU metrics database.

## Capacity Modeling

### Recommended Concepts

| Concept | Primary home | Examples | Notes |
|---|---|---|---|
| `MachineProfile` | Inventory metadata | host, OS, CPU, RAM, disk, network endpoints, service roles | Start from `node.json` and MCP `node` resources. |
| `AcceleratorProfile` | Inventory metadata plus projected state | GPU vendor/model, device IDs, VRAM observed, driver/runtime, health source | Keep current temperature, power, utilization, and occupancy out of static inventory. |
| `BuilderCapabilityProfile` | Inventory metadata plus projected state | MCP tools, available models, git/Claude/tool availability, isolation mode | A builder is an ontology actor, but its capabilities are projected metadata. |
| `ModelCapabilityProfile` | Inventory metadata plus learned projection | alias, backend, quantization, configured context, measured context, KV bytes/token | Separate declared limits from observed behavior. |
| `WorkloadShape` | Event payload and scheduler artifact | estimated prompt tokens, expected output tokens, repo size, test intensity, risk class | Derived from `Workflow` / `Segment`; not a standalone ontology entity yet. |
| `CapacityObservation` | Artifact referenced by ontology event | load time, TTFT, prompt tok/s, generation tok/s, VRAM high-water, failure mode | This is the core evidence unit. |
| `CapacityEstimate` | Projection | p50/p95 load time for model X on builder A, confidence, sample count | Rebuildable from observations. |
| `LiveResourceState` | Projection from telemetry and reservations | ready models, warm cache hints, queue depth, in-flight jobs, current headroom | Used by scheduler, never treated as durable truth. |

### Representation Tradeoffs

#### Ontology Entity

Use only when the concept is part of the business workflow story operators must reason about over time.

Good candidates:

- `Builder` as an execution actor.
- `Artifact` as durable evidence.
- `Decision` when the routing or promotion decision matters to workflow history.

Poor candidates:

- every GPU;
- every temperature sample;
- every model load measurement;
- every network interface.

Tradeoff:

- Pro: queryable in the canonical history.
- Con: ontology drift and noisy event streams if infrastructure details become business events too early.

Recommendation:

- Do not add `Machine`, `GPU`, or `ModelProfile` as core ontology entities now.
- Reference them through IDs in event payloads and artifacts: `builder_id`, `node_id`, `accelerator_ids`, `model_id`, `profile_snapshot_id`.

#### Projected State

Use for facts that must be current but are derived.

Examples:

- builder availability;
- model warm/cold state;
- current queue depth;
- rolling p95 TTFT by model/builder/context bucket;
- current VRAM headroom.

Tradeoff:

- Pro: scheduler can read a compact view.
- Con: can go stale and must be rebuildable.

Recommendation:

- Create a capacity reducer beside `project_state.py`, not inside it at first.
- Keep run-state and fleet-capacity-state separate projections over shared event/artifact inputs.

#### Telemetry Only

Use for raw, high-frequency, operational measurements.

Examples:

- per-second GPU utilization;
- temperature and power series;
- CPU and RAM gauges;
- disk I/O counters;
- network counters;
- engine logs.

Tradeoff:

- Pro: cheap to collect and aggregate with observability tools.
- Con: weak provenance unless summarized and linked to a workflow run.

Recommendation:

- Collect raw telemetry through OTel/Prometheus/log files.
- At workflow boundaries, summarize scheduling-relevant telemetry into immutable `CapacityObservation` artifacts.

#### Inventory Metadata

Use for declared or slow-changing facts.

Examples:

- hostnames and endpoints;
- GPU model and observed VRAM;
- runtime backends;
- configured model aliases;
- supported MCP tools;
- disk class and network placement.

Tradeoff:

- Pro: simple discovery and compatibility filtering.
- Con: static inventory is often wrong about actual capacity.

Recommendation:

- Treat inventory as claims.
- Treat observations as evidence.
- Treat projections as current best estimates.

## Execution Telemetry

The scheduler does not need every metric. It needs metrics that change placement, admission, priority, warm reuse, or risk decisions.

### Highest-Value Scheduling Metrics

| Metric | Scheduling use | Evidence type |
|---|---|---|
| Workload shape: prompt tokens, expected output tokens, context ratio | Predict KV footprint, latency, memory risk | `WorkloadShape` payload/artifact |
| Model cold load time | Decide wait vs start elsewhere; warm pool sizing | `CapacityObservation` |
| Warm readiness probe time | Decide if loaded model is actually usable | metric plus observation |
| Queue wait time | Priority, fairness, congestion, backpressure | metric and scheduler artifact |
| Admission decision and reason | Explain queue/defer/reject; train future policy | `SchedulerDecision` |
| TTFT | User-visible latency and streaming usefulness | histogram and observation |
| Prompt processing rate / prefill tok/s | Dominant long-context cost; placement comparison | observation |
| Generation tok/s / decode tok/s | Output latency; model/backend suitability | observation |
| VRAM high-water and free headroom | Fit/admission, context limits, spill avoidance | telemetry summary |
| Host RAM pressure and swap/spill signals | Detect cliff conditions | telemetry summary |
| Model residency and cache state | Prefer warm builder or prefix-cache locality | projected state |
| Prefix/KV cache hit rate | Reuse scheduling and cost reduction | metric and observation |
| Batch size, slot occupancy, scheduler queue delay | Batching effectiveness and concurrency limits | metrics |
| Failure mode: OOM, timeout, segfault, readiness failure, port collision | Hard filter and risk estimate | observation |
| Temperature/throttle state | Avoid sustained thermal degradation | metric plus observation |
| Assay result by builder/model/workload shape | Quality-aware scheduling | event plus projection |

### Conditionally Useful Metrics

| Metric | Useful when | Risk if misused |
|---|---|---|
| GPU utilization | Correlated with phase and queue state | Average utilization alone hides memory and cache bottlenecks. |
| CPU utilization | CPU-bound preprocess, agent tools, compression, tests | Generic CPU average rarely predicts LLM placement. |
| PCIe bandwidth | Multi-GPU placement, host spill, model load, KV movement | Raw counters without phase context are ambiguous. |
| Disk activity | Cold model load, artifact checkout, test runs | Not useful for warm inference decisions unless cold load matters. |
| Network RTT/throughput | Remote builder placement, artifact movement, MCP latency | Can distract from model runtime if not a real bottleneck. |
| Power consumption | Cost, throttling, thermal envelope | Interesting but not schedulable unless tied to limits. |

### Mostly Monitoring, Not Scheduling

These should remain in observability unless a specific decision uses them:

- raw per-second utilization streams;
- absolute temperature below throttle range;
- fan speed;
- generic process counts;
- request chunk count;
- raw stdout/stderr volume;
- total disk bytes over long windows;
- average CPU load without workload phase;
- dashboard-only health colors.

### Phase Capture Model

Capture capacity data around phase boundaries:

1. `work.accepted` / `planning.completed`
   - estimated workload shape;
   - expected model/tool requirements.
2. Scheduler pre-assignment
   - current projected fleet state;
   - compatibility filter result;
   - selected builder/model/placement;
   - explanation and alternatives.
3. `builder.assigned`
   - `SchedulerDecision` artifact ref;
   - inventory/profile snapshot refs.
4. Builder execution
   - model load, readiness, prompt/decode metrics, resource high-water marks.
5. `candidate.produced`
   - `CapacityObservation` artifact ref for builder execution.
6. `assay.*` and `risk.scored`
   - quality and risk outcomes linked back to builder/model/workload.
7. `retrospective.created`
   - capacity findings and confidence updates.

## OpenTelemetry Integration

The guiding rule is: business events and infrastructure telemetry should correlate, not collapse into one another.

### Spans

Use spans for timed operations that have start/end duration.

Recommended span layers:

- L0 infrastructure/debug spans:
  - `mcp.tool_call.node_status`
  - HTTP calls to Hermes;
  - SSH/MCP transport calls;
  - backend process launch;
  - readiness probes.
- L1 workflow phase spans:
  - `workflow.plan`
  - `workflow.dispatch`
  - `workflow.builder`
  - `workflow.assay`
  - `workflow.risk`
  - `workflow.promote`
  - `workflow.retro`
- Scheduler spans:
  - `scheduler.evaluate`
  - `scheduler.reserve`
  - `scheduler.admit`
  - `scheduler.release`
- Inference spans:
  - `gen_ai.request`
  - `model.load`
  - `model.ready_probe`
  - `model.prefill`
  - `model.decode`

Span attributes should be stable and bounded:

- `workflow_id`
- `run_id`
- `lap_id`
- `segment_id`
- `builder_id`
- `node_id`
- `model_id`
- `candidate_id`
- `assay_id`
- `scheduler_decision_id`
- `capacity_observation_id`
- `semantic.layer`
- `semantic.phase`
- `status`
- `outcome`
- `placement.mode`
- `workload.context_tokens_bucket`
- `workload.output_tokens_bucket`

Avoid putting large prompts, full artifacts, high-cardinality raw paths, or full telemetry series in span attributes.

### Span Events

Use span events for point-in-time occurrences inside a span.

Good span events:

- mirror ontology events such as `builder.assigned`, `question.raised`, `candidate.produced`;
- `scheduler.candidate_filtered`;
- `scheduler.decision_recorded`;
- `model.cache_hit`;
- `model.cache_miss`;
- `model.throttle_detected`;
- `resource.spill_detected`;
- `capacity.observation_written`;
- `assay.verdict_recorded`.

The durable ontology event remains authoritative. The span event is the trace-local pointer.

### Metrics

Use metrics for aggregate numerical behavior:

- histograms:
  - `scheduler.queue_wait.duration`
  - `scheduler.decision.duration`
  - `model.load.duration`
  - `model.ready_probe.duration`
  - `gen_ai.client.time_to_first_token`
  - `gen_ai.server.prefill.tokens_per_second`
  - `gen_ai.server.decode.tokens_per_second`
- counters:
  - `scheduler.admission.accepted`
  - `scheduler.admission.queued`
  - `scheduler.admission.rejected`
  - `model.load.failures`
  - `capacity.observation.count`
  - `assay.passed`
  - `assay.failed`
- gauges:
  - `scheduler.queue.depth`
  - `builder.active_runs`
  - `model.resident.count`
  - `gpu.vram.used`
  - `gpu.vram.free`
  - `gpu.temperature`
  - `gpu.power`
  - `host.ram.available`

Metrics should use low-cardinality labels. `model_id`, `builder_id`, and `node_id` may be acceptable in this fleet. `workflow_id`, `run_id`, `event_id`, and full artifact paths should generally not be metric labels. Use exemplars or logs to attach trace/run IDs.

### Logs

Use logs for detailed diagnostics and raw evidence:

- backend stdout/stderr;
- scheduler explanations too verbose for span attributes;
- exception stack traces;
- failed readiness payloads;
- benchmark raw output;
- driver/runtime warnings.

Logs should include `trace_id`, `span_id`, `workflow_id`, `run_id`, `builder_id`, `node_id`, and `model_id` when available.

### Identifier Propagation

Propagate these everywhere:

- `trace_id`, `span_id`;
- `workflow_id`, `run_id`;
- `lap_id`, `segment_id` when applicable;
- `builder_id`, `node_id`;
- `model_id`;
- `candidate_id`, `assay_id`, `risk_report_id`, `promotion_id` when applicable;
- `artifact_id` for emitted evidence;
- `scheduler_decision_id`;
- `capacity_observation_id`.

Use OTel context propagation for trace context. Use baggage sparingly for stable, small, non-sensitive identifiers that downstream instrumentation needs. Do not put prompts, payloads, or large JSON in baggage.

### Correlation Pattern

Every workflow event should be able to point into telemetry:

```json
{
  "event_type": "builder.assigned",
  "workflow_id": "wf_001",
  "run_id": "run_001",
  "lap_id": "lap_001",
  "actor": {
    "type": "builder",
    "id": "builder-am4",
    "model_id": "vllama-planner"
  },
  "artifact_refs": [
    {
      "artifact_id": "sched_001",
      "artifact_type": "scheduler_decision",
      "path": "runs/run_001/artifacts/scheduler/sched_001.json"
    }
  ],
  "trace": {
    "trace_id": "69f0312f...",
    "span_id": "..."
  },
  "status": "assigned",
  "payload": {
    "node_id": "am4",
    "placement_mode": "single0",
    "capacity_profile_snapshot_id": "profile_am4_2026-07-02"
  }
}
```

The scheduler decision artifact can then contain the candidate list, scores, filters, and telemetry snapshot refs without bloating the ontology row.

## Relevant Prior Art

### OpenTelemetry

Status: proven industry practice; GenAI conventions are emerging.

Relevant concepts:

- spans represent timed units of work;
- span context enables distributed correlation;
- metrics aggregate measurements over windows;
- logs correlate through trace/span IDs;
- semantic conventions give portable attribute names;
- GenAI semantic conventions now include spans, metrics, events, and MCP-specific work.

Transfer:

- keep traces for timing and correlation;
- keep metrics for high-volume numerical telemetry;
- mirror business events as span events but persist them in `events.jsonl`;
- use shared semantic attributes for IDs and phases.

Avoid:

- making Jaeger or Prometheus the business database;
- putting unbounded event payloads into span attributes;
- treating metric labels as arbitrary key-value storage.

Sources:

- https://opentelemetry.io/docs/concepts/signals/traces/
- https://opentelemetry.io/docs/concepts/context-propagation/
- https://opentelemetry.io/docs/specs/semconv/
- https://github.com/open-telemetry/semantic-conventions-genai

### Kubernetes Scheduler

Status: proven industry practice.

Relevant concepts:

- scheduling cycle and binding cycle;
- plugin extension points;
- queueing/backoff;
- filter infeasible nodes;
- score feasible nodes;
- reserve before bind;
- permit can approve, deny, or wait;
- device plugins advertise special hardware such as GPUs.

Transfer:

- implement the commandcenter scheduler as a small pipeline:
  - queue sort;
  - prefilter workload shape;
  - compatibility filter;
  - score;
  - reserve;
  - permit/admit;
  - bind via `builder.assigned`;
  - post-bind observation.

Avoid:

- adopting Kubernetes just to get this pipeline;
- modeling every node as a pod target before local evidence contracts exist.

Sources:

- https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/
- https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/624-scheduling-framework/README.md
- https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/

### Ray

Status: proven for distributed Python workloads; LLM scheduling use is active but context-specific.

Relevant concepts:

- resource requirements are hard feasibility constraints;
- default scheduling balances locality and resource utilization;
- placement groups atomically reserve bundles for gang scheduling;
- autoscaling responds to logical resource demand, not physical utilization.

Transfer:

- distinguish declared logical requirements from observed physical behavior;
- use placement groups conceptually for multi-builder workflows or multi-device inference;
- reserve related resources atomically before assignment;
- prefer locality when warm state or data reuse matters.

Avoid:

- assuming logical resource requests capture VRAM pressure or KV-cache state;
- adopting Ray before the system has stable work contracts and capacity observations.

Sources:

- https://docs.ray.io/en/latest/ray-core/scheduling/index.html
- https://docs.ray.io/en/latest/ray-core/scheduling/placement-group.html
- https://docs.ray.io/en/latest/cluster/vms/user-guides/configuring-autoscaling.html

### Dask

Status: proven for task graph scheduling.

Relevant concepts:

- task placement depends on dependencies, data locality, and worker load;
- resources can constrain tasks to eligible workers;
- work stealing uses compute-to-communication ratio and respects restrictions;
- adaptive systems track memory and workload to scale workers.

Transfer:

- use data/context locality as a first-class scoring feature;
- avoid moving work if the compute saved does not justify transfer or cold-start cost;
- enforce resource restrictions even during work stealing/rebalancing;
- keep memory pressure visible to the scheduler.

Avoid:

- treating all builder work as independent stateless tasks;
- stealing/reassigning stateful LLM work after context is already loaded unless the savings are clear.

Sources:

- https://distributed.dask.org/en/stable/scheduling-policies.html
- https://distributed.dask.org/en/latest/resources.html
- https://distributed.dask.org/en/latest/work-stealing.html

### Slurm And HPC Schedulers

Status: proven HPC practice.

Relevant concepts:

- multifactor priority;
- age and fair-share;
- partitions/QOS;
- TRES-like accounting for specialized resources;
- backfill and reservation concepts.

Transfer:

- keep fairness and queue age in scheduler scoring;
- track specialized resources separately from generic CPU;
- use reservations for scarce long-context slots;
- let priority be explainable, not hidden inside opaque optimization.

Avoid:

- full HPC complexity for a small agent fleet;
- overfitting policy to static partitions when actual model behavior is learned empirically.

Source:

- https://slurm.schedmd.com/priority_multifactor.html

### Distributed Inference Systems

Status: proven for serving engines; cache-aware distributed scheduling is emerging.

Relevant concepts:

- vLLM uses PagedAttention, continuous batching, chunked prefill, prefix caching, and model execution optimizations;
- prefix caching reuses KV-cache blocks for matching prompt prefixes;
- Triton exposes Prometheus request/GPU metrics and supports dynamic batching;
- Triton dynamic batching can delay requests briefly to form better batches and supports priority queues;
- Triton sequence batching routes stateful sequences to the same model instance.

Transfer:

- separate prefill/prompt processing from decode/generation;
- measure TTFT, prefill tok/s, decode tok/s, queue delay, cache hit rate, and VRAM;
- schedule related follow-on work to the builder with warm model/context when evidence says reuse pays;
- use bounded queue delays for batching only when latency objectives allow;
- treat stateful session/cache routing differently from stateless requests.

Avoid:

- assuming batching helps all workloads;
- assuming GPU utilization alone means useful progress;
- routing by stateless load balancer when prefix/cache locality dominates.

Sources:

- https://docs.vllm.ai/
- https://docs.vllm.ai/en/stable/design/prefix_caching/
- https://arxiv.org/abs/2309.06180
- https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/metrics.html
- https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/batcher.html
- https://github.com/triton-inference-server/model_analyzer/blob/main/docs/metrics.md

### Distributed Build Systems

Status: proven practice.

Relevant concepts:

- Bazel remote execution distributes work over workers and reuses remote cache outputs;
- Bazel dynamic execution races local and remote execution for the same action and uses the first successful result;
- dynamic execution needs profiling and careful tuning because duplicate execution consumes resources.

Transfer:

- use bounded speculative execution only for tasks cheap enough to duplicate;
- use cache hit likelihood as a scheduling input;
- learn whether local/warm or remote/capable wins for each action shape.

Avoid:

- racing expensive long-context model runs by default;
- speculative execution without cancellation and artifact consistency rules.

Sources:

- https://bazel.build/remote/rbe
- https://bazel.build/remote/dynamic

### CI/CD Systems

Status: proven practice.

Relevant concepts:

- Jenkins models node capacity as executors;
- executor count must reflect CPU, memory, I/O, and network behavior;
- one executor per node is safest for large or resource-heavy work.

Transfer:

- start AM4 with one long-context executor unless measurements prove otherwise;
- do not equate CPU cores or advertised slots with safe concurrency;
- expose slot policy in MCP and scheduler projections.

Source:

- https://www.jenkins.io/doc/book/managing/nodes/

### Render Farms

Status: proven for heterogeneous, resource-constrained batch work.

Relevant concepts:

- Deadline Cloud chooses fleets based on configured capabilities and host requirements;
- render jobs have queues, steps, tasks, dependencies, sessions, and worker fleets;
- OpenCue has jobs, layers, frames, allocations, tags, and procs with reserved core/memory requirements.

Transfer:

- use capability/requirement matching before scoring;
- keep session reuse and warm software/model state visible;
- split complex workflows into dependency-aware segments/laps;
- use tags/capabilities for specialized builders.

Avoid:

- importing render terminology into the ontology;
- treating render-farm scheduling as sufficient for stateful LLM cache behavior.

Sources:

- https://docs.aws.amazon.com/deadline-cloud/latest/userguide/jobs-processing.html
- https://docs.aws.amazon.com/deadline-cloud/latest/developerguide/build-jobs-scheduling.html
- https://docs.opencue.io/docs/concepts/glossary/
- https://github.com/AcademySoftwareFoundation/OpenCue

### Autoscaling And Resource Recommendations

Status: proven for container resources; direct LLM workload transfer is emerging.

Relevant concepts:

- Kubernetes VPA analyzes current and historical CPU/memory usage and stores target/lower/upper recommendations;
- HPA uses current metrics against desired utilization;
- Ray autoscaler responds to logical resource demand queues;
- Dask adaptive scaling uses scheduler knowledge such as tasks and memory.

Transfer:

- maintain target/lower/upper capacity estimates for model/builder/workload classes;
- include variance and OOM/resource incidents in recommendations;
- distinguish current utilization from learned capacity;
- avoid scaling or assignment from raw physical utilization alone.

Sources:

- https://kubernetes.io/docs/concepts/workloads/autoscaling/vertical-pod-autoscale/
- https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/
- https://docs.ray.io/en/latest/cluster/vms/user-guides/configuring-autoscaling.html

## Concepts Worth Adopting

### Proven Practice

- Append-only event streams with deterministic projections.
- Artifact-first durable evidence.
- OpenTelemetry traces for timing and metrics for quantitative behavior.
- Filter/score/reserve/bind scheduler structure.
- Hardware capability matching before scoring.
- Queue age and fairness.
- Explicit admission control for scarce resources.
- Resource reservations for multi-resource work.
- Per-model/per-placement benchmark profiles.
- Low-cardinality metric labels and high-cardinality IDs in traces/logs/artifacts.

### Emerging Practice

- GenAI-specific OTel conventions.
- Prefix/KV-cache-aware routing.
- Disaggregated prefill/decode thinking.
- Scheduling by observed context-depth curves rather than declared context window alone.
- Model warm-state and readiness as scheduling inputs.
- Evidence-weighted capacity projections for local LLM infrastructure.

### Speculative Ideas

- Quality-aware scheduler scoring by builder/model/assay outcomes.
- Context reuse across workflow segments and laps.
- Thermal envelope learning for long-running local inference nodes.
- Contextual-bandit exploration of builder/model placement.
- Predictive scheduling from retrospective content.

These speculative ideas should remain behind feature flags or offline analysis until the evidence corpus is large enough.

## Concepts To Avoid

1. Do not redesign the ontology around hardware.

   `Builder` is enough as the workflow actor. Hardware profiles should be referenced, not promoted into core ontology terms.

2. Do not put raw telemetry into `events.jsonl`.

   Store summarized capacity evidence as artifacts. Keep raw time series in OTel/Prometheus/log files.

3. Do not make Jaeger, Prometheus, or Grafana the source of workflow truth.

   They are observability surfaces. The canonical state should be reconstructable from durable events and artifacts.

4. Do not adopt Kubernetes, Ray, Slurm, or a render-farm system wholesale.

   The transferable value is in their scheduling patterns, not their operational weight.

5. Do not make static inventory the scheduler truth.

   `ctx=131072` or `memory_gib_observed=30.3` is a claim. The scheduler needs evidence about behavior under workload.

6. Do not optimize for average GPU utilization.

   Long-context work can be memory, cache, prefill, queue, or thermal constrained. Averages hide phase behavior.

7. Do not introduce ML scheduling before explainable heuristics have a replayable dataset.

   The first scheduler should be auditable and conservative.

8. Do not overfit AM4 conclusions to future machines.

   AM4 evidence is valuable, but it is evidence for AM4, its drivers, its RAM, its B70s, its backend, and its measured placements.

## Learning Scheduler

### Scheduler Questions

The platform eventually needs to answer:

- Which builder should execute this work?
- Which model is most appropriate?
- Is this workload memory constrained?
- Is another machine a better fit?
- Should this workload wait for a warm model?
- Is it better to reuse loaded context?
- Is a multi-builder lap worth the cost?
- Is the risk of failure high enough to ask an operator or defer?

### Minimal Scheduler Pipeline

1. Extract `WorkloadShape`.

   Inputs:

   - workflow/segment payload;
   - estimated prompt tokens;
   - expected output tokens;
   - required tools;
   - risk class;
   - assay requirements;
   - artifact movement size;
   - timeout and priority.

2. Load projected fleet state.

   Inputs:

   - inventory profiles;
   - live resource projection;
   - model residency;
   - queue state;
   - reservations;
   - recent failure/quarantine state;
   - learned capacity estimates.

3. Compatibility filter.

   Remove candidates that cannot satisfy hard requirements:

   - missing model/tool;
   - insufficient measured context capacity;
   - insufficient projected VRAM/RAM headroom;
   - known incompatible placement;
   - node busy with exclusive resource owner;
   - policy/risk restriction.

4. Admission estimate.

   Estimate:

   - cold start/load cost;
   - expected TTFT;
   - expected completion time;
   - memory high-water;
   - queue wait;
   - cache reuse likelihood;
   - failure probability;
   - confidence.

5. Score feasible candidates.

   A simple first scoring model:

   ```text
   score =
     feasibility_bonus
     + quality_weight * predicted_assay_success
     - latency_weight * predicted_duration
     - wait_weight * predicted_queue_wait
     - cost_weight * predicted_compute_cost
     - risk_weight * predicted_failure_risk
     + locality_weight * warm_or_cache_reuse_value
     + confidence_weight * estimate_confidence
     + fairness_weight * queue_age_or_starvation_guard
   ```

   Every score component should be stored in `SchedulerDecision`.

6. Reserve and permit.

   Before emitting `builder.assigned`, reserve:

   - builder slot;
   - model slot;
   - accelerator placement;
   - long-context lane;
   - queue position if not admitted immediately.

   Permit can return:

   - `run-now`;
   - `queue`;
   - `defer`;
   - `reject`;
   - `ask-operator`.

7. Emit assignment.

   Emit existing ontology event `builder.assigned` with:

   - `builder_id` as actor identity;
   - `model_id`;
   - `lap_id` / `segment_id`;
   - `artifact_refs` to `SchedulerDecision`;
   - profile snapshot IDs in payload.

8. Observe execution.

   After execution, write `CapacityObservation` artifacts and attach them to downstream events.

9. Rebuild projections.

   Capacity projections update from evidence, not from mutable scheduler memory.

### First Policy For AM4

The current AM4 evidence supports a conservative policy:

- default placement: `single0`;
- `layer` as measured stretch path, not default;
- one long-context slot by default;
- explicit admission for prompt depth and concurrency;
- queue rather than uncontrolled concurrency;
- cache-aware follow-on routing only after measuring reuse;
- no automatic 262k context admission until measured;
- no row/tensor placement except controlled experiments because previous runs found instability.

This matches the local evidence in the AM4 result documents and avoids converting a benchmark curiosity into production policy.

## Capacity Knowledge

### Evidence Unit

Introduce a `CapacityObservation` artifact. It is not a new ontology event. It is evidence referenced by ontology events.

Sketch:

```json
{
  "schema_version": "capacity-observation.v1",
  "observation_id": "capobs_001",
  "created_at": "2026-07-02T18:30:00Z",
  "source_event_id": "evt_005",
  "workflow_id": "wf_001",
  "run_id": "run_001",
  "lap_id": "lap_001",
  "segment_id": "seg_001",
  "builder_id": "builder-am4",
  "node_id": "am4",
  "model_id": "vllama-planner",
  "accelerator_ids": ["am4-b70-0"],
  "inventory_snapshot_id": "profile_am4_2026-07-02",
  "workload_shape": {
    "prompt_tokens": 14342,
    "output_tokens": 32,
    "context_window_configured": 131072,
    "context_utilization": 0.109,
    "risk_class": "moderate"
  },
  "execution_context": {
    "placement_mode": "single0",
    "kv_type": "q8_0",
    "parallel_slots": 1,
    "cold_start": false,
    "model_warm": true,
    "cache_reuse": "unknown"
  },
  "metrics_summary": {
    "model_load_ms": 14000,
    "ready_probe_ms": 500,
    "ttft_ms": 1660,
    "elapsed_ms": 23400,
    "prompt_tokens_per_second": 671.72,
    "generation_tokens_per_second": 15.74,
    "vram_high_water_mib": null,
    "ram_high_water_mib": null
  },
  "outcome": {
    "status": "ok",
    "failure_class": null,
    "assay_outcome": "passed",
    "risk_level": "low"
  },
  "telemetry_refs": {
    "trace_id": "69f0312f...",
    "span_id": "...",
    "metrics_window": {
      "start": "2026-07-02T18:29:30Z",
      "end": "2026-07-02T18:30:30Z"
    },
    "logs": []
  }
}
```

### Projection Outputs

Add projections beside run `state.json`:

- `fleet_state.json`
  - known builders;
  - live or last-known readiness;
  - model residency;
  - current reservations;
  - queue depth;
  - health/quarantine flags.
- `capacity_estimates.json`
  - per `(builder_id, model_id, placement, context_bucket, concurrency_bucket)` p50/p95 load time, TTFT, prompt tok/s, decode tok/s;
  - failure counts and classes;
  - sample count;
  - confidence;
  - last seen;
  - data freshness.
- `scheduler_index.json`
  - precomputed candidate eligibility;
  - hard constraints;
  - known bad combinations;
  - warm-state hints.

These files should be projections. If deleted, they should be rebuildable from event logs and evidence artifacts.

### Evidence Accumulation

Start simple:

- bucket by `model_id`, `builder_id`, `placement_mode`, `context_bucket`, `parallel_slots`, and `concurrency_bucket`;
- store count, min, p50, p90/p95, max, last value, last seen, and failure count;
- use decayed weighting so stale measurements lose influence;
- widen estimates when variance is high;
- mark estimates as low confidence until sample count crosses a threshold;
- preserve raw observations so a new estimator can rebuild history.

Evidence grade:

| Grade | Meaning | Scheduler use |
|---|---|---|
| `declared` | Inventory/config claim only | Compatibility hint, not enough for risky admission. |
| `single-observation` | One measured run | Useful for manual review and low-risk routing. |
| `measured` | Several consistent observations | Scheduler can rely on it with guardrails. |
| `stable` | Repeated over time and contexts | Default policy candidate. |
| `contradicted` | Recent evidence conflicts | Prefer conservative fallback. |
| `unsafe` | Repeated failure or hard fault | Filter until explicitly cleared. |

### Learning Quality, Not Just Speed

The platform's goal is not only throughput. It should learn quality and risk:

- Does Builder A produce better assay outcomes for long-context planning?
- Does Model X produce fewer promotion holds for certain work?
- Does a faster builder create more risk or rework?
- Does a slower warm model still win end-to-end because assay success is higher?

That means `assay.passed`, `assay.failed`, `risk.scored`, `promotion.*`, and `retrospective.created` events must join with capacity observations by `workflow_id`, `run_id`, `lap_id`, `builder_id`, `model_id`, and `candidate_id`.

## Recommended Architectural Evolution

### Addition 1: Profile Contracts

Add versioned JSON schemas for:

- `machine-profile.v1`;
- `accelerator-profile.v1`;
- `builder-capability-profile.v1`;
- `model-capability-profile.v1`;
- `workload-shape.v1`;
- `scheduler-decision.v1`;
- `capacity-observation.v1`.

These should be contracts and artifacts, not ontology event types.

Why:

- current `node.json` files are useful but informal;
- the scheduler needs stable fields;
- MCP resources can serve these profiles;
- tests can validate them.

### Addition 2: Scheduler Decision Artifact

Attach a `scheduler_decision` artifact to `builder.assigned`.

Contents:

- work item features;
- candidate builders/models;
- filters with reasons;
- score components;
- selected builder/model/placement;
- reservation;
- admission result;
- projected estimates used;
- trace/span IDs.

Why:

- `builder.assigned` remains the ontology event;
- the decision is explainable and replayable;
- later scheduler versions can be compared against old decisions.

### Addition 3: Capacity Observation Artifact

Attach `capacity_observation` artifacts to downstream events:

- `candidate.produced` for builder execution;
- `assay.passed` / `assay.failed` for evaluation execution;
- `risk.scored` for risk/quality correlation;
- `retrospective.created` for run-level learning summary.

Why:

- capacity learning becomes event-linked without adding noisy event types;
- observations can include raw telemetry refs and summaries;
- the capacity reducer can join performance and quality.

### Addition 4: Capacity Reducer

Add a reducer parallel to `tools/workflow/project_state.py`.

Initial behavior:

- read `events.jsonl`;
- follow `artifact_refs` for `scheduler_decision` and `capacity_observation`;
- update fleet/capacity projections;
- emit JSON projections.

Do not put this logic into the run-state reducer until the shape stabilizes.

### Addition 5: AM4 Node-Local Admission

Implement the narrow plan already described in [am4-fleet-node/results/2026-06-29-next-layer-tooling-plan.md](../am4-fleet-node/results/2026-06-29-next-layer-tooling-plan.md):

- request cost estimator;
- simple admission gate;
- queue depth and thresholds;
- MCP tools:
  - `request_cost`;
  - `admission_decision_preview`;
  - `scheduler_status`;
  - `queue_depth`;
  - `placement_policy`;
  - `long_context_capacity`.

Why:

- AM4 is currently the best measured capacity node;
- the problem is local and bounded;
- node-local admission avoids global scheduler overreach.

### Addition 6: OTel Semantic Correlation

Extend existing OTel intent:

- add semantic parent spans for workflow phases;
- add scheduler spans;
- add GenAI/inference spans and metrics;
- ensure `workflow_id`, `run_id`, `builder_id`, `node_id`, and `model_id` propagate into MCP calls;
- record ontology events as span events with `event_id`;
- record artifact IDs in span attributes.

Why:

- traces become readable;
- capacity observations can point into raw telemetry;
- dashboards can join business and infrastructure without treating OTel as truth.

### Addition 7: Offline Experiment Harness

Before adaptive scheduling, add repeatable workload sweeps:

- context ladders;
- mixed shallow/deep traffic;
- warm vs cold model;
- queue policy on/off;
- prefix-cache reuse;
- placement comparison;
- failure injection;
- thermal soak.

Why:

- production workflow runs alone are too sparse and biased;
- experiments can deliberately map the envelope;
- scheduler changes need regression evidence.

## Incremental Implementation Roadmap

### Phase 0: Canonical Repo Audit

Goal:

- reconcile this design with the canonical `~/work/commandcenter` runtime.

Actions:

- locate actual OTel initializer and span wrappers;
- locate conductor, dashboard API, run dir, event bus, and artifact schemas;
- identify current event emitters;
- confirm whether `events.jsonl` exists in the runtime path;
- capture one golden trace and one run directory.

Acceptance:

- updated current-state notes with exact source links;
- no implementation against stale assumptions.

### Phase 1: Contract-Only Capacity Evidence

Goal:

- make capacity observations a first-class artifact type.

Actions:

- add JSON schemas for `scheduler-decision.v1` and `capacity-observation.v1`;
- create one fixture derived from the existing AM4 service-path benchmark;
- add validation tooling similar to `validate_events.py`;
- document artifact refs from existing ontology events.

Acceptance:

- fixture validates;
- no ontology event type changes.

### Phase 2: Capacity Projection Prototype

Goal:

- rebuild capacity knowledge from event logs and evidence artifacts.

Actions:

- add `tools/workflow/project_capacity.py` or `tools/capacity/project_knowledge.py`;
- read workflow events;
- follow capacity artifact refs;
- emit `capacity_estimates.json`;
- start with count/min/p50/p95/max/failure count by model/builder/context bucket.

Acceptance:

- deterministic projection from fixtures;
- tests for at least one happy path and one failure/unsafe path.

### Phase 3: Scheduler Decision Recording

Goal:

- make assignment choices explainable.

Actions:

- create `SchedulerDecision` artifact schema;
- update assignment emitter path in canonical repo to attach decision refs to `builder.assigned`;
- include candidate list, filter reasons, score components, selected assignment, and profile snapshot IDs.

Acceptance:

- every `builder.assigned` can explain why that builder was selected;
- assignment replay can compare old and new scheduler policies offline.

### Phase 4: AM4 Admission Gate

Goal:

- stop long-context overload from being implicit.

Actions:

- implement request cost estimator;
- expose MCP preview/status tools;
- gate Hermes requests by estimated KV footprint, prompt depth, slot occupancy, and current policy;
- return explicit `run-now`, `queue`, `busy`, or `reject` with reason;
- write capacity observations for admitted requests.

Acceptance:

- uncontrolled concurrency can be compared against gated behavior;
- queued/rejected decisions are visible to operators and scheduler.

### Phase 5: Semantic OTel Correlation

Goal:

- correlate business events, scheduler decisions, and infrastructure telemetry.

Actions:

- add phase spans around workflow execution;
- add scheduler spans around decision/admission/reservation;
- add model load/readiness/inference spans;
- add metrics for queue wait, TTFT, prefill/decode throughput, load time, VRAM/RAM headroom, failures;
- include event IDs and artifact IDs.

Acceptance:

- golden trace shows L1 workflow spans, L0 MCP/backend spans, and event/artifact correlation;
- metrics can be joined to run evidence without high-cardinality labels.

### Phase 6: Conservative Adaptive Scheduling

Goal:

- use learned estimates for decisions while staying explainable.

Actions:

- implement compatibility filter;
- implement heuristic scoring;
- prefer warm/cache-local builders only when estimated benefit exceeds queue/load cost;
- include confidence and fallback;
- quarantine unsafe combinations;
- log alternatives.

Acceptance:

- scheduler decisions are replayable;
- policy can be evaluated against previous runs;
- no opaque model-driven assignment yet.

### Phase 7: Controlled Exploration

Goal:

- improve knowledge where uncertainty is high.

Actions:

- add explicit experiment flags;
- run occasional bounded placement comparisons;
- compare predicted vs actual outcomes;
- capture regret/cost of exploration;
- never explore into known unsafe combinations without operator opt-in.

Acceptance:

- the platform improves estimates over time without destabilizing normal workflow execution.

## Risks And Tradeoffs

### Source Of Truth Drift

Risk:

- telemetry, dashboard state, and workflow events diverge.

Mitigation:

- durable workflow state derives from events;
- capacity knowledge derives from events plus referenced evidence artifacts;
- OTel is correlation and raw measurement, not canonical workflow truth.

### Telemetry Cardinality And Cost

Risk:

- `workflow_id`, `run_id`, and artifact paths as metric labels can explode time-series cardinality.

Mitigation:

- keep high-cardinality IDs in traces, logs, and artifacts;
- use metrics labels for bounded dimensions;
- use exemplars where available.

### False Confidence From Sparse Data

Risk:

- one good run becomes a default policy.

Mitigation:

- evidence grades;
- sample counts;
- confidence values;
- conservative fallback;
- explicit experiment status.

### Feedback Loops

Risk:

- scheduler sends all work to the currently best builder, starving alternatives and reducing evidence diversity.

Mitigation:

- fairness/age term;
- controlled exploration;
- offline benchmark harness;
- stale estimate decay.

### Hardware And Runtime Drift

Risk:

- driver updates, thermal conditions, backend flags, or co-tenancy invalidate old estimates.

Mitigation:

- include profile snapshot ID, driver/runtime versions, placement, and active co-tenancy in observations;
- decay old data;
- invalidate profiles on material changes.

### Quality/Performance Conflict

Risk:

- fastest builder may produce lower assay success or higher promotion risk.

Mitigation:

- join capacity observations with assay/risk/promotion events;
- make scheduler objective multi-factor;
- keep operator override and promotion gates.

### Over-Abstraction

Risk:

- too many profile and scheduler abstractions before the runtime needs them.

Mitigation:

- start with two contracts: `SchedulerDecision` and `CapacityObservation`;
- derive profile contracts from existing `node.json` and AM4 facts;
- add only fields used by scheduler decisions or evidence projection.

### Canonical Repo Gap

Risk:

- this design diverges from runtime implementation on the canonical host.

Mitigation:

- Phase 0 audit before code changes;
- keep changes contract-first and portable.

## Open Questions

1. Should future capacity-related facts ever become ontology events, or should they remain artifact-backed evidence linked to existing workflow events?

   Recommendation for now: keep them as artifacts unless operators need capacity events on the same timeline as workflow events.

2. Where should profile snapshots live?

   Options:

   - `fleet/profiles/<snapshot_id>.json`;
   - `runs/<run_id>/artifacts/profiles/`;
   - MCP resources materialized into run artifacts at assignment time.

3. What is the exact identity model for physical devices?

   Need stable `accelerator_id` values that survive path changes such as `/dev/dri/renderD128`.

4. Which Intel B70 telemetry source is reliable enough for VRAM, temperature, power, and health?

   AM4 has probes and MCP tooling, but the final collector path should be validated.

5. How should quality outcomes be weighted against latency and cost?

   This is a product policy question, not only an engineering question.

6. How much raw telemetry should be retained per run?

   The answer affects replayability, disk use, privacy, and debugging depth.

7. What is the minimum evidence threshold before scheduler defaults change?

   Proposed starting point: at least five successful observations across two days for low-risk changes; more for high-context or expensive workloads.

8. Should NATS become required for high-volume telemetry or only for durable run events and fanout?

   Current AM4 docs correctly keep NATS optional. Do not require it until file-based artifacts and MCP visibility are insufficient.

9. How should prompt/cache identity be represented without leaking prompt contents?

   Likely answer: salted prefix/cache fingerprints with clear privacy rules, stored as evidence metadata, not raw prompt text.

10. How should the scheduler handle operator intent?

   Some workflows may prefer fastest answer, cheapest execution, highest assay success, or highest information gain. That objective should be explicit in work intake or planning output.

## Final Recommendation

The smallest useful architectural addition is a capacity evidence loop:

```text
ontology event
  -> artifact refs
  -> scheduler decision artifact
  -> execution telemetry
  -> capacity observation artifact
  -> capacity reducer
  -> scheduler projection
  -> next builder assignment
```

This turns every run into an evidence-producing dyno pass without weakening the ontology. It also lets AM4's existing benchmark discipline become an automated platform capability: measure, attach evidence, project knowledge, schedule more intelligently, and keep the whole loop explainable.

