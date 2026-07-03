# AM4 Next-Layer Tooling Plan

Date: 2026-06-29
Host: `am4`
Scope: control-plane and scheduler tooling for the current `32 GB` AM4 envelope

## Why this exists

The benchmark work narrowed the problem down enough to plan the next layer cleanly:

1. placement matters, but only at the margins
2. long-context service behavior is dominated by prompt ingest and KV pressure
3. under concurrent load, streaming usefulness collapses because requests sit behind prompt work
4. the real failure mode is scheduling work across a tight memory envelope without falling into host/shared-memory cliffs

This plan is for building the tooling that keeps AM4 inside those bounds.

## Operating assumptions

- default placement remains `single0`
- `layer` stays available as a measured stretch path
- host DDR is scarce control-plane headroom, not a normal extension of VRAM
- long-context requests are expensive enough that admission and scheduling must be explicit
- MCP remains the northbound control contract

## Goal

Build enough node-local control logic that AM4 can:

- classify incoming work by memory and latency risk
- admit or defer requests before they push the node over the cliff
- reserve capacity for long-context jobs
- expose those decisions through MCP/Hermes-visible status

## Phase 1: Measurement-to-policy bridge

Implement a small policy engine that converts request shape into estimated cost.

Outputs per request:

- estimated prompt tokens
- estimated KV footprint
- placement eligibility: `single0`, `layer`, or `reject`
- risk class: `shallow`, `moderate`, `deep`, `danger`
- scheduler hint: `run-now`, `queue`, `defer`, `reject`

Inputs:

- model-specific KV bytes/token numbers from Denning and AM4 probes
- prompt depth from request payload
- active backend placement
- current in-flight request count
- current slot occupancy

First artifact:

- `scripts/request-cost-estimator.py`

Acceptance criteria:

- deterministic output for the same input
- configurable thresholds in a flat config file
- explainable output that can be surfaced to operators and higher layers

## Phase 2: Admission control

Add a gate in front of the served path.

Behavior:

- reject or queue requests that would exceed the allowed active footprint
- protect one long-context slot from being crowded out by shallow burst traffic
- optionally cap total concurrent prompt-ingest jobs below total slot count

Implementation options:

- lightweight gate inside the Hermes facade
- or a separate scheduler shim in front of the facade

Recommendation:

- keep it outside the backend first
- do not patch llama.cpp until the control policy is proven

First artifact:

- `scripts/hermes-scheduler.py` or equivalent facade-integrated gate

Acceptance criteria:

- explicit `429`/busy or queue response instead of silent performance collapse
- configurable per-risk-class concurrency caps
- operator-visible decision reason for every rejected or queued request

## Phase 3: Queue policy

Once admission exists, make the queue intentional.

Needed policies:

- FIFO baseline
- long-context reservation lane
- shallow-work isolation so short requests do not poison deep ones
- starvation guard so deep jobs still run

Metrics to capture:

- queue wait time
- time to first scheduled token
- request age
- per-class latency

Recommendation:

- start with one queue plus one reserved deep lane
- avoid a full broker dependency for this phase

Acceptance criteria:

- queueing improves tail behavior relative to uncontrolled concurrent runs
- operators can see why a request is waiting

## Phase 4: Prompt-cache-aware scheduling

Use reuse deliberately rather than accidentally.

Behavior:

- group similar requests when reuse is likely to pay off
- keep related follow-on work on the same live backend when possible
- prefer reusing hot prompts over interleaving unrelated long prompts

Inputs:

- alias
- prompt prefix similarity
- recent request history
- backend cache state if observable

This phase only makes sense after Phase 1 and 2 exist. Otherwise reuse decisions are hidden inside uncontrolled overload.

Acceptance criteria:

- measurable TTFT improvement for related follow-on requests
- no regression for unrelated requests under light load

## Phase 5: MCP control surface

Expose scheduler state directly to the agent layer.

Add MCP tools/resources for:

- `request_cost`
- `scheduler_status`
- `queue_depth`
- `admission_decision_preview`
- `placement_policy`
- `long_context_capacity`

Resources:

- current queue snapshot
- current thresholds
- recent admissions/rejections

Acceptance criteria:

- operators and agents can ask AM4 why it is accepting, delaying, or refusing work
- policy changes become inspectable rather than hidden inside scripts

## Phase 6: Benchmark harness extensions

The current harnesses are enough to expose the problem, not enough to validate a scheduler.

Add:

- fixed resident-backend tests with policy on/off
- mixed workload tests: `8k + 16k + 32k`
- queueing tests with arrival offsets, not just synchronized bursts
- admission-threshold sweeps
- cache-reuse scenarios with shared prefixes

Acceptance criteria:

- every scheduler change can be tested against the same repeatable workload shapes
- regression surface is documented, not rediscovered by hand

## Suggested implementation order

1. request cost estimator
2. facade-level admission control
3. queue state + MCP visibility
4. repeatable scheduler benchmarks
5. prompt-cache-aware routing

That is the minimum order that turns benchmark data into an operational node.

## Non-goals for now

- no full broker-first queue system
- no kernel or driver surgery unless the policy layer proves the need
- no attempt to make AM4 a high-concurrency long-context box with current RAM
- no multicard-by-default switch before the scheduler story is real

## Immediate first build

If only one narrow slice gets built next, build this:

1. request cost estimator
2. simple admission gate with `run-now` / `busy` / `queue`
3. MCP exposure of thresholds, queue depth, and last decisions

That would be enough to stop the node from failing implicitly and start failing explicitly, which is the correct next step for this machine.
