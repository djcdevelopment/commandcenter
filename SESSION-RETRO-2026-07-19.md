# Session Retro — 2026-07-19 (the door learns to say no: authentication ≠ authorization)

> **We went to open a door for a container and found the lock had no tumblers** — the naive
> `0.0.0.0` flip was refused, a capability-profile authorization layer was built underneath it
> instead, and the exposure itself was left unmade behind a documented consent gate. The
> through-line: *a boundary you are about to move is the moment to find out what it was actually
> holding.*

## What this session was

A **security-build** session, triggered by an integration request and redirected by what the
integration exposed. Open Notebook — a Dockerized client — needs to reach HEARTH, and Docker
containers cannot reach a host service on `127.0.0.1`. The one-line fix (bind `0.0.0.0`) was
inspected before being applied, and the inspection found that HEARTH **authenticates but does not
authorize**: `AuthRegistry.resolve()` mapped an `X-Hearth-Key` to a `Caller` used *only* for
ledger attribution — no code path anywhere consulted it for access control. Every valid key held
the entire mounted surface: **47 tools across 16 providers**, verified live. On loopback that was
harmless. One bind change away, it was a container that could `read_file("hearth/var/callers.json")`
and walk off with every caller key including the frontier agent's.

So the session inverted itself: build the authorization concept HEARTH never had, *then* document
how to open the door — and stop short of opening it.

## What shipped

| Commit | What |
| --- | --- |
| `45e15a5` | ADR-0019 — container access via capability profiles + policy schema |
| `c8714a1` | Capability taxonomy + fail-closed profile loader |
| `9e6c876` | Authority-domain enforcement in auth, scope, and git |
| `70161ca` | Capability enforcement wired + explicit container bind mode |
| `9894a57` | `callerctl` — mint/rotate/revoke/list caller identities |
| `e2fd1fc` | Door health split into explicit facets (ADR-0020) |
| `9dba046` | Test — non-loopback access path |
| `9bb9263` | Docs — ratify the container access contract |
| `70ca913` | Test — container host routing |
| `41da33d` | Docs — phase 5 deployment preflight |
| `4542e04` | Docs — hearth deployment contract |
| `665548b` | Docs — open notebook hearth discovery plan |
| `edb3f50` | Policy — `generation-proxy` profile for inference-only components |

**Range:** `38fbbd5..edb3f50` — 13 commits, **+2372 / −52** across 23 files.
**Tests:** `590 passed, 38 subtests` in 38.6s (verified by running the suite during this retro;
was 527 last session).

**Durable artifacts:** [ADR-0019](docs/adr/0019-container-access-capability-profiles.md) (192
lines) · [ADR-0020](docs/adr/0020-phase-4-health-facets.md) ·
[profiles.toml](hearth/etc/profiles.toml) (policy as version-controlled config) ·
[callerctl.py](hearth/callers/callerctl.py) (496 lines) ·
[capabilities.py](hearth/kernel/capabilities.py) (300 lines) ·
[gateway-bindings.md](docs/operations/gateway-bindings.md) ·
[phase-5-deployment-preflight.md](docs/operations/phase-5-deployment-preflight.md) ·
[open-notebook-hearth-discovery.md](docs/plan/open-notebook-hearth-discovery.md) · the
[/checkmcp skill](.claude/skills/checkmcp/SKILL.md) updated in-session to match the new facet
semantics · this retro.

**Deliberately not shipped:** the gateway was **not restarted**, the bind change to container mode
remains **unmade and unconsented**, and the minted `docker-open-notebook-facade` key is **inert
until restart**. The implementation stops at a documented gate.

## The team retro — our collaboration across the seats

> *Caveat for every seat below: this retro is reconstructed from git, the artifacts, and live
> door/test queries — my context did not carry the working conversation (see Provenance). Seat
> reads are graded on evidence in the repo, not on remembered dialogue.*

**Architect.** The load-bearing call was refusing the requested change and building underneath it.
Everything downstream follows from taking "authenticates but does not authorize" seriously rather
than treating the bind as a networking task. The two-domain split is the sharpest piece of thinking
in the session: `filesystem` (`file_scope`) and `repository` (`repo_access`) are separate because a
repository is a *named resource*, not an ancestor path — git tools need exactly the repo root that a
narrowed file scope exists to deny, so neither domain can imply the other. And then the harder
follow-through: the domains are separate but **not independent**, because `repo_content` (`git_diff`)
renders the contents of files `file_scope` denies. `assert_authority_coherence` refuses that
combination outright, at gateway startup *and* at `callerctl mint`/`rotate` time, rather than
trusting whoever edits the policy to notice — which is precisely the by-convention containment the
ADR exists to replace. The design call I'd want re-examined is the legacy fail-open (below): correct
as a compatibility choice, but it is the one place the system's default is "allow".

