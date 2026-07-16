# registry/ — the software constellation

`constellation.toml` is commandcenter's **software** registry: the organs of the
mechnet (repos across OMEN and AM4) and the cross-repo contracts between them.

## Two-registry doctrine (ADR-0017)

| Question | Source of truth |
|---|---|
| What machines exist, how do I reach them? | `fleet/inventory.toml` |
| What model backends/rungs can generate? | `hearth/etc/backends.toml` |
| What software organs exist, what contracts join them? | `registry/constellation.toml` |

Kept deliberately distinct: hardware topology changes independently of software
architecture. The scheduler is the bridge — it reads the constellation for
dependencies and the fleet inventory for capacity. No fact appears in two files.

## Provenance

The schema (schema_version 1: archetype/topology/conformance, typed repos with
producer_type/lifecycle/surfaces/depends_on, declared contracts) is inherited from
Derek's **constellation-manifest** work (`/mnt/win/work/manifest` on the AM4 4TB,
May–June 2026), whose canonical instance was the GAD manifest maintained in
`gad/pm`. Imported during the AM4 seed intake, 2026-07-16:

- `seed/constellation-gad-2026-06-22.yaml` — verbatim copy of the donor artifact
  (gad@6186d08, the commit that declared the first cross-repo contract,
  `finetuned-gguf-handoff`). Guild-product contents are NOT enrolled here; the
  schema and the infra organs are the harvest.
- The Pydantic framework that validated it (`manifest/framework`, 249 tests) is
  **retired to read-only reference** — commandcenter is stdlib-first, so the
  registry is TOML parsed with `tomllib` (proven at intake), and the planned
  loader is dataclasses + `__post_init__` checks, same pattern as
  `fleet/inventory.toml`.

Full intake record: `docs/intake/AM4-SEED-INTAKE-2026-07-16.md`. Ruling: ADR-0017.

## Conventions

- `conformance = "reduced"`: hand-enrolled, vocab adapted, **unverified fields
  omitted rather than guessed** (the honest-placeholder invariant).
- `lifecycle = "reference"`: read-only donor — mine it, don't build on it.
- Enrollment bar: an organ enters when commandcenter actually touches it
  (dispatches to it, packs from it, or holds a contract with it).
