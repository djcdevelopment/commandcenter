# 0023 — Authority is granted by naming a role, never by omitting one

**Status:** Accepted (2026-07-20) — closes the fail-open left open by
[0019](0019-container-access-capability-profiles.md) §3/§6. Extends
[0019](0019-container-access-capability-profiles.md); does not supersede it.

## Context

ADR-0019 built capability profiles but kept a compatibility path: a caller
record with no `profile` field reached the entire mounted surface. Three callers
depended on that silence — `claude-frontier`, `dev-local`, `omen-worker-1` — and
the gateway named them at every startup. Nothing closed the window.

The obvious way to close it is to derive each caller's role from what the ledger
shows it calling. **That was rejected.** The owner's judgement on the evidence:
the network had been used opportunistically to build other things, so the
observed caller→tool history records how the door happened to be reached, not
what any identity is *for*. Deriving roles from it would encode improvisation as
policy and then enforce it. (The same ledger remains sound for offload
economics, which measure tokens per backend, not intent.)

So roles here are **authored from intent** and deliberately generous. The clean,
attributable dataset that would justify tightening does not exist yet; it starts
accruing the moment every call carries a declared role.

## Decision

**1. An absent `profile` means DENIED, not permitted.** `check_tool_access`
returns False for a caller with no profile. Authority is granted by naming a
role; "nobody said" reads as "no". A caller that genuinely needs everything
carries the `unrestricted` profile, which is reviewable in a diff and dated in
policy. Breadth was never the defect — silence was.

**2. Denial is per-caller, not per-door.** An unprofiled caller is loaded and
denied, with a loud startup warning naming it. It is deliberately NOT a startup
refusal: one malformed registry row must not take the whole lab's door down
while every other caller is fine. This differs from the startup refusals ADR-0019
uses for *policy authoring* errors (unknown capability, incoherent grant), which
are conditions no caller can work around.

**3. `callerctl assign` changes a role without rotating the secret.** Previously
`rotate` was the only way to set a profile, so every authorization decision cost
a credential change: re-key the client, update every config holding it, and
accept a window where a live agent cannot reach the door. That is a strong
incentive to leave authorization alone — exactly backwards. Assignment preserves
the secret byte-for-byte and re-checks authority coherence against the new
grants.

**4. `--legacy-unrestricted` is removed.** It used to create a profile-less
caller, which meant full authority; under this ADR the same flag would silently
mint a caller that can do nothing. A flag whose meaning inverts is worse than a
flag that is gone, so it errors and points at `--profile unrestricted`.

**5. The ledger label changed with the semantics.** `LEGACY_PROFILE` is now
`"unprofiled"`, not `"legacy-unrestricted"`. Events keep the label they were
written with, so the string dates the policy in force at the time of the call.
Old events are not retro-labelled.

## The v1 roster

| Caller | Role | Reach | Intent |
|---|---|---:|---|
| `claude-frontier` | `unrestricted` | 47/47 | The frontier operator/architect. Holds everything, on purpose, with a review date. |
| `omen-worker-1` | `builder` | 21/47 | A fleet builder node: reads, writes, tests, commits, queues work. |
| `dev-local` | `probe` | 1/47 | See below — its secret is public. |
| `docker-open-notebook-facade` | `generation-proxy` | 2/47 | Unchanged (ADR-0019). |

`operator` and `probe` are new. `operator` (46/47) is every kind of *work* and
withholds exactly `kernel_admin`: an operator acts **through** the door, it does
not reconfigure the door mid-flight. It is currently unassigned.

**`dev-local` is a public secret.** Its key is the literal string `dev-local`,
checked into git at `hearth/etc/callers.json` and identical in the live registry.
It was briefly assigned `operator` during this work; that was wrong and was
corrected the same session. A guessable key gets `probe` — `kernel_status`, one
tool. If a hands-on operator identity is wanted, mint a separate caller with a
real CSPRNG secret and assign `operator`; do not widen this one.

## Consequences

- **Discovery narrowed with it, and broke doorcheck's staleness check.** Tool
  discovery mirrors authorization, so the manifest a caller receives is its
  granted subset. doorcheck compared that against the full mounted surface and
  reported its own restrictions as a STALE door — `mcp_surface: degraded`,
  permanently, the moment its probe identity stopped being unrestricted. A health
  check that is always degraded is worse than none.
  - The manifest comparison is now scoped to the calling profile.
  - The staleness signal that must **not** narrow — did a provider fail to
    import? — moved to the mounted-provider list from `kernel_status`, which is
    server-side truth and unfiltered. A narrow caller can still detect the
    2026-07-12 incident shape.
  - If policy is unreadable, doorcheck reports the *wider* set and says so.
    Fail-open is correct there and only there: under-reporting would hide a
    genuinely stale door behind an apparent restriction.
- `--probe-cloud` requires `generate`, which `probe` does not hold. That flag now
  needs a properly-secured key.
- `[profile.unrestricted]` is written out capability-by-capability rather than
  wildcarded, so widening the taxonomy cannot silently widen the role. The same
  property means a new capability would silently *narrow* it, first surfacing as
  the frontier operator being denied a tool it just added — so
  `test_profiles_v1` asserts the role stays complete. A new capability fails a
  test instead of surprising an operator.
- Test fixtures that wrapped synthetic tools relied on the profile-less
  allow-everything path. They now map their fixtures into the capability
  taxonomy, which models the production guarantee (`assert_surface_complete`
  refuses to mount an unmapped tool) rather than a hole in it.
- **What this does not do: reduce blast radius.** `claude-frontier` still reaches
  47/47 and `dev-local`'s secret is still guessable. What changed is that both
  are now authored facts with owners and dates rather than accidents. Tightening
  is a separate, evidence-led decision — see below.

## Review

**By 2026-10-20**, with `profile`-attributed events from intentional usage:
decide whether `unrestricted` has earned a narrower role, and whether `builder`
should keep `dispatch` (a worker node that can queue fleet work is defensible
but was not separately argued). If a role proves too tight to flip safely, build
report-only enforcement — log would-denies without blocking — rather than
guessing again.
