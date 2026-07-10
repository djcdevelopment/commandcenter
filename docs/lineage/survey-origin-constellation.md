# Survey: the origin constellation at `/mnt/win/work/start/`

*Sonnet agent survey, 2026-07-10, read-only over SSH (`homebase` → AM4 NTFS
mount). Mission: find the pre-Farmer planner/critic projects ("names with
variants of plan; Ashley was one of them"). Report preserved verbatim.*

---

**Location found:** `/mnt/win/work/start/` — a subdirectory of `/mnt/win/work/` that was NOT visible at the top level of `/mnt/win/work` itself (confirming the clue). This single `start/` directory holds the entire early constellation named in Derek's June essay, all as sibling folders: `ashley`, `contextforge`, `planner`, `precheck`, `precheckv2`, `scarecrow`, plus a `portmap` service and `farmer` (the later, already-known April repo).

Also checked and ruled out as unrelated: `/mnt/win/work/planning` (dated 2026-04-07 to 05-08, post-Farmer, different project), `/mnt/win/work/planning-runtime`, `/mnt/win/work/ai-systems-research/plans` and `/mnt/win/work/chatGPT_parser/plans` (generic "plans" note-folders inside unrelated projects). `/mnt/win` has no sibling mounts.

## TARGET 1 (PRIMARY): `/mnt/win/work/start/planner` — "Planner" (gen-1, the true origin)

- **Git repo:** Yes. Remote `https://github.com/djcdevelopment/start-planner.git`. **1,660 commits.**
- **Date range:** First commit `2026-03-17 00:52:49 -0700` ("Document planner dry-run flow and validation"); last commit `2026-04-08 22:58:02` ("Add proprietary stewardship notice" — a batch license-notice commit applied across the whole constellation on the same day). Genesis vision doc (`FIRST_PASS.md`) internally references `created: 2026-03-16`, i.e. the actual brainstorming predates the first commit by a day. **~3 weeks before Farmer (April 6–16).**
- **File count:** 6,945 files (includes venv/pycache noise; core project is Python).

### What it IS
A **serial, file-based, Git-backed local planning system** — "Three Python scripts. Two PowerShell wrappers. Git. Ollama. That's it." (`FINAL_ARCH.md`). Main files: `plan.py` (137KB — the planner state machine), `planner-server.py` (51KB, HTTP server for `viewer.html`/`wiki.html`/`analysis.html` dashboards), `test_plan.py`, `expand_backlog.py`, `reseed_features.py`. Docs tree: `FINAL_ARCH.md`, `docs/archived/{FIRST_PASS.md, FIRST_PASS_ARCH.md, REFINED_ARCH.md, planner_refinement_prompt.txt}`, `.plan/` (the runtime state directory: `project-brief.md`, `guardrails.md`, `builder-rules.md`, `backlog.json`, `epics.json`, `suggestions.log.md`, `runs/`, `.plan/oldDocs_howWeGotHere/PHASE_1B_DESIGN.md`).

### Planner/critic evidence — the loop, in Derek's own terms
The genesis brain-dump (`docs/archived/FIRST_PASS.md`, dated 2026-03-16) originally specified **three separate AI roles using different providers**, before the project pivoted to fully local models:

> "Planner | Claude Opus via API (overnight) | Decomposes work into stories, sets builder policy, records reasoning"
> "Builder | Codex (daytime, developer-supervised) | Executes one story at a time, produces file edits + commands"
> "QA | Claude Haiku or local model (post-build) | Evaluates builder output against acceptance criteria"

The test project brief inside that same doc was literally **"Heartbeat — a personal health-tracking app for Android"** — the very first dry-run subject for the whole loop.

By `FINAL_ARCH.md` ("Local AI Dev System: Final Architecture (v3)"), the system had gone all-local:
> "**Planner** (phased loop) ... Model: Mistral Small 3.1 24B ... Constraint: Never touches source code. Only writes to `.plan/`"
> "**Builder** (daytime, developer-supervised) ... Model: Qwen2.5-Coder 14B ... Constraint: Receives fully specified story. Does not interpret the project brief."
> "**Evaluator** (after builder) ... Model: Llama 3.1 8B ... Produces pass/fail verdict with per-criterion evidence. Classifies failures using typed taxonomy."
> "The Closed Loop: Planner → Stories → Builder → Result → Evaluator → Feedback → Planner ... This is the Phase 5 closed loop. The planner adapts based on downstream defects." (`3.0.txt`)

`PHASE_1B_DESIGN.md` (dated 2026-03-17, "Hierarchical Planner with Bounded Assessment") names the actual critic mechanic:
> "INIT → GENERATE_EPICS → SELECT_EPIC → REFINE_FEATURE → **ASSESS** → **DECIDE** ... Four model verdicts: accept, refine, restructure, stop ... All loops bounded by hard counters."

Live critic output is preserved verbatim in `.plan/suggestions.log.md` — real assessment passes against a sample feature (payment-plan customization), e.g.:
> "**Verdict:** refine ... **Unresolved Blockers:** Business rules for eligibility and calculation are not defined, but the guardrails require all business rules to be defined before implementation..."

The QA failure taxonomy (`FINAL_ARCH.md §5`) distinguishes **Contract/Format failures** (`format_error`, `hallucination`) from **Implementation failures** (`missing_implementation`, `wrong_pattern`, `missing_wiring`, `scope_violation`, `guardrail_violation`, `incomplete_criteria`, `test_failure`) — this taxonomy is a direct ancestor of later QA/critic vocabulary in the fleet.

`docs/HANDOFF-2026-03-19.md` documents a subtle bug Derek chased: the critic's raw verdict (`"stop"`) vs. the coerced/normalized verdict (`"refine"`) diverging — `normalize_assessment_result()` forces `refine` when blockers are present. This `raw_verdict` vs `normalized_verdict` distinction is preserved as an explicit observability fix.

### Verbatim quotes
1. `3.0.txt`: *"This is the Phase 5 closed loop. The planner adapts based on downstream defects. Stories that consistently fail in certain ways cause the planner to write different stories."*
2. `FINAL_ARCH.md`: *"A serial, file-based, Git-backed planning system that runs one role at a time on a single Windows 10 machine with a 4070 Ti (12 GB VRAM). Not an agent platform. Not concurrent. Not a framework."*
3. `docs/archived/FIRST_PASS.md`: *"## App / Heartbeat — a personal health-tracking app for Android."*
4. `.plan/oldDocs_howWeGotHere/PHASE_1B_DESIGN.md`: *"Phase 1b evolves the planner from a single-pass story generator into a multi-pass, hierarchy-aware system with a bounded self-assessment loop."*
5. `docs/HANDOFF-2026-03-19.md`: *"Found and explained the verdict mismatch where raw assess output could say `stop` but the planner printed `refine`."*

### Dated/story-worthy artifacts
- `docs/HANDOFF-2026-03-19.md` — full session handoff log with concrete run IDs (`plan-2026-03-19T155814`, `plan-2026-03-19T172521`), root-cause writeups, exact commit hash `40c1bdb`.
- `.plan/RESEARCH_LOG.md` schema (defined in `FINAL_ARCH.md §3.4`) — an append-only, date-stamped human-in-the-loop research journal format: date / phase / runs reviewed / observations / hypotheses / decisions made / next experiment.
- `.plan/suggestions.log.md` — timestamped critic transcripts, e.g. `## [2026-03-21T05:59:12.678432+00:00] Run: plan-2026-03-20T225840`.
- Commit trail of `[STOPPED]` and `[EPIC]` tagged commits from March 17 showing live iteration: `03ed67f [STOPPED] plan-2026-03-17T034319 — refinement parse failure`, `6dd61f9 [STOPPED] ... max feature retries`, `a4883ce Implement Phase 1b: multi-pass hierarchy planner with bounded assessment loop`.
- `docs/archived/HUMAN------handoffnotes.txt` (not read in full, flagged for follow-up if wanted).

## TARGET 2 (PRIMARY): `/mnt/win/work/start/ashley` — "Ashley" (gen-3 rebuild)

- **Git repo:** Yes. Remote `https://github.com/djcdevelopment/ashley.git`. 20 commits.
- **Date range:** `2026-04-02 23:02:14` ("Stage 1: Repo skeleton...") to `2026-04-08 22:58:02` ("Add proprietary stewardship notice"). **Just before/overlapping Farmer's April 6–16 window** — Ashley is the direct one-week predecessor pivot into Farmer, not a Feb–March artifact itself (its 2025-02-24 file dates on stray `Npgsql.dll` binaries are NuGet package cache noise, not project history).
- **File count:** 3,301 files (heavy with `bin/Debug` build output; source is a real .NET+React solution).

### What it IS
> "Third-generation constraint-aware planning engine. .NET 9 API + React/TypeScript + PostgreSQL." (`CLAUDE.md`)

Architecture: 7 layers — Identity, Planning/CLI, Execution, Artifact, Analysis/Learning, UI Interpretation, Orchestration. Projects: `Ashley.Api` (HTTP+SSE, 20 endpoints), `Ashley.Engine` (state machine/providers/prompts/planner, 11 states), `Ashley.Learning` (harvester, lesson computer), `Ashley.Data` (Postgres migrations), `Ashley.Specs` (32 verification checks), `ui/` (React+Vite, 9 routes). Key files: `Ashley.sln`, `START.md`, `Workbook_Derek_Friday.md`, `docs/retrospective-2026-04-04.md`, `plan/rebuild-plan.md`, `3.0.txt` and `personas.txt` (same content shared with the Planner project — carried forward directly).

### Who/what is "Ashley"
**Ashley is a project/system codename, not a persona, character, or agent role.** It is used purely as a .NET namespace prefix (`Ashley.Api`, `Ashley.Engine`, `Ashley.Learning`, `Ashley.Data`, `Ashley.Specs`). No text anywhere in the repo personifies "Ashley"; every use treats it as the system's name (e.g. `Workbook_Derek_Friday.md`: *"Ashley is built. 9 commits, 10 stages, 32 spec checks passing."*). No naming rationale is documented.

### Planner/critic evidence and lineage
Ashley's own `CLAUDE.md` explicitly states its lineage:
> "**Reference Systems** — Precheck (`D:\work\start\precheck`) — Engine source, learning subsystem, UI product spec. Planner (`D:\work\start\planner`) — Pattern reference for simplicity, file-based state, suggestion logs."

Commit history literally documents "porting" the original Python planner's concepts into .NET:
> `3a884c4` "Stage 2: **Port execution engine** — state machine, providers, planner"
> `969976d` "Stage 3: **Port learning subsystem** with v2 profile-scoped keys"

`docs/retrospective-2026-04-04.md` explicitly names prior generations and a known bug fixed across the port:
> "Profile-scoped learning model — the `(accountId, profileId, guardrailId)` key solved the cross-profile contamination bug from **gen-2**."

The full loop, in Ashley's own words (`Workbook_Derek_Friday.md`): *"The full pipeline exists: intake → decompose → assess → decide → stream → persist → harvest → learn."* — same `assess`/`decide` critic terminology as gen-1's `PHASE_1B_DESIGN.md`. Governing principles from `plan/rebuild-plan.md`: *"1. Profile is the unit of value. 2. No validation, no commitment. 3. Projection, not reconstruction. 4. Earn the abstraction. 5. Ship execution first."*

### Verbatim quotes
1. `CLAUDE.md`: *"Third-generation constraint-aware planning engine. .NET 9 API + React/TypeScript + PostgreSQL."*
2. `docs/retrospective-2026-04-04.md`: *"Built from scratch in 18 commits, ~8,300 LOC across 97 source files."*
3. `Workbook_Derek_Friday.md`: *"Everything runs against echo provider. Nothing has run against a real LLM yet ... The single most important next step is one real run with a real model."*
4. `plan/rebuild-plan.md`: *"Status: COMPLETE — all 10 stages built ... 9 commits, ~9500 lines of code, 93 source files."*
5. `CLAUDE.md`: *"Key Invariants: 1. Profile is the unit of value and learning. 2. Learning key: (accountId, profileId, guardrailId)... 7. Providers are interchangeable, not decision authorities. 8. Every terminal outcome has an explicit reason."*

### Dated/story-worthy artifacts
- `docs/retrospective-2026-04-04.md` — a same-week self-retrospective covering "what went well," naming-drift cleanup, and known tech debt (Program.cs monolith, repeated SQL patterns).
- `Workbook_Derek_Friday.md` (dated "Friday 2026-04-03") — a prioritized personal to-do list for Derek's very next session: "1. First Real Run... 2. First Learning Loop... 3. Two-Profile Comparison..." with exact file paths.
- `plan/rebuild-plan.md` stage table (10 stages, each dated/checked off same night, 2026-04-02 to 04-03).

## Adjacent context

- **`/mnt/win/work/start/precheck`** — the missing **"gen-2"** link between Planner and Ashley. Git repo, 35 commits, `2026-03-24` → `2026-05-08`. Its own `CLAUDE.md`: *".NET API with PostgreSQL for operator verification workflows."* Its `plan/` subdirectory contains explicit porting docs: `port-python-planner-to-dotnet.md`, `engine-port-python-to-dotnet.md`, `v2-architectural-reset.md`, `closed-loop-learning-subsystem.md` — confirming the sequence **Planner (Python, gen-1) → Precheck (.NET port, gen-2) → Ashley (full rebuild, gen-3)**. Also contains a residual `planner_core/` Python package (`decision_policy.py`, `guardrails.py`) — the literal ported engine core. A separate `precheckv2` directory exists as a single-commit snapshot (`2026-04-04`, 1 commit) of a "Precheck v2 — operator verification workflow system."
- **`/mnt/win/work/start/contextforge`** — git repo, 5 commits, `2026-03-21` → `2026-05-07`. `CLAUDE.md`/`BUILD_PLAN.md` describe a passive-capture/association-engine system ("Windows App... Postgres is local for now").
- **`/mnt/win/work/start/scarecrow`** — git repo, 5 commits, `2026-04-06` → `2026-05-08`. `docs/architecture.md`: *"Scarecrow is an Electron desktop application that orchestrates and monitors Hyper-V VMs running Claude Code autonomous builder agents ... via SSHFS-mapped Windows drives and SSH."* Direct conceptual ancestor of today's fleet/builder-VM orchestration (`.comms/progress.md`, `.comms/port-registry.json` protocol files match today's mechnet vocabulary).
- **`/mnt/win/work/start/portmap`** — a small shared service (`registry.json` + `portmap.py`) that every one of the above projects registers its ports with, to avoid collisions across the constellation running side-by-side on the same box.
- **Not explored in depth** (flagged for follow-up): `precheck/plan/*.md` (35 planning docs — `v2-risk-critique.md`, `v2-feedback-adjudication.md`, `guardrail-effectiveness.md` look especially critic-relevant), `contextforge/docs/`, `scarecrow/src/`, `ashley/ui/` internals, `planner/scripts/overnight_runner.py` and `langfuse_smoke.py`.
