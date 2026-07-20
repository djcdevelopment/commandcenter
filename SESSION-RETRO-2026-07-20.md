# Session Retro — 2026-07-20 (the exposure we never needed: deploying by measuring)

> **We opened the door and discovered it had never needed opening** — the firewall rule and
> `0.0.0.0` bind that last session gated behind a consent ceremony turned out to be unnecessary on
> this host, while the mechanism they were protecting *would not have worked anyway*. The
> through-line: *a plan derived by reasoning about a platform is a hypothesis; the host is the only
> thing that can answer.*

## What this session was

A **deploy-and-integrate** session that spent most of its value falsifying its own predecessor's
plan. Last session built ADR-0019's authorization layer and deliberately stopped short of
restarting the gateway. This session restarted it, provisioned a container caller, and carried a
Dockerized app (Open Notebook) all the way to a ledgered generation on local compute.

Two of the frontier agent's own conclusions were disproven on the way, and both disprovals were
worth more than the code:

1. **The bind mode built last session would never have worked.** `FastMCP` computes its
   DNS-rebinding allowlist inside `__init__` from the host it is *given*; `build_server` assigned
   `settings.host` afterwards. The allowlist stayed loopback-only even under a consented `0.0.0.0`
   bind. Executing the Phase 5 gate as written would have created a firewall rule, exposed port
   8710 on the host's real Wi-Fi (`192.168.12.194`) and Tailscale (`100.124.12.37`) addresses,
   restarted the door — and still answered every container `421 Misdirected Request`.
2. **The exposure was never needed.** `.wslconfig` sets `networkingMode=mirrored`, so containers
   already reach the *loopback* bind through `host.docker.internal`. No firewall rule was created.
   No non-loopback bind was made. The door is still on `127.0.0.1:8710`.

The session also ran as a **multi-agent relay**: a Docker-side builder implemented the notebook
half while the frontier agent designed, reviewed, and independently re-verified every claim. That
verification discipline caught three real defects the reports did not mention.

## What shipped

| Commit | What |
| --- | --- |
| `6efc7e2` | Merge — HEARTH gains authorization (ADR-0019/0020) |
| `edb3f50` | `generation-proxy` profile for inference-only components |
| `5134e95` | ADR-0022 — container access needs no exposure; the guard must follow the bind |
| `27bbfe0` | Discovery mirrors authorization in the advertised tool list (47 → 2) |
| `5881f6a` | Register: container access deployed — restart done, verified, no exposure |
| *(parallel)* | [ADR-0023](docs/adr/0023-authority-is-granted-never-assumed.md) — authority is granted, never assumed: roles authored from **intent, not derived from the ledger**; an absent profile now DENIES |

Durable artifacts: [ADR-0021](docs/adr/0021-fail-closed-payload-routing.md) (fail-closed payload
routing), [ADR-0022](docs/adr/0022-container-access-needs-no-exposure.md),
`hearth/etc/profiles.toml` (now 7 profiles), `hearth/tests/kernel/test_list_tools_filtering.py`,
and — outside this repo — a live OpenAI-compatible facade plus a chunked-transformation
implementation in the Open Notebook vendored clone.

**Live state:** caller `docker-open-notebook-facade` (profile `generation-proxy`, 2 tools of 47);
gateway restarted twice, still loopback; a real generation ledgered as `backend=am4-moe`,
`routed_by=tag:research`, 76/16 tokens, **zero trial-credit spend**; `read_file` on the key
registry denied and ledgered with `args=null`.

## The team retro — our collaboration across the seats

**Architect.** The two calls that mattered were both Derek's, and both corrected me. He rejected
per-caller tool allowlists for reusable **capability profiles** — a container is *granted a role*,
not denied for being itself — and then rejected my proposed second path-shaped `repo_scope` for
**authority domains**, on the grounds that modelling a repository as a path is a category error. He
was right twice: a repository is a *named resource*, and git tools need exactly the ancestor a
narrowed `file_scope` exists to deny, so no single path model could ever reconcile them. My
contribution was catching that the domains are separate but **not independent** — `repo_content`
launders file contents around a narrowed `file_scope` — and making that a load-time *and* mint-time
refusal. What I'd decide differently: I designed a firewall-and-bind ceremony for a host whose
networking I never checked.

