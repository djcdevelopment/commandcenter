# ADR-0005 — One boundary, three planes: every offload crossing goes through HEARTH

**Status:** Accepted (2026-07-04) — ratified via the Banked Fire review; panel in
`HEARTH-BANKED-FIRE-STRATEGY.html`, doctrine memory `project-three-plane-doctrine`
**Context sources:** another Claude session's three-plane proposal (reviewed and amended this
session); `am4-fleet-node/scripts/am4-mcp-server.py`; `fleet/mechnet_watchdog.py`; the
centralized-capture principle.

## Context

As the mechnet grew a second MCP server (the AM4 fleet node: `render_owners`,
`oxen_backend_status`, `start_oxen_backend`), the obvious question arrived: should AM4's MCP
become a second door for dispatching work? The review that ratified this ADR found the decisive
argument is not tidiness. At this stage of the lab, **data is the deliverable** — the ledger's
completeness is what the belief layer (S1–S8) learns from. A second connection point that carries
work leaks observations: some crossings ledger, some don't, and learning fragments.

The planes are **control-surface roles, not machine roles** — one box can host two planes. AM4
hosts both a sense surface (AM4-MCP, Jaeger/OTel) and act capacity (the oxen B70 backend), and
that is fine *because its acting is only reachable through HEARTH*.

## Decision

Three planes, one boundary:

- **SENSE** — the AM4-MCP + OTel/Jaeger spine: infrastructure telemetry and self-maintenance.
  It never issues or carries engineering commands.
- **ACT** — the mechnet: builders, VMs, backends — where offloaded work runs and learning is
  generated.
- **THE ONE BOUNDARY** — HEARTH: connect + offload + capture. **Every offload/engineering
  crossing, in or out, goes through HEARTH and lands on one ledger.**

Sense and self-maintenance surfaces (AM4-MCP, mechnet-watchdog) may exist beside the boundary but
never carry work. The watchdog's events go to the kernel ledger, deliberately separate from
belief-projection sources — self-maintenance is captured without polluting beliefs.

The companion guardrail: **offload aggressively, guard the ledger** — bad observations poison the
beliefs the reps are meant to earn (precedents: the 2026-07-02 corpus overwrite; the null-action
winner exploit). The objective is throughput of *trustworthy* reps, not raw reps.

## Consequences

- Any proposal for a new capability is answered by the planes: new capability ⇒ reach it through
  HEARTH; new telemetry ⇒ sense plane; anything wanting a second command door ⇒ no.
- The bankedfire P2 occupancy probe reads AM4's sense plane (render-owner serve-truth) but the
  resulting dispatch decision executes and ledgers at the HEARTH boundary — the pattern to copy.
- The cost is indirection: work destined for AM4 always transits HEARTH even when a direct call
  would be shorter. That cost is accepted; it is the price of a complete dataset.
- If a future stage makes data no longer the binding deliverable (e.g. hardened multi-tenant
  serving), this ADR should be explicitly revisited rather than eroded door by door.