**Implementer.** The code is unusually careful and the care is *legible* — the commit bodies argue
their own invariants. `callerctl` is the standout: CSPRNG secrets printed exactly once and never to
a log, backup, or `list`; the whole read-modify-write under an exclusive lock; atomic replace via
temp-file + fsync + `os.replace`; a timestamped, permission-locked backup taken *before*
modification so backups hold only prior secrets; unknown fields preserved verbatim so a newer
callerctl can add metadata without an older one dropping it; `rotate` installing new and dropping
old in the *same* replacement so there is no window where both keys work. Enforcement used
`contextvars` set/reset per call rather than extending the shared-`HearthContext` attribute pattern
— thread isolation verified, and a deliberate refusal to grow the known race. `_scope.py` went from
string-safe to filesystem-safe: component-boundary matching (`C:\work` no longer appears to contain
`C:\workshop`), reparse points resolved through the nearest *existing* ancestor with the
non-existent tail re-attached, dangling symlinks via `lexists`, revalidation per operation rather
than only at config load. No rework is visible in the history — 13 commits, no reverts, no fixups.

**Reviewer / QA.** Test posture is the strongest it has been: 590 passing plus 38 subtests, up 63
from last session, and the new tests are targeted at the boundary rather than at the happy path —
`test_callerctl`, `test_doorcheck`, `test_gateway_health`, `test_gateway_http`,
`container_access_smoke.py`, and a `test_scope` updated to *pin* the stricter `..` contract rather
than grandfather the old one. The fail-closed assertions are the review artifact worth keeping:
unknown tool for a profiled caller denied; a mounted tool with no capability mapping refusing
startup; an incoherent grant refusing startup; a non-loopback bind without
`HEARTH_CONTAINER_ACCESS_ENABLED=1` exiting non-zero; an *unresolvable hostname* counting as
non-loopback so a typo refuses rather than exposes. What slipped past the session's own review and
surfaced only in this retro: the offload projection's bucket-key wart (L-7), and the fact that none
of this has been exercised against a live gateway — 590 green unit tests and zero live proof are
different kinds of confidence, and the deployment gate is where that distinction is parked.

**Operator / SRE.** ADR-0020 is the operator's win: `doorcheck` now answers four independent facets
(`process_listener`, `authentication`, `mcp_surface`, `backend_dependency`) instead of one verdict,
because a cold inference backend must never read as "HEARTH unavailable" — and the `/checkmcp`
skill doc was updated *in the same session* as the exit-code semantics it documents (1 → 0 for
door-up/backend-cold), which is the kind of doc/behavior sync that usually rots for a release. The
`GET /healthz` route is unauthenticated by design and returns a static payload with no fleet
detail. Firewall scoping was documented and deliberately never automated, because Docker Desktop
does not guarantee a stable source subnet across restarts and the doc says so plainly instead of
pretending to precision the platform doesn't offer. An unplanned operational win: the first real
`callerctl mint` tightened the live registry's ACLs for the first time — the registry predates
ADR-0019 and had inherited permissions, which is why `list` had been *correctly* reporting
`registry_acl` as degraded until that write. The seat's real miss is mundane and serious: **all 13
commits sit on a local branch with no remote tracking branch and no push.** 2372 lines of security
work exist in exactly one place on one disk. Last session ended "8 commits, all pushed."

**Product / planning.** The right thing got built, and the scope discipline is the reason. The ask
was "let a container reach HEARTH"; the delivery is an authorization layer plus a documented
deployment procedure plus an explicit refusal to execute it — larger than the ask in depth, but not
one inch wider in surface. `builder` and `orchestrator` profiles are *defined* as the growth path
and deliberately left unassigned in v1. The `generation-proxy` profile (`edb3f50`) is the same
instinct at finer grain: the Open Notebook facade calls exactly one tool, so it got a role sized to
two tools of forty-seven rather than the twelve `research` would have handed it — and the commit
argues explicitly that a proxy is *not* a sub-role of an investigator, so if Open Notebook later
speaks MCP directly that is a separate identity. The discovery plan
(`open-notebook-hearth-discovery.md`) forbids choosing an integration mechanism until all six phase
evidence gates pass — planning that refuses to pre-commit. Pacing held to the doctrine that matters
most here: the last irreversible, outward-facing step was left on Derek's desk.

