# 0026 — Cloud deployments are ephemeral by default

**Status:** Accepted (2026-07-23) — the class-fix for the trial-credit burn diagnosed
in GCP-AGENT-ASSESSMENT.html; governs every cloud-hosted resource we create, on any
provider, from this date.

## Context

Between 2026-07-11 and 2026-07-23 the project `lumberjacks-exp-20260711-djc` drew
~$36 of GCP trial credit. The 2026-07-23 read-only diagnosis showed logging and
monitoring ingest at ≈$0 — inside Cloud Logging's 50 GiB/month free tier — and the
dollars in standing compute: two Vertex AI Agent Engine ReasoningEngines
(`baseline`, the Track 2.0 ADK demo, and `AGENT_DESIGNER_GENERATED_DO_NOT_DELETE`,
Agent Designer scaffold), created 2026-07-21 through Agent Platform Studio's web UI
and idling at ~est $3.5/day each. For contrast, the gemini-pro rung's entire
lifetime inference priced out at ~$5.02. **The meter runs on what is deployed, not
on what is thinking.**

Two aggravating factors made this a pattern rather than a one-off mistake:

1. **"Redeploy-per-campaign" was policy without tooling.** The engines were created
   by hand in Studio's UI; no repo-tracked deploy script or IaC existed. That made
   deleting the standing demo feel expensive — rebuilding meant clicking through a
   web console — so it idled. (The engine API returns no agent source
   (`inlineSource` empty; capture in `hearth/gcp/engine-specs-2026-07-23.json`), so
   a Studio-built agent is not even recoverable from the platform after the fact.)
2. **Telemetry defaulted to full capture.** `baseline` ran with
   `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` and
   `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` — prompt and response
   bodies landing in Cloud Trace/Logging. Free-tier noise today, a privacy leak
   regardless.

## Decision

**1. Ephemeral by default.** Any cloud deployment — Agent Engine, VM, managed
service — is torn down when the campaign or session that created it ends. An empty
`reasoningEngines` list is the resting state. Deploy-per-campaign is the norm, not
the exception.

**2. Standing compute requires a named justification.** Anything left running past
its campaign gets a recorded entry (DECISIONS-PENDING.md or an ADR) stating what it
is, why it must stand, its estimated $/day, an owner, and a review date. The P7 VM
qualifies today: it is the tester-facing game server, justified and sized in
ADR-0004 and the P7 terraform.

**3. Deploys go through repo-tracked tooling, never a console UI.**
`hearth/gcp/adk_demo.py deploy|teardown` is the path for the ADK demo; equivalents
apply to anything else. A resource that exists in the cloud but not in the repo's
tooling or record is drift — delete it or adopt it into the record, same as the
Terraform discipline.

**4. Content-capturing telemetry is off at deploy time.** The deploy tooling sets
`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=false` and
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`. Turning capture on is a
per-campaign, time-bounded choice made for a stated debugging purpose, and it ends
with the campaign.

**5. Real dollars stay ledgered, with a tripwire.** Observed spend continues to
land in `knowledge/cloud_spend.json` (cloud-spend.v1), and a budget with 50/90/100%
alert thresholds stands on the billing account so idle burn cannot run silent for
twelve days again.

## Consequences

- **The standing Track 2.0 demo is gone as a side effect** — and returns as a
  one-command redeploy when a campaign wants it. What we lose is a warm demo
  nobody was watching; what we keep is ~$210/mo of runway (≈ seven months of
  tester-VM compute per month, at current sizing).
- **ADR-0025's identity path is untouched.** The Funnel → Caddy → HEARTH stamp is
  independent of any engine; it costs nothing on GCP and its own revisit trigger
  (Studio shipping MCP API-key auth) is already tracked.
- **This governs behavior across providers**, not just GCP — the same rule applies
  the day anything lands on Azure or AWS.
- **The discipline costs minutes per campaign** (one deploy command, one teardown
  command) and removes the entire idle-burn class, which was 64% of the observed
  bill.
- Studio's UI remains fine for *authoring* and exploration — what it may not do is
  leave deployed compute behind after the session ends.
