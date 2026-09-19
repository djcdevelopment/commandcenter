# Capacity correction and failed local-build proof

The delegated builders produced neither hermes-capacity.patch nor
CAPACITY-REVIEW.md. Builder2 used405seconds, builder3 about396seconds; both spent
their budgets reading source, much of it clipped by the runner's observation
limit. Their rc0, generated fallback retro/result files, and inherited162-test
assay do NOT establish a useful build. No automatic merge occurred.

Conductor run: hearth-hermes-br-20260919-215719-70852cc4-5f05200a.
Unmodified result: broad-build-result.json. Local candidate commits:
e799e3f096b48f09e5ef6479ef33e06124761b95 and
072f0978c3622f469045a837d0f76a6179621ba7.
Farmer main stayed303a2f0f7c5cedf192e1f8642d7984822415d742.

## Useful code delivered separately, authored by Codex

- fleet/inventory.toml and knowledge/am4_gpu_catalog.json now agree on both AM4
  GPUs, UUIDs and PCI addresses. Authored catalog update only; no projection
  rebuild. Prior single-card rate evidence is preserved as historical.
- Native Dense metadata records128k, the one shared slot, and conservative
  per-card charges derived from observed11400/10899MiB card-wide allocations.
  Rate and coding qualification are unknown, not invented. The old default
  builder configurations were not relabelled as AM4 routes.
- hearth/scheduler/native_capacity.py validates fresh native model/context/
  resource identity. Multiple aliases never multiply that physical slot. Bad,
  stale, future, stopped and conflicting replies fail closed. HTTP readiness
  leaves gpu_placed:null; no full-GPU-placement certificate is fabricated.
- capture_resource_snapshot performs an authenticated, bounded, passive facade
  query. The advisory map has one AM4 machine; a live native route takes
  precedence over obsolete Ollama metadata but remains placement-gated. The
  per-run builder gate separately verifies the actual preset and endpoint.
- New catalog/native tests plus existing GPU scheduler tests: 36 passed. Final
  combined controller, receipts, task lane, knowledge, guards and ledger
  regression: **286 passed, 86 subtests** in 22.60 seconds. Tests are CPU/fake-engine checks unless explicitly
  described as live in CONTROLLER-REPORT.md.

## Budget and decision

Preparation21:37:44–21:55:48UTC, inside its30-minute ceiling. Live window started
21:56 with a23:26 ceiling and22:16 first-artifact gate. The local artifact gate
was missed; additional local experiments were held. Codex's direct correction
is not substituted for a passed delegated-code criterion.

A single source-packed helper retry was proposed at22:05; its bounded brief is
fleet/hermes/capacity-leaf.md. It has NOT been dispatched. Recommendation: one
small helper+tests run, roughly5–8minutes, with integration/review by Codex,
instead of repeating repository-wide exploration. Await Derek's preference.
