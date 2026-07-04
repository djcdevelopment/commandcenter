# HEARTH Findings — 2026-07-03

## Summary

HEARTH is now a viable MCP augmentation layer for a frontier agent. The current repo slice proves the core shape: a frontier model can treat HEARTH as the action, provenance, and learning surface instead of reaching directly for SSH and ad hoc local mutation.

## Findings

1. HEARTH works as a real MCP surface.
   The gateway exposes a live streamable-HTTP MCP endpoint, accepts authenticated tool calls, and records provenance into the HEARTH ledger.

2. The right split is frontier cognition over HEARTH execution.
   Frontier models should do planning, synthesis, decomposition, and judgment. HEARTH should own action, logging, guard enforcement, and routing to local or remote capacity.

3. OMEN is the right host for this slice.
   The kernel and always-on Ollama path are now both locked to OMEN. That keeps the system warm, reactive, and local-first for the routine development loop.

4. Frontier callers should remain MCP-only.
   General SSH write access for frontier callers would undercut the single-audited-write-path goal. HEARTH is strongest when the gateway is the enforced choke point.

5. Common development actions belong on the local warm path.
   Repetitive inner-loop work such as file operations, git inspection, test digests, local dispatch, and local inference should stay on OMEN through HEARTH rather than depending on summoned external agents.

6. The main augmentation is better action surfaces, not a smarter model.
   HEARTH improves frontier-agent performance by exposing coarse audited tools, persistent provenance, local inference, reusable learning artifacts, and optional remote muscle behind one interface.

7. Reusing the workflow ontology stack was the correct move.
   Exporting HEARTH ledger events into workflow-style runs allows the existing projector chain to derive findings, capabilities, coverage, acceptance, and economics without inventing a second learning system.

8. The build is beyond concept stage.
   The current implementation includes the authenticated gateway, append-only ledger, provider registry, dispatch/progress tools, manifest-based worker bridge, local-model caller harness, ledger-to-knowledge projection, and live MCP smoke coverage.

9. The remaining work is mostly operational policy.
   The largest open items are host-level lockdown implementation, stricter kernel ceremony enforcement, explicit break-glass design and logging, learned routing, and external arena work.

10. Break-glass should exist, but remain exceptional.
    Defining it as operator policy without implementing it yet is the right posture. If it is built too early or too conveniently, it will become the default route around HEARTH instead of the fallback.

11. HEARTH is best understood as a mini commandcenter.
    It is not the whole orchestration fabric. It is the warm local execution and learning core for already-specific work requests.

12. The architecture is coherent enough for real frontier augmentation now.
    A client that supports MCP can use HEARTH as the action layer today. Further work primarily deepens policy and hardening rather than changing the basic shape.

## Current Recommended Posture

- Host HEARTH on OMEN.
- Keep Ollama always on through the local warm path.
- Keep frontier callers MCP-only.
- Keep common development actions local and warm.
- Use remote workers only through HEARTH summon/worker tools.
- Treat the ledger and projectors as always-on learning infrastructure, not optional reporting.

## Verification Context

The findings above were formed after:

- building the HEARTH gateway, tool surface, caller harness, and projection pipeline;
- running the HEARTH unit suite on the OMEN venv interpreter;
- rerunning the existing repository test suite on system python;
- exercising HEARTH over a real MCP session and confirming ledger writes and projection outputs.

