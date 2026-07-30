# Buddy Demo Reference Freshness Audit — 2026-07-29

Scope: every document linked or named in
`BUDDY-DEMO-2026-07-29.html`, plus the runtime/config sources on which
their demo claims depend. Audit and chart validation completed on OMEN by
approximately 14:55 PDT.

## Bottom line

The architectural spine is sound: one HEARTH boundary, separate sense and act
planes, two ledgers with different bounded contexts, profile-scoped caller
identity, and a plan/build/assay learning path. The unsafe material was mostly
presentation freshness: dated artifacts used present tense, generated
snapshots looked live, and the conductor dashboard mixed current run history
with obsolete health probes.

The buddy briefing now labels each reference by safe use. Historical artifacts
were not silently rewritten into new designs; they were visibly stamped, and
current claims were moved to current probes/contracts.

## Artifact-by-artifact verdict

| Artifact | Verdict | What remains reliable | What was stale, incomplete, or misleading |
|---|---|---|---|
| `HEARTH-DASHBOARD.html` | Generated snapshot | Aggregate economics/capacity/routing as of its displayed watermarks; recent ledger window at generation | It is not a live query. Browser refresh reloads the same static file. At audit start it was generated 17:04 UTC from capacity watermark 17:02 UTC and offload watermark 15:42 UTC. The generator now says this explicitly. |
| `HEARTH-OVERVIEW.html` | Historical architecture, July 8 | Boundary rationale, ledger split, advisory scheduler, early tool-family narrative | 35 tools/13 providers, 381/784 tests, 13 ADRs, three-caller model, inference ladder, authorization model, and watchdog/timer story all predate the current system. A prominent historical banner was added. |
| `COMMANDCENTER-OVERVIEW.html` | Archived snapshot, July 18 | Provenance and a useful picture of that date | 8,535 events, economics, 12/12 uptime, queue counts, machine state, and catalog findings are not current. It already said “Snapshot”; an explicit do-not-use-for-live-status notice was added. |
| `docs/mechnet-dfd.svg` | Structure only, July 9 | One door, sense/act roles, two stores, conductor/build/assay flow | 35-tool surface, Ollama/oxen-only inference, caller/identity model, cloud ingress, and three-loop inventory are obsolete. The subtitle now says so. |
| `docs/network-topology.svg` | Structure only, July 9 | Machine lanes use `*.mshome.net`/LAN; Tailscale is not a machine control lane | `172.17.*` boot addresses, AM4 service inventory, 35 tools, old inference ladder, and lack of GCP-to-HEARTH ingress are stale. The subtitle now says so. |
| `docs/workflow-ontology-design.md` | Design/discovery history | Commands/events/states/artifacts separation, durable event envelope, identity propagation, and OTel layering | It specifies 19 events while code/schema now have 21 (`idle.observed`, `idle.ended`). It says question/answer/resume and promotion paths are absent/manual even though the reference runner implements them. A current implementation note was added at the top. |
| `docs/adr/README.md` | Current after correction | Decision trail and statuses through 0025 | It omitted local accepted ADR-0026 and still called ADR-0015 “build pending” despite the July 9 implementation. Both were corrected. ADR-0016 remains intentionally reserved. |
| `docs/adr/0005-one-boundary-three-planes.md` | Current decision; historical implementation nouns | Every engineering/offload crossing goes through HEARTH; sense does not become a second command door | The July 4 references to “oxen B70” and early occupancy mechanics predate the resident `am4-moe` rung and later identity/ingress ADRs. They are context, not current inventory. |
| `docs/adr/0010-two-ledgers-two-bounded-contexts.md` | Current decision | Kernel ledger is audit truth; workflow ledger is replayable domain truth; derived knowledge may consume both | `stream_seq` and idempotent workflow append are explicitly described as hardening targets and remain unimplemented. The ADR does not falsely claim they shipped. |
| Conductor `fleet-dashboard.html` | Partial | Current run-history rows and trace links | Its two-minute generator still hardcodes July 1 `172.19.*` VM addresses and AM4’s retired tailnet address, so node/GPU health is false. Use OMEN `fleet/fleet_ping.py` for reachability. |
| `HEARTH-CALL-MIX.html` | Generated aggregate | Boundary-call volume by family, daily mix, and inference token flow by backend | It intentionally counts only kernel-ledger events. The separate sampled Ollama bypass signal is disclosed but not merged because it is incomplete, mostly unattributable, and has no request/token payload. Regenerate directly with `python -m hearth.projection.call_mix_dashboard`; the six-hour knowledge-rebuild timer also refreshes it. |

## Current source-of-truth checks

- `python fleet/fleet_ping.py --all-services --no-color`: 11 up, 0 down,
  1 optional/offline. It also exposed a stale inventory description claiming
  `claudefarm1` is down when it is reachable.
- `python -m hearth.callers.doorcheck --json --strict`: gateway, auth, scoped
  tool discovery, and all 16 providers pass. Its backend facet is a TCP/process
  readiness check, not an inference proof: OMEN Ollama still returns HTTP 500
  because the interrupted installation lacks `llama-server.exe`.
- A pinned `am4-moe` file-packed generation was already proven at about 28
  seconds with caller identity, token/cost receipt, and ledger write.
- Current registered surface: 47 tools, 16 providers, 21 tool-capability
  names, and 7 profiles. This authorization taxonomy is distinct from the
  still-empty materialized belief-capability store. Scoped doorcheck correctly
  exposes only the probe profile’s one tool.
- The generated call-mix snapshot contained 19,644 kernel events at 14:51 PDT;
  the buddy page uses `19.6k+` because unattended timers keep advancing it.
- Current workflow ontology and JSON Schema contain the same 21 event types
  with no unphased events.
- Final full repository test run: 1,083 tests and 86 subtests green.

## Documentation checks

- Every direct local link and in-page anchor in the buddy briefing resolves.
- Every local HTML/Markdown reference in the seven linked repository
  documents resolves after honoring their `:line` suffixes.
- `python -m tools.workflow.check_doc_claims` passes, but its registry covers
  only two other authored-document claims. One passes (`45` experiment
  candidates); the zero-capability claim is explicitly waived. It does not
  validate the demo artifacts, so this manual audit remains necessary.

## Demo truth hierarchy

1. For reachability, use `fleet/fleet_ping.py`, then explain that TCP is not
   inference.
2. For door/auth/provider health, use strict `doorcheck`.
3. For an inference proof, use pinned `am4-moe` and keep its returned receipt.
4. For caller scope and total audit count, use `kernel_status`.
5. For aggregate economics/capacity, use `HEARTH-DASHBOARD.html` and quote all
   displayed timestamps.
6. For fleet history, use the conductor dashboard’s run table, not its node
   cards.
7. Use older overviews/diagrams only for the durable structure stated in the
   buddy briefing’s audited library table.
