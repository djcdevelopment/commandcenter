# Open Notebook × Hearth discovery plan

## Objective

Understand the existing Hearth architecture, integrations, model backends, and
capacity controls well enough to evaluate Open Notebook integration. This plan
does not choose an integration path. A recommendation is allowed only after the
evidence gates below pass.

## Current evidence baseline

- Hearth is the MCP control plane: caller identity, capability profiles,
  authorization, tool routing, ledger events, inference, occupancy, and the
  Mechnet task lane.
- Hearth inference is declarative in `hearth/etc/backends.toml` and implemented
  by `hearth/toolsurface/backends.py` plus `inference.py`. Routing order is
  endpoint pin, backend pin, occupancy-checked tag/task routing, then the
  default backend.
- `am4-moe` is the resident `gpt-oss-120b` OpenAI-compatible rung at `:8082`,
  with a 14k-token door budget, 420-second timeout, and HTTP slot occupancy
  probe. `am4-oxen` is the banked dual-B70 Qwen rung at `:8090`, pin-oriented
  after the resident MoE handover.
- `gcp-gemini` is the opportunistic trial-credit flash rung; `gcp-gemini-pro`
  is a deliberate pin for the larger thinking model. Both use Vertex auth and
  are governed by the trial runway in `backends.toml` and `knowledge/offload.json`.
- Open Notebook has separate provider credentials, model records, language and
  embedding model types, source ingestion, background processing, search, and
  notebook APIs. Its documented OpenAI-compatible provider accepts per-service
  base URLs, including separate LLM and embedding endpoints.
- Open Notebook is currently a stock Docker Compose deployment with localhost
  UI/API bindings; the notebook-side Hearth adapter is only a configuration
  scaffold.

## Phase A — Establish authoritative source maps

Read and index the following without modifying runtime behavior:

1. Hearth entrypoints: gateway launcher, MCP client, caller registry,
   capability profiles, toolsurface manifest, ledger, `/checkmcp`, and caller
   security tests.
2. Hearth integrations: `inference.py`, `backends.py`, `occupancy.py`,
   `task_lane.py`, `scheduler.py`, `summon.py`, and the AM4 facade/server
   launchers.
3. Capacity/economics: `hearth/contracts/*.schema.json`, projections,
   `knowledge/capacity.json`, `knowledge/offload.json`, capacity observations,
   and the current backend/capacity test fixtures.
4. Open Notebook: Compose, provider registry, credential/model routes,
   OpenAI-compatible configuration, source routes, search routes, processing
   commands, and persistence boundaries.

Deliverable: a component-and-data-flow map with each claim labeled as verified
code behavior, recorded runtime evidence, or historical/design intent.

## Phase B — Trace model integrations end to end

For each backend, document:

- endpoint and protocol;
- authentication source and secret boundary;
- model identifiers and context/output limits;
- routing tags and pin behavior;
- occupancy signal and failure behavior;
- timeout, retry, escalation, and trial-cost behavior;
- ledger fields emitted for the dispatch.

Trace one representative call for each local model class and Gemini class from
caller input through target selection, HTTP payload, response normalization,
ledger event, and capacity projection. Do not send new production calls until
the existing live evidence and tests are reconciled.

Deliverable: a backend integration matrix and an evidence-backed sequence for
`local_generate`.

## Phase C — Understand notebook workload shape

Map Open Notebook work into observable workload classes without assigning them
to Hearth backends yet:

- source extraction and chunking;
- embedding/vectorization;
- notebook/source chat;
- search and retrieval;
- summaries/insights/transformations;
- long-context synthesis;
- background processing and retry behavior.

For each class, identify input size, output size, concurrency, ordering,
latency sensitivity, idempotency, provenance/citation requirements, and whether
Open Notebook or Hearth currently owns retries and queueing.

Deliverable: a workload/capacity demand table grounded in Open Notebook code and
API contracts.

## Phase D — Reconcile scheduling and capacity authorities

Determine which system is authoritative for each decision:

- Hearth backend selection and occupancy;
- Mechnet queued work and worker capacity;
- Open Notebook background task concurrency;
- model context limits and timeouts;
- trial-credit suppression;
- GPU residency and mode switching;
- freshness of capacity projections and ledger watermarks.

Inspect whether a notebook workload can be represented by current capacity
contracts without inventing a competing scheduler. Record races, stale-data
windows, and whether a request is synchronous inference or queued execution.

Deliverable: an authority/coherence table and list of integration invariants.

## Phase E — Security and provenance review

Verify the intended notebook caller against the approved authority model:

- `repo_metadata`, `repo_content`, `repo_write` remain distinct;
- `file_scope` remains independently enforced;
- `git_diff` remains excluded from research;
- container paths are translated before crossing the boundary;
- Hearth keys, worker credentials, inventory, and registry contents never enter
  the notebook container;
- every derived answer can retain repository, commit, path, and content-hash
  provenance.

Deliverable: a threat/data-flow review and negative-test checklist.

## Phase F — Compatibility and feasibility probes

Only after A–E, run non-destructive probes:

1. Enumerate Hearth tool/capability metadata through the authenticated MCP door.
2. Confirm which Open Notebook provider/model APIs can represent the observed
   OpenAI-compatible endpoints and separate embedding routes.
3. Confirm request/response shape, streaming, timeout, and error compatibility.
4. Measure representative payload sizes against Hearth packing and backend
   context budgets.
5. Exercise capacity/status reads without mutating caller registry, firewall,
   gateway bind, or production notebook data.

Deliverable: a compatibility report with blocked, verified, and unverified
items.

## Decision gate — only then consider integration paths

Do not recommend MCP direct use, an OpenAI-compatible Hearth facade, a notebook
adapter, or a hybrid until the following are answered with evidence:

- Who owns model selection for each workload class?
- Who owns queueing, retries, and backpressure?
- How are chat and embedding models represented without duplicating provider
  truth?
- How are capacity reservations and occupancy reflected in notebook behavior?
- How are citations and source provenance preserved through transformations?
- What is the rollback boundary and what state changes are reversible?
- Can the design stay adapter-based without forking Open Notebook or creating a
  second source of truth?

## Planned artifacts

- architecture/data-flow map;
- backend integration matrix;
- notebook workload/capacity demand table;
- scheduler and authority coherence table;
- security/provenance review;
- compatibility probe report;
- only after review: decision record and bounded integration experiment.

## Constraints

- Git/repository documents remain authoritative.
- Hearth remains the MCP front door when available.
- Mechnet workers may be used only for bounded, self-contained discovery tasks
  after capability discovery and with evidence returned to the owner.
- No live gateway restart, firewall change, caller-secret mutation, or notebook
  production-data migration during discovery.
- Assumptions must be separated from verified observations.
