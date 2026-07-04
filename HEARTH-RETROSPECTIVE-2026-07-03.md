# HEARTH Retrospective — 2026-07-03

## Scope

This retrospective covers the HEARTH build work completed in the current repo session: initial recovery after the prior agent crash, the in-repo H0-H4 implementation, the architecture/policy decisions locked during the session, and the end-to-end MCP verification work.

## What Went Well

1. Recovery was clean.
   There was no committed HEARTH branch to untangle, and `master` remained at the expected baseline. That made it safe to build from scratch without merge archaeology.

2. The existing repo had the right primitives.
   The workflow event schema, projectors, corpus guards, worker MCP exemplars, and test corpus provided strong reuse points. That avoided greenfield drift.

3. Path ownership in the build doc translated into clean implementation boundaries.
   Kernel, tool-surface, and caller/projection work were easy to reason about because the original orchestration doc already separated those concerns well.

4. The first implementation pass was structurally right.
   The gateway, ledger, providers, caller harness, and projection adapter all fit together without needing architectural rewrites. The later work was mostly wiring and contract correction.

5. The workflow projector reuse paid off.
   Converting HEARTH ledger events into workflow-style runs let the existing knowledge machinery produce capabilities and acceptance/economics artifacts from real HEARTH traffic.

6. Live MCP validation was decisive.
   Exercising HEARTH over real streamable-HTTP MCP removed ambiguity about whether the gateway was only theoretically correct. It proved the interface, auth path, tool invocation, ledger write, and knowledge path in one chain.

7. The OMEN-first posture sharpened the design.
   Once OMEN hosting, MCP-only frontier access, and the warm local path were explicitly chosen, the architecture became more coherent and less split-brain.

## What Went Poorly

1. The original build request implied parallel worktrees, but the crashed session left no usable partial branches.
   In practice the work had to be executed serially inside one session, so the orchestration plan served more as a design partition than as an actual multi-branch landing flow.

2. A few integration mismatches showed up only after real wiring.
   Examples:
   - workflow schema details like `trace` shape and `builder.assigned` requiring `lap_id`;
   - projector entry-point naming differences such as `materialize_knowledge` vs `materialize_capacity`;
   - capability emergence depending on the actual association rules, not just any two successes.

3. Worker-bridge implementation crossed the async boundary incorrectly at first.
   Using `asyncio.run()` from inside gateway tool execution caused runtime warnings during the live smoke. Moving that bridge behind a subprocess helper fixed the boundary cleanly.

4. Runtime artifacts needed explicit hygiene.
   The ledger and other HEARTH runtime state needed to be ignored to keep the repo clean. That was straightforward, but it surfaced only after live smoke runs.

## Surprises

1. The architecture was easier to prove than the policy layer.
   The technical slice came together quickly once the repo assets were understood. The hard part is not the gateway mechanics; it is the operational boundary decisions.

2. The projector chain is strict in useful ways.
   Several test failures were not noise; they exposed real contract expectations. That made the integration more trustworthy.

3. A mini commandcenter framing fits better than a full orchestration framing for this slice.
   The implementation naturally settled into a warm local execution-and-learning core rather than trying to be the whole system at once.

## Decisions Locked During The Session

- OMEN hosts HEARTH.
- OMEN Ollama is the always-on local inference path.
- Frontier callers are locked down to MCP-only operation.
- Common development actions should stay local and warm.
- Break-glass exists as operator policy, but is not implemented yet.
- HEARTH is the mini commandcenter for already-specific work requests.

## Risks Still Present

1. Live-host lockdown is not yet implemented.
   The design direction is fixed, but host-level enforcement still needs to be built carefully to avoid breaking the working local loop.

2. Kernel ceremony is still light.
   There is a `kernel_change` helper and documented posture, but stronger enforcement and approval semantics remain future work.

3. Break-glass is defined but not realized.
   That is intentional, but it means there is no concrete emergency bypass yet.

4. Learned routing is not built.
   HEARTH can collect the right evidence now, but H5-style dispatch optimization still depends on enough real ledger data.

## What We Built

- HEARTH event schema and caller registry
- append-only NDJSON + SQLite ledger
- authenticated FastMCP gateway
- provenance-wrapped tool registration
- filesystem/git/test/knowledge/dispatch/summon/worker/local-generate providers
- manifest-based worker bridge
- MCP caller harness and runner manifest support
- ledger-to-workflow export and knowledge projection pipeline
- HEARTH acceptance and economics artifacts
- repeatable live MCP demo script

## Verification

- HEARTH tests: `21/21` passing on the OMEN venv interpreter
- Existing repo tests: `177/177` passing on system python
- Live gateway smoke: passing
- Live MCP demo against `http://127.0.0.1:8710/mcp`: passing

## Next Best Moves

1. Implement the real host-level H2 lockdown on OMEN without damaging the local warm path.
2. Tighten kernel ceremony from documented policy into enforceable mechanism.
3. Decide and later implement the break-glass logging path.
4. Start collecting enough real HEARTH traffic to make H5 routing work evidence-driven instead of aspirational.

## Bottom Line

The session was successful. HEARTH moved from an orchestration document and a partial crash state to a working in-repo MCP system with provenance, learning, and live interface validation. The remaining work is real, but it is mostly operational hardening and policy enforcement rather than missing architecture.

