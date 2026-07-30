# hearth/gcp — GCP agent-platform artifacts

## engine-specs-2026-07-23.json

Full REST capture (`GET …/locations/us-west1/reasoningEngines`, 2026-07-23, read-only)
of the two Agent Engine ReasoningEngines deployed 2026-07-21 via Agent Platform
Studio, taken **before their deletion** per the burn-stop decision
(DECISIONS-PENDING.md 2026-07-23, GCP-AGENT-ASSESSMENT.html):

- `baseline` (2356622854230900736) — the Track 2.0 ADK demo agent
  (`agentFramework: google-adk`, entrypoint `main:app`). Ran with
  `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` and
  `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` (the trace/log bloat +
  privacy leak — any redeploy sets both `false`). The API returns an empty
  `inlineSource`: the agent's code is **not** recoverable from this capture. Its
  distinctive config was Studio's "MCP Server" tool pointed at the HEARTH Funnel
  URL (ADR-0025); everything else was Studio boilerplate.
- `AGENT_DESIGNER_GENERATED_DO_NOT_DELETE` (50779845017206784) — Agent Designer
  auto-provisioned scaffold: no deployment spec, no source, no description. The
  name is Studio boilerplate, not a covenant.

## Rebuilding the demo (redeploy-per-campaign)

Do **not** rebuild through Studio's UI. The repo-owned path is `adk_demo.py`
(deploy/teardown wrapper, telemetry off) — see ADR-0026 (ephemeral-by-default).
The agent itself is a minimal ADK app whose only special part is an MCP toolset
aimed at the Funnel endpoint; the stamped-identity path (ADR-0025) is independent
of any engine and needs no change.
