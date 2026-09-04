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
- [x] 2026-07-18 — Set `.mcp.json` hearth server timeout so deliberate long moe
      calls survive the client idle cap (source: [SESSION-RETRO-2026-07-18.md](SESSION-RETRO-2026-07-18.md)).
      DONE, superseded: 600000 → 900000 (am4-moe timeout_s=600 headroom) → 1300000
      (2026-07-21, alongside the flat 1000s rung-timeout baseline) — local `.mcp.json`
      updated directly; the tracked template is [hearth/callers/mcp-config-snippet.json](hearth/callers/mcp-config-snippet.json).
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
- [x] 2026-07-19 — Close the legacy fail-open — **DONE 2026-07-20, live and verified**
      ([ADR-0023](docs/adr/0023-authority-is-granted-never-assumed.md)). Roles were **authored from
      intent, not derived from the ledger** — Derek's call: the network had been used
      opportunistically to build other things, so observed usage records how the door happened to
      be reached, not what an identity is for. v1 roster live on the door: `claude-frontier` →
      `unrestricted` (47/47, dated review), `omen-worker-1` → `builder` (21/47), `dev-local` →
      `probe` (1/47), `docker-open-notebook-facade` → `generation-proxy` (2/47). An absent profile
      now DENIES. New `callerctl assign` changes a role **without rotating the secret**, so policy
      no longer costs a credential change. 628 tests green; all four doorcheck facets healthy.
- [ ] 2026-10-20 — **Role review (ADR-0023).** With `profile`-attributed events from intentional
      usage, decide whether `unrestricted` has earned a narrower role for `claude-frontier`, and
      whether `builder` should keep `dispatch` (a worker that can queue fleet work is defensible
      but was never separately argued). If a role is too tight to flip safely, build report-only
      enforcement rather than guessing again
      (source: [ADR-0023](docs/adr/0023-authority-is-granted-never-assumed.md))
- [ ] 2026-07-20 — Optional: `dev-local`'s secret is the literal string `dev-local`, checked into
      git at `hearth/etc/callers.json`. It is held to `probe` (1 tool) for exactly that reason, and
      `doorcheck --probe-cloud` now needs `generate` so it requires a real key. If you want a
      hands-on operator identity, mint a SEPARATE caller with a CSPRNG secret and assign the
      (already-defined, currently unassigned) `operator` role — do not widen `dev-local`
      (source: [ADR-0023](docs/adr/0023-authority-is-granted-never-assumed.md))
