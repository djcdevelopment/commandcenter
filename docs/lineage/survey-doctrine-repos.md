# Survey: `ai-systems-research` and `ai-dev-system` (March 2026 doctrine repos)

*Sonnet agent survey, 2026-07-10, read-only over SSH (`homebase` → AM4 NTFS
mount, `/mnt/win/work/`). Report preserved verbatim (HTML-escape artifacts
cleaned).*

---

Both are real git repos carrying a March 2026 proprietary-copyright notice, and are the two lower tiers of a documented **4-repo ecosystem**: `ai-dev-system` (producer) → `liveView/ingest` → `liveView/ui` → `ai-systems-research` (meta-layer), with `chatGPT_parser` as an external-corpus producer.

**Upfront correction to the survey brief:** the framing "OpenTelemetry + Microsoft Agent Framework from day one, a builder farm, QA scoring, an association engine" does **not** match what is literally in these repos. OTel and MAF appear only in a **later (May 2026) retrospective/next-gen-framework layer**, not in the March 2026 original design, and "builder farm" / "QA scoring" / "association engine" do not appear verbatim anywhere in either repo.

## Repo 1 — `ai-systems-research`

- **README:** "Artifact-driven research repository for capturing, indexing, and analyzing AI-assisted engineering sessions... the **research loop** side of the broader system." Framed around **re-entry after context switching** — the session-handoff repo.
- **19 commits**, 2026-03-07 → 2026-05-28. Dense March 7–14; then April 8 (license), April 10–11, and two late-May commits (constellation.yaml enrollment, MANIFEST_LINK pointer) — hot for a week, then touched as a higher-altitude registry document.
- 166 files; 115 `.md` — a documentation/research repo, not code.

### The design
`plans/research/current_architecture_overview.md` (602 lines) defines a **four-role ecosystem**: Execution/Decision Surface (`ai-dev-system`), Artifact Synthesis/Meta-Analysis (this repo), Observability/Contract Inspection (`liveView` — "a visual debugger for contract inconsistency and system state"), External Corpus Ingestion (`chatGPT_parser`).

Core vocabulary — **Evidence → Artifacts → Projections → Contracts** ("the four-noun model"): "Artifacts are the interoperability layer. Not agents. Not raw telemetry. Not UI components." Parallel framing everywhere: **dual-loop architecture** — Implementation Loop (build → run → artifact → inspect → improve) and Research Loop (artifact → analysis → synthesis → writing → new questions).

