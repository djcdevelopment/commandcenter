# Decisions pending — Derek's desk

One register for open decisions accumulated across retros, ADRs, and review docs.
Appended by `/retro` (Phase 2e); check off with a link to where it was decided.

- [x] 2026-07-04 — Ratify "two ledgers = two bounded contexts; one door ≠ one store"
      as an ADR-0005 amendment or new ADR (source: [docs/CQRS-ES-STANDARDIZATION.md](docs/CQRS-ES-STANDARDIZATION.md))
      — DONE 2026-07-04 ("make it so"): [ADR-0010](docs/adr/0010-two-ledgers-two-bounded-contexts.md)
- [x] 2026-07-04 — `record_event` double-write: keep + document (reviewers' lean) vs
      special-case the gateway wrapper (source: [docs/CQRS-ES-STANDARDIZATION.md](docs/CQRS-ES-STANDARDIZATION.md))
      — DONE 2026-07-04 ("make it so"): keep + document, [ADR-0011](docs/adr/0011-record-event-double-write-is-intentional.md)
- [x] 2026-07-04 — Green-light fleet briefs for CQRS plan steps 2–4 (atomic writes +
      capacity.json guard, Ledger.reindex, canonical Corpus enumerator)
      (source: [docs/CQRS-ES-STANDARDIZATION.md](docs/CQRS-ES-STANDARDIZATION.md))
      — DONE 2026-07-04, merged f1f2b8b/bd636d5/ad486d6 (bfaaf9f)
- [ ] 2026-07-03 — known_good/known_bad_models.json guard coverage
      (source: DECISION-NEEDED-A2.md, flagged again by the CQRS review)
- [x] 2026-07-05 — Canonical AM4 B70 bring-up (2 per-B70 ports). RESOLVED: AM4 is native
      Ubuntu; only the :8080 planner slot has Linux backing (`~/baseline/relaunch-qwen3-baseline.sh`),
      :8081 critic slot unbacked. Planner woken; pilot + confirmation sweep ran cross-machine.
- [x] 2026-07-05 — Fix the knowledge-guard bug (read tools refused on knowledge/ paths).
      DONE in code + tested (`80c41d6`): am4/scheduler read tools added to EXTRA_KNOWLEDGE_READERS,
      11 guard tests green. **Goes live on next gateway reload.**
- [x] 2026-07-05 — Reload the HEARTH gateway (carries BOTH the guard fix `80c41d6` AND the
      commander door tools `refine_idea`/`refine_result`); CLI + unit tests already green
      (source: [ADR-0012](docs/adr/0012-commander-intent-lane-frontier-out-of-loop.md))
      — DONE 2026-07-09: gateway restarted for the ADR-0014 lane change; 35 tools live
      (commander tools + guard fix confirmed aboard).
- [ ] 2026-07-05 — Harvest + synthesize the 24-pour idle campaign; curate/land the JS5 +
      assay-acceptance branches (source: [SESSION-RETRO-2026-07-05.md](SESSION-RETRO-2026-07-05.md))
- [x] 2026-07-09 — Tailscale in the machine loop (browser re-auth blocked the conductor
      lane). DECIDED + SHIPPED same day: machine lanes moved to mshome/LAN, Tailscale =
      humans + Funnel only; conductor stays a Hyper-V VM (WSL/AM4 relocation rejected)
      — [ADR-0014](docs/adr/0014-machine-lanes-off-the-tailnet.md), verified live.
- [ ] 2026-07-09 — Derek: Tailscale admin hygiene for the remaining HUMAN lanes —
      disable key expiry on server nodes (OMEN, AM4), flip SSH ACL `check`→`accept`
      (admin console; source: ADR-0014 consequences).
- [ ] 2026-07-09 — Derek: confirm nothing human-facing still rides cc-conductor's
      tailnet identity (dashboard :8080 from phone?) → then `tailscale logout` on the
      conductor (source: ADR-0014 consequences).
- [ ] 2026-07-09 — BUILD: fold patrol/watchdog/drain/perception into the gateway as
      internal timers; shrink Task Scheduler to two headless boot entries; deregister
      the superseded tasks ([ADR-0015](docs/adr/0015-ops-loops-fold-into-the-gateway.md)
      — decided, unbuilt).
- [ ] 2026-07-09 — PINNED (decide after use-case discovery): repo-aware `local_generate`
      — gateway-side context assembly (a `paths`/glob param packing scope-guarded files
      into the prompt) was proposed for the "point a local model at a repo" bootstrap
      gap. Before building: collect the OTHER use cases (feeding knowledge/, repo-grounded
      experiment briefs, …) and decide extend-vs-enhance on those findings.
- [ ] 2026-07-09 — PINNED (decide after use-case discovery): fleet builds targeting a
      NON-conductor repo (trigger: Valheim fieldlab mod wanted mechnet help; today
      CCMETA has no repo concept — [task_lane.py](hearth/toolsurface/task_lane.py) is
      hardwired to the conductor repo). Candidate: optional CCMETA `repo` field
      (conductor-side change, coordinate — concurrently-owned code). Interim: the
      comfy_gateway (:8720, HEARTH_SCOPE=C:\work\comfy) covers the interactive slice.
      Gather concrete use cases before an implementation decision.
