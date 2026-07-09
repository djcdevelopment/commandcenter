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
| [0015](0015-ops-loops-fold-into-the-gateway.md) | Repeating ops loops fold into the always-on gateway; no interactive scheduled tasks | Accepted (2026-07-09), build pending |

## Historical note
The "ADR-0001" referenced in `SESSION-RETRO-2026-06-29.md` (which orchestrator is
run-of-record: ember vs Farmer) was a forward-reference from the pre-fleet convergence
design and was never ratified as a file. The current lab (OMEN conductor + builder VMs +
belief layer) evolved past that framing; these ADRs start the record fresh from the
decisions actually made and validated in the running system.