### Two seats, two views

**From Claude's seat.** The judgment worth keeping is the one made at the very top: treating a
one-line config change as a question rather than a task. Nothing else in the session happens if the
bind gets flipped first and inspected later. The pattern generalizes — *the moment you move a
boundary is the moment to audit what it was silently holding* — and it is the second session in a
row where the win came from refusing the fast path (last session: refusing to trust the resident
brain before characterizing it). My own miss this retro was small and instructive: I passed
`max_tokens=4000` to a thinking rung and got a draft clipped mid-sentence, on the call immediately
after reading a CLAUDE.md that says omit `max_tokens` on the gemini rungs and an ADR that explains
why. Lesson L-2026-07-18-2 was about deriving budgets from measured physics; I had the physics
written down and still burned the call. The retry with the budget omitted came back clean. What I
want next time: the working conversation. This retro was rebuilt almost entirely from commit
messages that happened to carry their own reasoning — that worked, and it worked *because* the
commits were written that way, which is not something to rely on twice.

**From Derek's seat** *(my reconstruction from stated preferences and the shape of the work — I had
no conversational signal this session, so correct me freely).* "Good. You found the thing I would
have found three weeks later, in production, when a container did something I couldn't explain. I
don't want the door opened without me — you stopped in the right place, and the preflight reads like
something I can actually execute in one sitting instead of three interruptions. Two things: get that
branch pushed, because right now a dead SSD costs me the whole night's work; and tell me plainly
what's still fail-open, because 'legacy callers keep everything' is the kind of sentence that
disappears into a doc and resurfaces as an incident."

## Last time's lessons — follow-through (2026-07-18)

| Lesson | Status |
| --- | --- |
| L-2026-07-18-1 — Closed receipts are immutable; successor receipt after closure | **not exercised** — the build-request lane wasn't used this session |
| L-2026-07-18-2 — Derive dispatch budgets from the physics you already measured | **pending** — violated in *this retro*: `max_tokens=4000` on a thinking rung clipped the draft mid-sentence; retry with it omitted was clean |
| L-2026-07-18-3 — Servers may silently degrade rather than refuse | **acted-on (generalized)** — the same principle now governs binds: a non-loopback bind without consent *refuses* rather than quietly falling back to loopback |
| L-2026-07-18-4 — A resident tenant needs its own occupancy semantics | **acted-on (generalized)** — [ADR-0020](docs/adr/0020-phase-4-health-facets.md): one component's state must not stand in for another's; cold backend ≠ door down |
| L-2026-07-18-5 — Batch the root-gated asks | **acted-on** — [phase-5-deployment-preflight.md](docs/operations/phase-5-deployment-preflight.md) batches subnet-confirm + firewall rule + env vars + restart into ONE operator window, as a document |
| L-2026-07-18-6 — Task-shape doctrine for the two drafting rungs | **partially acted-on** — pro rung used correctly for both the doc condense and the role-reads; the "omit `max_tokens`" half was violated once (see L-2 above) |
| L-2026-07-18-7 — Pressure-test the architecture the day it lands | **pending** — 590 green unit tests, zero live proof; the gateway is unrestarted and `container_access_smoke.py` has not run against a live door |
| L-2026-07-18-8 — Local drafts of self-referential prose invert causality | **acted-on** — an explicit anti-inversion rule was written into the offload prompt; draft graded **minor-fixes** vs last session's **hallucinated** |
| L-2026-07-18-9 — New-rung onboarding is a checklist, not a vibe | **acted-on** — the `am4-moe: sunk` cost-class fix is live; `offload_ratio` now **1.0** (was 0.69 reported) |

## Lessons learned

1. **L-2026-07-19-1 — Authentication is not authorization, and loopback was hiding the
   difference.** A resolved caller used only for ledger attribution *looks* like an access-control
   system until you read the call graph. Harmless at `127.0.0.1`; a 47-tool blast radius one bind
   change away. *(→ [ADR-0019](docs/adr/0019-container-access-capability-profiles.md), done)*
2. **L-2026-07-19-2 — A service that binds narrower than asked must refuse, not fall back.** Silent
   fallback to loopback is operationally indistinguishable from a container networking fault, and
   that ambiguity costs hours. Fail-closed extends to detection itself: an unresolvable hostname
   counts as non-loopback, so a typo refuses rather than exposes. *(→ ADR-0019 + practice)*