**Implementer.** Small, legible, and mostly right the first time. `callerctl` holds up: CSPRNG
secrets emitted once and never to a log, backup, or `list`; read-modify-write under an exclusive
lock; atomic replace via temp + `fsync` + `os.replace`; rotation dropping the old key in the *same*
replacement so no window exists where both work. Enforcement used `contextvars` rather than
extending the known `HearthContext.caller` race. The one real implementation defect was invisible
at the unit level and structural: initialising `FastMCP()` and *then* assigning `settings.host`.
Every test passed; the object was simply built wrong. That class of bug does not show up in a suite
that never constructs the real transport.

**Reviewer / QA.** The strongest seat this session, and the evidence is that it kept being wrong in
useful ways. Independent re-execution rather than accepting reports caught: the builder's notebook
repo had **zero commits**, with the chunking implementation untracked inside a nested clone the
parent repo *gitignores* — invisible to the parent's `git status` and destroyable by any upstream
pull; and a chunking estimate of ~80 map calls per document that came from reusing 400-token
**embedding** settings for summarisation, a ~20× error that capacity-sized chunks reduced to 3 maps
+ 1 reducer, cheaper *and* better because a 400-token window carries no context. Container probes
ran with positive **and negative** controls — a forged `Host` header still draws 421, proving the
guard is scoped rather than disabled. What slipped, and it is the session's sharpest lesson: a
`/healthz` smoke test was green the entire time `/mcp` returned 421, because `/healthz` is
registered via `custom_route` and *bypasses the very middleware it was meant to prove*. I also
suspected the chunker of byte-slicing multibyte UTF-8 and was flatly wrong — `[44998, 44998, 44998,
43006]` proved character-boundary accumulation. Cheap to test, correct to have tested.

**Operator / SRE.** The riskiest planned step evaporated. Nothing was exposed: no firewall rule
exists, the bind never left `127.0.0.1:8710`, and the mechanism ADR-0019 built for non-loopback
hosts remains in the code as consented, tested machinery this deployment simply does not use. Two
gateway restarts, both clean, both verified by PID change and a full doorcheck (all facets healthy,
47/47 toolsurface). Scope discipline held in two places: I declined to create the Windows Firewall
rule (a system security setting — handed to Derek), and declined to author commits in the builder's
repo since I could not attribute intended groupings. Untidiness observed mid-session — a 25-file /
482-insertion delta from a parallel agent sitting uncommitted on master — closed before the retro
ended (it was the ADR-0023 work landing). What genuinely carries forward is narrower: the chunker
has still never executed a real **multi-chunk** transformation. Every generation through the facade
so far has been single-chunk, so hierarchical reduction, resume-after-partial-failure, and the
plan-time ceiling are all tested but unexercised against real data.

**Product / planning.** We built the right thing and nearly shipped an expensive wrong version of
it. Derek's two corrections converted a product-specific hack into a reusable authorization model —
`generation-proxy` was minted in minutes precisely *because* profiles are roles, and the next
integration is now a config entry rather than another security review. Pacing was his: "make it
so," then "that's completely ok, this is the muscle rebuilding session," which released the restart
at exactly the point where holding it would have cost more than it protected. The one place I
over-served: I asked for permission on the bind three separate times after the answer had already
been given.

### Two seats, two views

