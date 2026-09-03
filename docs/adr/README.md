# Architecture Decision Records

Short, dated records of architectural decisions for commandcenter — the *why* behind
choices that aren't obvious from the code, and the ones we'd otherwise re-litigate.

Format: Status · Context · Decision · Consequences. One decision per file,
`NNNN-kebab-title.md`. Supersede rather than delete — a reversed decision stays as a
record with its status changed and a pointer to the ADR that replaced it.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-assay-acceptance-gap.md) | The assay is a regression gate, not an acceptance oracle | Accepted (2026-07-04) |
| [0002](0002-belief-layer-excludes-infra-failures.md) | The belief layer must not ingest infra/harness-caused failures | Accepted (2026-07-04) |
| [0003](0003-allowlist-overrides-exclusion.md) | A CCMETA allow-list overrides `exclude_from_build_pool` | Accepted (2026-07-04) |
| [0004](0004-retrospection-is-frontier-assembled.md) | Retrospection is frontier-assembled; the fleet drafts, it does not author | Accepted (2026-07-04) |
| [0005](0005-one-boundary-three-planes.md) | One boundary, three planes: every offload crossing goes through HEARTH | Accepted (2026-07-04) |
| [0006](0006-idle-drain-arming-policy.md) | Unattended autonomy is an authored, suspendable toggle, earned by a supervised cycle | Accepted (2026-07-04) |
| [0007](0007-watchfire-coherence-watching-and-auto-heal.md) | The guard dog watches coherence, and auto-heals the obvious | Accepted (2026-07-04) |
| [0008](0008-scheduler-advisory-first.md) | The scheduler advises until the ledgered regret trend earns it dispatch | Accepted (2026-07-04) |
| [0009](0009-tiered-builds-integration-is-the-review.md) | Tiered agent builds: integration is the review; frontier merge review is load-bearing | Accepted (2026-07-04) |
| [0010](0010-two-ledgers-two-bounded-contexts.md) | Two ledgers, two bounded contexts: one door is not one store | Accepted (2026-07-04) |
| [0011](0011-record-event-double-write-is-intentional.md) | The record_event double-write is intentional | Accepted (2026-07-04) |
| [0012](0012-commander-intent-lane-frontier-out-of-loop.md) | The commander issues intent; mechnet carries it, no frontier in the run loop | Accepted (2026-07-05) |
| [0013](0013-wake-am4-live-serve-truth-single-claimant.md) | wake_am4 goes live: serve-truth idempotency, queue-gated occupancy, one systemd claimant per port | Accepted (2026-07-07) |
| [0014](0014-machine-lanes-off-the-tailnet.md) | Machine lanes ride local networks; Tailscale is for humans and the Funnel | Accepted (2026-07-09) |
| [0015](0015-ops-loops-fold-into-the-gateway.md) | Repeating ops loops fold into the always-on gateway; no interactive scheduled tasks | Implemented (2026-07-09), amended by 0024 |
| 0016 | *(reserved — pending ratification, no file yet; scheduler actuation decision, H1 — see SCHEDULER-STRATEGY.html)* | Pending Derek |
| [0017](0017-software-constellation-registry-am4-seed-intake.md) | Software constellation registry + the AM4 seed intake rulings (manifest/ember/gad) | Accepted (2026-07-16), registry landed |
| [0018](0018-resident-moe-steady-state-tenant.md) | The resident big-MoE is oxen's steady-state tenant: goodput-routed, budget-enforced | Accepted (2026-07-18), live |
| [0019](0019-container-access-capability-profiles.md) | Container access is capability-profiled: explicit non-loopback bind, profile-gated tool surface | Accepted (2026-07-19), Phase 4 complete |
| [0020](0020-phase-4-health-facets.md) | Phase 4 health facets and compatibility | Implemented (2026-07-19) |
| [0021](0021-fail-closed-payload-routing.md) | Payload budgeting is fail-closed at the inference router | Accepted (2026-07-20) |
| [0022](0022-container-access-needs-no-exposure.md) | Container access needs no network exposure: mirrored WSL + an explicit transport-security allowlist | Accepted (2026-07-19), amends 0019; amended 2026-08-24 (host address moved; alias is host-side only) |
| [0023](0023-authority-is-granted-never-assumed.md) | Authority is granted by naming a role, never by omitting one | Accepted (2026-07-20), closes 0019's fail-open |
| [0024](0024-gateway-liveness-lives-outside-the-gateway.md) | The gateway's own liveness watch lives outside the gateway | Accepted (2026-07-20), amends 0015 |
| [0025](0025-funnel-caddy-stamps-identity-until-studio-can.md) | A Funnel-facing Caddy proxy stamps caller identity until Google Agent Platform Studio can send one itself | Accepted (2026-07-21), first external cloud caller |
| [0026](0026-cloud-deployments-are-ephemeral-by-default.md) | Cloud deployments are ephemeral by default | Accepted (2026-07-23) |
| [0027](0027-gateway-dispatches-are-not-observations-yet.md) | Gateway dispatches are not observations until we say what they observe | Accepted (2026-07-30), option B built; first capability re-earned |
| [0028](0028-one-door-means-one-host.md) | One door means one host: a containerized MCP must not become HEARTH's second mouth | Accepted (2026-07-30), amends 0005/0022; native + loopback done, Ollama repair + firewall pending operator |
| [0029](0029-moe-serves-all-am4-llm-roles.md) | The resident MoE serves all AM4 LLM roles | Accepted (2026-07-30) |
| [0030](0030-hearth-is-the-system-of-record-for-ai-execution.md) | HEARTH is the system of record for AI execution | Accepted and implemented (2026-07-30), amends 0014 |
| [0031](0031-a-pin-chooses-a-rung-not-a-waiver-of-arithmetic.md) | A pin chooses a rung; it does not waive arithmetic | Accepted and implemented (2026-08-06); makes every `context_bytes` load-bearing |
| [0032](0032-ollama-updates-are-deliberate-not-ambient.md) | Ollama updates on OMEN are a deliberate act, not an ambient one | Accepted and implemented (2026-08-19); tray autostart retired, `fleet/update_ollama.ps1` is the lane |
| [0033](0033-one-definition-of-a-run.md) | One definition of a run: the directory is the run, `result.json` is the only terminal marker | Accepted and implemented (2026-08-20); `nodes.json` no longer filters the guard dog's sweep |
| [0034](0034-omen-dual-b70-rung.md) | The dual-B70 rung lives on OMEN itself: omen-arc is the door default | Accepted (2026-08-21); campaign-proven config, am4 rungs tombstoned, omen-ollama demoted |
| [0035](0035-media-render-is-its-own-authority.md) | Rendering is its own authority: `media_render` is withheld from `operator` | Accepted (2026-08-25); live through the door the same day |
| [0036](0036-gpu-execution-leaves-the-control-plane.md) | GPU execution leaves the control plane: the interactive render agent | Accepted (2026-08-25); session 0 has no adapter, gateway stays sole ledger writer |
| [0037](0037-the-producer-reads-the-raw-footage.md) | The producer reads the raw footage: extraction runs where the bytes already are | Accepted (2026-08-25); forced by the retired copper link, deployed and proven the same day |
| [0038](0038-a-verdict-cites-only-evidence-from-the-configuration-it-promotes.md) | A verdict cites only evidence measured on the configuration it promotes | Accepted (2026-08-27); caught a scorecard about to credit an MTP-on winner with MTP-off quality |

