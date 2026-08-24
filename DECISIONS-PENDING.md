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