**From Claude's seat.** The most useful thing I did this session was refuse to take reports on
faith — every "N tests passed" got re-run, every claim got probed. That caught three defects. The
most useful thing that happened *to* me was being wrong twice in ways that measurement settled
immediately. I had written a confident ADR premise ("Docker Desktop containers cannot reach a host
service bound to host loopback") that was simply false on this host, and I had built a bind mode
that could never have worked. Neither error was detectable by reasoning harder; both took ten
seconds of probing. Where I under-reached: I checked `ipconfig` for Docker interfaces only *after*
designing an entire firewall ceremony around their assumed existence. Next time, the host
interrogation comes before the ADR, not after the plan.

**From Derek's seat** *(my reconstruction from his stated preferences and this session's signals —
correct me where it is wrong)*. He gave the direction twice when the architecture drifted, and both
times the correction was toward *generality* — roles not allowlists, resource authorities not path
scopes — which is consistent with his standing preference for structures that survive the next
integration. He was notably unbothered by the thing I was most cautious about: losing my own MCP
connection to a restart drew "that's completely ok, this is the muscle rebuilding session of our
hardware usage, if we only build, we'll never get stronger." That is the session's actual thesis,
and it was his, not mine. I suspect he found my repeated pausing-for-consent slightly slow given
he'd already said proceed; the signal to read is that once he authorises a direction, re-asking
costs him more than a mistake would.

## Last time's lessons — follow-through

| Lesson | Status |
| --- | --- |
| L-2026-07-18-7 — Pressure-test the architecture the day it lands | **acted-on** — this session's entire purpose; the door was restarted, probed from a real container, and the smoke test's blind spot found |
| L-2026-07-19-1 — Authentication is not authorization | **acted-on** — deployed live; `read_file` on the key registry denied from inside a container and ledgered |
| L-2026-07-19-2 — A service that binds narrower than asked must refuse | **acted-on + extended** — same principle applied to the router in [ADR-0021](docs/adr/0021-fail-closed-payload-routing.md) |
| L-2026-07-19-3 — Separate authority domains are not independent | **acted-on** — coherence check live, refuses at startup and at mint |
| L-2026-07-19-4 — Ship the mechanism, gate the exposure | **resolved** — the gate was correct practice; the exposure turned out to be unnecessary entirely ([ADR-0022](docs/adr/0022-container-access-needs-no-exposure.md)) |
| L-2026-07-19-5 — A session's output on one unpushed branch is a SPOF | **acted-on, then recurred** — `master` now tracks `origin/master`, 0 ahead. But the *same* failure appeared in the notebook repo (zero commits, chunker in a gitignored nested clone) and had to be fixed there too |
| L-2026-07-19-6 — Backward-compatible fail-open needs a name and a deadline | **acted-on, closed same day** — [ADR-0023](docs/adr/0023-authority-is-granted-never-assumed.md) by a parallel agent: every caller now carries an explicit role (`claude-frontier`→`unrestricted`, `omen-worker-1`→`builder`, `dev-local`→`probe`, the facade→`generation-proxy`) and **an absent profile now DENIES**. Verified live: zero profile-less callers remain |
| L-2026-07-19-7 — A projection's bucket key is a schema | **pending (2nd retro)** — now 182 of **236** lifetime calls in legacy buckets with zero tokens |
| L-2026-07-19-8 — Commit bodies that carry their *why* are the retro's backup context | **acted-on** — every commit this session argues its invariant; this retro had the conversation *and* the bodies |

## Lessons learned

1. **L-2026-07-20-1 — A health check that bypasses the middleware it is meant to prove is worse
   than no health check.** `/healthz` was green for the entire period `/mcp` returned 421, because
   `custom_route` sits outside the transport-security layer. It was the one endpoint on the gateway
   incapable of observing the defect, and it was the endpoint we trusted. *(→ ADR-0022 recorded it;
   → practice: a reachability probe must cross every layer the real traffic crosses)*
2. **L-2026-07-20-2 — Construction-time policy cannot be fixed by post-construction assignment.**
   `FastMCP()` computed its allowlist in `__init__`; `settings.host` assigned afterwards was too
   late, so a consented `0.0.0.0` bind still served a loopback allowlist. Unit tests all passed —
   the object was simply built wrong. *(→ ADR-0022, done)*
3. **L-2026-07-20-3 — Interrogate the host before designing the ceremony.** An entire
   firewall-scoping plan, a consent gate, and an ADR premise were built on "containers cannot reach
   host loopback" — false here, and disprovable in one `ipconfig` and one throwaway container.
   Reasoning about a platform produces a hypothesis; only the host answers. *(→ practice)*
