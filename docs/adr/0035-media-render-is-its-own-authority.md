# 0035 — Rendering is its own authority: `media_render` is withheld from `operator`

**Status:** Accepted (2026-08-25); live through the door the same day

## Context

The BF6 highlights pipeline moved rendering off AM4's RTX 5070 and onto the two Arc Pro
B70s already sitting in OMEN (ADR-0034), reached through a new `media.render` operation on
HEARTH's Operations/Jobs plane. That raised two authority questions that the existing
taxonomy did not answer.

**1. Capability granularity is per TOOL, not per operation.** The obvious wiring was to let
the BF6 dispatcher call `submit_execution(operation="media.render")`. But `submit_execution`
carries the `execution` capability, and granting it hands over the whole execution
surface — `llm.chat`, `cancel_execution`, `get_execution_artifact`, the lot. A dispatcher
whose entire job is "render this clip" would gain the ability to spend metered inference.

**2. Media paths are not filesystem paths.** The render lane reads and writes under
`E:\BF6-Highlights`, which is outside `HEARTH_SCOPE` (`C:\work\commandcenter;C:\work`).
Appending the media root to `HEARTH_SCOPE` would work, and would also hand every
`unrestricted` and `builder` caller read/write access to the media volume for the sake of
one operation.

## Decision

**Rendering gets its own capability, its own tools, and its own path authority.**

- A new capability `media_render`, mapped to three dedicated tools (`submit_render`,
  `get_render_status`, `list_render_lanes`) rather than folded into `execution`.
- A new least-privilege profile `bf6-render` holding `media_render` and nothing else — no
  inference, no cancellation, no filesystem, no repository.
- A separate media authority domain (`hearth/toolsurface/_media_scope.py`) with its own
  root, borrowing `_scope`'s containment primitives but never reading `HEARTH_SCOPE`. It
  adds what `_scope` lacks: UNC refused as a category rather than incidentally, typed
  subtrees, and `raw/` refused for writes — reproducing on OMEN the read-only guarantee
  that AM4 gets structurally from its cifs mount.

**`operator` does NOT receive `media_render`.**

This is the first deliberate second exclusion from `operator`, and it changes a boundary
that `hearth/tests/kernel/test_profiles_v1.py` previously asserted as
"exactly `kernel_admin`". Rendering is not a read-only console action: it consumes real
workstation resources (a GPU media engine, sustained I/O against the media volume) and it
**writes promoted media** into the drafts tree that the review UI treats as authoritative.
Authority stays narrow until an actual operator workflow needs it; widening later is a
one-line profile change, while narrowing after the fact is a revocation.

## Consequences

- `operator` now withholds exactly `{kernel_admin, media_render}`. The test asserting the
  boundary was updated together with this ADR, which is the process its own docstring
  demands — a quiet edit to that assertion would have hidden a policy change.
- `unrestricted` gains `media_render`, as it must to keep covering the whole taxonomy.
- The BF6 dispatcher is minted against `bf6-render`, so the credential that reaches HEARTH
  can queue renders and read their status — and cannot spend a metered token.
- The media root is reachable by exactly one capability. Widening `HEARTH_SCOPE` remains
  unnecessary, and a future non-BF6 media surface gets its own root rather than inheriting
  this one.
- A gateway with no render subsystem configured **refuses** `media.render` at submit rather
  than queueing work nothing will drain.

## Alternatives considered

**Grant `execution` to the dispatcher.** Simplest wiring, rejected: it is a strictly larger
grant than the job requires, and the excess is precisely the expensive part of the system.

**Widen `HEARTH_SCOPE` to include the media root.** Rejected: it converts a
single-operation need into an ambient grant for every profiled caller, and the capability
taxonomy already models distinct resource models as distinct authority domains.

**Give `operator` `media_render` for symmetry with other operator tools.** Rejected as
above; revisit if and when a human operator workflow actually needs to trigger renders,
at which point it is a deliberate profile edit rather than an inherited default.
