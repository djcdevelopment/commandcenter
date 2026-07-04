# ADR-0003 — A CCMETA `builders` allow-list overrides `exclude_from_build_pool`

**Status:** Accepted (2026-07-04) — applied to the live conductor (`63935ee`)
**Context sources:** `docs/conductor-pour-howto.md`, pour-c2 (2026-07-03),
`CONDUCTOR-FOLLOWUPS-2026-07-04.md` (finding #1).

## Context

`conductor_maf.py`'s `load_nodes()` builds the ready set as
`"worker" in roles and not exclude_from_build_pool`. A per-run CCMETA `builders` allow-list
was then *intersected* with that already-trimmed set by `_select_builders()`. So a node
carrying `exclude_from_build_pool: true` (e.g. `cc-builder-4`, an overnight MoE critic, or
`claudefarm1`) could **never** be opted into a single run — the daemon logged
`requested builders missing from ready set` and silently proceeded without it.

This bit cc-builder-4's mixtral debut (pour-c2): it was explicitly requested but silently
dropped. The only workaround was to hand-edit `fleet.json` (flip the flag and flip it back),
which is exactly the kind of manual step the fleet is meant to avoid.

## Decision

**An explicit CCMETA `builders` allow-list overrides `exclude_from_build_pool`.**
`_select_builders()` re-admits a node that was trimmed *only* by the exclusion flag, but
only when it is (a) explicitly named in the allow-list, (b) `mcp_ready` with the literal
`worker` role, and (c) passes a live `_ssh_healthy` probe. The **default** pool is
unchanged: with no allow-list, excluded nodes stay excluded.

## Consequences

- `exclude_from_build_pool` now means "not in the *default* critical-path pool" rather than
  "unreachable forever." An operator can dispatch a one-off run to an excluded node via
  `{"builders": ["<node>"]}` without editing `fleet.json`.
- Edge case (documented in the code + follow-ups): the override can re-admit a node even if
  it is the assay node — acceptable, because it's an explicit operator choice.
- `docs/conductor-pour-howto.md` §"dispatch_pool" was corrected: the previous claim that
  there is "NO way to opt an excluded node into a single run" is now false.
- Low blast radius — additive, guarded by a health probe, default path untouched. Applied
  live and the conductor restarted clean.
