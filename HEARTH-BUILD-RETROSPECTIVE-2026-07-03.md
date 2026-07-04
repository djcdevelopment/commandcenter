# HEARTH Build Retrospective — 2026-07-03 (orchestration session)

This is the retrospective of the session that designed HEARTH, orchestrated the
3-parallel-worktree build, ran the integration gate, and took the system live.
It complements — and partially conflicts with — `HEARTH-RETROSPECTIVE-2026-07-03.md`,
which was written by a *different* concurrent session. The conflict is itself the
most important finding; see "The two-HEARTHs incident" below.

## What was built and shipped

- Design: `HEARTH-FULL-BUILDOUT.html` (5 layers L0–L4, phases H0–H6).
- Orchestration: `HEARTH-BUILD-ORCHESTRATION.html` — 3 parallel worktree builds
  against frozen contracts (hearth-event.v1, `get_tools()` provider contract,
  `X-Hearth-Key` auth), disjoint path ownership, integration gate at the end.
- Landed on master: `9f306fb` (H-A kernel), `e642210` (H-B tool surface),
  `a6b6863` (H-C callers + projection), `b817c8e` (octopus merge),
  `3c3855f` (integration fix), `65ed618` (go-live).
- Gates: regression 177/177 · hearth suites 111/111 · end-to-end smoke 15/15.
- Live: gateway on :8710 (7 providers, 21 tools), real caller keys, frontier
  wired via `.mcp.json`, boot-level autostart (`HearthGatewayBoot`, elevated by
  Derek), first local + frontier dispatches on the ledger, live ledger projected
  into `runs/hearth-gateway/` and consumed by the S1–S8 spine.

## What went well

1. **Frozen contracts + disjoint path ownership made the parallel build boring
   (in the good way).** Three agents built kernel, hands, and callers
   concurrently; the octopus merge was conflict-free on the first attempt.
2. **The session-limit interruption cost nothing.** All three builders died
   mid-build when the account limit hit; resuming them from transcript with
   their worktrees intact lost zero work. Checkpoint/resume is now proven at
   the orchestration layer, not just the fleet layer.
3. **The integration gate earned its existence.** It caught a real cross-stream
   defect no unit suite could see: H-C sent `task_id` via MCP request meta,
   H-A's gateway never read it — ledger events silently lost task attribution.
   Both streams' tests were green; only the seam test failed.
4. **Reuse held.** Guards, projectors, append_event, and the workflow event
   schema were wrapped, not reinvented. The ledger→workflow-event adapter let
   existing projections consume HEARTH traffic on day one.
5. **Derek's mid-build decisions were absorbed without re-planning** (OMEN
   placement, MCP-only frontier callers, break-glass as policy-only, warm local
   path) — passed to running agents as deltas, reflected in the landed code.

## What went poorly

1. **The two-HEARTHs incident (the big one).** A second, concurrent session —
   unaware of the worktree builds — concluded the build had crashed, rebuilt a
   serial HEARTH draft directly in the main checkout, ran its own live MCP
   demo, and wrote its own findings/retrospective. This session, finding that
   draft as untracked files that blocked the merge, verified it was divergent
   and deleted it in favor of the tested worktree build. Both sessions acted
   reasonably on incomplete views; neither could see the other. **This is
   precisely the disease HEARTH treats: uninstrumented actors with write access
   and no shared ledger.** Had both sessions been HEARTH callers, the second
   session's first write would have been a ledger event the first could query.
   The other session's artifacts (`HEARTH-RETROSPECTIVE-2026-07-03.md`,
   `HEARTH-FINDINGS-2026-07-03.md`) are preserved as its honest perspective;
   its architectural conclusions (frontier cognition over HEARTH execution,
   MCP-only frontier access, warm local path) independently agree with the
   landed design — convergent evolution is corroboration.
2. **Silent-failure modes ate debugging time.** Three in one day:
   a gateway leaked from H-A's own tests squatted :8710 and absorbed the first
   smoke run's traffic (fix: smoke fails fast on an occupied port); PowerShell
   5.1's `Out-File -Encoding utf8` BOM broke callers.json parsing (fix: write
   BOM-free); `pythonw.exe` under Task Scheduler died with exit 1 and no
   output (fix: `python.exe` + redirect to `hearth/var/gateway-task.log`).
3. **The first local dispatch worked mechanically and failed epistemically.**
   qwen3-coder:30b drove 3 tools correctly through the gateway, then wrote a
   report claiming the repo was on "main" and describing HEARTH as an OS
   kernel. Full marks for the harness, poor marks for grounding — recorded on
   the ledger as the first real local-quality data point rather than lost as
   an anecdote.

## What we'd do differently

- **Session coordination before parallel orchestration.** Check for and
  announce concurrent sessions (a lock file, a ledger event, anything) before
  spawning builders. Until all actors are HEARTH callers, the two-HEARTHs
  incident can recur.
- **Ban `pythonw` and BOM-writing cmdlets in ops wrappers by convention** —
  both are now documented in the build notes, but convention beats memory.
- **Give the smoke a port allocator from the start.** Fixed well-known ports
  and leaked test servers are a known-bad combination.

## Next

- First **build-class** dispatch through the gateway — the one that can earn a
  capability (H4's done-when: `capability_count > 0` from real gateway events).
- H5 learned router once the ledger has enough frontier-vs-local rows.
- Migrate remaining fleet actors (conductor loops, Codex pours) to HEARTH
  caller keys so the single-writer invariant becomes true in practice, not
  just in architecture.
