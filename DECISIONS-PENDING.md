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
- [x] 2026-07-03 — known_good/known_bad_models.json guard coverage
      (source: DECISION-NEEDED-A2.md, flagged again by the CQRS review)
      — DONE 2026-07-11 (S2 of [SCHEDULER-STRATEGY.html](SCHEDULER-STRATEGY.html)): 12 tests
      landed in `hearth/tests/toolsurface/test_knowledge.py` (`2ad68e0`), mutation-checked
      (inverted guard → 4 tests fail). Note: real classification logic lives in
      `tools/workflow/project_capacity.py`; the A2 watermark gap itself stays deferred
      (option 1 "leave unguarded" still in effect, now with the contract pinned by tests).
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
      — 2026-07-11 PARTIAL: harvest DONE (23/23 → `campaign/harvest/`, `0fff65f`);
      assay-acceptance branch LANDED (`9e01612`, null-action regression pinned). JS5 CANNOT
      be curated: the pour produced empty laps on both builders (the null-action exploit,
      live) — remaining sub-decision is H1b in SCHEDULER-STRATEGY.html (re-pour vs direct
      build). Synthesis = S7, in flight.
- [x] 2026-07-09 — Tailscale in the machine loop (browser re-auth blocked the conductor
      lane). DECIDED + SHIPPED same day: machine lanes moved to mshome/LAN, Tailscale =
      humans + Funnel only; conductor stays a Hyper-V VM (WSL/AM4 relocation rejected)
      — [ADR-0014](docs/adr/0014-machine-lanes-off-the-tailnet.md), verified live.
- [x] 2026-07-09 — Derek: Tailscale admin hygiene for the remaining HUMAN lanes —
      disable key expiry on server nodes (OMEN, AM4), flip SSH ACL `check`→`accept`
      (admin console; source: ADR-0014 consequences). DONE 2026-07-09 (Derek, separate
      session).
- [x] 2026-07-09 — Derek: confirm nothing human-facing still rides cc-conductor's
      tailnet identity (dashboard :8080 from phone?) → then `tailscale logout` on the
      conductor (source: ADR-0014 consequences). DONE 2026-07-09 (Derek, separate
      session): conductor is off the tailnet — confirmed independently the same evening
      via `tailscale status` from OMEN (only omen/am4/i5/pixel-8a remain); cc-builder-4's
      rogue tailnet node also removed. inventory.toml updated to match.
- [x] 2026-07-09 — BUILD: fold patrol/watchdog/drain/perception into the gateway as
      internal timers; shrink Task Scheduler to two headless boot entries; deregister
      the superseded tasks ([ADR-0015](docs/adr/0015-ops-loops-fold-into-the-gateway.md)).
      DONE 2026-07-09 (slice 1): `hearth/kernel/timers.py` + 291 tests green; gateway
      arms patrol/watchdog/drain; live cutover verified (all three ticked `exit 0` with
      ledger ids) and the 3 superseded tasks deregistered (XML backed up to
      `hearth/var/retired-tasks-adr0015/`). Perception + tracing proxy stay tasks (homing
      decision: out-of-repo / persistent service). Boot entries DONE 2026-07-09:
      `HearthGatewayBoot` + `OllamaBoot` re-registered `LogonType=S4U` ("run whether
      user is logged on or not", no stored password — needed UAC only, not the
      password). ADR-0015 end state complete; proof = next OMEN reboot.
- [x] 2026-07-09 — PINNED (decide after use-case discovery): repo-aware `local_generate`
      — gateway-side context assembly (a `paths`/glob param packing scope-guarded files
      into the prompt) was proposed for the "point a local model at a repo" bootstrap
      gap. Before building: collect the OTHER use cases (feeding knowledge/, repo-grounded
      experiment briefs, …) and decide extend-vs-enhance on those findings.
      — DECIDED + BUILT 2026-07-16: un-pinned by the offload-first strategy
      ([HEARTH-OFFLOAD-STRATEGY.html](HEARTH-OFFLOAD-STRATEGY.html) WP1.1 — the use-case
      evidence accumulated). `files=` param landed on branch `feat/repo-aware-intake`,
      drafted by the door's own gcp-gemini-pro rung and live-proven post-restart
      (receipt br-20260716-070602-756035bd; 445 tests green).
- [ ] 2026-07-16 — PINNED (decide after sentinel data accrues): full per-request
      interception of direct Ollama traffic — a ledgering proxy owning :11434 (Ollama
      moves to :11435) would capture every bypass with content digests, but puts a
      moving piece in front of a production serving lane the fleet uses directly.
      Slice 0 shipped instead: the ollama-sentinel gateway timer (fleet/ollama_sentinel.py,
      120s netstat sampling, hearth/var/sentinel/ollama-direct.ndjson) — sampling can
      miss short calls between ticks. Revisit when the ndjson shows how much direct
      traffic exists and from whom. Also open: same sentinel pattern for AM4 oxen :8090
      (facade is our code — could ledger natively) and Vertex-direct (only GCP audit
      logs can see it).
