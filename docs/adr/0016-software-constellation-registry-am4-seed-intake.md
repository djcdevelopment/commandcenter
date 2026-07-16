# ADR-0016 — Software constellation registry + the AM4 seed intake rulings

**Status:** Accepted (2026-07-16) — registry landed same day (`registry/constellation.toml`).

## Context

Three pre-commandcenter seed repos lived only on the AM4 4TB (`/mnt/win/work/…`):
**manifest** (constellation-manifest: Pydantic framework + the typed
constellation.yaml schema, last commit 2026-06-05), **ember** (.NET idea-to-PR
pipeline with 19 ADRs, last commit 2026-06-17), and **gad** (hub whose `pm/`
workspace held the canonical constellation.yaml, last commit 2026-06-22). The
2026-06-28 constellation baseline named them the strongest convergence seeds, but
nothing had been physically baked into commandcenter. Derek cued the intake
2026-07-16.

Intake mechanics: repos pulled whole (working tree + .git) to
`C:\work\am4-harvest\` — inside HEARTH_SCOPE, so the deep reads ran as three
parallel `gcp-gemini-pro` assessments through the door with `files=` packing
(~135K tokens in on trial credits; zero frontier spend on the reads). The
apparent "dirty working trees" on AM4 (136 files in manifest) were ntfs3
mode-bit phantoms — extracted on OMEN, manifest and ember are clean at HEAD; only
gad carries real untracked content (guild attendance analysis + two Reflect
journal entries).

## Decisions

1. **Two registries, distinct layers.** commandcenter gains a SOFTWARE registry,
   `registry/constellation.toml` (organs + cross-repo contracts), deliberately
   separate from `fleet/inventory.toml` (machines) and
   `hearth/etc/backends.toml` (model rungs). Hardware topology and software
   architecture change independently; the scheduler bridges them. No
   double-truth.

2. **TOML-native, stdlib-only; the Pydantic framework retires.** The schema
   (schema_version 1) is inherited; the implementation is not. Registry parsing
   is `tomllib` + (planned loader) dataclasses with `__post_init__` validation —
   the `fleet/inventory.toml` pattern. `manifest/framework` (Pydantic, 249
   tests) becomes read-only reference; its tests die with it. The donor GAD
   manifest is preserved verbatim at `registry/seed/constellation-gad-2026-06-22.yaml`.

3. **ember is a pattern donor, not a code port.** Its execution half (worktree
   builder, FIFO queue, SQLite sessions, telemetry) is superseded by the
   conductor/HEARTH spine. Three patterns are worth rebuilding, drafted as
   slices awaiting Derek's cue (fleet-pacing directive):
   - **E1 — critic-verdict loop + soft gate** into the commander refine lane:
     strict JSON verdict `{assessment, issues:[{severity, summary, fix}]}`,
     iterate-until-no-blocking, human-vetoable gate before the inbox
     (ember ADR-0005).
   - **E2 — Reflect nightly recap** as a gateway patrol job: dual **lens-diverse**
     judges + XML-cite grounding + a comparer pass for contradictions/omissions
     (ember ADR-0014/0016/0018), feeding the belief spine. Merges ember's
     dual-judge evidence with windtunnel's independent finding that repeated
     judges are near-deterministic — diversify, don't just duplicate.
   - **E3 — overnight backlog planner** (ember ADR-0019): objective state →
     planner/critic synthesis → tiered reconciliation, auto-applying only safe,
     reversible, in-repo stubs.

4. **XML-cite grounding is adopted doctrine** for evidence-bearing lanes
   (build-request criteria, refine lane, future Reflect): force models to quote
   evidence in citable blocks *before* synthesizing. Empirical basis: gad
   EXP-0001 — 0/3 cross-repo hallucinations with cite-first vs 3/3 without.

5. **Atomic digest cards are the interaction contract** for surfacing pending
   decisions (gad decision 0002): one decision per card, 1-line header,
   independently actionable ✅/❌/💬 — never a wall of text. Applies to the
   DECISIONS-PENDING register now, and to any future Discord/front-door surface.

6. **Mirror discipline.** `C:\work\am4-harvest\` is the read-only local mirror
   (HEARTH-packable); the AM4 4TB stays the archive of record. Seeds are
   enrolled in the registry with `lifecycle = "reference"`.

## Consequences

- The registry is declarative truth from day one (tomllib parse + referential
  integrity proven at intake) even before a loader exists — the loader is the
  planned consumer slice, alongside a CI check that imports it (the corpus's #1
  documented unforced error is a source-of-truth nobody imports).
- constellation.yaml's guild-product repos (tempo, leopard, campfire, hearth-web,
  raidui, lantern, discoverlay) are deliberately NOT enrolled — commandcenter
  registers what it conducts, not everything Derek owns.
- E1–E3 are draft-ready slices, not scheduled work; release paced by Derek.
- Superseded-by-design and left behind: MAF-WORKFLOW topology, ADO/Discord
  enrollment provisioning, ember's OllamaSwapProxy and build queue, gad's ADO
  board mechanics.

## Verification

`registry/constellation.toml` parses under stdlib tomllib: 15 repos, 4 contracts,
0 dangling `depends_on`/contract references. Full intake record with the three
per-seed assessments: `docs/intake/AM4-SEED-INTAKE-2026-07-16.md`.
