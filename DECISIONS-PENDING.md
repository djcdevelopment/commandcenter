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
      **Build FF1, or stop claiming work-per-machine-hour.** The work-slice harness
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
- [ ] 2026-08-29 — **Get HWiNFO's VSB export enabled.** Spill, eviction and thermal are now
      excluded *in the degraded state* by direct measurement (`non_local` 0.002/0.446 GB, 0 °C
      spread at 50 °C), leaving **GPU clock/power state** as the surviving candidate for the idle
      mechanism. IGCL cannot measure it on this box — b70tools lists voltage/frequency as unusable
      on the top slot. This was already registered under ADR-0041 with a vaguer question; the
      question is now sharp: *what do the clocks do across a 120 s idle gap and the four requests
      that follow it?*
      (source: [ADR-0043](docs/adr/0043-the-rung-goes-cold-when-idle.md))

- [ ] 2026-08-30 — **E2 is BOUNDED, not repaired — confirm this as standing policy.** The 288
      pre-fix receipts in epoch E2 keep their `PLACEMENT_CONTEXT_UNKNOWN` /
      `INCUMBENT_HEALTH_UNKNOWN` labels permanently. Derive only conclusions that hold across
      plausible E2 states; anything that flips is marked **`NON-IDENTIFIABLE FROM HISTORICAL
      EVIDENCE`** and left there. ⚠ **A modern re-measurement is not a historical correction** — it
      is a fact about today's machine and, where both exist, they are two rows rather than one
      corrected row. Recorded in [docs/CLAIM-REGISTER.md](docs/CLAIM-REGISTER.md); flagged here
      only so the policy is ratified rather than assumed.

