# Constellation Baseline — Wave 2 (Companion)

**Date:** 2026-06-28
**Companion to:** `C:\work\commandcenter\CONSTELLATION-BASELINE-2026-06-28.md` (Wave 1 — deep profiles of the 13 core repos + the convergence thesis + the thin slice)
**Scope of this document:** the *rest* of the ~70-directory constellation (everything Wave 1 did not deep-profile) plus three cross-cutting distillations — house-style engineering patterns, the lessons/decisions ledger, and the naming cosmology — all reconciled into one ruling on what the broader landscape changes for the build.

> Wave 1 answered *"what are the 13 organs and how do they converge?"* Wave 2 answers *"what is everything else, what collapses together, what is dead weight, and what did the whole portfolio teach us about how to build?"*

---

## 1. Executive Summary

The 13-repo spine profiled in Wave 1 is the **load-bearing skeleton**; the remaining ~55 directories fall into four buckets, and only a thin slice of them touch the spine at all.

**How the broader constellation relates to the 13-repo spine:**

- **Inference substrate extensions (the real new signal).** `xpu-train` (on-rig LoRA fine-tuning → GGUF handoff to denning), `infexp` (denning's research-program wrapper), and the `8350`/ai-1 ops dir (a LAN Ollama node) are genuine extensions of the B70 inference substrate the spine already reads. `xpu-train` is the single clearest commandcenter-relevant repo outside the 13 — it is the **missing "training half"** that turns the planner/critic/dev loop from self-orchestrating into self-improving, and it carries the *first declared cross-repo contract* (`finetuned-gguf-handoff`, xpu-train → denning).
- **A large WoW/guild product cluster (GAD).** `RaidUI`, `lantern`, `campfire`, `hearth`, `Guild`, `leopard`, `gad-bot`, `discoverlay`, `parser-handoff`. Domain content is **LEAVE** (it's a product, not the fleet). What we INHERIT from it is *patterns and exemplars*: the local-Ollama bridge, the Constitution-as-tests idea, the file-bus/last-good-spec overlay pattern, the provider-as-config contract.
- **The lineage / ancestry layer.** `ai-dev-system`, `ai-systems-research`, `archStandards`, and the `start/` incubator (scarecrow, ashley, contextforge, old farmer/portmap copies). These are the *quarry* the spine was mined from — read for ORIGIN rationale; inherit the mature copies (in `writing`, `planning`, `gad/manifest`), not the ancestors.
- **Personal / vendored / junk / empty.** `reader`, `stars-app`, `work-visuals`, `game`, `comfy`, `comfyrp`, `mfa`, `platform-tools`, `tools`, `Antigravity`, `event-viewer`/`azure-event-grid-viewer`, plus a pile of empties (`lit`, `inf`, `opencode`, `charter`, `lootbox`, `coworkhardware`, `CoworkHardware (1)`, `website`). Most are out-of-scope; several have secret-hygiene smells.

**Biggest convergence opportunities (detail in §3):**
1. **Collapse `hearth` + `Guild`** — same Next.js + Postgres guild-memory site, forked into two dirs (Guild bundles the GoatHerder bot; hearth pushes the bot to a separate repo). They are one product.
2. **Reconcile the two raid-review UIs** — `RaidUI` (older DuckDB parser+viewer) and `lantern` (newer player-first surface now folded into Tempo). They are already converging onto Tempo; pick Tempo as host.
3. **Fold `work-visuals` into `liveView`** — both are local artifact/workspace visualizers; liveView is the contract/provenance-driven engine, work-visuals is a throwaway D:\work snapshot.
4. **Merge the two Azure Event Grid viewer forks** (`event-viewer` ≈ `azure-event-grid-viewer`) — near-byte-identical; keep one.
5. **Merge `comfy` + `comfyrp`** into a Valheim save-tooling bucket and re-cluster them *out* of inference-ml.
6. **Consolidate business-ops** (`steppe-strategy` + `steppe-launch` + archStandards positioning docs) into one workspace kept **outside** the technical constellation.

**The dead weight (delete/archive/exclude):** all empties (`lit`, `inf`, `opencode`, `charter`, `lootbox`, `coworkhardware`, `CoworkHardware (1)`); vendored bundles (`platform-tools`, `tools`, `Antigravity`, `mfa`); abandoned design dump (`website`, superseded by `www`); scratch/junk (`herm`, `ConsoleApp1`, `stars-app`). **Never traverse** `start/data` (live Postgres), `Antigravity` (~300MB Chromium), or any committed `node_modules`.

**Three cross-cutting distillations** (§4–§6) confirm the spine: the whole constellation independently re-converged on **a thin conductor over a file-based truth layer, commodity dependencies behind single adapter seams, every decision an ADR, every run replayable from disk, every failure captured as data, readiness proven by exercising the real path.** commandcenter should standardize on this explicitly rather than re-derive it a 14th time.

**What this changes for the build (§7):** mostly *confirms* Wave 1, with three concrete additions — (a) `xpu-train`'s GGUF handoff is the canonical example of the cross-repo contract pattern the spine needs; (b) the merge map removes ~20 dirs of noise from the portability/path surface; (c) the lessons ledger hardens the day-one ADR (which orchestrator is run-of-record) and the safety floor (denning's zero-dep watchdog is non-negotiable for any unattended fleet job).

---

## 2. Full-Constellation Taxonomy

Grouped by category. `cc-relevance` is relevance to commandcenter specifically (not general quality). "Overlaps" lists the strongest merge/duplication relationships.

### 2.1 Inference / inference-ml (the new spine-adjacent signal)

| Repo | Maturity | cc-relevance | Overlaps |
|---|---|---|---|
| `xpu-train` | **active**, loop closed 2026-06-22 (Phases 0–4 validated) | **high** — training half; first declared cross-repo contract (`finetuned-gguf-handoff` → denning); enrolled in constellation.yaml | infexp, battlemage, denning |
| `infexp` | plans-only, recent (docs 2026-06-19), runs/ empty | **med** — denning's research-program PLAN/governance wrapper; rigor spec, not shippable code | xpu-train, **denning** (merge candidate) |
| `8350` (ai-1 ops) | active, single-burst 2026-06-22 | **med** — real LAN Ollama node (`http://ai-1.lan:11434`); another OpenAI-compatible endpoint cc would consume; reusable node-provisioning runbook | — |
| `bring-your-own-ollama` (local-llm-bridge) | shipped reference 2026-05-09, dormant | low-med — local-first browser↔Ollama reference; trace-overlay-as-verification idea; MIT, secret-clean | mfa |
| `mfa` | stale/vendored (Dec 2023 upstream) | **none** — **MISCLUSTERED**; Auth0 quickstart samples; committed node_modules (never traverse) | bring-your-own-ollama |

### 2.2 UI-surfaces / viewers / overlays

| Repo | Maturity | cc-relevance | Overlaps |
|---|---|---|---|
| `liveView` | stale prototype, last 2026-04-11 | **med-high (as pattern)** — generic snapshot-contract + provenance local viewer; "ingest owns truth / ui owns interpretation"; strong REFERENCE for the Kinetic Console run/artifact viewer | work-visuals, discoverlay, reader |
| `workbench` | first-light MVP, stale ~3wk | **med** — operator visibility/control of the exact prompt+context before dispatch; provider-contract overlaps vllama's `/v1` facade; Kinetic Console panel seed | vllama, contextforge, ember |
| `discoverlay` | working, last 2026-06-09 | **med** — live consumer of b70tools JSONL run-dir handoff; proves tail→project→validated-spec→dumb-renderer; **overlay = suspected BSOD trigger** | reader, work-visuals, liveView |
| `RaidUI` | **active**, most mature of GAD cluster (333 commits) | med — NOT orchestration; case-study of local-Ollama bridge; one of two raid-review surfaces (merge question w/ lantern) | lantern, contracts |
| `lantern` | working, disciplined, mid-migration into Tempo | med — clean exemplar of typed-API-client + 404-tolerant safe-empty fallback + perspective-surface-in-host-process; build:cloud static prerender | **RaidUI** (merge) |
| `work-visuals` | stale/abandoned, 2 commits | low — throwaway D:\work activity dashboard | **liveView** (fold-in) |
| `reader` (IndexCard) | working, tiny+personal | **none** — pure accessibility utility; Win32 overlay technique only | discoverlay (technique only) |

### 2.3 Guild / social (the GAD product cluster)

| Repo | Maturity | cc-relevance | Overlaps |
|---|---|---|---|
| `campfire` | working/closed-out, frozen (capstone retro) | low-med — exemplar of Constitution-as-tests + "substrate is the product"; fleet-safety lesson on machine-global vs session-local under parallel agents | hearth, Guild |
| `hearth` | working then stale (production cutover) | low — guild-content; Constitution-pattern ORIGIN (HEARTH_CONSTITUTION.md) | **Guild** (DUPLICATE), campfire |
| `Guild` | working but stale, messy | low-med — holds the GoatHerder bot token (couples to gad keystone); ran the dual-PM `.signals/` async handoff (prior art) | **hearth** (DUPLICATE), campfire |
| `leopard` | **ACTIVE**, last 2026-06-16 (11 ADRs) | **med-high** — real consumer of the inference contract; `provider-contract.md` = the vllama `/v1` facade abstraction; `render()===serialize()` grounding invariant; feeds discoverlay via live-insight.jsonl | game |
| `gad-bot` | mixed/active-ish then frozen | low — WoW/build-in-public Discord bot; committed `.env` (flag for secret review); shares Discord-bot surface pattern with ember/gad | chatGPT_parser, parser-handoff |

### 2.4 Parser / data

| Repo | Maturity | cc-relevance | Overlaps |
|---|---|---|---|
| `chatGPT_parser` | largely shipped, stale | med — embodies durable-NDJSON + JSON-schema contracts + recomputable projections + EVIDENCE-token provenance; reference pattern, not code | parser-handoff, gad-bot |
| `parser-handoff` | complete single-session artifact (docs-only) | low — WoW-domain; ADDP handoff *form* already captured better in `writing`/`handoff` | chatGPT_parser, gad-bot |
| `comfy` | working then frozen (1-wk sprint 2026-05-26) | **none** — **MISCLUSTERED** ("Comfy" = Valheim modpack, NOT ComfyUI); Valheim save analyzer | comfyrp |
| `comfyrp` | finished review/handoff deliverable (3rd-party repo) | **none** — Valheim Rewind-format parser review; not Derek-authored | comfy |

### 2.5 ai-dev lineage / origin (the quarry)

| Repo | Maturity | cc-relevance | Overlaps |
|---|---|---|---|
| `ai-systems-research` | active precursor, largely superseded | **med-high (lineage)** — literal source of the ADDP session-triad + SESSION_LOG.jsonl + ARTIFACT_SCHEMA + proto-`constellation.yaml`; inherit MATURE copies from writing + gad | writing, manifest, gad, ai-dev-system, archStandards |
| `ai-dev-system` | shipped v0.1, frozen | med (historical) — documented genesis of the artifact-envelope + run-metadata model; mine ORIGIN rationale | ai-systems-research, archStandards, writing, planning |
| `archStandards` | active-then-frozen origin doc set | med (conceptual) — NAMES "Dual-Loop Orchestration" + "artifacts as context waypoints"; carries the "don't custom-build infra unless it's the product" lesson | ai-systems-research, ai-dev-system, steppe-strategy/launch, writing |
| `start/` (incubator) | stale graveyard/quarry | med (as quarry) — scarecrow→Kinetic Console UI; ashley/planner→ember+planning; contextforge→workbench; **NEVER traverse `start/data`** (live PG) | planning, ember, workbench, portmap, scarecrow, ashley, contextforge |

### 2.6 Runtime data / scaffold

| Repo | Maturity | cc-relevance | Overlaps |
|---|---|---|---|
| `planning-runtime` | working but stale (runs Apr 15, dormant ~2.5mo) | **med** — concrete EXAMPLE of Farmer's run contract; 3 real completed run dirs + 7 sample-plan fixtures (incl. `impossible-task`) = ready-made v0-slice test fixtures; treat as `planning`'s runtime data | **planning** (it IS planning's run store) |

### 2.7 Business / marketing (keep OUT of the technical constellation)

| Repo | Maturity | cc-relevance | Overlaps |
|---|---|---|---|
| `www` | **active/production** (steppeintegrations.com); CI+ADRs | low — published-output surface where cc's articles land; not an organ | website, writing, steppe-launch |
| `steppe-strategy` | scaffold/planned, stale | **none** — business strategy doc; context for *why* cc exists | steppe-launch, archStandards |
| `steppe-launch` | stale draft kit (1 day) | **none** — launch-week collateral | steppe-strategy, www, writing |
| `GasperCards` (threat-trace) | early public-release snapshot, stale | med — **MISNAMED** (it's threat-trace); CIAM/security multi-agent composed-signals demo; **IP CAUTION** (Auth0/Cloudflare-shaped fixtures — verify synthetic before reuse) | — |
| `website` | abandoned design dump (Stitch comps) | **none** — superseded by `www` | www |

### 2.8 Experiments / dev-tools / vendored / hardware-ops

| Repo | Maturity | cc-relevance | Overlaps |
|---|---|---|---|
| `game` (Lumberjacks) | working-ish, stale | low — **MISCLUSTERED** (game platform, not guild); portmap-integrated; agent-graded-acceptance rubric idea | leopard |
| `herm` (Hermes) | abandoned scratch, no git | low — ConPTY/PTY bridge spike (Windows interactive-CLI driving) + an unrelated AI short story; **junk/scratch** | — |
| `host-cleanup` | complete one-shot runbook | low — node-prep checklist reference (Defender exclusions, adapter cleanup) | planning, scarecrow |
| `ConsoleApp1` | abandoned scratch (Aug 2025) | low — minimal-API JSON-webhook-ingest skeleton; Auth0 EventPayload (CIAM) | start |
| `event-viewer` | stale fork (~1yr) | low — SignalR live-event-stream UI reference | **azure-event-grid-viewer** (near-duplicate) |
| `azure-event-grid-viewer` | stale fork (~1yr) | low — same as event-viewer | **event-viewer** (near-duplicate) |
| `platform-tools` | vendored Android SDK binaries | **none** — not a repo; bundled sqlite3.exe only incidental | — |
| `tools` | single vendored `grpcurl.exe` | low — manual gRPC probe for contextforge/MAF surfaces; nothing to inherit | start/contextforge |
| `Antigravity` | installed 3rd-party IDE (~300MB) | **none** — competing agentic IDE; **NEVER traverse**; exclude entirely | — |
| `stars-app` | stale/abandoned (~5mo), junk artifacts | **none** — RN/Expo/Supabase personal app; committed `.env`, stray `nul`/.docx junk | — |

### 2.9 Empties / placeholders / junk (EXCLUDE from the map)

| Repo | State | Action |
|---|---|---|
| `lit` | empty dir (2026-06-19) | exclude; reserved light-name |
| `inf` | empty dir (2026-06-19); likely became `infexp` | exclude |
| `opencode` | empty dir (2026-06-04); reserved for an OpenCode trial | exclude |
| `charter` | empty dir; role already filled by `handoff` + cfc `CHARTER.md` | safe to delete |
| `lootbox` | one 0-byte `Lootbox-design.md` | safe to delete; idea seed |
| `coworkhardware` | empty dir | delete (Cowork *workflow* lives in b70tools) |
| `CoworkHardware (1)` | empty dir (Windows duplicate-copy artifact) | delete |

---

## 3. Overlap / Merge Map (the money section)

Concrete rulings on what collapses, what the converged thing should be, and what to archive.

### 3.1 GAD guild cluster — collapse the duplicated site; let the rest converge onto Tempo

- **`hearth` + `Guild` → ONE repo (web app + bot).** These are the same Next.js + Postgres ("Hearth") guild-memory site forked into two dirs. `Guild` (uppercase) bundles the GoatHerder Discord bot (`bot/` with `/ask`, `/stats`, `/compare`) and holds the shared bot token (`D:\work\Guild\.env.local`, the `token_file` the gad hub references). `hearth` explicitly pushes the bot to a *separate* repo. **Converged thing:** one repo = site + `bot/`, single Prisma schema (gad-bot copies hearth's schema today). **Casing trap to fix:** constellation.yaml's `hearth` entry sets `local_path: D:\work\guild` and bundles the bot — i.e. the canonical map already treats the `Guild` dir as "hearth". Pick one name, one dir. **Secret note:** the shared `.env.local` bot token couples this to the gad keystone — handle on convergence.
- **`gad-bot` → fold into the merged Guild/hearth repo** (or keep as the bot package within it). It already shares hearth's Postgres and Prisma schema and touches only `score_sets`/`contracts`/`collective_queries`. Flag: committed `.env` (secret review), and 13 ad-hoc `.mjs` posting scripts that are the "real workshop content."
- **`RaidUI` vs `lantern` (vs Tempo's own RaidView) → converge onto Tempo.** Both are raid-review/analysis surfaces: RaidUI = older DuckDB parser+viewer, lantern = newer player-first surface *already folded into Tempo's host process* (consuming Tempo's ViewerApi :5780, sharing `/api/pulls/{id}/replay`). The duplication is the clearest consolidation question in the cluster, and it is **already resolving toward Tempo as host.** **Ruling:** Tempo is the host; lantern is a perspective surface inside it; RaidUI's parser is the upstream truth, its standalone viewer is the legacy surface to retire. (Live contract-integrity lesson: RaidUI hardcodes its own `SCHEMA_VERSION` instead of importing `@guild/contracts` — see L16.)
- **`campfire` = the substrate** the above are read-mostly clients of (via `/v1`). Frozen/closed-out; LEAVE the WoW domain, inherit only the Constitution-as-tests and "substrate is the product" patterns.
- **`parser-handoff` → archive** (docs-only handoff for raidui's parser; the pattern is captured better in `writing`/`handoff`).

### 3.2 Viewer/visualizer overlap — `liveView` is the engine, `work-visuals` is a view

- **`work-visuals` → fold into `liveView`** as one snapshot view (commit heatmap / repo-size / extension-mix charts). liveView is the contract/provenance-driven reusable foundation ("ingest owns truth / ui owns interpretation"); work-visuals is a one-off D:\work activity dashboard whose data isn't even checked in.
- **`liveView` itself** is the strongest REFERENCE for the Kinetic Console run/artifact viewer — but it is *superseded if commandcenter builds its own console.* Do not adopt wholesale; mine the snapshot-contract + provenance discipline.
- **`discoverlay` / `reader`** overlap each other only on the *Win32 layered click-through overlay technique* (same author reusing the pattern), not domain. Keep separate; `reader` is out-of-scope personal tooling.

### 3.3 Valheim save-tooling — re-cluster out of inference-ml

- **`comfy` + `comfyrp` → one "Valheim save-tooling" bucket, re-clustered OUT of inference-ml.** Both parse Valheim Comfy-modpack saves (comfy = Kakoen `.db`, comfyrp = Rewind format; comfyrp is a 3rd-party repo Derek reviewed). Naming trap: "Comfy" reads like ComfyUI but is a game. Zero orchestration relevance. **Action:** archive as game tooling; note only the file-as-interface discipline they demonstrate.

### 3.4 Azure Event Grid forks — collapse two into one

- **`event-viewer` + `azure-event-grid-viewer` → keep ONE fork, delete the other.** Byte-identical README, same `.sln`, same `azuredeploy.json`, same upstream (`Azure-Samples/azure-event-grid-viewer`); they differ only by backup-remote name and ~1 commit. Pure duplication. (Marginal value: SignalR live-event-stream UI reference.)

### 3.5 Lineage layer — already merged-forward; read, don't absorb

- **`ai-systems-research` + `ai-dev-system` + `archStandards` → already converged into `writing` (ADDP) + `planning` + `gad/manifest`.** These are ancestors. Inherit the **mature** copies; read the ancestors only for ORIGIN rationale. `ai-systems-research`'s own `MANIFEST_LINK.md` self-deprecates to `gad/pm/manifest`.
- **`start/` incubator → treat as a quarry, inherit nothing wholesale.** Its children are prior generations of the spine: `scarecrow` (Electron VM-monitor) → Kinetic Console UI; `ashley`/`planner` → ember + planning; `contextforge` (gRPC capture mesh) → workbench; `start/farmer` & `start/portmap` are stale duplicates of the canonical repos. **NEVER traverse `start/data`** (live Postgres, bulk of ~12G).

### 3.6 Research-program / control-plane overlap

- **`infexp` → consolidate with `denning`'s research scaffolding.** Same KV-residency thesis: denning is the BUILT control plane (shim), infexp is the forward research-PROGRAM/governance wrapper (pre-registration, gated review, prediction cards). Merge infexp's rigor docs into denning's `docs/` rather than maintaining a separate not-yet-executed planning dir.

### 3.7 Runtime data — attach to its producer

- **`planning-runtime` → treat as `planning`/Farmer's runtime data**, not a standalone repo. Keep its run dirs and sample plans as **reference fixtures** for the v0 slice (esp. `impossible-task`, `surprise-me`); do not carry the dir into commandcenter as an organ.

### 3.8 Business-ops — split off entirely

- **`steppe-strategy` + `steppe-launch` + archStandards positioning docs + (marketing parts of) `writing` → one "business-ops" workspace, kept OUT of the technical constellation.** `www` stays as the standalone production marketing site (downstream of writing's drafts). `website` is superseded by `www` — **archive/delete.**

### 3.9 Archive / exclude outright

`website`, `Antigravity`, `platform-tools`, `mfa`, `stars-app`, `herm`, `ConsoleApp1`, all empties (`lit`, `inf`, `opencode`, `charter`, `lootbox`, `coworkhardware`, `CoworkHardware (1)`). Re-cluster (don't delete) the misclustered: `game` → game/experiment, `comfy`/`comfyrp` → Valheim tooling, `GasperCards`/threat-trace → security/portfolio, `mfa` → identity/CIAM samples.

---

## 4. House-Style Patterns

> These are conventions Derek **independently re-converged on** across the 13 deeply-profiled repos. Where a pattern appears in 3+ otherwise-unrelated repos it is a ratified house rule. Each gives **where it appears**, **the rule**, and **the recommendation for commandcenter.**

### 4.1 File-as-API with single-writer ownership
**Where:** claude-fleet-control (`docs/contracts.md` ownership table), planning/Farmer (immutable run dir), ember (`.ember/PLAN.md`), writing (`SESSION_LOG.jsonl`), portmap (`registry.json`), vllama (`runs/slots-state.json`), b70tools (run-dir + `manifest.json`).
**Rule:** Components hand off through **files on disk, not in-process calls or chat history.** Every artifact has **exactly one writer**; everyone else reads. Writes are **atomic** (write-`.tmp`-then-rename; readers ignore `*.tmp`). The filesystem *is* truth; in-memory state is a projection.
**For commandcenter:** Adopt cfc's ownership table verbatim as the v1 contract surface and generalize it to the fleet. Every cross-machine handoff is a file with one declared writer — the only pattern that survives a crash, reboot, and partition, and the substrate the cross-host transport *moves* but never *interprets*.

### 4.2 Immutable run directory + three-file anti-drift invariant + rehydration guarantee
**Where:** planning/Farmer (`runs/{run_id}/` immutable; `events.jsonl`/`state.json`/`result.json` must agree — `BugRegression_*` tests, ADR-003), cfc (rehydration via `input_hash`/`prompt_hash`), b70tools (one run dir, JSONL + exit-code verdict).
**Rule:** One immutable directory per run. Multiple views of one run **must not drift** (pinned by regression tests). **A run is valid only if fully reconstructable from disk alone.** Inputs are content-hashed so replay is provable.
**For commandcenter:** Inherit the immutable-run-dir + 3-file model as the run-of-record format; enforce rehydration with a "reconstruct from disk" test, not a promise. This is the auditability spine.

### 4.3 ADR discipline (Nygard, numbered, load-bearing)
**Where:** ember (19 ADRs + index), planning (11), vllama (7), manifest (11), denning (`docs/`).
**Rule:** Every load-bearing decision is a numbered Nygard ADR; CLAUDE.md/README cross-link inline. ADRs are operationally live — gotchas point back to the ADR explaining *why* the foot-gun exists.
**For commandcenter:** Start `docs/adr/` on day one (zero-padded `0001-…` + README index). The first ADR records *which orchestrator is the run-of-record and why.* Treat un-ADR'd drift as a defect.

### 4.4 CLAUDE.md as session-handoff contract
**Where:** planning/CLAUDE.md (gold standard); writing's `meta/AGENTS.md` + `meta/HANDOFF.md` + `START_HERE.md`.
**Rule:** Repo-root `CLAUDE.md` lets the next agent resume cold, with fixed grammar: (1) one-paragraph what-this-is, (2) phase state + PR refs, (3) ports table, (4) **gotchas (each → ADR)**, (5) **do-not-touch list** (others' worktrees, pushed branches, parallel implementations), (6) build/test/run, (7) cold-read order, (8) collaboration notes. The commit log, not the plan, is authoritative on disagreement.
**For commandcenter:** Author this template before writing code. The do-not-touch list + gotchas-link-to-ADR are what keep a *fleet* of agents off each other's worktrees.

### 4.5 Charter-governed design
**Where:** cfc/CHARTER.md (explicit constitution), gad/charter, handoff (de-facto narrative), planning/README + writing/WRITING_CONTRACT.md (domain charters).
**Rule:** One CHARTER states Mission / Objectives / **Operating Principles** / **Rejected Patterns** / Success+Losing criteria; "decisions that can't trace to the charter don't belong." Operating Principles = *evidence over vibes; truth-layer separation; delete hacks rather than normalize them; extend proven patterns before new frameworks; plan parallel/cheap, build selective/expensive; file-as-API; thin conductor; cognitive ownership over throughput; architectural judgment is the deliverable.* Rejected Patterns = *framework-adoption-as-procrastination, UI-first design, mixed-ownership state, direct canonical mutation from autonomous workers.*
**For commandcenter:** Adopt cfc's CHARTER verbatim; update the claims the convergence changes (it is no longer "design only"). The Rejected Patterns list is a ready-made guardrail.

### 4.6 Soft-fail-everywhere / fail-closed at boot / data-is-the-product
**Where:** ember (soft-fail; boot-recovery fails closed — ADR-0008; soft abort gate w/ countdown), planning (ADR-007 QA-as-postmortem; retry feeds prior verdict as `0-feedback.md`), denning (safing watchdog).
**Rule:** Runtime failures are **soft** (captured, retried, surfaced — never crash the loop). The one place that **fails closed** is *boot recovery*: ambiguous on-disk state after a crash → refuse, don't guess. Failures are first-class data.
**For commandcenter:** Encode both halves — soft-fail in steady state, fail-closed on boot/recovery and at the approval gate. Carry Farmer's retry-with-feedback (synthetic feedback file from prior `ReviewVerdict.Findings`).

### 4.7 Readiness ≠ liveness (serve-truth, not process-up)
**Where:** vllama (ADR-0007: `/vllama/ready[?alias=]` does a real 1-token generation; honest 503 with reason+remedy; no autoload — born from a 2026-06-17 incident), battlemage (`StatusProbe.cs`), denning (placement-aware gating), planning (Heartbeat middleware).
**Rule:** "Process up" is **never** "can serve." Readiness is proven by exercising the real path. When not ready, return an **honest error with reason + remedy** — never a silent autoload or misleading 200.
**For commandcenter:** Gate all dispatch on serve-level readiness the moment local inference enters any path. Generalize honest-failure-with-remedy to every fleet endpoint, machine-readably.

### 4.8 Contract-first / schema-lands-in-the-contract-package-first
**Where:** manifest (Pydantic `models.py` source of truth; `framework load --json` is THE consumer contract — ADR-011), planning (typed `RunRequest`/`TaskPacket`/`Manifest`/`ReviewVerdict{Accept|Retry|Reject}`/`RunStatus`), contracts (`@scope/contracts` buildless Zod; "schema lands in the contract package first"; strict-semver + `[CONTRACT]` signal), vllama (`contracts/projection-heartbeat.v1.schema.json`), ember (`contracts/comparer-schema`).
**Rule:** Shared types live in **one schema** (Pydantic/Zod/JSON Schema), versioned; consumers derive from it. The schema changes **first**, then consumers; semver + `[CONTRACT]` signal. JSON is **snake_case**. Verdict enums are closed sets.
**For commandcenter:** Stand up a dedicated versioned contract package *before* any consumer. Heed the documented integrity failure: **a "source of truth" nobody imports is a lie** — enforce it by importing it, with a CI check that fails if a consumer hardcodes a divergent `SCHEMA_VERSION`.

### 4.9 The canonical fleet map (`constellation.yaml` + `load --json`)
**Where:** gad (canonical `constellation.yaml`, `schema_version: 1`), manifest (Pydantic schema + `framework load --json`), handoff (component glossary).
**Rule:** One declarative registry (name/role/github/local_path/depends_on/surfaces/lifecycle/contracts), machine-readable, with `schema_version`. Prose summaries exist but the YAML is the checkable projection. Consumers read it through a stable `--json` seam.
**For commandcenter:** **Read** `constellation.yaml` via `framework load --json` — do not re-declare the fleet. Derive ember's hard-coded repo-allowlist and the scattered `D:\work\…` paths *from* the manifest. This kills the biggest portability tax (hard-coded Windows paths).

### 4.10 JSON `--json` / exit-code-as-API
**Where:** manifest (`--json`, exit 0/1), gad (`constellation-glance.py --json`, `board-sync-check.py --json`), portmap (10 commands, documented exit codes, file-as-state), b70tools (`verdict --json` + exit-code file), denning (`verdict --json` consumer).
**Rule:** CLIs are first-class integration surfaces: every consumed tool offers `--json` + documented branchable exit codes, state in a flat JSON file. No bespoke RPC where a `--json` CLI suffices.
**For commandcenter:** Expose orchestration primitives as `--json` CLIs with documented exit codes — keeps the thin conductor composable across a heterogeneous fleet.

### 4.11 Provenance tagging + SHA-256 ledgers
**Where:** portmap (`source: manual | scan:<type> | agent:<name>` via `PORTMAP_AGENT_NAME`; non-destructive merge), vllama (`substrate/PROVENANCE.md` SHA256 ledger), cfc (`input_hash`/`prompt_hash`), denning (reuse-provenance lifetime classes), b70tools (PCI-BDF/UUID GPU identity; never persist `vk:N`).
**Rule:** Every record carries who/what produced it; every derived artifact carries a content hash. Merges are non-destructive. Identity binds to **stable physical identifiers**, never ephemeral handles.
**For commandcenter:** Stamp every fleet artifact with agent provenance (`agent:<name>`, host, run_id) + content hash. Bind hosts/GPUs/ports to BDF/UUID/MAC.

### 4.12 Zero-dependency guardrails for the safety-critical core
**Where:** portmap (stdlib only), vllama (zero NuGet, single self-contained exe), b70tools (zero runtime deps, ~490KB exe), denning (`safing_watchdog.py` + `preflight.py` — stdlib + ctypes), battlemage (`LlamaServerController.cs` dependency-free).
**Rule:** Pieces that **must not fail** (port allocator, server lifecycle, don't-brick-the-box supervisor, inference facade) have **zero external deps** — no supply-chain/version-resolution failure surface. denning binds on **commit charge, not free RAM**; serving on device 0 hard-hangs the rig — NEVER-bypass guards.
**For commandcenter:** Keep the conductor core and any unattended-fleet safety supervisor zero-dep. Inherit denning's watchdog/preflight + vllama's RAM-preflight + b70tools verdict gate as the non-bypassable safety floor. Push frameworks to the edges (§4.13).

### 4.13 Blast-radius isolation of the AI-SDK / swappable adapters
**Where:** planning (`Farmer.Agents` is the *only* assembly importing MAF — ADR-005; ADR-001 externalized runtime), ember (`IChatClient` underneath MAF, one per model role), denning (`EngineAdapter` Protocol), cfc (robocopy transport as swappable adapter), manifest (Pydantic-AI confined to discover).
**Rule:** The volatile commodity dependency (LLM SDK, transport, inference engine) is confined behind **one adapter seam / one importing assembly.** Provider choice is an ADR, not scattered.
**For commandcenter:** Keep exactly one assembly importing the AI SDK and one `ITransport`/`IEngineAdapter` seam — start the slice on hosted Claude, re-point the judge/retro to local inference later without a rewrite.

### 4.14 Full OpenTelemetry tracing with GenAI conventions + middleware pipeline
**Where:** planning (OTel→Jaeger; per-stage middleware `Telemetry → Logging → Eventing → CostTracking → Heartbeat`, order pinned, ADR-004), ember (OTel 1.15.3 GenAI conventions, ADR-0007), denning (OTel), shared Jaeger sink.
**Rule:** Every run is traced per-stage with **GenAI semantic conventions**; fixed-order middleware wraps each stage (reordering is a documented foot-gun). Cost-per-run is first-class.
**For commandcenter:** Reuse Farmer's middleware order + OTel/GenAI conventions; emit to shared Jaeger. Add CostTracking from the start (it was *accidentally dropped and restored* in Farmer PR #12 — proof it's easy to lose and load-bearing). Standardize on the NATS subject taxonomy `farmer.events.run.{runId}.{stage}.{status}`.

### 4.15 The "sonnet" task-chunk unit + plan-parallel/build-selective economics
**Where:** ember (planner/critic → one plan → one build), cfc (N cheap planning cycles, selective expensive builds, >70% acceptance), planning (one `TaskPacket`/run, retry chains via `parent_run_id`), writing/ADDP (one purpose per artifact).
**Rule:** Work decomposes into bounded, independently-runnable, independently-reviewable chunks ("plan parallel and cheap; build selective and expensive"). Each chunk → one converged plan → at most one build → one draft PR. Acceptance rate steers; tooling-commits-exceeding-product-commits = "we are losing."
**For commandcenter:** Make the chunk (idea → one plan → one optional build → one PR) the atomic unit, with the human approval gate **decoupled** from autonomous workers (zero unapproved builds reach canonical). Track acceptance rate + tooling-vs-product commit ratio.

### 4.16 ADDP — artifact-driven session handoff (agent memory)
**Where:** writing/WRITING_CONTRACT.md + `ARTIFACT_SCHEMA.md` (triad `lesson_learned.md` + `research_bridge.md` + `session_reasoning_graph.json`; `SESSION_LOG.jsonl` append-only; `START_HERE.md`), echoed by planning/CLAUDE.md + gad/pm journals.
**Rule:** Every session leaves a durable, predictably-named artifact folder a future agent resumes from: separate **facts / inferences / open questions**; label uncertainty; "if a decision matters, record it; if a claim matters, ground it; if uncertainty matters, label it." `session_reasoning_graph.json` is a machine-readable agent trace.
**For commandcenter:** Adopt the ADDP triad + `SESSION_LOG.jsonl` verbatim as the fleet's agent-memory layer; use `session_reasoning_graph.json` as the standard machine-readable agent-trace contract. CLAUDE.md solves this at repo scope; ADDP solves it at run scope.

### 4.17 How these compose (the meta-pattern)
One architecture re-discovered in every organ: **a thin conductor over a file-based truth layer, commodity dependencies behind single adapter seams, every decision an ADR, every run replayable from disk, every failure captured as data, readiness proven by exercising the real path.** Highest-leverage adoptions in order: **(1)** file-as-API ownership + immutable-run-dir/anti-drift (§4.1–4.2) as the spine; **(2)** contract package + canonical `constellation.yaml` read (§4.8–4.9) as the vocabulary; **(3)** CHARTER + ADR + CLAUDE.md (§4.5/4.3/4.4) as governance; **(4)** OTel middleware + readiness-not-liveness + zero-dep safety floor (§4.14/4.7/4.12) as guardrails.

The one place these are **not yet reconciled** — the first ADR commandcenter must write — is the **two overlapping run models** (ember's SQLite-session/soft-gate/draft-PR vs Farmer's NATS/immutable-run-dir/typed-verdict). Both follow the house style; they instantiate it differently. **Recommended ruling:** ember owns intake+execution primitive, Farmer owns the run contract/event bus/observability. Without this ruling the contract forks on day one — exactly the documented `contracts`-package integrity failure where a declared source-of-truth was never imported.

---

## 5. Lessons Ledger + Design Principles

> Mined from ADR indexes, retrospectives, safety runbooks, and the 2026-06-16 portfolio panel review. Each lesson: **failure → fix → rule**, with source. This is the *negative-knowledge* layer on top of the Wave 1 baseline.

### 5.A Lessons Ledger

#### 5.A.1 Safety-critical — "the rig has a loss-of-vehicle history" (denning / b70tools / vllama)

**L1 — Serving on the display card (device 0) hard-hangs the rig.** *Failure:* inference on the GPU driving the Windows desktop → 20-min hang, once threatened a non-POST/BIOS-reflash recovery. *Fix:* device 0 hard-refused everywhere; compute card kept headless; display routing in pre-flight. *Rule:* **Never schedule fleet inference onto the display adapter.** Exclude device 0 by policy. *Source:* `denning/docs/operational-safety-runbook.md`, `denning/README.md`.

**L2 — The host wall is *commit charge*, not free RAM.** *Failure:* allocations failed with ~13.5 GB physical free — commit charge had hit 92% (83.1/89.9 GB). *Fix:* gates watch `host.commit.available_bytes`; denning rates this assumption **C** (strong enough to lean on). *Rule:* **Gate on commit charge.** *Source:* `denning/docs/assumptions.md`, `b70tools` README, `vllama` ADR-0006.

**L3 — VidMm will involuntarily evict a model that *fits* (the demotion cliff).** *Failure:* a co-tenant pushing the GPU past its ~13 GB budget → Windows evicts a max-priority serving model; decode collapses 5× as ~5 GB spills to PCIe. D3D12 priority does not hold. *Fix:* admission control on the **live** budget (`QueryVideoMemoryInfo.Budget`); the budget oracle predicted the cliff 5/5. *Rule:* **Treat the OS memory manager as an adversary.** Admit against the live budget; never assume a fitting model stays resident. *Source:* `denning/README.md`, `denning/docs/denning-as-shim.md`.

**L4 — The load-test BSOD (0xD1 mode-set-under-load) — fixed by driver, *verified under exact crash conditions*.** *Failure:* reproducible `0xD1` in `igfxnd` from mode-set-under-load (`Win+Shift+S` then `Ctrl+Alt+Del`) during dual-card inference. *Fix:* Intel driver `8801 → 8826`; declared fixed only after re-running the *exact* crash config and firing both triggers (held; 3-5 ms stall). *Rule:* **"Stopped doing the thing that crashed" ≠ "it no longer crashes."** Confirm under the hardest repro. *Source:* `b70tools/docs/retrospective-bsod-fix-and-sycl-unlock-2026-06-18.md`.

**L5 — The shared-memory cascade walks toward non-POST; arm the abort *before* you test the thruster.** *Failure:* a 70B `-fit off -ts 1,1` spilled ~6 GB into host memory; prior overnight runs produced **empty result files** (silent failures). *Fix:* a pure-observer **safing watchdog** (`ops/safing_watchdog.py`, stdlib + ctypes) that timestamps last-progress and kills the child on commit/RAM/`non_local` spill/TDR/telemetry-staleness; rehearsed all six modes via `--simulate` before the real run. *Rule:* **No unattended run without an external watchdog that makes time-of-death knowable and fails the box safe.** *Source:* `denning/docs/operational-safety-runbook.md`.

**L6 — The overlay is a suspected BSOD trigger under GPU load.** *Failure:* discoverlay / RTSS-class overlays suspected BSOD triggers under load. *Fix:* overlay **off by default**; RTSS ruled out as cause of the 2026-06-17 vllama 503 only after a clean post-warmup verdict. *Rule:* **Overlays are guilty until proven innocent under load.** *Source:* baseline §2.4; `vllama` ADR-0007; `b70tools` README.

#### 5.A.2 Readiness, observability, anti-drift (vllama / planning / ember)

**L7 — "Process up" ≠ "can serve" (the serve-readiness incident).** *Failure:* 2026-06-17 ~06:23 a two-judge Reflect run silently degraded to one. A 30B loaded clean, warmed healthy, idle — but every request 503'd at the facade routing layer (deterministic alias↔slot naming-namespace mismatch); pre-flight tested residency *by model name*, which matched → declared "up", fired work into a 503. *Fix:* `GET /vllama/ready[?alias=]` resolves the alias as the chat proxy does, then issues a real 1-token generation; alias/slot naming enforced on both load and route sides. *Rule:* **Readiness means "can serve," proven by a real generation.** Two truth namespaces must never silently disagree. *Source:* `vllama/docs/adr/0007-…`.

**L8 — A degraded result you can't tell is degraded is worse than a loud failure.** *Failure:* Reflect lost to a hand survey twice: (1) one judge 503'd and it *silently* dropped to a one-perspective recap with a buried footnote; (2) evidence was assembled **commit-led**, missing the in-flight majority (20/12/27 uncommitted files) — "commit-age lies; the working tree is where the night's work is." *Fix:* judges retry → fail over once to the sibling card → **degrade loudly** with a banner (which says a cross-failover is *not* an independent second opinion); evidence became **glance-first** (`git status` WIP + lifecycle + drift primary). *Rule:* **Degradation must shout, never whisper; silence is not synthesis.** *Source:* `ember/docs/adr/0018-…` (EXP-0002).

**L9 — Three files describing one run *will* drift the moment you look at a failure.** *Failure:* first real Farmer failure — `state.json` said `phase: "Delivering"` while `result.json` said `Failed`, same run. *Fix:* anti-drift invariant — `events.jsonl`/`state.json`/`result.json` agree on final phase + stages-completed for *every* run; pinned by tests "that can never be disabled"; middleware sets `Failed` *before* the snapshot. *Rule:* **If you claim the filesystem is source-of-truth, pin it with a test that fails on drift — and verify against the failure path.** *Source:* `planning/docs/adr/adr-003-…`.

**L10 — Per-stage traces beat one long-lived session span.** *Failure:* one long span (or console-only OTel on the wrong OTLP port) gave no queryable real-time signal. *Fix:* per-stage spans under one traceId (100+ per `/trigger`); the consumer-side trace-context gotcha (fire-and-forget handlers lose `Activity.Current`) was found *before* committing — `msg.Headers?.GetActivityContext() ?? default` as consumer parent. *Rule:* **Instrument per stage, propagate context across async/IPC explicitly, and prove traces land before relying on them.** *Source:* `ember` ADR-0007, `planning` ADR-010.

#### 5.A.3 Run lifecycle, recovery, the approval gate (ember / planning / cfc)

**L11 — Non-idempotent interrupted work must fail closed, never resume.** *Failure:* a restart kills in-memory drivers of `PLANNING` and `BUILDING` (a half-populated worktree), but the DB row persists. *Fix:* `RecoveryService` marks interrupted `PLANNING`/`BUILDING` `FAILED` (never resumes), removes the orphaned worktree (branch/commits survive), runs *first, synchronously*. Only `AWAITING_GATE` (persisted deadline) is resumable. *Rule:* **Resume only checkpointable state; fail everything else closed.** *Source:* `ember` ADR-0008, ADR-0005.

**L12 — Double-claim protection must be *implemented*, not just designed.** *Failure:* cfc designed single-writer ownership but the double-claim protection was **designed and never implemented**; Farmer's `InboxWatcher` processes sequentially and defers locking to the multi-VM future. *Rule:* **A file hopper needs atomic move conventions (`new → ready → running`) before any fan-out;** "single-writer on paper" is not a claim protocol — the seam where "behaves as one machine" is proven. *Source:* `cfc` ADR-001, `planning` ADR-002, baseline §6.

**L13 — A converged plan that was *forced forward* must never read as *endorsed*.** *Failure:* a critic loop terminates `Approved`/`RoundCap`/`Stalled`; a fixed round count is arbitrary, bland consensus is the symmetric-loop failure mode. *Fix:* asymmetric roles (Claude authors, GPT red-teams with a structured verdict); a `gate_reason` records which path ended the loop; a soft gate freezes a snapshot + persisted countdown. *Rule:* **Distinguish "endorsed" from "ran out of rounds"** — surface the termination reason. *Source:* `ember` ADR-0005.

**L14 — Throttle by design; in-memory queues lose entries on restart (covered by recovery).** *Failure:* several gates elapsing together could launch multiple heavy builds racing git state. *Fix:* one **global FIFO build queue** (exactly one build at a time across all repos); the queue owns the in-flight `CancellationTokenSource`; an aborted-while-queued session is skipped on dequeue; restart loss covered by boot recovery. *Rule:* **Predictability over throughput for a single-operator tool; every in-memory coordination structure needs a restart recovery story.** *Source:* `ember` ADR-0006.

**L15 — Worker variance is information; the VM/worktree *is* the trust boundary.** *Finding:* a locked-down worker suppresses signal; full `--dangerously-skip-permissions` produced varied-but-informative output (one built a whole web server). *Fix:* workers run dangerous-mode **inside isolation** (VM or per-run worktree); the host trusts nothing until Collect copies into the immutable run dir; output classified (`file|directory|archive|binary|report`); the builder gets **no push/PR creds** → **draft PR only**. *Rule:* **Let workers be autonomous, make the isolation boundary real, let no unapproved build reach canonical.** *Source:* `planning` ADR-008, `ember` ADR-0004, cfc CHARTER.

#### 5.A.4 Integration / contract integrity (contracts / cfc / manifest)

**L16 — A documented source-of-truth that nobody *imports* is a lie waiting to happen.** *Failure:* `@guild/contracts` claimed single source of truth, but neither consumer imported it — `RaidUI` hardcoded its own `SCHEMA_VERSION`. *Rule:* **A shared contract must be enforced (imported/runtime-validated), not merely documented** — exactly the day-one risk of two unreconciled orchestrators. *Source:* baseline §2.8, §6.

**L17 — Misleading docs are worse than no docs ("active traps").** *Failure:* cold-clone found in-tree docs that actively mislead: `lantern/CONTRACTS.md` describes a deleted pre-pivot architecture; `Guild`'s README forward-references seven nonexistent docs; a `leopard` ADR-0004 cites a `setTrend()` method never built. *Rule:* **Doc-drift is a load-bearing defect** — tombstone the old doc in the same change. *Source:* `writing/portfolio-assessment-2026-06-16/PANEL-REVIEW.md`.

**L18 — Hidden-not-fixed is the purest "scaffolding cosplaying as product."** *Failure:* `gad-bot/hide-commands.mjs` (uncommitted) hides `/stats` and `/compare` with a header admitting they're "documented-broken; hidden for now" — concealed, not repaired, ~1 month among 11 uncommitted scaffolds. *Rule:* **Repair or tombstone — never hide.** *Source:* PANEL-REVIEW Rubric 3.

**L19 — Bus-number is provably 1; the only adversary is a machine the author configured.** *Failure:* every commit resolves to one identity; the only "second mind" is a machine Derek configured; CI-gated repos survive a sabbatical, author-dependent ones freeze. *Rule:* **Crystallize tacit judgment into mechanical enforcement** (failing build, constitution-as-tests, linter, templater) — prefer a CI gate over a documented habit. *Source:* PANEL-REVIEW Rubric 1.

#### 5.A.5 Tooling / platform gotchas (planning / b70tools / battlemage)

- **L20 — A screenshot is a lead, not a verdict.** The shared-memory-spill hypothesis (Task Manager: 15 GB shared) was disproven by a live PDH probe (22.78 GB dedicated / 0.49 GB shared); cause was the Vulkan attention kernel. *Rule: settle causal claims with a measurement that can contradict the preferred answer.* (`b70tools` BSOD retro.)
- **L21 — Telemetry has blind spots; don't run an experiment whose load-bearing signal is in one.** SYCL/Level-Zero VRAM is nearly invisible to the PDH counter (1 GB reported / 29 GB resident); `D3DKMT ADAPTERPERFDATA` is non-functional on Win10 19045; PresentMon frame-pacing was never captured. *Rule: confirm the scored signal is present before walking away.* (`denning` runbook §4, `b70tools` known-issues.)
- **L22 — Pin GPU identity by stable physical address, never `vk:N`.** Index ordinals and LUID drift across re-enumeration/TDR; bind by PCI-BDF/UUID. (`b70tools` invariant, baseline §2.4.)
- **L23 — PowerShell 5.1 landmines around native exes.** `ConvertTo-Json` serialized a 70 KB string as `{"value":…}` (fix: `JavaScriptSerializer`); `2>&1` on native exes corrupts exit-code sensing. *Rule: serialize large payloads carefully; don't trust PS object-pipeline JSON for HTTP bodies.* (`b70tools` BSOD retro.)
- **L24 — Flash attention is load-bearing, not optional, at depth.** Non-flash at 25k context timed out past 600 s; KV quantization requires flash-attn — but flash-attn *triples* decode latency at depth. *Rule: optimal kernel/quant config is context-depth dependent — long context → SYCL, short → Vulkan.* (`b70tools` retro, `denning` battery.)
- **L25 — The agent confidently imports wrong "facts" from its own memory.** It asserted "48 GB per card" and built a capacity argument on it; each B70 is 32 GB (48 = 32 dedicated + a 16 GB host-RAM window). *Rule: the human keeps the confident agent honest on load-bearing constants.* (`b70tools` BSOD retro, Operator perspective.)

### 5.B Recurring Design Principles

1. **File-as-API / filesystem-is-truth, with rehydration.** Every run reconstructable from disk alone; single-writer; atomic `.tmp`-then-rename; readers ignore `*.tmp`. Note Farmer **retired the file inbox for NATS as the transport while keeping files as the durable truth** (additive, never authoritative).
2. **Truth-layer separation — isolate the volatile blast radius.** `Farmer.Agents` is the only AI-SDK importer; ember reaches every local server via one `IChatClient`, so "which local server" has *never touched the code* across three serving-stack pivots.
3. **Fail closed / fail loud / degrade honestly.** Boot recovery fails interrupted work closed; serve-readiness refuses up front; judges degrade with a banner; honest 503 with reason+remedy, no silent autoload.
4. **Evidence over vibes; predictions before data.** denning git-commits prediction cards *before* collecting data (a refuted prediction is a success of the method); the panel scores disk-verified claims with a `revisionFromPrior` trail.
5. **Plan parallel and cheap; build selective and expensive.** A hard human-approval gate decoupled from autonomous workers; no unapproved build reaches canonical.
6. **Thin conductor, autonomous workers inside a hard isolation boundary.** The primary stays a conductor; workers run "full send" but only the VM/worktree is the trust boundary; the host trusts nothing until Collect.
7. **Admission control on the *live* budget against an adversarial OS.** `N* = min(compute_knee, memory_budget, live_budget)`; suspend before you thrash. (Named for Peter Denning's working-set model.)
8. **Make the gate math visible.** vllama's `status` reports raw total / tracked resident / effective pressure / threshold; denning logs a timestamped operator GO/NO-GO. *A safety gate should explain its own arithmetic.*
9. **Pre-flight checklist + rehearsed abort ("treat every run as a flight").** GO/NO-GO logged before every run; the watchdog rehearsed (`--simulate` all six modes) before the dangerous test; an aborted run is VOID and re-run, not a result.
10. **Extend proven patterns before adopting new frameworks; let a prototype be the cutover evidence.** The NATS cutover was justified by `prototype-nats` surfacing the trace-context gotcha *before* commitment.
11. **The artifact is the deliverable (ADDP).** Every run leaves a reconstructable triad + a `SESSION_LOG.jsonl` line; durable content lives in 50-year formats (markdown/git/plaintext ADRs), not binaries.

### 5.C Superseded / Abandoned Decisions Worth Remembering

The constellation has strong, honest supersession discipline (the panel praised ember's `0009 ↔ 0012` chain). Do not re-litigate these:

| Decision | Superseded by | Why it flipped — the lesson |
|---|---|---|
| **vLLM (Intel LLM-Scaler) under WSL2** (ember ADR-9) | **llama.cpp Vulkan on native Windows** (ember ADR-12, battlemage ADR-021) | WSL2 bridge dead on this host (two upstream kernel bugs: `dxgkrnl` mutex deadlock; NEO abort `wddm_memory_manager.cpp:914`). vLLM's reasoning **not refuted** — resumes on native Linux. *A paper decision must carry a first-contact caveat; "deferred, not dead" is a real disposition.* |
| **Ollama on Arc** (+ IPEX-LLM Ollama path) | dual `llama-server` per card | Standard Ollama silently runs on CPU on Arc; its host-RAM overhead blew the 32 GB ceiling where `llama-server` fit. (IPEX-LLM later *returned* as the long-context SYCL decode unlock — a closed door re-opened because the driver moved.) |
| **OpenAI `/v1` with in-memory run state** as Farmer's primary entry | **file-first `InboxWatcher`** (planning ADR-002) | In-memory loses every in-flight run on restart, no forensic trail. HTTP demoted to a thin secondary adapter. |
| **File-based inbox polling** as the coordination fabric | **NATS JetStream + ObjectStore** (planning ADR-010) | Forced by host relocation (no `D:\`, share unreachable) + zero real-time observability. Files stayed the durable truth; NATS is additive. |
| **`Microsoft.Agents.AI.Anthropic` (preview)** for the host retrospective | **`Microsoft.Agents.AI.OpenAI` 1.1.0 (stable)** (planning ADR-006) | Preview, API drift, **no documented structured-output path** (~120 lines of fragile parser avoided by `RunAsync<T>()`). Cheap to flip *because of* blast-radius isolation (ADR-005). |
| **Claude Dispatch / desktop computer-use** as the fleet control path | **pure CLI file-API workers** (cfc ADR-002) | "Not reliable enough to carry the fleet control path." Claude CLI fits: local, full-send, frozen-prompt-file-driven, deterministic artifacts, no screen state. |
| **denning as an inference engine** ("rebuild vLLM") | **denning as a thin co-residency *shim*** over an unmodified engine (denning-as-shim, 2026-06-20) | A web re-check confirmed vLLM/llm-scaler now ships replica-per-card on the B70 — that part is commodity. The novel part (KV-residency arbitration vs an adversarial *Windows desktop* OS) is the whole contribution. "Keep the control plane, rent the tensor math." |
| **The swap proxy** (single-card residency mediation) | retired on Arc; kept for the 4070 Ti rehearsal | 64 GB VRAM (a card per model) retired the contention; the proxy is preserved as the NVIDIA rehearsal record. *Retire-but-preserve over delete when a pattern still applies elsewhere.* |
| **`n_parallel=4 × 131072` KV** for a judge reading ~1.5k tokens | `n_parallel=1`, `ctx=16384` right-sized by role (vllama ADR-0007) | A latent 128k×4 KV over-allocation; lowering KV is strictly safer for the 32 GB floor. *Right-size resident footprint to the actual role, not the default.* |

**Two superseded-but-still-open items to carry forward:** (1) per-card VRAM headroom under concurrent residency is documented but **not yet gated** (vllama ADR-0007 Decision 4); (2) manifest's **F-044** dogfooding gap — "the framework has never once been run on itself" — is still open, and the closing experiment is plan-only.

---

## 6. Naming Cosmology + commandcenter Component Names

### 6.1 The cosmology at a glance

Derek's repo names are a coherent **mythos built in concentric rings around a fire on the open steppe**: night falls on the grassland, you light a fire, and from it radiate light, shelter, storytelling, and — far above — the stars you navigate by. Layered on is a second register of **predators and dens** (the wild things) and a thin seam of **classical/mythic** names for the parts that carry messages. The brand **Steppe Integrations** is the keystone — the whole cosmology *is* the steppe, and every repo is a thing you'd find on it.

Four families do almost all the work:
1. **Fire / Light** — how knowledge is generated, carried, made legible.
2. **Shelter / Hearth** — where the group's memory and presence live (folded into Fire; on the steppe, fire *is* shelter).
3. **Celestial** — navigation, the map, what you orient by.
4. **Pastoral / Wild** — husbandry (tending), dens (residency/safety), beasts (the hardware and the predators).

A fifth register is **Classical/Mythic** (Hermes the messenger) plus a deliberate **Plain-Instrument** register (`b70tools`, `xpu-train`, `portmap`, `workbench`, `manifest`) for the parts that must read as *engineering tools*, not poetry.

### 6.2 The rules the names obey

- **The name encodes the role in the fire-scene, not the tech.** `ember` = ignition/intake; `lantern`/`reader` = things you carry to see by; `hearth`/`campfire` = where memory is kept warm. You can guess a repo's job from where its name sits in the scene.
- **Brightness/heat ≈ stage of combustion.** `ember → campfire → hearth` maps onto *spark of an idea → shared/living memory → permanent chronicle.* Light = observability (the sellable thesis: "observability ≠ trust").
- **Wild/pastoral names guard the dangerous, physical layer.** Anything touching bare-metal GPUs (which literally BSOD the box) gets an animal/den name: `battlemage` (Intel's Battlemage silicon), `leopard`, `denning` (a den = safe co-residency), and the husbandry name **Farmer** (`planning`) for the thing that *tends* worker VMs. The danger is legible in the name.
- **Plain names for plain tools, by intent.** Where Derek wants a credible, sellable instrument he drops the poetry: `b70tools`, `portmap`, `xpu-train`, `workbench`, `manifest`. A deliberate register switch, not a gap.
- **Casing convention:** lowercase = infrastructure (`gad`), Title-case = surface/product, ALL-CAPS = product cluster (`GAD` = Goats After Dark).

### 6.3 Glossary (repo → meaning, by family)

**Fire / Light — generation, carriage, legibility**

| Repo | Literal | Role in the cosmology |
|---|---|---|
| `ember` | a glowing spark | **Ignition/intake** — the plan→critic→build→PR bot; the coal that starts everything. |
| `campfire` | gathering fire | **Shared, living memory substrate** — the fire people gather around; warm, not permanent. |
| `hearth` | the home fire | **The permanent chronicle** — fire brought indoors; the kept, governed record (it has a Constitution). |
| `lantern` | carried light | **A perspective surface you carry into the dark** — "what happened to me last night?"; portable observability. |
| `lit` | illuminated | (stub) reserved light-name — an illumination surface not yet built. |
| `reader` / IndexCard | a reading light | **Focused legibility** — dims everything except the line you're on; light as *attention*. |
| `discoverlay` | discover + overlay | **Live signal painted over the world** — a research HUD making load-vs-cost visible while you play. |

**Celestial — navigation and the map**

| Repo | Literal | Role |
|---|---|---|
| `constellation.yaml` / `manifest` | the star-map | **The canonical fleet map** — every repo a star, `depends_on` the lines between them. |
| `stars-app` | the stars | a celestial-named consumer surface. |
| `gad` ("Goats After Dark") | goats *after dark* | the keystone hub — pastoral animals under the night sky: the steppe at night, the whole cosmology in one phrase. |

**Pastoral / Wild — husbandry, dens, beasts (the physical & dangerous layer)**

| Repo | Literal | Role |
|---|---|---|
| `planning` / Farmer | one who tends | **Husbandry of workers** — orchestrates Claude CLI workers on VMs; tends the herd, runs the seasons, harvests results. |
| `denning` | making a den | **Safe co-residency** — KV/expert residency control so models share a den (the GPU) without eviction. |
| `battlemage` | Intel Battlemage / war-mage | **The beast itself** — the dual Arc B70 substrate; powerful, prone to violence (BSODs). |
| `leopard` | the predator | **A fast local hunter** — the reflection engine that pounces on your own raid logs locally. |
| `xpu-train` | train the XPU | **Breaking/training the beast** — on-rig LoRA fine-tuning; the only path that changes weights. |
| `lootbox` | a hoard | (stub) the spoils/collection idea. |

**Classical/Mythic + Plain-Instrument — messengers and tools**

| Repo | Literal | Role |
|---|---|---|
| `herm` / Hermes | the messenger god | **The bridge/conduit** — a ConPTY/PTY bridge (CLI↔web); carries messages across a boundary. |
| `vllama` | Vulkan + llama(.cpp) | **The serving facade** — the `/v1` local-model endpoint; reads as infra. |
| `b70tools`, `portmap`, `manifest`, `workbench`, `handoff`, `charter`, `contracts` | plain nouns | **Deliberate plain register** — telemetry, ports, schema engine, context bench, baseline narrative, constitution, shared schemas; named to read as credible engineering (some are the sellable artifacts). |
| `steppe-launch` / `steppe-strategy` | the brand | **Steppe Integrations** go-to-market — the ground the cosmology is pitched from. |

### 6.4 Recommended naming scheme for `commandcenter`

`commandcenter` is the convergence layer ("behave as one machine"). Its own name is intentionally **plain-operational** (like `workbench`/`manifest`) — the credible product brand. But its *components* should be named from the cosmology so the fleet reads as one mythos. Governing question per component: **where does this thing sit in the fire-on-the-steppe scene?**

**The naming rule (one line):** *plain-instrument name for the product and anything you'd sell or sign; a fire/light/celestial/pastoral name for each internal organ, chosen by its role in the scene. Lowercase = infrastructure, Title-case = surface/product.*

| commandcenter component | Recommended name | Why it fits | Family |
|---|---|---|---|
| Conductor / control plane (thin core) | **`steward`** (or keep "Farmer" if planning is absorbed wholesale) | A steward tends the *whole steppe*, not one herd — the right escalation from Farmer's single-herd husbandry to a fleet. | Pastoral |
| Cross-machine transport / event backplane | **`drovers`** / **`relay`** (or fold in **Hermes** if the PTY bridge moves in) | Drovers move the herd across the open plain — the network *is* the backplane. | Pastoral / Mythic |
| Operator UI (Kinetic Console) | **`watchtower`** / **`lookout`** | A high vantage from which you watch the whole fleet at night — a *fixed* vantage (vs `lantern`/`lit` which are *carried* light). | Light / Celestial |
| Run registry / fleet-map consumer | reuse **`constellation`** + **`manifest`** | Already the star-map vocabulary; the console *reads the constellation*. | Celestial |
| Idea intake (voice→Discord→plan front) | extend **`ember`** → optionally **`kindling`** | Kindling is what you feed the ember — raw captured ideas before they catch; a natural pre-ember stage for the idea-to-plan pipeline. | Fire |
| Approved/active run record (the kept truth) | **`embers`** ledger / **`ashpit`** | The immutable run dir = the kept fire record; ash is what's left after a run, permanently. | Fire |
| Safety supervisor / watchdog | **`firewatch`** / **`ranger`** | The steppe's real danger is the fire spreading (the GPU bricking); a firewatch guards against it — inherits denning's `safing_watchdog` role. | Light / Pastoral |
| Local-inference dependency (read-only) | keep **`vllama`** / **`denning`** / **`battlemage`** | Already named; commandcenter *reads* this den, doesn't rebuild it. | Wild |
| Port/identity allocation across hosts | extend **`portmap`** → **`grazing`** (network-aware) | Apportioning ports across hosts ≈ apportioning grazing so herds don't collide. | Pastoral |
| Governance / charter | reuse **`charter`** + **`contracts`** | Plain register, already the constitution layer. | Plain |

**Guardrails for new component names:**
1. **Place it in the scene first, then name it.** Generation = fire; carriage = drovers/Hermes; legibility = light; memory = hearth/campfire; navigation = celestial; husbandry/safety = pastoral; bare metal = wild/beast.
2. **Brightness ladder for fire-names:** spark (`ember`/`kindling`) → gathering (`campfire`) → kept (`hearth`/`ash`). Position signals lifecycle stage.
3. **Wild/predator names are a warning label** — reserve for anything touching GPUs or arbitrary code execution.
4. **Drop into plain register for anything you'd sell, sign, or publish** — product, schemas, the map, telemetry. Poetry stays internal.
5. **Casing:** lowercase = infra/organ; Title-case = surface/product; ALL-CAPS = product cluster.
6. **Don't reuse a live name for a new role.** `lit` and `lootbox` are claimed-but-empty stubs — build them into their implied roles or release the names; don't repurpose silently.

**README one-liner:** *"Night on the steppe: `ember` is the spark, `campfire` and `hearth` keep the memory warm, `lantern` and the `watchtower` console let you see, `constellation` is the map you steer by, the `steward` tends the herd of workers, `drovers` move them across the plain, `denning` and `battlemage` are the dens where the beasts (GPUs) sleep, and a `firewatch` makes sure nothing burns down — all of it ground that Steppe Integrations stands on."*

---

## 7. What This Changes for the Build (revisions to Wave 1)

Wave 2 mostly **confirms** the Wave 1 convergence spine. Four concrete revisions/additions:

**7.1 The convergence spine is unchanged, but its first ADR is now sharper.**
The day-one ruling stands: **reconcile ember vs Farmer run models** before any code. The lessons ledger hardens *why* — L16 (a contract nobody imports is a lie) + L9 (three files drift on first failure) + L12 (double-claim was designed, never built) mean the run-of-record contract must be **imported and enforced**, not just declared. **Recommended ruling (now with evidence):** ember owns intake + execution primitive; Farmer owns the run contract / event bus / observability. Write it as ADR-0001 with a CI check that fails on a divergent hardcoded `SCHEMA_VERSION`.

**7.2 New: `xpu-train` is the canonical cross-repo-contract exemplar — and the self-improving seam.**
Wave 1 didn't profile it. `xpu-train` carries the **first declared cross-repo contract** (`finetuned-gguf-handoff`, xpu-train → denning) and is the **training half** that makes the loop self-improving (a fine-tuned GGUF re-enters denning's existing KV-restore/admission serving path unchanged). For the build: (a) use `finetuned-gguf-handoff` as the *worked example* when designing commandcenter's contract package; (b) note that commandcenter **serves** xpu-train, it doesn't absorb it; (c) the loop closing (held-out loss 2.96→1.13, quantized + served on Vulkan in ~6s, 2026-06-22) means a self-improving dev-model is no longer hypothetical — a future commandcenter objective.

**7.3 The merge map shrinks the portability/path surface (good for the thin slice).**
Wave 1's biggest portability tax is hard-coded `D:\work\…` paths and ember's hard-coded repo-allowlist. §3 removes ~20 dirs of noise (empties, vendored, duplicates, business-ops, game tooling) from the surface that needs to be path-portable, and §4.9 says **derive all paths from `constellation.yaml` via `framework load --json`** rather than re-declaring them. Net: the thin slice should read the manifest, not the disk layout — and the manifest has far fewer real entries than the ~70 dirs suggest.

**7.4 The safety floor is non-negotiable and now has concrete inheritance targets.**
Any unattended fleet job must inherit, *before* it runs anything dangerous: denning's `ops/safing_watchdog.py` + `ops/preflight.py` (zero-dep), vllama's host-RAM/commit-charge preflight, b70tools' verdict gate, and the device-0 exclusion (L1) + commit-charge gating (L2) + live-budget admission (L3). The Wave 1 risk picture is unchanged but the mitigations are now named files to copy, not principles to re-derive. **Concretely:** the thin slice can run on **hosted Claude with zero GPU risk**; the safety floor only becomes load-bearing when local inference (vllama/denning) enters the dispatch path — at which point readiness-not-liveness gating (§4.7/L7) and the rehearsed-abort watchdog (L5) are prerequisites, not enhancements.

**7.5 Thin-slice fixtures already exist.**
`planning-runtime` contains 3 real completed Farmer run dirs (full artifact sets) + 7 sample-plan inputs including `impossible-task` ("solve the halting problem") and `surprise-me`. These are **ready-made test fixtures** for the v0 slice's run-contract + anti-drift + rehydration tests — inherit them as reference fixtures; do not carry the dir as an organ.

**Net:** Wave 1's thin slice (thin conductor over file-based truth, hosted Claude, immutable run dir, soft approval gate, draft-PR-only builder) stands. Wave 2 adds the *contract example* (`finetuned-gguf-handoff`), the *path discipline* (read the manifest), the *named safety floor* (denning's zero-dep ops), and *test fixtures* (planning-runtime). The single biggest unforced error to avoid is the one the corpus already documents three times: **declaring a source of truth that no consumer imports.**

---

*Companion document complete. Wave 1 baseline: `C:\work\commandcenter\CONSTELLATION-BASELINE-2026-06-28.md`. This file: `C:\work\commandcenter\CONSTELLATION-WAVE2-2026-06-28.md`.*