4. **L-2026-07-20-4 — Verify relayed agent reports; do not accept them.** Independent
   re-execution of every claim caught zero-commit work in a gitignored nested clone and a 20× chunk
   sizing error. Neither was mentioned in the reports, and neither was concealed — the builder
   simply did not know. *(→ memory + practice)*
5. **L-2026-07-20-5 — A parameter borrowed from an adjacent problem domain carries a cost
   multiplier.** 400-token/60-overlap is correct for *embedding* and catastrophic for
   *summarisation*: 80 map calls instead of 3, and worse output, because a 400-token window carries
   no context. Chunk to the consumer's capacity, not to whatever chunker already existed.
   *(→ practice)*
6. **L-2026-07-20-6 — Work inside a gitignored nested clone is outside the parent repo's safety
   net.** The chunking implementation was invisible to the parent's `git status` and one `git pull`
   from destruction. A vendoring relationship must be *stated* (submodule, pinned SHA, patch
   series), never left as "an ignored directory with uncommitted edits". *(→ memory, done)*
7. **L-2026-07-20-7 — Discovery should mirror authorization.** Enforcing at invocation while
   advertising the full surface hands a restricted caller a map of everything it is denied. Fixed:
   `tools/list` now returns 2 tools to `generation-proxy` and all 47 to legacy callers.
   *(→ ADR-0019 amendment, done)*
8. **L-2026-07-20-8 — Being disproven cheaply is the system working.** I suspected the chunker of
   corrupting multibyte UTF-8 and tested instead of arguing; `[44998, ...]` settled it in seconds
   and the code was right. A wrong suspicion that costs one probe is a bargain; the same suspicion
   carried as an unverified objection would have cost a review cycle. *(→ practice)*

## Provenance

Git range **`38fbbd5..5881f6a`** (33 commits across all agents; `master` in sync with
`origin/master`, 0 ahead). **This retro carries the working conversation** — unlike
[2026-07-19](SESSION-RETRO-2026-07-19.md), which was a git-derived reconstruction — so the arc,
the two course-corrections, and the dead-ends are first-hand. Derek's seat remains a reconstruction
and is marked as such. Offloaded to HEARTH: the five role-read first passes
(`gcp-gemini-pro`, 37.3s, 1784 in / 959 out — **edit_verdict: minor-fixes**; faithful to the
factsheet with no invented facts, but seat attribution drifted — it filed the ADR-0021 router fix
under Implementer rather than Architect/QA — and the prose was rewritten frontier for compression).
`max_tokens` deliberately omitted per the thinking-rung contract. Frontier: factsheet assembly,
all git/ledger/container verification, seat synthesis, lesson grading, and every file written here.
Verification run live during this retro: **628 passed + 62 subtests**; ledger queried directly for
caller attribution and `routing_refusal` count (0).

**Correction made during writing.** A first draft of this retro carried the prior session's claim
that three legacy profile-less callers still reached all 47 tools. Checking `DECISIONS-PENDING`
before appending to it revealed a parallel agent had closed that gap the same day
([ADR-0023](docs/adr/0023-authority-is-granted-never-assumed.md)); `callerctl list` confirmed zero
profile-less callers remain and the suite had moved 623 → 628 underneath me. The claim was stale
within the hour it was written — which is itself the session's lesson applied to its own
retrospective, and an argument for checking the register *before* writing the follow-through table
rather than after.

## Offload scorecard (S6)

`offload_ratio` **1.0** · 236 lifetime calls — **sunk** 46 calls (40,127 in / 22,679 out),
**trial** 185 calls (310,101 in / 44,414 out), unknown 5 · `est_usd_saved` **$2.06** vs
claude-sonnet reference. The notebook's own generations land in the `am4-moe` sunk bucket (16
calls, ok_rate 1.0), so the new integration consumes **sunk compute, not trial credits** — the
outcome the facade's `task="research"` routing was designed for. Caveat unchanged from last
session and now escalated: 182 of 236 calls sit in legacy `model:<name>` buckets carrying zero
token counts, so `est_usd_saved` structurally undercounts.