- [ ] 2026-07-09 — PINNED (decide after use-case discovery): fleet builds targeting a
      NON-conductor repo (trigger: Valheim fieldlab mod wanted mechnet help; today
      CCMETA has no repo concept — [task_lane.py](hearth/toolsurface/task_lane.py) is
      hardwired to the conductor repo). Candidate: optional CCMETA `repo` field
      (conductor-side change, coordinate — concurrently-owned code). Interim: the
      comfy_gateway (:8720, HEARTH_SCOPE=C:\work\comfy) covers the interactive slice.
      Gather concrete use cases before an implementation decision.
- [ ] 2026-07-18 — Set `.mcp.json` hearth server `"timeout": 600000` so deliberate long moe
      calls survive the client idle cap (source: [SESSION-RETRO-2026-07-18.md](SESSION-RETRO-2026-07-18.md))
- [ ] 2026-07-18 — Review/apply the tracker sync recommendations (items 5 superseded, 8 done,
      13 done) (source: [docs/DECISIONS-PENDING-SYNC-2026-07-18.md](docs/DECISIONS-PENDING-SYNC-2026-07-18.md))
- [ ] 2026-07-18 — D4: edit + publish call on the O4 Windows-delta draft
      (source: [docs/drafts/o4-windows-delta-draft.md](docs/drafts/o4-windows-delta-draft.md))
- [ ] 2026-07-18 — Optional: remount `/mnt/win` ro (`sudo mount -o remount,ro /mnt/win`) —
      resident serving is ro-mmap safe (source: [SESSION-RETRO-2026-07-18.md](SESSION-RETRO-2026-07-18.md))
- [x] 2026-07-19 — **The container-access deployment gate — RESOLVED SMALLER, same day.**
      Originally: confirm Docker subnet → firewall rule → `0.0.0.0` bind → restart. Investigation
      falsified the premise: this host runs WSL2 `networkingMode=mirrored`, so containers already
      reach the loopback bind, and the real blocker was the MCP SDK's DNS-rebinding allowlist —
      which ADR-0019's bind mode could not influence (FastMCP was constructed before
      `settings.host` was assigned), so the gate as written would have opened the LAN and still
      returned 421. Fixed in `build_server` + `_transport_security()`; verified from a container
      against a loopback-only gateway (200 on `/healthz`, 406 not 421 on `/mcp`); 609 tests green.
      — [ADR-0022](docs/adr/0022-container-access-needs-no-exposure.md). **No firewall rule and no
      bind change will be made.**
- [x] 2026-07-19 — **Restart the durable gateway — DONE 2026-07-20 00:01, verified.** Preflighted
      first with a full-provider dry-run on a spare port against the real caller registry (startup
      capability-completeness + authority coherence both passed) so a refusal could not take the
      door down. `HearthGatewayRestart` bounced it cleanly. Evidence: `/healthz` 200 from host
      **and** container (was 404); container `/mcp` **406, not 421**; bind still `127.0.0.1` only;
      **zero firewall rules for 8710**; doorcheck all four facets healthy
      (`process_listener`/`authentication`/`mcp_surface`/`backend_dependency`); authenticated
      `local_generate` through the door returned `ok:true` via `gcp-gemini`;
      `docker-open-notebook-facade` live under the `generation-proxy` profile. ADR-0019 + 0020 +
      0022 are now in force on the live door with **no network exposure created**.
- [x] 2026-07-19 — Push / land `hearth-container-access-adr-0019`: 13 commits and 2372 lines of
      security work sat on a local branch with no remote tracking branch. DONE same day ("make
      sure everything is merged and push to master"): merged `--no-ff` to master and pushed
      (`6efc7e2`), along with the stranded twin fix `claude/upbeat-swirles-e6be44` (`288873e`)
      that the branch sweep surfaced. Both local branches deleted after merge; 609 tests green.
      (source: [SESSION-RETRO-2026-07-19.md](SESSION-RETRO-2026-07-19.md) L-5)
- [ ] 2026-07-19 — Close the legacy fail-open: callers minted before ADR-0019 with no `profile`
      keep the full 47-tool surface. **Now the only open item from this arc, and confirmed live on
      the restarted door: `claude-frontier`, `dev-local`, `omen-worker-1` all report
      `legacy-unrestricted`** (the gateway names them at every startup). Decide: migrate each to a
      profile via `callerctl rotate --profile <name>`, or record an explicit accept-with-expiry
      (source: [ADR-0019](docs/adr/0019-container-access-capability-profiles.md)
      consequences, [SESSION-RETRO-2026-07-19.md](SESSION-RETRO-2026-07-19.md) L-6)
- [ ] 2026-07-19 — Low priority: fix the offload projection's legacy bucket keys — 182 of 229
      lifetime calls sit in `model:<name>`-shaped buckets with zero token counts, so
      `est_usd_saved` undercounts. Decide backfill vs alias-map vs leave-and-annotate
      (source: [SESSION-RETRO-2026-07-19.md](SESSION-RETRO-2026-07-19.md) L-7)