### Named concepts
- **Session artifact set** — the 7-file bundle a session must produce: `system_snapshot.md`, `lesson_learned.md`, `complexity_inflection_points.md`, `strategy_context_reduction.md`, `research_bridge.md`, `request_log.json`, `session_reasoning_graph.json`.
- **SESSION_LOG.jsonl** — append-only one-line-per-session ledger. **START_HERE.md** — fixed re-entry landing page ("reduces restart cost to reading two small files").
- **RESEARCH_CONTRACT.md** — 5 research artifact types: Observation, Experiment, Question, Snapshot, Synthesis + "Done Criteria for Article-Ready Topic."
- **CHAT_BOOTSTRAP.md / CHAT_SESSION_PROMPT.md** — a literal prompt library for converting a chat conversation into the 7-artifact bundle.
- **constellation.yaml / MANIFEST_LINK.md** (May 2026 layer) — hand-enrollment against a "hardened v1 schema" in `D:\work\gad\pm\manifest\`; declares this repo the "proto-registry" superseded by the next-generation manifest framework.
- **why_connected_candidates / why_connected_queue / hub_suggestions** — the scored-pair/ranking/grouping pipeline (closest analog to an "association engine") — belongs to `chatGPT_parser`, described secondhand here.
- **independent_adversarial_review_claude.md / independent_fullrepo_review_claude.md** (2026-03-12) — two commissioned opposing "MIT professor" portfolio reviews of the 5-day output, one hunting for reasons to dismiss it, one sympathetic.

### MAF / OTel / MCP evidence
- **MAF:** zero mentions in March docs. `MANIFEST_LINK.md` (May): "**Microsoft Agent Framework (MAF) as the execution substrate.** MAF 1.0 GA shipped 2026-04-03; Python 1.6.0 on 2026-05-22. New." — explicitly labeled *new*, not inherited.
- **OTel:** absent from March docs. `bridge-synthesis-2026-05-26/04-counterfactuals.md`: *"The March 2026 work treats session artifacts as the canonical observability substrate... The field has since converged on the OpenTelemetry GenAI semantic conventions... **Assessment. Field right, repo wrong.** The instinct to make sessions observable was correct and early, but the substrate — markdown bundles with custom JSON sidecars produced by a chat-mode prompt — doesn't interoperate with LangFuse, can't emit OTel spans."* — the repo auditing itself.
- **Ledgers:** heavy, literal — "artifact ledger" snapshots by name; immutable dated session folders.

### Quotes
1. "The system is organized around artifacts and contract surfaces, not around a single runtime or application boundary."
2. "Artifacts are the interoperability layer. Not agents. Not raw telemetry. Not UI components. Not repo-specific internal models."
3. "The challenge now is not to reduce ambition. The challenge is to convert emergent patterns into declared architecture."
4. "this repo was the *implementation surface* for '**Artifacts as Context Waypoints. Artifacts are the "shared memory" between me and the agents.**'" — MANIFEST_LINK.md
5. "Code can be disposable if the deterministic artifact history that produced it is preserved." — `artifacts/sessions/2026-03-09-conversation-ledger/lesson_learned.md`

Also: `MANIFEST_LINK.md` self-narrates its recursion — *"The framework that points back to ai-systems-research is using ai-systems-research's protocol to do the pointing. The pattern observes itself observing itself."*

## Repo 2 — `ai-dev-system`

- **README:** "Local-first artifact-producing run harness... generates traceable run artifacts that feed the LiveView observability pipeline... to make AI-assisted development deterministic, inspectable, and replayable by emitting structured artifacts and lifecycle events instead of opaque logs." Internally: the **"Traceable AI Build Companion"**.
- **5 commits**, 2026-03-06 → 2026-03-11 (+ one license commit 04-08). A tight 5-day build that never resumed.
- 139 files (76 `.json`, 16 `.py`) — small Python codebase plus 16 real, timestamped run directories proving it was executed.

### The design
`docs/SYSTEM_ARCHITECTURE.md`: *"Every meaningful action produces a durable artifact."* Pipeline: run → `RunContext` → `run_started` → workflow_fn → `write_artifact()` (immutable JSON envelope: `schema_version, artifact_id, run_id, artifact_type, created_at, source, content`) → `artifact_created` per artifact → `run_completed`/`run_failed` → `write_manifest()` → `append_to_index()` (global `run_index.jsonl`). Fixed event vocabulary enforced in code (`EVENT_TYPES` frozenset — raises on unknown types). Only one workflow implemented (`design_note_generator`).

`docs/Build_memo_Arifact_engine.md` narrates the build's own emergent discovery — Phase 1 finished quickly and in the *same session* grew a Live Operational View, an Ingestion Pipeline, a formalized Artifact Pipeline, and a Live Visualization layer — tagged `v0.1-artifact-engine`. `docs/OBSERVABLE_PIPELINE_v0.1_RUNBOOK.md` documents the validated 3-repo end-to-end smoke test (this CLI → liveView/ingest → liveView/ui).

### MAF / OTel / MCP evidence
**Zero hits** for MAF, OTel, MCP, ollama, CP-SAT/OR-Tools across every file. Plain-Python dataclass + JSON + pytest, hand-rolled runner. Event-sourcing pattern (event stream + manifest snapshot + global index) used literally but never named.

### Quotes
1. "Every meaningful action produces a durable artifact." — SYSTEM_ARCHITECTURE.md
2. "It is evolving into a **system for capturing work, reasoning about it, and compounding that reasoning into future leverage**." — Build_memo
3. "Artifacts now function as **the system's memory units**." — Build_memo
4. "Event vocabulary is fixed: run_started, artifact_created, run_completed, run_failed." — src/contracts.py

## Cross-cutting conclusion

What genuinely *is* here, robustly and by name: the **artifact/evidence/projection/contract vocabulary**, the **dual-loop framing**, the **session-ledger/re-entry protocol**, and a real working **producer → ingest → UI → research** pipeline — Derek's original end-to-end design instinct, executed in plain Python/JSON/PowerShell, with MAF/OTel bolted on deliberately two and a half months later as a named "next-generation surface."