| [0039](0039-depth-specialists-earn-pin-only-rungs.md) | Depth specialists earn pin-only rungs, not the default | Accepted (2026-08-28); the 27B wins at depth and loses at 512, so it is pinned not promoted |
| [0040](0040-serving-lifecycle-is-adopted-not-built.md) | Serving lifecycle is adopted, not built: llama-swap owns process lifecycle | Accepted (2026-08-28); probe-gated, ~60-70% of the proposed controller deleted |
| [0041](0041-co-residency-poisons-the-incumbent.md) | Co-residency poisons the incumbent: restart before you measure | Accepted (2026-08-29); 105 → 28.39 tok/s with the co-tenant already stopped, restart restored 104.86 |
| [0042](0042-devices-are-selected-by-type-never-by-index.md) | Devices are selected by type, never by index | Accepted (2026-08-29); the visibility filter had been costing a whole card — removed, not corrected |
| [0043](0043-the-rung-goes-cold-when-idle.md) | The rung goes cold when idle: keep it warm, don't restart it | Accepted (2026-08-29); >60 s idle costs ~4×, a 1-token ping every 20 s prevents it entirely — supersedes 0041's trigger |
| [0044](0044-rate-is-not-a-scalar.md) | A rate is not a scalar: baseline epochs, and degraded states are classified | Accepted (2026-08-30); four stable levels in one night — baseline is epoch-scoped, and the restart discriminates ADR-0043 idle collapse from INC-2026-08-30-A |
| [0045](0045-the-scheduler-plans-a-rotating-host.md) | The scheduler plans a rotating host: OMEN is a stateful Machine, llama-swap owns its lifecycle, and every number comes from a receipt | Accepted (2026-09-03); catalog, rotation substrate, `omen-swap` rung, rung state, task families landed; production cutover scripted but NOT executed (blocked by the imagegen tenancy); `rotation_plan` in progress; tenancy owner OPEN |

## Historical note
The "ADR-0001" referenced in `SESSION-RETRO-2026-06-29.md` (which orchestrator is
run-of-record: ember vs Farmer) was a forward-reference from the pre-fleet convergence
design and was never ratified as a file. The current lab (OMEN conductor + builder VMs +
belief layer) evolved past that framing; these ADRs start the record fresh from the
decisions actually made and validated in the running system.
