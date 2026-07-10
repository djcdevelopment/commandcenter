# Survey: `liveView` and the `writing` workspace (AM4 mount)

*Sonnet agent survey, 2026-07-10, read-only over SSH (`homebase` → AM4 NTFS
mount). Report preserved verbatim.*

---

## Part 1 — liveView (`/mnt/win/work/liveView`)

### What it is
A **local-first artifact exploration tool for folder-structured engineering outputs** — JSON, JSONL, logs, traces, derived metadata. Deliberately split into two independent git repos with a hard ownership boundary: `ingest/` (scans a target folder, emits deterministic **snapshot** artifacts; Node/TypeScript) and `ui/` (loads snapshots, renders for inspection; React/TS/Vite). Governing principle, stated identically in README and PROJECT_CONTEXT: **"ingest owns truth. ui owns interpretation."** Explicitly not real-time: "Real-time watching is not required for v1."

### Timeline
| Repo | Commits | First | Last |
|---|---|---|---|
| top-level wrapper | 10 | 2026-03-06 | 2026-04-11 |
| `ingest/` | 6 | 2026-03-06 | 2026-03-11 |
| `ui/` | 13 | 2026-03-06 | 2026-04-08 |

**`ui/` has 31 uncommitted changed/untracked files** — substantial unshipped work (new `WorkbenchShell`, `PipelineObservatoryShell`, `ConversationExplorerShell`, `read-models/`) more advanced than the last commit shows. Dormant since ~2026-04-11.

### The "deep nested" part
`ui/plans/pipeline/` contains a `workbench-v2/` design folder **and** a `work/` subfolder that recursively re-contains a duplicate copy of the same path inside itself (`ui/plans/pipeline/work/liveView/ui/plans/pipeline/workbench-v2/...`) — the project nested inside a snapshot of itself. Real design docs: `ui/plans/pipeline/workbench-v2/*.md`.

### Key design docs
- **PROJECT_CONTEXT.md** — canonical architecture; explicit per-repo model split: "Claude/Sonnet is used primarily for `ingest/`... Codex is used primarily for `ui/`."
- **SNAPSHOT_CONTRACT.md** — the data contract: `inventory.json`, `events.json`, `series.json`, `edges.json`, `summary.json`, `schema-version.json`.
- **plans/PLAN_ORCHESTRATION_INDEX.md + PART0–6** — gated sequence with a live table distinguishing *published* contract fields from *quarantined/speculative* ones the UI is forbidden to consume until ingest publishes them.
- **workbench-v2/ARCHITECTURE.md** — four-layer separation (projection contract truth → derived read model → overlay/view state → render-only presentation) for visualizing "~49k candidates → 250 ranked pairs → hubs → stable projections" — the UI for the chatGPT_parser corpus.
- **ingest/PHASE2_REPLAY_DESIGN.md** — deterministic replay/diff design (`run-manifest.json`, strict vs. reprocess modes).
- `artifacts/sessions/20260410-222906/research_bridge.md` — documents a real cross-repo fix connecting liveView's UI to `chatGPT_parser` and `ai-systems-research`.

### Load-bearing quotes
- "ingest owns truth. ui owns interpretation." — README.md
- "The UI must not invent artifact shapes. If the UI needs a new artifact, the contract must be published in the ingest repo first (schema + fixture)." — PLAN_ORCHESTRATION_INDEX.md
- "Ingest owns truth. If there is ambiguity, make it visible rather than silently guessing." — ingest/CLAUDE.md

## Part 2 — writing (`/mnt/win/work/writing`)

Git repo, 19 commits (2026-04-09 → 2026-06-16), **plus 83 uncommitted/untracked paths** — leading edge runs to 2026-06-17. Top-level: `README.md`, `WRITING_CONTRACT.md`, `ARTIFACT_SCHEMA.md`, `START_HERE.md`, `SESSION_LOG.jsonl`, `pre-publish-gate.md`, `meta/`, `drafts/` (36 files), `articles/demo-launch/`, `docs/adr/`, `origin-layer/`, `portfolio-assessment-2026-06-16/`, `artifacts/sessions/`.