3. **L-2026-07-19-3 — Separate authority domains are not independent authority domains.**
   `repo_content` launders around a narrowed `file_scope`. Coherence has to be machine-refused at
   startup *and* at mint time; leaving it to the policy author's attention rebuilds the
   by-convention containment you just removed. *(→ ADR-0019, done)*
4. **L-2026-07-19-4 — Ship the mechanism, gate the exposure.** Building the full capability system
   and then *not* restarting the gateway is the correct shape for anything outward-facing: the
   irreversible, owner-visible step stays on the owner's desk with a one-sitting runbook.
   *(→ practice)*
5. **L-2026-07-19-5 — A session's entire output on one unpushed branch is a single point of
   failure.** 13 commits, 2372 lines, no remote tracking branch. Push is not a ceremony; it is the
   second copy. *(→ practice + DECISIONS-PENDING)*
6. **L-2026-07-19-6 — Backward-compatible fail-open is a debt that needs a name and a deadline.**
   Profile-less legacy callers keep all 47 tools. The gateway warns about them by name at startup,
   which is the right instrumentation — but the window stays open until every caller is migrated,
   and nothing currently *closes* it. *(→ DECISIONS-PENDING)*
7. **L-2026-07-19-7 — A projection's bucket key is a schema; changing its shape silently splits
   history.** 182 of 229 lifetime offload calls sit in legacy `model:<name>`-shaped buckets carrying
   zero token counts, so `est_usd_saved` is computed from only 47 calls' worth of structured data
   and undercounts. The ratio reads healthy while most of the corpus contributes nothing.
   *(→ doc + candidate projection fix)*
8. **L-2026-07-19-8 — Commit bodies that carry their *why* are the retro's backup context.** This
   entire retrospective was reconstructed without the working conversation, and it was possible only
   because the commits argued their own invariants rather than naming their own diffs. That is a
   habit to keep deliberately, not a lucky accident. *(→ practice)*

## Provenance

Git range: **`38fbbd5..edb3f50`** (13 commits, **unpushed**, no remote tracking branch). **This
retro is a git-derived reconstruction** — my context began at the `/retro` invocation and did not
carry the working session, so the conversation arc, dead-ends, and any corrections Derek made are
*not* represented; seat reads are graded on repo evidence only, and Derek's seat is a reconstruction
from stated preferences rather than from session signals. Offloaded to HEARTH: the artifact condense
(`gcp-gemini-pro`, 26.7s, 8546 in / 1304 out, six files packed door-side — **edit_verdict:
faithful**, one precision fix: it read the `HearthContext.caller` race as covering authorization
when enforcement is contextvar-isolated and only ledger attribution is affected) and the five role-read
first passes (`gcp-gemini-pro`, 56.6s, 2536 in / 1690 out — **edit_verdict: minor-fixes**;
misattributed this retro's own findings to the session's QA, and over-dramatized two items; structure
and technical content kept, prose rewritten frontier). One call was burned first on a clipped
`max_tokens=4000` draft (thinking rung; retried once with the budget omitted, per the door's
documented contract). Frontier: factsheet assembly, git/test verification, all synthesis, lesson
grading, and every file written here. Test suite run live during this retro: 590 passed + 38
subtests. `--fleet` not used; no cc-conductor writes. No new ADR was needed — the session wrote
[0019](docs/adr/0019-container-access-capability-profiles.md) and
[0020](docs/adr/0020-phase-4-health-facets.md) itself and indexed both.

## Offload scorecard (S6)

Projection refreshed during this retro (watermark `2026-07-20T05:22Z`). Lifetime through the door:
**229 calls, 336k in / 64k out, offload_ratio 1.0, est. $1.97 saved** (claude-sonnet reference).
Per class: **sunk** 44 calls (40k in / 22.6k out), **trial** 180 calls (296k in / 41.2k out),
**unknown** 5 calls. Last retro's `am4-moe: unknown` fix is live — the rung now buckets as `sunk`
(14 calls, ok_rate 1.0) and the ratio corrected 0.69 → **1.0**, as predicted. Trial burn 337.7k of
the 200M runway (**0.17%**). Caveat per L-7: 182 of the 229 calls sit in legacy `model:<name>`
buckets with zero recorded tokens, so `est_usd_saved` reflects 47 calls' structured data and is a
floor, not a total.
