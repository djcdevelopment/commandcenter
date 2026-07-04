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

## Historical note
The "ADR-0001" referenced in `SESSION-RETRO-2026-06-29.md` (which orchestrator is
run-of-record: ember vs Farmer) was a forward-reference from the pre-fleet convergence
design and was never ratified as a file. The current lab (OMEN conductor + builder VMs +
belief layer) evolved past that framing; these ADRs start the record fresh from the
decisions actually made and validated in the running system.
