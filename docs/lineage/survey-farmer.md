# Survey: djcdevelopment/planning — "Farmer" (April 2026)

*Sonnet agent survey, 2026-07-10, of a local read-only clone. GitHub repo
`planning`; the in-repo name is always **Farmer**. Report preserved verbatim.*

---

## 1. Identity & timeline

One-line self-description (README.md:3): *"A .NET 9 control plane that orchestrates Claude CLI workers on Hyper-V Ubuntu VMs, with a retrospective agent on the host (via Microsoft Agent Framework + OpenAI) that reviews every run's output."*

- **Total commits:** 81. First: `2026-04-06 21:01` ("first commit"). Last in clone: `2026-04-16 03:08` (PR #16 merge).
- **Lifespan:** 10 days, clustered into sprint nights: 2026-04-06/07 (Phases 1–4, 17 commits), 04-08 (proprietary notice), 04-10 (Phase 5, OTel + externalized runtime, 12 commits), 04-11–13 (Phase 6, MAF retrospective agent + real worker.sh, 8 commits), 04-15 (a 34-commit marathon: NATS cutover + Phase 7 retry driver + 6 merged PRs in one session), 04-16 (12 commits, retry-loop polish).
- **133 tests green** at final state (128 unit + 5 integration).

## 2. Structure

`src/`: Farmer.Core (45 .cs — Config, Contracts, Layout, Middleware, Models, Telemetry, Workflow+Stages), Farmer.Tools (SSH/SCP/mapped-drive, VmManager), Farmer.Host (ASP.NET, `/trigger`), Farmer.Agents (the MAF/OpenAI "blast radius" project), Farmer.Messaging (NATS JetStream + ObjectStore), Farmer.Worker (worker.sh + CLAUDE.md — lives ON the VM), Farmer.Tests, Farmer.Tests.Integration. Plus `docs/adr/` (11 ADRs + index), `docs/diagrams/` (4 SVGs), `data/sample-plans/`, `infra/`, `tools/`, `scripts/`.

## 3. The system

Farmer turns numbered markdown prompt files into autonomous Claude CLI work on a Hyper-V Ubuntu VM, then has a second, different LLM review the output as a post-mortem — never as a gate.

The **7-stage `RunWorkflow` pipeline**: `CreateRun → LoadPrompts → ReserveVm → Deliver → Dispatch → Collect → Retrospective`. Middleware chain (outermost→innermost): `Telemetry → Logging → Eventing → CostTracking → Heartbeat`.

Two runtimes, deliberately different LLM providers: **Host** (Windows .NET) — MAF + OpenAI (`gpt-4o-mini`) retrospective agent; **VM** (Ubuntu) — Claude CLI in full `--dangerously-skip-permissions` mode, no tool allowlist, up to 500 turns.

Coordination evolved: file-based `InboxWatcher` polling (Phase 5, ADR-002) → **NATS JetStream + ObjectStore** (ADR-010, pivot on 2026-04-15 after the mapped drive died). Tracing: **Jaeger**, ~100–130 spans per run, one traceId end-to-end.

## 4. Named concepts inventory

- **Farmer** — the system's name (repo called `planning` on GitHub, never in-repo)
- **RunWorkflow / 7-stage pipeline** — `CreateRun, LoadPrompts, ReserveVm, Deliver, Dispatch, Collect, Retrospective`
- **Worker** — Claude CLI on the VM, full dangerous mode; consistently "worker," never "builder"
- **Retrospective agent** (`MafRetrospectiveAgent`) — host-side MAF+OpenAI reviewer; explicitly "not a QA gate"
- **Verdict** (`Accept | Retry | Reject`) — descriptive, not prescriptive; risk_score 0-100
- **Directive suggestions** — the forward-looking learning channel (scope `Prompts | ClaudeMd | TaskPacket`)
- **RetryDriver / FeedbackBuilder** — Phase 7 bounded retry loop; prior verdict injected as synthetic `0-feedback.md`
- **RunEvent / events.jsonl, state.json, result.json** — the "anti-drift" triad; must agree, pinned by regression tests (`BugRegression_FailedRun_AllThreeFilesAgreeOnFailedPhase`)
- **worker_mode** (`real | fake | fake-bad`) — `fake-bad` purpose-built to prove the retry loop fires on a genuine Reject
- **Farmer.Agents "blast radius"** (ADR-005) — the one project allowed to import MAF/OpenAI SDK types

**Absent** (corrects the working hypothesis): no association engine, no OR-Tools/CP-SAT, no planner/critic ensemble, no ledger/event-sourcing framework. Farmer is specifically the build-orchestration + QA-postmortem control plane.

## 5. The "discipline"

- Numbered prompt files (`1-SetupProject.md`, …); PowerShell bracket-globbing gotcha documented.
- Immutable run directories: `runs/{run_id}/` never mutated after completion.
- Externalized runtime (ADR-001): repo is pure engine; dated run state in a sibling `planning-runtime\`, never committed.
- ADRs as the why-log: 11 numbered, MADR-lite, several quoting Derek verbatim as the deciding evidence.
- Session retros + build logs as dated artifacts: `docs/session-retro-2026-04-15.md`, `docs/phase5-build-log.md` — structural ancestor of today's `/retro`.
- CLAUDE.md as repo-root handoff — direct ancestor of the commandcenter convention.

## 6. Technology mentions

- **MAF**: central — `CLAUDE.md:7`: *"The competitive differentiator is using MAF 'as much as possible' while keeping workers autonomous on VMs."* ADR-005: *"MAF v1.0 shipped April 3, 2026 — brand new."*
- **OpenAI over Anthropic for the host agent** — ADR-006, quoting Derek: *"if microsoft agent framework has better support for openAI remote API, so be it, i don't care. i just need the data to flow now."*
- **OTel/Jaeger**: pervasive; earlier phases targeted .NET Aspire Dashboard before standalone Jaeger.
- **MCP**: one incidental mention (tool categories available to Claude CLI). No MCP architecture in this repo.
- **ollama / OR-Tools / CP-SAT**: zero mentions.

## 7. Story-worthy artifacts

- `docs/phase5-build-log.md` — §12 opens: *"Honest scorecard: 57% of stages green, 2 latent bugs surfaced, 1 config blocker. Details below — celebrate nothing until §13 is empty."*
- `docs/session-retro-2026-04-15.md` — 6 PRs merged in one sitting, run-IDs preserved as "receipts," "What's NOT done (named, not blocked)" backlog.
- `docs/retry-demo-2026-04-16.md` — narrated demo of the retry loop firing on a *real* Reject→Accept, with timestamps and traceIds.
- `docs/phase6-retro-verification.md` (2026-04-12) — "First Real QA Run": the first time an LLM reviewed another LLM's work in this lineage.
- `docs/end-to-end-verification.md` — fake-worker calibration protocol with a falsifiability sentinel (`FAKE_WORKER_NO_REAL_CHANGES`).

## 8. Verbatim quotes

1. *"If you ever want to know 'what did Farmer ask, what did the worker do, what did the reviewer think' — it's in the run directory on disk, in the `farmer-runs-out` ObjectStore bucket over the wire, and as a 100+ span waterfall in Jaeger."* — README.md
2. *"I thought QA would run after builds completed, more of a post-mortem on quality that can impact the next set of planning prompts"* — Derek, ADR-007
3. *"that is still data, which means that is still success"* — Derek, ADR-007
4. *"I leave them 100% control on that VM ... some of them packaged a zip file up, some of them gave me a directory, one of them fucking built an entire web server. And I think that in itself is good information."* — Derek, ADR-008
5. *"when i ran my sample it was full --dangerous mode, that's 90% of the reason we're using VMs, full send."* — Derek, ADR-008
6. *"'Data is the product.' Failures are captured as data, not treated as terminal errors."* — CLAUDE.md:117, citing ADR-007

**Net read:** the direct architectural ancestor of commandcenter's fleet-builder + retrospective-agent pattern, built and mostly closed in a single 10-day sprint two-plus months before commandcenter's late-June rebuild.