### Draft clusters
- *Dual-B70 GPU cluster* (2026-05-24, 5 files): `two-b70s-on-windows`, `dual-b70-windows-vulkan-recipe`, `the-cruise-before-the-cut`, `three-times-i-almost-called-it`, `li-post-two-b70s`.
- *Constellation-manifest cluster* (2026-06-07): `cognitive-snapshot`, `recommended-package`, `recursive-artifact-engine`.
- *Second-order-AI-thesis cluster* (2026-06-16/17, YAML-fronted with hold/verify flags): `entropy-not-corpus-size`, `premature-abstraction-deferred-value`, `the-processor-everyone-can-buy`, `treat-attention-like-its-yours`.
- Standalone: `the-mechsuit.md` (outline-stage), `dispatch-maf-otel-stack.md`, `five-layer-build-session-blueprint.md` (+ .docx), `metric-that-punishes-right-behavior.md`, `hashed-tokens-stop-llm-citation-hallucination.md`, `crash-on-the-wrong-drive.md`.

### Five most substantial pieces
1. **"Artifact-Driven Development Is How I Show the Whole Work"** (`drafts/2026-06-05-artifact-driven-development-homage.md`) — the ADD manifesto: traces a restart/memory fix in `ai-systems-research` becoming the connective discipline across the constellation (`ai-dev-system`, `planner`, `precheck`, `contextforge`, `ashley`, `portmap`, `scarecrow`). Thesis: artifacts let him show failure and pain, not just success. Complete, polished, 19-source citation trail.
2. **"Entropy, Not Corpus Size"** (2026-06-16) — AI coding fluency tracks "idiom entropy," not corpus size; `fluency ≈ corpus density × (1/idiom variance)`. Near-complete.
3. **"Five Layers for a Replicable AI-Assisted Build Session"** — context tiers, bootstrap protocol, gate signals for parallel multi-agent sessions. Most publish-ready.
4. **"The Processor Everyone Can Buy"** (2026-06-17) — forestry "high-grading" mapped onto AI commoditizing code production; judgment/stewardship becomes the scarce skill. Flagged for fact-verification, else complete.
5. **"The Mechsuit"** — live incident narrative: an agent repeatedly declared dual-Arc-B70 inference "impossible"; Derek's rule: disbelieve "impossible," try the layer below. §1–2 of 5 drafted.

*Honorable mention*: `portfolio-assessment-2026-06-16/` — a brutally frank self-audit ("bus-number is provably 1," publish cadence 13→4→1 across Apr/May/Jun).

### Flagged for documentation-practice storytelling
- The workspace enforces the **same** session-artifact/restart discipline (`lesson_learned.md`, `research_bridge.md`, `session_reasoning_graph.json`) as the build repos — one documentation OS applied uniformly.
- `origin-layer/PROVENANCE.md` — 17 rescued "crown jewel" idea docs, SHA-256 hashed into git (2026-06-16) — organizational-memory preservation as an explicit, dated act.
- `persona-perspectives-repo-stewardship.md` — one governance decision written through 5 stakeholder lenses.
- `QuestionsDerekShouldAnswerToRefineAgentContent.md` — async human-in-the-loop content refinement.

### Quotes worth reusing
- "If AI work cannot survive a restart, it is not a system yet."
- "Not to pretend the work was clean. To make the mess useful."
- "The agent has no way to mark the difference between *I exhausted my solution space* and *the solution space is empty*. So it reports the latter." — the-mechsuit.md
- "When cutting is cheap, stewardship is the product." / "A legible wrong answer is more dangerous than a messy one, precisely because it survives the walkthrough." — the-processor-everyone-can-buy.md
- "If a decision matters, record it. If a claim matters, ground it. If uncertainty matters, label it." — WRITING_CONTRACT.md

## Part 3 — `/mnt/win/writing` (root)

Not a second workspace: exactly one file, `article-prep-PSA-native-module-abi.md` (2026-05-15) — a postmortem on a Node native-module ABI mismatch caused by concurrent AI agent sessions fighting over machine-global state. Canonical home is the `campfire` repo; this is a stray copy. Good self-contained case study on multi-agent coordination failure.