- [x] 2026-07-20 — **Logging on the gateway's critical start path made the door unstartable.
      FIXED + verified 2026-07-20** ([ADR-0024](docs/adr/0024-gateway-liveness-lives-outside-the-gateway.md)).
      `start-hearth-gateway.cmd` now probes the primary log, retries a few times (a bounce leaves
      the old wrapper's handle open ~1-2s), and only if it is genuinely wedged falls back to a
      unique per-launch file — so the door boots regardless of a stale log handle. The retry sleep
      is `ping`, not `timeout` (which aborts under the `stdin=DEVNULL` that `doorcheck --revive`
      uses). Verified across repeated live bounces: door returns clean, 0 fallback files on a
      normal restart; a genuinely-locked primary falls back and still boots (staged-lock test).
      Recovery wrapper already retired (door back on the normal boot task).
- [x] 2026-07-20 — **The gateway had no external liveness watch. FIXED + verified 2026-07-20**
      ([ADR-0024](docs/adr/0024-gateway-liveness-lives-outside-the-gateway.md)). New scheduled task
      `HearthGatewayWatchdog` (`hearth/etc/watchdog-gateway.cmd`, MINUTE/3) runs
      `doorcheck --json --facet door` and, on two consecutive failed probes, triggers
      `HearthGatewayRestart` (the high-integrity S4U path — preserves Hyper-V admin, no UAC). The
      one deliberate ADR-0015 exception: a loop that cannot fold into the thing it watches.
      Up-path verified live (healthy door → no-op, PID unchanged); down→revive verified by
      component (doorcheck.py:670 returns exit 1 when the listener is down; the restart task
      revives, seen repeatedly) since the test shell cannot kill the higher-integrity door to stage
      a full outage. Rollback: `schtasks /Delete /TN HearthGatewayWatchdog /F`.
- [x] 2026-07-20 — **Mirrored-WSL fate-sharing recorded as a cost of ADR-0022.** Now documented in
      [ADR-0024](docs/adr/0024-gateway-liveness-lives-outside-the-gateway.md) Context (the
      `WinError 64` listener death) and Consequences (mitigated by the facade's per-call reconnect
      + the external watchdog, not removed). A reader weighing mirrored mode now sees both the
      benefit (ADR-0022) and the cost (ADR-0024).
- [x] 2026-07-20 — **Transformation outputs were truncated at their token caps. FIXED + verified
      2026-07-20.** The first run landed on EXACTLY 2,250 / 3,000 (truncation). The builder raised
      the token caps to 3,600 / 3,600 / 4,700 (keeping the 9,000-byte map-output validation as the
      hard backstop) and matched the map prompt to the 9,000-byte target. Re-run on a 49,095-byte
      source, ledger-verified: map 1,160 & 1,550, reduce 2,205 — all STRICTLY under cap, finish
      reason "stop", am4-moe/tag:research, zero trial, zero refusal, final output a complete
      conclusion. Note (not a concern): the token cap (3,600) is now looser than the 9,000-byte
      cap (~2,380 tokens), so the byte validation is the binding constraint and a very dense map
      that naturally runs long would fail-and-retry rather than clip — the intended backstop.
- [x] 2026-07-20 — **Exercise the chunker against a real multi-chunk source. DONE + verified
      2026-07-20.** 108 KB source → 3 chunks, resume isolation confirmed (recomputed chunk 0, reused
      1 & 2 from checkpoints = 2 model calls, matched by ledger), map 2,250 / reducer 3,000 tokens
      both `max_completion_tokens` on am4-moe/tag:research, zero trial-credit routing, zero refusals,
      ~4m15s. Hierarchical reduction, durable checkpoints, and budget enforcement all exercised
      against live compute for the first time. Follow-on quality item registered above.
- [ ] 2026-07-19 — Low priority: fix the offload projection's legacy bucket keys — 182 of 229
      lifetime calls sit in `model:<name>`-shaped buckets with zero token counts, so
      `est_usd_saved` undercounts. Decide backfill vs alias-map vs leave-and-annotate
      (source: [SESSION-RETRO-2026-07-19.md](SESSION-RETRO-2026-07-19.md) L-7)
- [ ] 2026-07-21 — **Full Track 2 build or not.** Track 1 (live benchmark: `am4-moe` 93.8 vs
      `gcp-gemini` 89.2 vs `gcp-gemini-pro` 90.6, gemini ~4-5x faster) and Track 2.0 (minimal
      Studio-hosted ADK agent proven live over HEARTH MCP via Funnel+Caddy) both landed; whether
      to invest in the full docx Agent-1 build (`repo_search`/`git_show`/`ledger_query`/
      `manifest_query` tools, a dedicated `cloud-steward` profile, `baseline` registry
      enrollment, the 8-scenario eval harness) is explicitly left to Derek
      (source: [Hearth_Google_Agent_Implementation_Plan.docx](hearth/Hearth_Google_Agent_Implementation_Plan.docx),
      this session's plan file, SESSION-RETRO-2026-07-21.md session 2).
- [ ] 2026-07-21 — Commit or discard this session's new, currently-uncommitted files:
      `hearth/experiments/doc_adr_bench.py`, `hearth/experiments/run_doc_adr_bench.py`,
      `hearth/projection/gemini_pricing.py`, `hearth/etc/caddy/Caddyfile`,
      `hearth/etc/start-hearth-funnel-proxy.cmd` (not asked for this session — see
      SESSION-RETRO-2026-07-21.md session 2, Operator/SRE).
- [x] 2026-07-21 — Fill real Vertex AI per-Mtok pricing into `hearth/projection/gemini_pricing.py`
      — DONE 2026-07-23, verified against Google's Vertex pricing page (Global/Standard, <=200K):
      flash $1.50/$9.00, pro-preview $2.00/$12.00 (pro tiers to $4/$18 above 200K prompt tokens —
      noted in the module, flat table carries the <=200K rates that match observed usage).
      Cross-check: the pro rung's entire lifetime tokens (1.79M in / 119K out) price out to
      ~$5.02 — corroborates the 2026-07-23 finding that the ~$36 was standing compute, not inference.
- [ ] 2026-07-23 — **Stop the GCP standing-compute burn (the real "$36").** Diagnosis (read-only
      Monitoring API, 2026-07-23) reconciled Derek's ~$36 console figure: NOT monitoring/logging
      ingest (0 chargeable samples; ~287 MB logs/7d, inside free tier) — the dollars are standing
      compute: **two Agent Engine ReasoningEngines live in us-west1 since 2026-07-21 06:41 UTC**
      (`baseline` + `AGENT_DESIGNER_GENERATED_DO_NOT_DELETE`, billing vCPU/GiB-hours while idle,
      ~est $3.5/day each) + the `comfy-lumberjacks-p7` e2-medium VM since ~07-11 (~est $1/day) +
      the $13.30 agent-session drawdown. ⚠ 2026-07-23 engine inspection: `baseline` IS the deployed
      ADK demo agent (`agentFramework: google-adk`) — deleting it deletes the Track 2.0 demo — and
      it runs with `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` +
      `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` (full message content into Cloud
      Trace/Logging — the actual source of the 50K trace calls and the "identity logging" bloat;
      also a privacy consideration: prompt/response bodies land in GCP logs). Corroboration: real
      per-Mtok pricing (now in gemini_pricing.py) prices the pro rung's ENTIRE lifetime inference
      at ~$5.02. Decisions: (a) delete/undeploy the two ReasoningEngines
      (the Studio-generated one despite its name — verify in console first; accept losing the
      standing demo, redeploy-per-campaign later), (b) stop the VM when
      not integrating, (c) optionally disable the Data Access audit logs (41K entries/3d — the
      "interim identity logging") and Ops Agent docker-stdout ingestion (136K entries/3d) — free-tier
      today, noise regardless. Registered in the new spend ledger
      [knowledge/cloud_spend.json](knowledge/cloud_spend.json) ([cloud-spend.v1](contracts/cloud-spend.v1.schema.json));
      full findings in [GCP-AGENT-ASSESSMENT.html](GCP-AGENT-ASSESSMENT.html).
      → 2026-07-23 session 2 progress: engine specs captured pre-deletion to
      hearth/gcp/engine-specs-2026-07-23.json (API exposes no agent source — rebuild is
      repo-owned via hearth/gcp/adk_demo.py, staged); deletion/budget commands handed to
      Derek in the burn-stop runbook (agent-side cloud mutations blocked by the permission
      layer). CORRECTIONS to the diagnosis: the VM is n2-highmem-2 as of 2026-07-23 ~03:09
      (manually downsized; it was n2-highmem-8 from 07-11 — the "e2-medium" in this item and
      cloud_spend.json is wrong; ~$3.14/day at current size, ~$94/mo). Terraform defaults in
      both checkouts now pinned to n2-highmem-2 so the next apply cannot silently resize back
      up. The existing "Lumberjacks Stage 1 monthly budget" ($25) actually scopes the whole
      project and has likely breached — rename/raise it rather than adding a duplicate.
      Class-fix drafted as ADR-0026 (ephemeral-by-default, staged for docs/adr/).
- [x] 2026-07-21 — ~~Revisit [ADR-0025](docs/adr/0025-funnel-caddy-stamps-identity-until-studio-can.md)'s
      Caddy-stamped-key auth once Studio ships MCP Server API-key auth~~ **DONE 2026-08-24, but
      not the way this was written.** Studio never shipped the auth; Derek killed the Studio agent
      for cost (~$16/day). The stamp was dropped anyway because (a) it had leaked its key into the
      proxy's own logs and (b) a peer caller arrived that sends its own header. Per-request auth is
      live, `gcp-adk-test` is revoked, and the `--runner-class` taxonomy question dies with it —
      no cloud-hosted callers remain. See the ADR amendment.
- [ ] 2026-08-24 — **Rate limiting on the Funnel ingress is now material, not theoretical.**
      Carried from ADR-0025 where it was acceptable because the only caller was one test agent.
      That ingress is now intended for a real peer's router. Stock Caddy has none; needs a custom
      `xcaddy` build with `caddy-ratelimit`. Decide before handing the key to anyone.
- [ ] 2026-08-24 — **`hearth/var/callers.json` stores caller secrets in PLAINTEXT**, as the
      top-level JSON keys. Discovered while pruning backups: 8 distinct plaintext secrets were
      sitting across 10 auto-generated `.bak` files, 5 of them still valid — including
      `claude-frontier`, which holds the `unrestricted` profile. Pruned to one backup, but the
      shape is the issue, not the pile. The gateway could store `sha256(secret)` and compare
      hashes on arrival, so a registry read would yield nothing usable. Two sub-decisions:
      (a) move to hashed storage, and (b) make `callerctl` prune its own backups, since every
      mutation writes another full-credential copy and that is what built the pile.
- [ ] 2026-07-29 — **Decide on an actual fix for unpushed/unattached git state — the escalation
      trigger has been hit.** [SESSION-RETRO-2026-07-21.md](SESSION-RETRO-2026-07-21.md)'s
      L-2026-07-21-3 said "a fourth occurrence should trigger an actual fix." As of today
      `master` is **10 commits ahead of `origin/master`** (from earlier sessions, incl. the Buzz
      and i5 work), making this at least the fourth documented instance across 07-19, 07-20,
      07-21 and now. Options to pick from: (a) a session-start `git status -sb` / `symbolic-ref`
      habit baked into the retro or a `SessionStart` hook, (b) a pre-commit or post-commit hook
      that warns on ahead-count > N, (c) accept it explicitly as normal for this repo (Derek
      pushes in batches deliberately) and stop counting it in retros. Choosing (c) is a
      legitimate outcome — the cost right now is retro noise, not lost work — but it should be a
      decision rather than a fourth observation. Source:
      [SESSION-RETRO-2026-07-29.md](SESSION-RETRO-2026-07-29.md).
      **Update 2026-07-30:** the *symptom* is cleared — master pushed on request, now 0 ahead,
      working tree clean. The *decision* (a/b/c) is still open and deliberately not closed here;
      one push does not settle whether the habit needs a hook. See
      [SESSION-RETRO-2026-07-30.md](SESSION-RETRO-2026-07-30.md).

- [ ] 2026-07-30 — **Find what re-pushes deleted fleet branches.** 70 merged remote branches were
      deleted; **61 reappeared within ~30 minutes** carrying Jul 5–18 commit dates (same branches
      re-pushed, not new runs), while the 9 non-`fleet/*` ones stayed deleted. Nothing on OMEN
      explains it: the local `conductor-mirror.git` holds only `main`, and no scheduled task pushes
      to this repo. Most likely a clone on cc-conductor or a builder VM doing a `push --all`.
      Pruning again is a treadmill until the source is found. Remote sits at 158 branches. Source:
      [SESSION-RETRO-2026-07-30.md](SESSION-RETRO-2026-07-30.md).
- [ ] 2026-07-30 — **Decide whether to pin Ollama's version and stop the auto-updater
      half-applying.** `app.log` shows the updater looping hourly on "new update available /
      already downloaded", and `repair-install-2026-07-17.log` (417 KB, "Installation process
      succeeded") shows this exact break was already repaired once, 13 days before it recurred. The
      pattern: it half-applies, deletes `lib/ollama`, and leaves Ollama answering `/api/tags` while
      no generate can succeed. The sentinel now catches it inside 120 s, so this is about
      prevention, not detection. Pinning means updating deliberately. Source:
      [ADR-0028](docs/adr/0028-one-door-means-one-host.md).
- [ ] 2026-07-30 — **Retire `comfy_gateway`'s duplicate `local_generate`** in favour of HEARTH's.
      It is the surviving artifact of the Valheim-MCP-into-HEARTH merge and a second inference path;
      right now it is kept honest only by both ends living on loopback. Changing it touches a tool
      surface in active use. Source: [ADR-0028](docs/adr/0028-one-door-means-one-host.md) §Decision 4.
- [ ] 2026-07-30 — **Decide whether `hearth/experiments/*` should push a `dispatch_identity`.**
      Those runs are real backend × task matrices — the richest varied-axis evidence in the repo and
      exactly what the association engine's gate 2 is starving for — but they currently emit no
      observations. Opting them in is a curation question (which experiment runs count as evidence),
      not plumbing. Source: [ADR-0027](docs/adr/0027-gateway-dispatches-are-not-observations-yet.md).
- [ ] 2026-07-30 — **Name in the offload doctrine that the scorecard measures door traffic, not
      offload.** In-process `local_generate` calls bypass the gateway wrapper and never reach the
      kernel ledger, so this session's live dispatches appear in the observation artifacts but not
      in `knowledge/offload.json` (and `omen-ollama` still reads `ok_rate 1.0` despite failed
      in-process attempts). The doc change belongs in `CLAUDE.md`, which a **concurrent session is
      editing** — deferred rather than conflicted. Source:
      [SESSION-RETRO-2026-07-30.md](SESSION-RETRO-2026-07-30.md) L-2026-07-30-7.
- [ ] 2026-08-25 — **Decide whether AM4 should stay in the BF6 media path at all.** Producer-local
      extraction cut the crossing 40x (3.94 GB → 97.9 MB), but AM4's remaining role is Whisper on
      the RTX 5070 plus the review UI and database. Moving analysis to OMEN too would make the
      cross-machine sidecar bridge and its revision-matching largely unnecessary — a smaller
      system, at the cost of stranding the 5070 on a job it does well. Explicitly out of scope
      when the extraction work was ordered; recorded so the option is not lost. Source:
      [ADR-0037](docs/adr/0037-the-producer-reads-the-raw-footage.md) §Alternatives considered.
- [ ] 2026-08-25 — **Decide what replaces `RENDER_BACKEND=am4` as a rollback path.** The flag is
      still coded and tested but is no longer operable: a local render reads the raw segment, which
      now costs ~16 min of wireless transfer per clip. Today's real rollback is `git revert` plus
      restoring the retired copper cable. Options: accept it (documented), restore a fast link, or
      drop the flag so nothing suggests a fallback that is not there. Source:
      [ADR-0037](docs/adr/0037-the-producer-reads-the-raw-footage.md) §Consequences.
- [ ] 2026-08-25 — **Decide whether the render lane's own `h264_qsv` output needs a real-decode
      check.** The iGPU proxy proved that correct size/geometry/duration/`nb_frames` and a clean
      `ffprobe` can all pass over a stream a decoder cannot read. The render lane validates its
      outputs the same cheap way and has **not** been checked for that failure mode. Source:
      [docs/RENDER-LANE-BACKLOG.md](file:///E:/omen/bf6-highlights/docs/RENDER-LANE-BACKLOG.md) §4.
- [x] 2026-08-25 — **Decide whether the BF6 lane needs liveness watching on BOTH ends.**
      Demonstrated twice in one day. AM4's worker crash-looped on a dead mount from the moment
      the cable moved until a deploy happened to notice. Then all three OMEN processes died and
      sat dead for ~30 minutes with nothing reporting it — found only because a second retro
      went looking. Both ends already expose a cheap signal: AM4's `/health` reports
      `rawMounted`, and the render agent writes `hearth/var/render/agent.heartbeat.json` with a
      timestamp. Neither is watched. Sources:
      [SESSION-RETRO-2026-08-25.md](SESSION-RETRO-2026-08-25.md) §Operator and §Addendum.
      — DONE 2026-08-25: `hearth.media.watchdog` plus the external minute-cadence
      `BF6PipelineWatchdog` task heal stopped OMEN workers, restart a stale render
      agent, and record AM4 mount/API health. See the operational amendment to
      [ADR-0036](docs/adr/0036-gpu-execution-leaves-the-control-plane.md).
- [x] 2026-08-27 — **Decide whether the Qwen3.8-27B earns a long-context / vision pin-only rung.**
      The campaign verdict is `do_not_promote` and that is correct for a *default-rung swap*:
      the candidate is −31.3% on jobs/hour at the gate's 512-token operating point. But the
      gate measures the one regime where a dense 27B loses to a 3B-active MoE. Along prompt
      length it inverts — **2.63× the baseline at 8K prompts, 5.49× at 32K** — and HEARTH's
      real traffic is `files=` packs, which is why `context_bytes` was widened 4× in the first
      place. It also wins blind judging 44/42/4 (95.6% win-or-tie) and has vision, which the
      incumbent lacks entirely. Adding a rung is a standing-cost deployment decision, so it is
      not mine to make. Sources: [SESSION-RETRO-2026-08-27.md](SESSION-RETRO-2026-08-27.md),
      `E:\work\battlemage\qwen38-bench-2026-08\results\promotion-verdict.json`.
      — **DECIDED 2026-08-28 (Derek): yes, pin-only, now** — text-only and MTP-off until the
      vision-decode and divergence questions below resolve. Recorded in
      [ADR-0039](docs/adr/0039-depth-specialists-earn-pin-only-rungs.md), which also enlists
      **fx99** as the CUDA sidecar rung (Derek chose fx99 over reopening the AM4-5070 ruling).
      Phase 1's accuracy-at-depth grading (R10) confirms the rung's charter or retracts it.
- [ ] 2026-08-27 — **Decide whether MTP output divergence at temperature 0 is an engine defect.**
      MTP-on and MTP-off produced zero identical responses across every compared cell, and all
      126 `p8192` MTP-off requests stopped early while MTP-on did not. Speculative decoding is
      only output-safe when verification is exact, so either the pinned build's verification is
      inexact or something else differs between the paths. Blocks any MTP-on deployment.
      Source: [ADR-0038](docs/adr/0038-a-verdict-cites-only-evidence-from-the-configuration-it-promotes.md).
- [ ] 2026-08-27 — **Decide whether to record `ttft` and prompt tokens on `local_generate` ledger events.**
      The prefix-cache-miss penalty is now measured at **~306× the warm TTFT at 26K depth**
      (39.75s → 0.13s once cached). Its *frequency in production* is unmeasurable: the ledger
      records `duration_ms` but not TTFT, so a re-prefill is indistinguishable from a slow
      generation, and only 11 `omen-arc` events carry token counts at all. The campaign harness
      already captures exactly this field; the door does not. It is a change to a live gateway.
      Source: [todo.txt](todo.txt).

- [x] 2026-08-27 — ~~Resolve the 3× Flash-Next decode gap before any Flash figure enters the
      bake-off table~~ — **RESOLVED same day, by the campaign's own placement ladder.** The two
      numbers are two residency levels of one model, and the ladder is monotonic: 4.86 tok/s at
      0 blocks on GPU, 7.41 at 16, 9.24 at 24, 11.66 at 32, 16.38 at 40, **27.70 at all 48**,
      with host commit tracking it 5.7 GB → 60.4 GB. `llama-bench`'s **8.91 sits between the
      16- and 24-block rungs**: the arm passed `-ngl 48` but `-lm mmap`, so the weights stayed
      file-backed and it never obtained the residency it asked for. The within-run decay is the
      same axis (9.97 → 8.4 tok/s ≈ four blocks of residency lost to page reclaim). The
      "unexplained" framing was mine and it was wrong — the answer was already published four
      sections below the number, in this article. **No `-r 10` probe is needed.** The chart now
      carries 27.7 (all 48 blocks named). Fixed in steppeintegrations-site `a725827`.
      ⚠ Still genuinely open, and narrower: the **text-vs-vision split inside one server
      instance** — 27.4 tok/s text against 6.8 vision at identical memory state, MTP off,
      same placement. Vision `latency_s` is ~360 s against ~4 s of `predicted_ms`, so almost
      all of it is image encoding, but that does not explain why *decode* is 4× slower after
      an image. Source: [SESSION-RETRO-2026-08-27.md](SESSION-RETRO-2026-08-27.md).

- [x] 2026-08-27 — **Decide whether Flash-Next gets its speculative-decoding lane measured at
      all.** — **DECIDED 2026-08-28 (Derek): yes** — run the cheapest version (lift the
      harness gate, eight-task compat probe MTP-on at `-ngl 48`, diff against the retained
      MTP-off outputs) inside a supervised maintenance window. Measurement only: the
      temperature-0 divergence question above still blocks MTP-on *deployment* everywhere.
      Original register text follows for the stakes. Every Flash figure in the campaign and the article is `mtp_enabled: false`, and
      not by choice: `server-control.ps1` hard-gates the `-md / --spec-type draft-mtp` path to
      `qwen38-27b` and throws for any other candidate. Flash-Next ships MTP natively, and on
      the dense 27B that path was the single largest configuration win measured — 510 → **1591
      completed jobs/hour**, acceptance 0.65 → 0.86. So the published 27.7 tok/s is the
      model's *non-speculative* speed, and the fast configuration was never run. Two reasons to
      weigh before spending an outage: the same MTP path produced **zero identical responses at
      temperature 0** on the 27B (the open divergence decision above), so a throughput win here
      would land on an output-safety question that is already unresolved; and Flash at full
      residency needs 61.2 GB VRAM + 65.3 GB commit, leaving little room for draft weights.
      Cheapest version: lift the gate, run the eight-task compat probe MTP-on at `-ngl 48`, and
      compare outputs to the MTP-off run already retained.
      Source: [SESSION-RETRO-2026-08-27.md](SESSION-RETRO-2026-08-27.md).
      ⚠ **2026-08-28: the cheap version measured NOT-cheap — blocked on a missing artifact.**
      The fork's Flash MTP path (`--spec-type draft-dflash`) requires a **sidecar draft
      file**, same pattern as the 27B's separate `mtp-*.Q4_0.gguf` — and the pinned unsloth
      repo revision (`824f539b`, 26 files) ships **no dflash/draft/MTP sidecar at all**
      (verified against the cached HF tree). R7 now needs an acquisition step first:
      identify the correct sidecar repo + revision (likely Qwen's original release or a
      separate unsloth sidecar repo), add it to `artifacts.json` as optional, acquire
      revision-pinned, re-lock, THEN run the probe. Not freelance-downloaded mid-window on
      purpose — acquisition discipline holds.
      **Sidecar hunt 2026-08-28:** no publisher ships a fork-native `dflash-*` sibling for
      Flash-Next — unsloth's repo at HEAD (c8b5954, checked same day) still has none, and
      the z-lab/incoai DFlash2 drafters exist only for the 27B. The one candidate is
      `quimmedes/Qwen3.8-Flash-Next-MTP-GGUF` (community; mtp-Q4_K_M 2.79 GB; its `mtp-*`
      naming matches OUR fork's sidecar convention) — but it was published against a
      different fork ("cafe-llama.cpp"), so wire-format compatibility with our qwen4exp
      binary is a load-time experiment, not a given. TWO gates before R7 can run: (a) Derek
      accepts pinning a community artifact (or we wait for unsloth/Qwen to ship one), and
      (b) commit headroom — Flash 48-blk needs 60.4 GB and current margin with production
      down is ~0.9 GB, which is poisoned-load territory; needs a pagefile/headroom decision
      first.
      **RESOLVED-NEGATIVE 2026-08-28 (W3):** Derek approved the pin; sidecar acquired,
      locked, and load-tested on the campaign fork at ngl48 (headroom fine in-window — the
      drained render backlog left 81 GB free; gate passed). **The fork refused the draft
      loudly**: `check_tensor_dims: tensor 'blk.0.hc_attn_norm.weight' not found` — the
      cafe-llama.cpp hyper-connections layout is not what qwen4exp `draft-mtp` expects.
      Cross-fork wire mismatch, exactly the flagged risk; failed at load, no garbage.
      Flash's MTP ceiling stays unmeasured. Paths forward, cost order: (1) wait for a
      fork-native sibling from unsloth/Qwen (free, unknown wait); (2) cafe-llama.cpp as a
      second campaign binary (new provenance, comparability caveats); (3) extract the MTP
      head ourselves from safetensors via the qwen4exp conversion (deep work, best
      provenance). The pinned artifact stays on disk as the negative-control receipt.

- [x] 2026-08-28 — **Ratify llama-swap as the serving-lifecycle layer (pending probes P5–P7).**
      — **RATIFIED same day: all seven probes passed** in a supervised maintenance window
      (production restored + proved). Headline numbers: KV slot save 2.68 GB / **1.74 s**,
      restore **1.19 s** across a full server restart with `prompt_n = 1` on the identical
      29K prompt (0.84 s vs 102.8 s cold — **KV-preserving rotation is real**); heterogeneous
      per-card co-residency held solo rates under concurrent fire (99.2 / 21.6 tok/s); swaps
      **drain** in-flight streams, never cut; cross-model restore 400s (naming manifest still
      mandatory — no model identity in the file format); no Intel VRAM telemetry anywhere
      (Arc telemetry confirmed BUILD). Recorded as
      [ADR-0040](docs/adr/0040-serving-lifecycle-is-adopted-not-built.md).
      Original register text follows.
      The Phase −1 framework bake-off (llama.cpp router mode, SGLang gateway, NVIDIA Dynamo,
      Ray Serve, plus a landscape sweep) concluded that ~60–70% of the proposed rotation
      controller is commodity: llama-swap v251 (Windows binary, per-model `env` so the proven
      B70 command lines survive byte-for-byte, groups for mutual exclusion) should own model
      lifecycle, with the in-tree `llama-server` router as plan B and HEARTH building only
      what verified as absent everywhere — bytes-per-card VRAM admission, Arc telemetry,
      card-aware eviction policy, KV hydration across swaps, and the belief/epoch-scheduling
      layer (JS7b has no open-source peer). Becomes ADR-0040 once probes P5–P7 pass
      (heterogeneous per-card co-residency, Intel telemetry fallback, swap-drain semantics).
      Source: the 2026-08-28 rotation-program session plan; probe receipts to follow in
      ROTATION-PROGRAM.html.

- [ ] 2026-08-28 — **Decide when the remaining builder VMs lift.** Conductor-first executed
      2026-08-28: cc-conductor Running, its SSH hop verified, `patrol,watchdog` re-armed
      (first clean patrol 05:55:57Z after 8 days dark). `drain,fleet_harvest` stay held and
      cc-builder-1..3/claudefarm1 stay Off until the rotation measurement campaign finishes —
      R0 measured commit at 96.9 GB of a 135.3 GB limit with production up, so a Flash-Next
      full-residency epoch (60.4 GB commit) does not fit without the resident model unloaded
      and headroom managed; builder VMs would eat further into that. `ollama-sentinel` stays
      off for good (ADR-0034). Owner: Derek, after Phase 1.
      **Pagefile posture REVISED 2026-08-28 (later):** the 32 GB pagefile was configured
      (unapplied — needs an elevated run + reboot) to make full-residency Flash fit beside
      production. The experts-on-host discovery makes that mostly unnecessary: Flash-lite
      (`-ot exps=CPU`) proved **on tap alongside live production at zero pagefile tax**
      (file-backed experts never become commit), and full-residency Flash fits fine in
      dark windows now that renders drained. Derek's stated preference is to AVOID the
      pagefile penalty. New posture: leave the pagefile config staged but unapplied;
      apply only if a workload ever needs full-fat Flash CO-RESIDENT with production.
      **Option raised by Derek 2026-08-28:** move the conductor role to fx99 (always-on,
      15 GB RAM) so it stops soaking OMEN memory as a VM. Checked same day: no conductor
      exists on fx99 today (dashboard ports closed, no SSH trust from OMEN; loopback-only
      services would evade this check) — the operative conductor is the OMEN VM, patrol-
      verified. A migration is a real Phase 3 candidate; it also concentrates more roles on
      the box whose 2070 SUPER is earmarked to leave, which is fine for a CPU-side daemon.

- [ ] 2026-08-29 — **Confirm the co-residency poisoning mechanism.** Behaviour is characterised
      (105 → 28.39 tok/s with the co-tenant already stopped; restart restores 104.86; idle does
      not; thermal/spill/KV-depth/sustained-use all ruled out by direct test) but the CAUSE is
      not. Needs HWiNFO power/clock telemetry — installed at `C:\Program Files\HWiNFO64` but its
      VSB registry export is **not enabled**, and IGCL's frequency is unusable on the top slot per
      b70tools. Until confirmed, we have a rule without a reason.
      (source: [ADR-0041](docs/adr/0041-co-residency-poisons-the-incumbent.md),
      [SESSION-RETRO-2026-08-29.md](SESSION-RETRO-2026-08-29.md))
- [ ] 2026-08-29 — **Decide how to enforce the thermal rule now that indices are untrustworthy.**
      "The hot card gets the lighter model" (ROTATION-PROGRAM constitution) is index-targeted and
      therefore unreliable — in the four-venue lap the *lighter* model landed on the *cool* card.
      Symmetric `-ts 1,1` makes ordering harmless, but unequal splits or `--main-gpu` need
      identity-based placement, and llama.cpp exposes no PCI-BDF selector. Options: verify-and-
      retry per launch, an upstream BDF selector, or accept symmetric-only placement.
      (source: [ADR-0042](docs/adr/0042-devices-are-selected-by-type-never-by-index.md))
- [x] 2026-08-29 — **RESOLVED: all five SUSPECT items re-measured.** *(Two done. (1) The
      `-ub 512` vs `1024` A/B was re-run warm-vs-warm at `-np 2`, interleaved A-B-A-B, and
      `-ub 1024` is now PROMOTED — the 4x regression is refuted, not just withdrawn. The
      (2) B3's topology crossover is LOCATED — dual-split changes sign between 512 and 1024
      tokens, reaching +72% at 8192 for a ~5–6% decode cost, so production's forced dual-split is
      also correct on merit. Remaining: B4 (Flash's −42% co-residency tax, which now has a clean
      14.56 solo baseline and a changed evidentiary burden) — **refuted**: the tax on Flash is
      −2.3% to −3.9%, inside its own drift floor, and the cost actually lands on the incumbent
      (−15 to −28%, fully recovered). And B5 (dense-vs-MoE) — **corrected to 4.5–5.0×, not ~6×**;
      the dense side reproduced exactly and the MoE side was a llama-bench number. All five closed;
      what remains is the provenance repair on pre-2026-08-29 receipts, not a suspect measurement.)* Every co-resident measurement
      taken before ADR-0041 lacks restart-before-measure discipline: the four-venue seat rates,
      Flash's "−42% co-residency tax", the dense-vs-MoE decode comparison, and the `-ub 512` vs
      `1024` A/B (whose retraction stands on config-default grounds, not on its stated causal
      reasoning). Owner: next FF session.
      (source: [docs/FACTORY-FRONTIER-CARDS.md](docs/FACTORY-FRONTIER-CARDS.md) §0.0)
- [ ] 2026-08-29 — **Finish `ff_ratecheck`: measure at plateau, not a burst.**
      *Partially resolved 2026-08-29 evening* — a baseline now exists (**106.00 tok/s**, set on a
      restarted server with dual-split placement asserted by BDF) and it has been exercised: it
      passed production back at 106.02 (100%, 0.87% spread) after the W-A window. What remains is
      the original limitation — it measures a burst, and the lab optimizes work per machine-hour,
      which is a sustained quantity.
      (source: `campaign/ff-probes/ff_ratecheck.py`, `campaign/ff-probes/rate-baselines.json`)
- [ ] 2026-08-29 — **⭐ PRIORITY: build FF1.** *(Elevated 2026-08-30 after the suspect-measurement
      campaign closed. Numerator quality is now substantially better — five corrected claims, two
      instrument gates, and a warmth control — so the **denominator is the dominant epistemic
      weakness** in every higher-level efficiency claim. Orientation tax, continuity dividend and
      prefill amortization all divide by FF1.)*
      **⚠ START WITH THE DEFINITION, NOT THE INSTRUMENT.**
      [`docs/FF1-DENOMINATOR-AUDIT.md`](docs/FF1-DENOMINATOR-AUDIT.md) finds the denominator
      has never been recorded at all (0 of 405 receipts), exists in two competing
      specifications, and that its `b70_*_s` axes have **no instrument on this box** —
      per-process GPU counters read 0 under S4U, adapter counters cannot attribute between
      co-tenants. **The open decision is which denominator the lab wants:** wall-clock
      occupancy (measurable today), exclusive-resource seconds (what the axes imply, and
      unbuildable here), or opportunity-cost occupancy (arguably the right unit, but a
      scheduling quantity rather than a counter reading). That is a judgement about what the
      lab is for. **Build FF1, or stop claiming work-per-machine-hour.** The work-slice harness
      is the campaign's declared gate and is still unbuilt, so every FF number to date is a
      throughput proxy. Orientation tax, continuity dividend, and prefill amortization all divide
      by it. Either build it or re-scope the campaign's unit honestly.
      (source: [docs/FACTORY-FRONTIER-CARDS.md](docs/FACTORY-FRONTIER-CARDS.md) FF1)

- [ ] 2026-08-29 — **Decide whether serving rungs should pre-warm their common batch shapes at
      startup.** W-A measured a **~12× first-eval penalty paid per batch geometry**, not once per
      server: on Flash, `prefill@512` read 4.80 tok/s (**105.8 s**) on its first eval and 57.28
      warm — *after* the 22-token shape had already warmed the process. For an autonomous lab that
      rotates models, this is a real tax on every rotation and it is invisible in any benchmark
      that discards rep 1. Options: warm a fixed shape ladder at load (costs seconds of startup
      per rung), warm lazily and accept the first-request latency, or ignore it for rungs that
      only ever see one shape.
      (source: [docs/FACTORY-FRONTIER-CARDS.md](docs/FACTORY-FRONTIER-CARDS.md) W-A §5)
- [ ] 2026-08-29 — **Re-measure dual-vs-single at pp2048, solo.** W-A settled the 512-token case
      on the server for the first time: dual-split costs **5.5% decode** and buys **−0.6%** (i.e.
      nothing) on 512-token prefill. The recorded "+27% / +42% prefill" was at **pp2048** on
      llama-bench, which has no `-np`. So the crossover is real but its location is unmeasured on
      a serving topology. ⚠ Note this is an optimization question, **not** a config question:
      production cannot run single-card anyway — at `-c 131072` the KV block is ~12 GB, so
      model+KV+compute is ~30.1 GB of a 32.5 GB card, which is precisely the ADR-0042 defect
      footprint. Dual-split is required for headroom regardless of the decode delta.
      (source: [docs/FACTORY-FRONTIER-CARDS.md](docs/FACTORY-FRONTIER-CARDS.md) W-A §2)
- [x] 2026-08-29 — **RESOLVED (Derek): the keep-alive runs from fx99.** Shipped as
      `fleet/fx99-keepalive/` (30 s warm ping + a 5 min deep probe) driving
      `fleet/arcserve/warm-arc.ps1` over SSH, so fx99 holds the schedule and OMEN keeps the
      token and the loopback binding. Verified holding 105.43 tok/s across 6 minutes.
      ⚠ Running on the **tailnet** fallback; the LAN path needs one scoped firewall rule
      (see the README). ⚠ It cannot revive an already-collapsed rung — restart first.
      ~~Decide where the `omen-arc` keep-alive pinger lives, and ship it.~~ Measured:
      more than ~60 s idle costs the rung **~4×** (106.5 → 39.7 at 120 s idle, 28.7 at 300 s), and a
      **1-token request every 20 s holds it at 104.83** — indistinguishable from a freshly loaded
      server. This is roughly a 4× throughput recovery for one trivial request every 20 seconds, and
      it is almost certainly the single highest-value change available to the lab right now. It was
      deliberately **not** shipped from a measurement window: where the timer lives is a design
      choice with an owner. Options: a scheduled task beside `ArcServeBoot`; a timer inside the
      HEARTH gateway (which already knows every rung and its auth); or llama-swap, which owns
      serving lifecycle per ADR-0040. ⚠ Whichever it is, it must ping **every rung that is meant to
      be hot**, not just `omen-arc`, and it must not count as traffic in the offload ledger.
      (source: [ADR-0043](docs/adr/0043-the-rung-goes-cold-when-idle.md))
- [ ] 2026-08-29 — **Check whether real door traffic has been running in the cold regime all
      along.** *(Partly answered: door overhead is now measured at a flat 175–264 ms
      independent of size, so the 4.0–44.1 tok/s reconstructed from the ledger reflects the
      RUNG, not the gateway. The keep-alive's own receipts will now settle it directly.)* If an agent asks the door a question every few minutes, every call is past the idle
      threshold and `omen-arc` has been serving at ~a quarter of its measured capability in normal
      use. The kernel ledger (`hearth/var/ledger/index.sqlite`) carries `duration_ms` per call; with
      `tokens_out` from the result envelopes it would settle this from history rather than from
      argument. Consequential either way: it decides whether the keep-alive is a *fix* or merely a
      benchmarking hygiene item.
      (source: [ADR-0043](docs/adr/0043-the-rung-goes-cold-when-idle.md) consequences)
- [x] ~~2026-08-29 — **Get HWiNFO's VSB export enabled.**~~ **CLOSED 2026-08-30 — not needed.**
      The premise ("IGCL cannot measure it on this box") was inherited from a different rig and is
      false here: `b70tools` already emits `gpu.frequency_hz` / `gpu.voltage_v` /
      `gpu.energy_j_counter` per card at 1 Hz and reads sane values on **both** B70s. Every consumer
      script filtered those fields out, so the instrument existed and was never read. HWiNFO would
      have closed nothing in any case — two repo statements say it cannot see the B70s on this
      driver at all. **The clock/power hypothesis it was meant to test is now REFUTED** for
      INC-2026-08-30-A: while degraded, both cards sit at 2800 MHz / 1.04–1.06 V drawing 161 W and
      burn **26% more energy per token** than the healthy arm at identical clocks
      (`docs/adr#0044` continuation; receipts `E:\work\battlemage\ff-probes\statewatch-20260830\`).
      ⚠ **Still open, and now cheap:** the *idle*-collapse mechanism (ADR-0043) has never been
      looked at with this instrument — the same 1 Hz capture across a 120 s idle gap would answer
      it, and no longer needs any new tooling.
      (source: [ADR-0043](docs/adr/0043-the-rung-goes-cold-when-idle.md))

- [ ] 2026-08-30 — **E2 is BOUNDED, not repaired — confirm this as standing policy.** The 288
      pre-fix receipts in epoch E2 keep their `PLACEMENT_CONTEXT_UNKNOWN` /
      `INCUMBENT_HEALTH_UNKNOWN` labels permanently. Derive only conclusions that hold across
      plausible E2 states; anything that flips is marked **`NON-IDENTIFIABLE FROM HISTORICAL
      EVIDENCE`** and left there. ⚠ **A modern re-measurement is not a historical correction** — it
      is a fact about today's machine and, where both exist, they are two rows rather than one
      corrected row. Recorded in [docs/CLAIM-REGISTER.md](docs/CLAIM-REGISTER.md); flagged here
      only so the policy is ratified rather than assumed.

- [ ] 2026-08-30 — **⭐ THE FF1 BLOCKING DECISION: what does `work per machine-hour` PRICE?**
      Implementation is deliberately blocked on this, not on tooling. Three candidate quantities,
      which rank configurations differently: **(1) elapsed possession** — the resource was held,
      whatever was done with it; **(2) attributable resource consumption** — this work's share of the
      hardware; **(3) scheduling opportunity cost** — what else could not run. ⚠ **(2) has no
      observable on this box** (per-process GPU counters read 0 under S4U; adapter counters charge
      tenants for each other, reproducing B4 structurally), and it is what the current `b70_*_s`
      axes imply. **Choose the economic quantity first, then the observable** — building first risks
      a beautifully measured denominator for the wrong quantity, which no amount of precision would
      reveal. (source: [docs/FF1-DENOMINATOR-AUDIT.md](docs/FF1-DENOMINATOR-AUDIT.md))
- [ ] 2026-08-30 — **INC-2026-08-30-A: watch, do not poke.** A restart-surviving ~61% state that
      cleared spontaneously ([ADR-0044](docs/adr/0044-rate-is-not-a-scalar.md)). The deliberate
      posture is **no experiment**: the deep probe detects it, the signature is distinct, and per R10
      an intervention scored against a machine that transitions on its own would prove nothing.
      **Spontaneous recurrence is the more informative observation — waiting is the experiment.**
      Revisit if it recurs, or if a second signature appears that the restart discriminator cannot
      separate.


- [x] 2026-09-03 — **DECIDED 2026-09-03 (Derek): production `omen-arc` cuts over to llama-swap.**
      llama-swap on `127.0.0.1:8081` owns the process lifecycle; the production entry keeps
      `--host 127.0.0.1 --port 8082` behind a per-model `proxy: http://127.0.0.1:8082`, so every
      `:8082` consumer (door rung, fx99 keep-alive, `ff_ratecheck.py`, `occupancy.probe_omen_arc_slots`,
      the ETW/keep-alive readers) stays **byte-identical**. The INC-2026-08-30-A observation epoch
      **ends deliberately** and is recorded as a boundary (`rate-baselines.json` `epoch_boundaries`,
      ADR-0044 observation log) — baseline 106.0 preserved, never silently re-baselined.
      **EXECUTED 2026-09-03 12:45:02–12:45:25** — window `rot-cutover-20260903-1245` (23 s,
      `assay.passed`, `hearth/var/rotation-windows.jsonl`), commit `26a1d66`. Receipts: placement
      asserted from the server's own `-lv 5` log (2 B70 `using device` lines, 0 iGPU, 2 Vulkan model
      buffers, 0 CPU buffers, 49/49 layers offloaded); api key enforced (bare request → 401);
      `ff_ratecheck` PASS as the warm burst; keep-alive resumed inside the window (12:45:21 ok,
      prompt_ms 10.3); epoch boundary stamped 12:45:25. Two earlier attempts (12:35, 12:43) aborted on
      ceremony defects — llama-swap `/logs` is a ~10 KB tail so the `using device` lines had scrolled
      out (placement is now read from the server's own `--log-file`), then a `ReadAllText` sharing
      violation on that file (now a shared-mode read) — and rolled back to the direct launcher in
      36 s each. Unattended boot under llama-swap verified 17:31 after another lane's
      `restart-arc.cmd` teardown at 12:59 (`/running` ready, keep-alive ok, `at_rate` 107.3 tok/s =
      101% of baseline). Door tools mounted after the gateway restart (`rotation_status`,
      `recommend_rung`, `query_rung_state`, `rotation_window/load/unload/kv_save/kv_restore`).
      Plan: `C:\Users\derek\.claude\plans\you-have-1-5-hours-mellow-meteor.md` (§ Derek's decisions,
      § P13). Derek's decisions section, verbatim:
      > ## Derek's decisions (2026-09-03, in session)
      >
      > 1. **Cut production over to llama-swap now** (not side-port-first). Concern stated once: this ends the
      >    INC-A epoch under observation and the keep-alive must restart from a warm rung (ADR-0043) — so the
      >    cutover step warms immediately and the boundary is written into ADR-0044's observation log as a
      >    deliberate epoch boundary. Then execute.
      > 2. **"Leverage the dual B70s on OMEN first"** — not a fleet-dispatch cue: M6 stays `--dry-run`; VM
      >    builders reaching the B70 rung is a registered follow-up (design below), not this window.
      > 3. **Restart the HEARTH gateway at the end** ("don't worry about it") — P10 runs `HearthGatewayRestart`
      >    after the shadow-door boot passes, then `doorcheck`.

- [x] 2026-09-03 — **DECIDED 2026-09-03 (Derek): "leverage the dual B70s on OMEN first."** Not a
      fleet-dispatch cue: the mechnet exerciser (plan M6, `campaign/mechnet_exerciser.py`) stays
      `--dry-run`; `--go` was not cued. **VM builders → B70 rung is a registered follow-up**, not
      this window: llama-swap's admin endpoints (`/api/models/unload*`) are **unauthenticated**, so
      binding it on `0.0.0.0` / `172.19.240.1` would let any VM unload production, and the Default
      Switch NAT prefix drifts across reboots. Shape: keep llama-swap on loopback; expose inference
      only through an **authenticated reverse proxy on `omen.mshome.net`** (the
      `OmenOllamaTracingProxy :11435` pattern, forwarding `/v1/*` to `:8081` with the bearer) plus
      **one operator firewall rule scoped to the `vEthernet (Default Switch)` interface** (Derek's
      action); then re-point `cc-builder-2/3` `runner.json`. (source: plan § M6, § "VM builders → B70
      rung"; same plan file as above)

- [ ] 2026-09-03 — **OPEN follow-up: VM builders → B70 rung through an authenticated proxy + one
      operator firewall rule.** Registered from the decision above; nothing built. llama-swap stays on
      loopback (its admin endpoints are unauthenticated — the bare unload endpoint drops production).
      Shape: an authenticated reverse proxy on `omen.mshome.net` (the `OmenOllamaTracingProxy :11435`
      pattern) forwarding `/v1/*` to `:8081` with the bearer, plus **one inbound firewall rule scoped
      to the `vEthernet (Default Switch)` interface — Derek's action**; then re-point `cc-builder-2/3`
      `~/fleet-worker-node/runner.json` (backups exist). Evidence for the need (M6, read-only from
      cc-builder-2): `curl http://omen.mshome.net:8081/v1/models` → unreachable today. Waits on: the
      proxy build, then Derek's rule. (source: ADR-0045 Consequences; plan § "VM builders → B70 rung")

- [ ] 2026-09-03 — **OPEN: `GpuTenancyStore` owner is the literal `'imagegen'`**
      (`hearth/execution/coordination.py:261`). The rotation substrate only **reads** the fence
      (`active_image_session("omen-b70-pool")` non-None → refuse to load); it never claims tenancy
      under its own name. Parameterizing the owner (so rotation loads can hold the pool as a peer
      tenant) **belongs to the imagegen lane** — that file is theirs and was committed by them today.
      Decide there: owner as a constructor/param vs. a second store. Until then rotation and imagegen
      are mutually exclusive on the pool by read-only convention. (source: plan § Hard constraints;
      `hearth/imagegen/` untouched by the rotation build)

- [x] 2026-09-03 — **DONE: P11 rotation proof PASSED through the door + M1 evidence pour on
      `omen-swap` — gate 2 OPEN (`capability_count` 1 → 2).** Window `rot-side-20260903-B`
      (opened 2026-09-04T00:33:47Z, closed `passed` 01:03:54Z; `assay.started`/`assay.passed` in
      `hearth/var/rotation-windows.jsonl`); fix-ups `92f3cd6`, suite 1646 passed; nothing pushed.
      Receipts: `rotation_load phi4-vk1` → entry `phi4-vk1` on BDF `0000:04:00.0`, 3.344 s wall (warm
      file cache; 20.14 s on a later reload after the other model evicted the cache), placement from
      the server's own `-lv 5` log (1 B70 with weights, iGPU clean, 9.7 GB) corroborated by +9.729 GB
      commit on `0000:04:00.0` / 0.0 on `0000:09:00.0`, canary timings present;
      `rotation_load qwen14b-vk1` → same BDF (env=1 maps there), 57.469 s wall (cold file cache),
      +9.621 GB corroborated. KV: `rotation_kv_save phi4-vk1` slot 0 → `phi4-vk1.0.e9480f7e3f3cf3d6.bin`,
      1239 tokens, 253,768,028 bytes; unload phi4 → load qwen14b → unload → reload phi4 →
      `rotation_kv_restore`: `n_restored` 1239 in 168.7 ms, replayed prompt `prompt_n=1 cache_n=1238`
      (vs a 1239-token re-prefill); negative control into `qwen14b-vk1` refused before any HTTP
      (`CrossModelRestore`). Production `qwen3-30b-a3b` on `:8082` listed after every step and
      `at_rate` before and after the pour (107.3 / 109.18 / 107.99 tok/s = 101–103% of the 106.0 baseline), with a dip on the three deep probes taken during it — 17:47:06 74.36, 17:52:11 71.14, 17:57:12 73.33 tok/s, `decode_degraded`, prompt_ms 14.8–15.3 vs 10.0 — while the pour's held-out `omen-arc` judge calls hit production beside a decoding side model; back to 107.99 at 18:02:12 with no intervention; the two causes were not separated, and the probes sit inside the ledgered window; keep-alive
      unbroken); teardown `/running == [qwen3-30b-a3b]`. **Pour:** doc/ADR bench (`e44b726`) arms
      `omen-swap:phi4-vk1` + `omen-swap:qwen14b-vk1`, tasks `adr-0042-vs-launcher` +
      `adr-0041-claims-vs-receipts`, held-out judges `gcp-gemini:gemini-3.5-flash` +
      `omen-arc:qwen3-30b-a3b`; phi4 mean 92.5 (2/2 cells, ~26.8 s), qwen14b mean 88.25 (2/2, ~29.3 s);
      datasets `hearth/var/experiments/doc-adr-bench-20260904T005139Z-sweep` + `-20260904T005728Z-sweep`.
      Rebuild: `knowledge/capabilities.json` `capability_count` 1 → 2
      (`capability:task_kind=offload-generate|backend=omen-swap`; qualified resources (omen, phi4-vk1) +
      (omen, qwen14b-vk1); confidence medium; workflows `wf-hearth-offload-experiment-doc-adr-bench` +
      `wf-hearth-offload-rotation-proof`; watermark `2026-09-04T01:02:55Z`). Gate-1 mechanism
      (ADR-0027): gateway dispatches are bridged with `task_kind` = the tool name, so door calls never
      join the `offload-generate` bucket — the second workflow had to be an in-process caller with its
      own `DispatchIdentity` (`rotation-proof`); the plan's "two door calls from claude-frontier" could
      never have closed gate 1. Five defects surfaced, all fixed in `92f3cd6`: (1) env=2 is the iGPU on
      this driver (ADR-0042 caught it live — the side server was READY on Intel Graphics) → siblings
      renamed `-vk0`/`-vk1` (env 0/1); (2) the door ran code older than the swap-log reader → gateway
      restarted 17:39/17:50 (the 56fe865 note blaming llama-swap's `/logs` tail for the side entries was
      wrong — they already read their own `--log-file`); (3) an expired-MCP-session retry unloaded a
      correct already-resident placement → delta corroboration skipped for resident entries,
      receipt carries `already_resident`; (4) `kv.save_slot` saved the last-held slot (1 canary token,
      205 KB) → prefills the prompt first, restore verifies with the same prompt; (5) the bench harness
      needs the launcher's env (`OMEN_ARC_TOKEN`) → run under a wrapper that CALLs
      `hearth/var/gateway.cmd` from PowerShell (Git Bash fails the cmd chain).
      (source: [ROTATION-PROGRAM.html#board](ROTATION-PROGRAM.html#board);
      [ADR-0045](docs/adr/0045-the-scheduler-plans-a-rotating-host.md) "Lessons from the first live proof")

- [x] 2026-09-03 — **DECIDED 2026-09-04 (Derek): per-member context budgets on `omen-swap`, with the
      rung value as the fallback.** The rung declares one `context_bytes` = 14336 (ADR-0031 arithmetic:
      MIN over members, `-c 4096 × -np 1 × 3.5 B`), so a pin on any member is refused by the smallest
      member's budget — it refused 5 of the 8 doc-bench tasks during the M1 pour although the chosen
      members could carry them. **Shape:** a `context_bytes_by_model` table in `[backend.settings]`;
      a pin is judged against its own member's budget, and any rung or model without an entry keeps
      today's rung-level number, so no other rung's behavior changes. Rejected: strict per-member
      (refusing an undeclared member) — it would require declaring all nine `omen-swap` members before
      anything works, for a fail-closed benefit the fallback already gives cheaply.
      **IMPLEMENTATION STILL OPEN** — not built 2026-09-04 (that session's scope was the rotation-proof
      staging and housekeeping). Where: `hearth/etc/backends.toml`, `Backend.context_bytes()` in
      `hearth/toolsurface/backends.py:138` (needs an optional `model` argument) and its five call sites
      in `route_backend`, the refusal in `hearth/toolsurface/inference.py:582`; tests in
      `hearth/tests/toolsurface/test_backends_omen_swap.py`. Accept when the doc/ADR bench dry-run shows
      the 8k tasks `fits` for phi-4 and refused for the `-c 4096` members.
      (source: M1 pour receipts; ROTATION-PROGRAM.html#board)

- [ ] 2026-09-03 — **OPEN: activate the `-vk0` sibling entries at the next ArcServe restart; two side
      models on DIFFERENT cards waits on it.** `92f3cd6` renamed the single-card siblings `<m>-vk0` /
      `<m>-vk1` (env 0 / 1 — index 2 is the iGPU on this driver, caught live by the ADR-0042 assertion),
      but the running llama-swap keeps the entries it was started with: the yaml takes effect at the
      next ArcServe restart (any lane's restart does it), never by editing it. Until then a `-vk0` pin
      gets a fast 404 refusal (not a 240 s poll), and env=1 puts every side model on `0000:04:00.0` —
      the pour ran phi4 and qwen14b sequentially. No deliberate restart is scheduled: production is
      `at_rate` and INC-2026-08-30-A is WATCH, DO NOT POKE; the next peer-lane or maintenance restart
      activates them. (source: `92f3cd6`; `fleet/arcserve/llama-swap/omen.yaml` header note)

      **CONFIRMED 2026-09-04 (Derek): ride the imagegen lane's restore path — no deliberate production
      restart.** INC-2026-08-30-A stays watch-do-not-poke. Status at 01:52 on 2026-09-04: the imagegen
      lane holds `omen-b70-pool` (`imgsess_c1972c5d42e8ec8be76f488d18268e01`, process started 01:47:51)
      and production is stopped under the fence, so the activating restart is already in flight — its
      restore path restarts ArcServe. Readiness is now machine-checkable rather than remembered:
      `python -m hearth.rotation.preflight` gate **G1** asks the RUNNING llama-swap for its declared
      entries (`/v1/models`), which is the only thing that can distinguish "the yaml says `-vk0`" from
      "`-vk0` is loadable".

- [ ] 2026-09-03 — **OPEN (registered from the Phase 2 TODO row): token hole #2 (conductor repo) ·
      R2c prefix-affinity tuning (`-sps/-cram/--cache-reuse`, a measurement campaign) · BDF-pinned
      placement (no llama.cpp lever today — placement is asserted, not chosen) · eviction actuation
      (advice-only; ADR-0008 stays advisory until the regret trend earns dispatch).** None started; each
      needs its own window or campaign. VM builders → B70 rung and the `GpuTenancyStore` owner literal
      are the two entries above. (source: ROTATION-PROGRAM.html#board Phase 2 TODO)

- [x] 2026-09-03 — **DECIDED 2026-09-04 (Derek): bridge `local_generate` dispatches as
      `offload-generate`; every other door tool stays tool-named.** Background: gateway dispatches are
      bridged with `task_kind = tool name`, so door calls never feed the `offload-generate` bucket — the
      2026-09-03 gate-2 unlock for `omen-swap` needed an in-process `DispatchIdentity("rotation-proof")`
      beside the bench identity, and the plan's assumption that two door calls would close gate 1 could
      never have held. **Rationale:** a door `local_generate` *is* an offload generate — it carries
      backend, model and tokens — so excluding it made the belief layer blind to the richest source of
      real usage while `offload.json` counted the same calls (1284 lifetime, ratio 0.9998). Workflow
      diversity keeps coming from the caller identity, not from `task_kind`, so gate 1 still means what
      it means. Rejected: bridging *every* dispatch (`rotation_status` and the `query_*` readers are not
      generate work and would inflate every rung's evidence).

      ⚠ **PREREQUISITE, STILL UNANSWERED — resolve before implementing.** ADR-0027's addendum records
      that the dispatch-time producer emitted nothing for the three door pins although the identity push
      is in place, and that whether this is a **defect** (it should have fired and did not) or a
      **semantics gap** (it fired and was excluded, or the pins never reached `inference.py`'s emitter)
      was never established from the receipts. A defect is fixed; a semantics gap is authored. Derek's
      decision settles *what should count*, not *why nothing was emitted* — the first implementation step
      is still to read that path and answer it.
      **IMPLEMENTATION OPEN.** Where: `hearth/projection/ledger_adapter.py:201` (the `task_kind` mapping),
      `tools/workflow/project_associations.py` gates, an ADR-0027 addendum recording the outcome.
      (source: SESSION-RETRO-2026-09-03.md L-2026-09-03-3; ADR-0027 addendum "Decision question")

- [ ] 2026-09-04 — **OPEN (defect, found live): `ExecutionLedger` assigns sequence numbers with no
      cross-process lock, so two gateways writing the same execution dir corrupt the ledger.**
      `hearth/execution/ledger.py` guards appends with a `threading.RLock` — in-process only. Two
      gateway subprocesses started concurrently each read count 8138 and each appended sequence 8139
      (`hearth/var/execution/events.ndjson`, 2026-09-04T09:12:59Z), and both were
      `invocation.failed` for the SAME invocation `inv_dadaf8eb995579276b00ca07fe96a509` — each
      booting gateway independently ran the recover-in-flight-invocations path over a shared ledger.
      Consequence: `rebuild()` then raises (`non-contiguous sequence`, and after renumbering,
      `invocation is already terminal`), which makes the **gateway unstartable**. Fix shape: an
      OS-level lock (lockfile / `msvcrt.locking`) around read-count-then-append, or one writer by
      construction. (source: this session's concurrent test runs; `hearth/execution/ledger.py:60-67,
      291, 416-425`)

- [ ] 2026-09-04 — **OPEN (data repair, needs a quiet window): one duplicate terminal event sits at
      line 8140 of `hearth/var/execution/events.ndjson`.** Caused by the race above during this
      session's concurrent test runs; I own it. Current state: the file **is contiguous** (its
      sequence was patched 8139 → 8140 in place) and the live door has appended cleanly past it ever
      since, so **a restart works today** — verified by constructing `ExecutionLedger` over a copy of
      `events.ndjson` **plus the current `projection.sqlite`**: OK, no rebuild triggered. It is a
      **latent** landmine: any forced rebuild (projection lost, deleted, or stale) fails with
      `invocation is already terminal: inv_dadaf8eb995579276b00ca07fe96a509` and the door will not
      start. Repair: drop line 8140 and renumber the tail down by one — **not** while another lane is
      appending (it grew 8140 → 8160 mid-repair; the guard aborted, which is why the file is intact).
      Do it with the execution lane idle. Pre-repair backup:
      `hearth/var/execution/events.ndjson.bak-20260904-seqrepair` (8140 lines, pre-patch).
      Note the file is `hearth/var/**` — gitignored, so none of this is in git history.

- [x] 2026-09-04 — **FIXED: `test_gateway_http.py` was not flaky under concurrency — it was silently
      SKIPPING.** The label carried in the handoff ("flakes under concurrency") was stale: the port
      time-of-check/time-of-use race it names had already been fixed by binding `--port 0`. The real
      behaviour was that `default_execution_dir()` does **not** derive from `HEARTH_ROOT` (it falls
      back to the repo's `hearth/var/execution`), so every gateway the test spawned wrote to the real
      shared ledger; once that ledger was corrupt the subprocess could not boot, `_wait_for_bound_port`
      returned None and `setUp` called `skipTest` — **exit 0, "3 skipped", nothing proven**. Measured:
      **18 of 20** concurrent runs skipped all three tests while reporting success. Fix: set
      `HEARTH_EXECUTION_DIR` to a per-test tempdir in both `setUp`s. After: **12 of 12** concurrent
      runs passed, 0 skipped, and the shared ledger grew by **0** lines. The deselect note in the
      handoff and `hearth/rotation/README.md` can be retired. (source: this session; a monitor that
      cannot be falsified is decoration — L-2026-08-30-7)

- [ ] 2026-09-04 — **OPEN (imagegen lane): a non-forced `stop_image_session` abandons QUEUED work
      silently.** `force=False` drains **in-flight** jobs only. Measured this session: the session
      moved `draining_imagegen` → `restoring_llm` at 09:44:39Z with **7–8 jobs still queued**, and at
      `session.restored` (09:46:22Z) the queue read 0 with `hearth/var/imagegen/queue/` and
      `results/` both empty — those renders did not run and were not persisted anywhere findable.
      Nothing in the tool's result or its `session-events.ndjson` transitions names a dropped-job
      count, so a caller who checks `ok:true` cannot tell that work was discarded. Decide (imagegen
      lane owns this): persist the queue across a restore and replay it on the next session, or
      report a `dropped` count in the stop result and the session event so the loss is at least
      **loud**. Today it is neither. Chosen deliberately as the *gentler* option over `force=True`,
      which makes the silence worse. (source: this session's pool handover;
      `hearth/rotation/POOL-HANDOVER.md`)

- [x] 2026-09-04 — **DONE: two-card rotation proof PASSED** (window `rot-twocard-20260904-A`,
      09:49:39Z → 09:52:29Z `assay.passed`, `candidate_id` carried). `phi4-vk0` (env 0) landed on
      `0000:09:00.0` (+9.729 GB, 0.0 on the other card) and `qwen14b-vk1` (env 1) on `0000:04:00.0`
      (+9.621 GB), both `bdf_corroborated`, both iGPU clean, both **first attempt with no sibling
      retry**; `/running` held all three ready at once and teardown returned
      `[qwen3-30b-a3b]`. **Closes the `-vk0` activation item above** — the entries went live on the
      ArcServe restart inside the imagegen lane's restore, exactly as the decision predicted, with no
      deliberate production restart. Production: `at_rate` 109.13 tok/s (103%) before the window;
      inside it 5 samples, all pings, `prompt_ms` 10.1–13.3 vs 10.0 baseline, no stalls — **no deep
      sample fell inside the 2m50s window, so no in-window decode rate is measured or claimed.**
      Receipts: `hearth/var/rotation/windows/rot-twocard-20260904-A.json`,
      `hearth/var/rotation/last-load.json`, `hearth/var/rotation-windows.jsonl`,
      events `evt_rotwin_90f5ba3e90a7` / `evt_rotwin_d269ac0473b2` on `runs/hearth/events.jsonl`.

- [x] 2026-09-04 — **FIXED (found live inside the proof): the window-exclusion reader was blind
      during OPEN windows.** `0cb5275` taught `live_rung_state` to exclude ledgered rotation windows,
      but `rung_state` compared `start <= t <= end` with `end=None` for an open window → `TypeError`,
      swallowed by the passive reader into `verdict: "unknown"`. The rung therefore reported nothing
      for exactly the interval the exclusion was written to cover. Every existing test used a window
      carrying both an open and a close row, so `end` was never `None`. Fixed in
      `hearth/health/rungstate.py` (open window excludes from its start onward) with regression tests
      at both the `rung_state` and `live_rung_state` levels, and verified live against the real
      keep-alive file with the real open window. **This is the second live catch on a gate written
      the day before, and it only surfaced because the proof asked the reader a question while a
      window was open — a step the original handoff sequence did not contain.**
      (source: window `rot-twocard-20260904-A`)
