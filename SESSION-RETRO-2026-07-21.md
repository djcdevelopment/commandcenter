# Session Retro — 2026-07-21 (a memory said "works anywhere"; it didn't)

> **The bug and the false-negative were the same claim.** `reference-user-level-skill-junctions.md`
> asserted "checkmcp/checkmechnet run repo scripts by absolute path, so they work anywhere" — written
> ten days ago and never tested from outside `C:\work\commandcenter`. Today it was: Derek ran
> `/checkmcp` from a new repo and hit `ModuleNotFoundError`. The through-line: *an absolute path to
> the interpreter is not the same claim as an absolute path to the module* — `python -m` resolves
> against process cwd, not exe location, and nothing had ever exercised that distinction.

## What this session was

A narrow **diagnose-and-fix** session, small on purpose: one broken assumption, traced to its root,
fixed at the two places it was wrong (the skill docs and the stale memory that had certified them),
verified live in both shells, shipped straight to master per the docs-go-to-master standing rule.

## What shipped

| Commit | What |
| --- | --- |
| `c67aee3` | `fix(skills): checkmcp/checkmechnet run from any repo, not just commandcenter` |

Durable artifacts: [`.claude/skills/checkmcp/SKILL.md`](.claude/skills/checkmcp/SKILL.md) and
[`.claude/skills/checkmechnet/SKILL.md`](.claude/skills/checkmechnet/SKILL.md) now invoke
`doorcheck`/`fleet_ping` with `PYTHONPATH=C:\work\commandcenter` set explicitly instead of relying on
caller cwd; corrected memory
[`reference-user-level-skill-junctions.md`](C:\Users\derek\.claude\projects\C--work-commandcenter\memory\reference-user-level-skill-junctions.md).

**Incidental to this session but worth recording honestly:** `git push` also flushed **6
pre-existing local commits from an earlier/other session** (`9b19b3e`..`6b036dc` — the ADR-0024
gateway-liveness work: unstartable-door logging fix, external watchdog, S4U conversion, plus two
`docs(register)` chunker-verification entries) that had sat committed-but-unpushed on this checkout.
Not authored this session; only pushed by it. See Operator/SRE below.

## The team retro — our collaboration across the seats

Most seats have little to report — this was a single, well-bounded fix, not a design session — so
each note below is honest about scope rather than padded.

**Architect.** No architectural call was made or needed. The only design-adjacent decision was
scope: fix the two SKILL.md files and the memory that certified the old (wrong) claim, and stop
there — not touch the venv, not add a `pyproject.toml`/editable install for `hearth`/`fleet` (CLAUDE.md:
don't build for hypothetical future requirements when `PYTHONPATH` fully solves the stated problem).

**Implementer.** Two-line root cause, two-file fix, ~15 minutes wall clock. The only real work was
proving the fix rather than asserting it: reproduced the failure from `C:\work` as cwd, confirmed
`PYTHONPATH` (not `cd`, not a wrapper script) closes the gap without side-effecting shell state, then
verified the *exact* shipped command in both bash and PowerShell before writing it into either
SKILL.md.

**Reviewer / QA.** No test suite touches markdown skill instructions, so verification had to be
live execution, not assertion: ran `doorcheck --facet door` and `fleet.fleet_ping --all-services`
from `C:\work` (outside any repo) in both shells and read full output, not just exit code. Caught
that PowerShell's `git` doorcheck output needed the same treatment as bash's — didn't assume shell
parity.

**Operator / SRE.** The interesting finding wasn't the bug — it was two adjacent git-hygiene gaps
surfaced while landing the fix. (1) The repo was in **detached HEAD** at session start (visible in
the very first `git status`, unrelated to this session's own actions); my first commit landed on
that detached tip and would have been orphaned on the next checkout had it not been caught and
fast-forwarded onto `master` immediately. (2) `master` was **7 commits ahead of `origin/master`** —
1 mine, 6 from an earlier/untitled session (the ADR-0024 watchdog work) that had never been pushed.
Neither gap was this session's doing, but both are the same failure shape as
[L-2026-07-19-5](SESSION-RETRO-2026-07-19.md) ("a session's output on one unpushed branch is a
SPOF") recurring in a new form: *unpushed on the correct branch* and *uncommitted-to-any-branch*,
not *uncommitted-to-the-wrong-branch*. Both are now resolved (pushed, HEAD reattached) but the
pattern — local commits/HEAD state silently drifting from what a reader would assume — is now its
third documented occurrence across three retros.

**Product / planning.** Derek's ask was a single compound instruction — "commit, merge, push,
/retro" — and the right call was to do exactly that without expanding scope: no sweep of the other
pending working-tree changes (`HEARTH-DASHBOARD.html`, `hearth/etc/backends.toml`,
`knowledge/*.json`, and clearly-personal untracked files like `diana-payton-prep-*.html`/`todo.txt`)
that predated this session and weren't part of the ask.

### Two seats, two views

**From Claude's seat.** The most useful thing I did was not trust my own prior memory file at face
value — I re-tested the "absolute path works anywhere" claim live instead of reading it and moving
on, which is exactly how it went stale for ten days in the first place (nobody had run it from
outside the repo since it was written). The detached-HEAD catch was luck of ordering as much as
diligence: I happened to check branch state *before* pushing, not after; a session that pushed first
and checked later would have shipped the same fix from a phantom ref. Where I could tighten: I should
default to `git symbolic-ref -q HEAD` as a standing pre-flight before any commit, not just when
something looks off.

**From Derek's seat** *(my reconstruction — correct me where wrong)*. The compressed instruction
("commit, merge, push, /retro") reads as trust that I'd handle the mechanics correctly without
narrating every step — consistent with the do-exactly-what's-asked and no-extra-steps preferences.
He'd likely want the detached-HEAD/unpushed-backlog finding surfaced plainly (it is, above) but
would not want it turned into a new process or tooling change on the strength of one occurrence —
this is the kind of thing to watch for a third and fourth time, not solve preemptively.

## Last time's lessons — follow-through

| Lesson | Status |
| --- | --- |
| L-2026-07-20-1 — A health check that bypasses the middleware it proves is worse than none | acted-on — closed same day ([ADR-0022](docs/adr/0022-container-access-needs-no-exposure.md)); nothing new this session |
| L-2026-07-20-2 — Construction-time policy can't be fixed by post-construction assignment | acted-on — closed same day (ADR-0022) |
| L-2026-07-20-3 — Interrogate the host before designing the ceremony | **acted-on** — tested the `PYTHONPATH` fix live in both shells from a non-repo cwd before writing it into either SKILL.md, rather than trusting the stale memory's "works anywhere" claim |
| L-2026-07-20-4 — Verify relayed agent reports; do not accept them | acted-on — captured as [[feedback-verify-relayed-agent-reports]] 2026-07-20; no relayed reports to verify this session |
| L-2026-07-20-5 — A parameter borrowed from an adjacent problem domain carries a cost multiplier | pending — no occasion this session |
| L-2026-07-20-6 — Work inside a gitignored nested clone is outside the parent repo's safety net | acted-on — captured in [[project-open-notebook-hearth]], done |
| L-2026-07-20-7 — Discovery should mirror authorization | acted-on — closed same day (ADR-0019 amendment) |
| L-2026-07-20-8 — Being disproven cheaply is the system working | **acted-on** — same instance as L-2026-07-20-3: tested instead of trusting the existing memory, and it was in fact wrong |
| L-2026-07-19-7 — A projection's bucket key is a schema | pending (3rd retro) — legacy zero-token buckets now 167 of 309 lifetime calls (was 182/236); already tracked in [DECISIONS-PENDING.md](DECISIONS-PENDING.md), low priority, no new action this session |

## Lessons learned

1. **L-2026-07-21-1 — A "works anywhere" memory is a claim about the day it was written, not a
   standing fact.** `reference-user-level-skill-junctions.md` certified the absolute-path fix as
   sufficient without ever being exercised from outside the repo. The memory-freshness warning
   ("point-in-time observations... verify against current code") exists for exactly this, and this
   is the first session that actually hit it as a live bug rather than a hypothetical. *(→ memory,
   done — corrected in place with a dated correction note rather than silently rewritten)*
2. **L-2026-07-21-2 — `python -m pkg` resolves against process cwd, not interpreter path.** An
   absolute path to `python.exe` says nothing about where the module import will look; only
   `PYTHONPATH` (or an actual `pip install`) is cwd-independent. Any future skill that shells out to
   a bare-package `-m` invocation needs the same treatment. *(→ practice, and the memory correction
   above)*
3. **L-2026-07-21-3 — Unpushed/unattached git state is a recurring failure shape, not a one-off.**
   Third documented instance across three retros (07-19: work stranded on an untracked branch;
   07-20: resolved, then recurred in a vendored nested clone; today: detached HEAD plus a 6-commit
   unpushed backlog from an untitled session). No process change proposed yet — flagging the pattern
   is the value; a fourth occurrence should trigger an actual fix (e.g., a pre-commit or session-start
   `git status`/`symbolic-ref` habit). *(→ watch, escalate on next recurrence)*

## Provenance

Git range for this session's own work: **`6b036dc..c67aee3`** (1 commit, 2 files, 35 insertions / 6
deletions). Full range pushed to `origin/master` this session: **`00516df..c67aee3`** (7 commits; 6
pre-existing from an earlier/untitled session, not authored here — see Operator/SRE above).
`master` now matches `origin/master`, 0 ahead. No HEARTH offload this session: the work was live
diagnostic verification (testing import behavior across two shells) and repo-coherent doc/memory
editing — neither is offload-shaped, and the retro itself was short enough to draft frontier
directly rather than pay a round-trip for a ~600-word doc. All investigation, fixes, memory
correction, and this retro are frontier.

## Offload scorecard (S6)

`offload_ratio` **1.0** · 309 lifetime calls — **sunk** 110 calls (325,092 in / 135,309 out),
**trial** 194 calls (524,896 in / 56,494 out), unknown 5 · `est_usd_saved` **$5.43** vs
claude-sonnet reference. Zero calls added this session (see Provenance). Legacy zero-token buckets
(`model:<name>`-shaped) still stand at 167 of 309 lifetime calls — the pre-existing, already-tracked
undercount noted in the follow-through table above.

---

# Session Retro — 2026-07-21, session 2 (GCP trial-credit benchmark and the first cloud agent behind HEARTH)

> **The benchmark's own result undercut the reason to build the thing it was measuring.**
> `am4-moe` — the $0 sunk-cost local rig — scored *highest* (93.8) of the three backends on the
> exact documentation-consistency task class GCP trial credits were being evaluated for, beating
> both Gemini rungs (89.2, 90.6). The case for spending the credits turned out to be speed
> (~4-5x), not quality. Then, entirely separately, the session produced the first real MCP
> connection from an external, cloud-hosted agent into this lab — and found, live, that the
> platform meant to hold that connection (Google's Agent Platform Studio) can't yet send the
> header HEARTH's authorization model requires at all.

## What this session was

A **benchmark-then-build** session that changed shape twice on real evidence: started as "plan
a 3-agent ADK build," narrowed to "run the cheap benchmark first" after exploration found the
docx's assumed tooling didn't exist, then narrowed again mid-build (a live user correction —
"start simple," prove session continuity, not the full eval harness) into a minimal connectivity
test that ended up surfacing two genuine infrastructure bugs and one real, consciously-accepted
security tradeoff.

## What shipped

No commits this session (see Provenance) — the git range is empty by choice, not omission. What
shipped is real, live-verified infrastructure and a benchmark result:

**New files (uncommitted — see Operator/SRE and [DECISIONS-PENDING.md](DECISIONS-PENDING.md)):**
[`hearth/experiments/doc_adr_bench.py`](hearth/experiments/doc_adr_bench.py),
[`hearth/experiments/run_doc_adr_bench.py`](hearth/experiments/run_doc_adr_bench.py),
[`hearth/projection/gemini_pricing.py`](hearth/projection/gemini_pricing.py),
[`hearth/etc/caddy/Caddyfile`](hearth/etc/caddy/Caddyfile),
[`hearth/etc/start-hearth-funnel-proxy.cmd`](hearth/etc/start-hearth-funnel-proxy.cmd).

**Live infrastructure changes (outside git):** Caddy installed on OMEN (`winget`); Tailscale
Funnel enabled on OMEN for the first time (`https://omen.tail8e749c.ts.net` → Caddy `:8711` →
gateway `:8710`); HEARTH caller `gcp-adk-test` minted (`probe` → `research` profile, scoped to
`commandcenter`); the HEARTH gateway restarted twice via `HearthGatewayRestart`; a real Google
Agent Platform Studio agent (project `lumberjacks-exp-20260711-djc`) wired to HEARTH over MCP,
safety-hardened, and redeployed.

**New durable artifact:** [ADR-0025](docs/adr/0025-funnel-caddy-stamps-identity-until-studio-can.md).

## The team retro — our collaboration across the seats

**Architect.** The load-bearing call was to stop treating the docx
(`Hearth_Google_Agent_Implementation_Plan.docx`) as documentation of a system to extend, and
start treating it as a design draft to verify — three parallel Explore agents confirmed its
assumed tool surface (`manifest.query`, `ledger.query`, `repo.search`) and its whole
ADK/Agent-Runtime/Memory-Bank stack don't exist in HEARTH at all. That reframing produced the
two-track plan (cheap benchmark gates the expensive build) before a line of new code was written.
The second architectural call came mid-build, from Derek, not me: when he said the goal was
"maintain its own context a bit longer... start simple," Track 2's heavy new-tools/new-profile/
eval-harness design got explicitly deferred behind a Track 2.0 that reused HEARTH's *existing*
`probe`/`research` profiles instead of inventing a `cloud-steward` role up front — the right
sequencing call, but not one I originated.

**Implementer.** Track 1 (`doc_adr_bench.py`, `run_doc_adr_bench.py`, `gemini_pricing.py`) went in
cleanly and ran live on the first real attempt after fixing one `HEARTH_SCOPE` env-var oversight.
Track 2.0's infrastructure work surfaced two real bugs neither of us predicted: (1) Funnel's
default Host-header passthrough collided with ADR-0022's existing DNS-rebinding allowlist —
`421 Invalid Host header` on the very first live test, fixed with a `header_up Host` rewrite;
(2) Studio's native MCP Server tool turned out to only support `Authentication: None` (`OAuth`/
`API key` both "Coming soon"), a hard connectivity blocker discovered only by actually trying to
add the tool, not by reading docs beforehand. Both were root-caused and fixed the same session,
verified live, not asserted.

**Reviewer / QA.** Every claim in this session has a corresponding real check, not a self-report:
the benchmark numbers came from the actual ledger (`routed_by: "pinned:<backend>"` confirmed on
every row); the Funnel fix was confirmed with `curl -v` showing the exact `421` before and `406`
after; the auth-stamp workaround was confirmed with a real MCP client sending *zero* headers,
matching Studio's actual behavior, not a plausible guess at it; the ADK agent's own final answer
(the ADR-vs-`capabilities.py` comparison) was independently checked against the real files and
found accurate — verbatim, correctly-elided, not fabricated. The one gap: the agent's answer to
an earlier "what is OMEN?" turn, built partly on two `local_generate` calls made without the
required `files=` parameter (a real footgun — naming a path in prompt text gives the delegated
model nothing to read), was never independently checked because the conversation was closed
before it could be reviewed. Flagged as unverified, not asserted as either good or bad.

**Operator / SRE.** This session leaves the repo in an unusual, worth-naming state: **zero
commits, but real, live operational changes** — new software installed on the host, a public
network exposure enabled for the first time, a new credential minted into the live (gitignored)
caller registry, the production gateway bounced twice, and real GCP trial-credit dollars spent
(the Studio banner's balance moved from $196.72 to $183.42 over the session). None of that shows
up in `git log`. This is a different failure shape than [L-2026-07-21-3](#lessons-learned)'s
unpushed-commit pattern from earlier today — not drift, but a session that correctly followed
"don't commit unless asked" and, as a side effect, left real infrastructure state that git cannot
see at all. Also notable: I made a real security mistake mid-session and caught it myself before
it landed — writing the live Caddy secret directly into a git-tracked file — restructured to the
repo's existing gitignored-secret convention before ever staging it. And, separately, early in the
session I wasted several tool calls spawning throwaway "placeholder" sub-agents while waiting on
a background research task, for no reason — a pure process error, not a security one, caught and
stopped without prompting.

**Product / planning.** Derek drove every real pivot this session: the initial scope-narrowing
answers (full docx build, `commandcenter`+`baseline`, gemini-vs-`am4-moe` not vs-Claude), the
mid-benchmark decision to run the full sweep rather than stop at the smoke test, the "narrow
reverse-proxy not bare port" and "stock Caddy, skip rate-limiting" infrastructure calls, the
explicit "let it ride" acceptance of the Caddy-stamped-key tradeoff once the alternative dead-
ended, and the closing instinct to add a safety prompt before calling it done. My job across all
of it was instrumenting each choice with a real test rather than a plausible-sounding plan —
the recurring shape of this whole session was propose → verify live → report exactly what the
evidence showed, including when it undercut the premise (the benchmark result) or the plan
(Studio's auth ceiling).

### Two seats, two views

**From Claude's seat.** The most valuable thing I did this session was refuse to guess twice: I
would not hardcode a Gemini per-Mtok price I didn't have (leaving `gemini_pricing.py` honestly
unpriced rather than inventing a plausible number), and I would not ship a Caddy directive for a
placeholder syntax I could not confirm existed (the query-param-to-header idea), saying so
plainly instead. Both times the honest "I don't know" turned out to matter — the alternative in
each case would have been a confidently wrong answer sitting in production config. Where I'd
tighten: the placeholder-agent-spam mistake early in the session was avoidable — I should have
recognized immediately that "waiting" doesn't require a tool call at all, rather than discovering
that after three wasted ones.

**From Derek's seat** *(my reconstruction — correct me where wrong)*. The session likely read as
satisfying in the way a good instrument-then-decide loop should: he set the direction and made
every real tradeoff call (Studio vs raw ADK, proxy shape, rate-limiting, the auth workaround), and
I did the work of turning each choice into something proven rather than asserted. The mid-session
correction ("start simple... maintain its own context") reads as him steering scope down the
moment it started drifting toward the full docx build's weight — consistent with pacing releases
deliberately rather than letting momentum decide scope. He'd probably want the uncommitted-files
state named plainly (it is, above) without me either committing unasked or treating it as an
emergency.

## Last time's lessons — follow-through

| Lesson | Status |
| --- | --- |
| L-2026-07-21-1 — A "works anywhere" memory is a claim about the day it was written | pending — no occasion this session |
| L-2026-07-21-2 — `python -m pkg` resolves against process cwd, not interpreter path | pending — no occasion this session |
| L-2026-07-21-3 — Unpushed/unattached git state is a recurring failure shape | **recurred in a new shape** — this session made zero commits at all (not drift, deliberate), yet left real, git-invisible infrastructure changes (installed software, a live public exposure, a minted credential, real spend) — see the new lesson below and Operator/SRE |

## Lessons learned

1. **L-2026-07-21-4 — A benchmark can invalidate the premise of the build it's gating, and that's
   the benchmark working, not failing.** `am4-moe` beat both Gemini rungs on quality for the exact
   task class the credits were being justified against. The two-track plan (cheap benchmark gates
   the expensive build) only pays off if the team is actually willing to let the result say "the
   expensive thing isn't obviously worth it yet" — which it did, honestly, without either side
   spinning it. *(→ practice: keep gating expensive builds on cheap, real measurements)*
2. **L-2026-07-21-5 — A platform's documented capability and its actual UI state can disagree, and
   only trying it live catches the gap.** Studio's MCP Server tool visibly offers "API key" auth
   in the dropdown — greyed out, "Coming soon." Planning against the docs alone would have shipped
   a design assuming header auth existed. *(→ practice: for any new external platform, exercise
   the actual UI/API before designing around its documented capability)*
3. **L-2026-07-21-6 — Git-invisible operational state is a real gap for a session that correctly
   follows "don't commit unless asked."** New software installed, a public network path opened, a
   credential minted, real money spent — none of it is in `git log`. Not a process violation (the
   no-auto-commit rule is correct), but worth a retro naming it explicitly every time it happens,
   the same way unpushed commits already get named. *(→ practice, tracked in
   [DECISIONS-PENDING.md](DECISIONS-PENDING.md))*
4. **L-2026-07-21-7 — Refusing to guess a credential-shaped or config-shaped unknown is worth the
   friction it causes.** Twice this session (Gemini pricing, a Caddy placeholder syntax) the
   honest "I don't have this confirmed" was slower than asserting a plausible value, and correct
   both times. *(→ practice; matches [[feedback-untested-not-impossible]]'s spirit in the opposite
   direction — enumerate what's uncertain rather than assert past it)*
5. **L-2026-07-21-8 — A new, less-trusted external caller earns a deliberate safety preamble, not
   just a capability grant.** The profile system (ADR-0019/0023) already bounds what `gcp-adk-test`
   can *do*; it says nothing about how the model *behaves* within that grant. Adding explicit
   evidence-discipline and tool-usage guardrails to the agent's own instructions, on the first
   occasion a genuinely external surface got real access, was the right instinct and should be
   the default for the next one too. *(→ practice, and candidate for the eventual Agent-1
   system-instruction template if the full Track 2 build happens)*

## Provenance

Git range for this session's own work: **none — zero commits** (advisory/infrastructure session;
per the skill's own guidance, an empty range is normal and does not mean nothing happened — see
"What shipped" above). Working tree carries five new, uncommitted files (listed above) plus
pre-existing unrelated modifications (`knowledge/*.json`, `HEARTH-DASHBOARD.html` — background
projection writes, not from this session). One `local_generate` offload attempt this session (see
below); everything else — the benchmark design, the Caddy/Funnel debugging, the ADR, this retro —
is frontier, because the judgment and live-debugging work in this session (root-causing two real
bugs from symptom to fix, verifying every claim against a real system) was not draftable prose.

**Offload**: one `mcp__hearth__local_generate` call (`gcp-gemini`/`gemini-3.5-flash`,
`quality="good"`, 2,674 in / 581 out tokens, 23.9s) drafting a first pass of three of the five
role-read sections above (Architect/Implementer/Reviewer-QA) from a full factsheet plus a style
exemplar. **Edit verdict: minor-fixes** — the draft was directionally accurate but contained one
real inaccuracy (attributed the stock-Caddy-over-xcaddy choice to "minimizing host footprint";
the real reason was skipping rate-limiting complexity) and cut off mid-sentence before covering
Operator/SRE, Product/planning, the two-seats section, or lessons — all of which were written
frontier. No `--fleet` second opinion dispatched.

## Offload scorecard (S6)

`offload_ratio` **1.0** · 353 lifetime calls — **sunk** 112 calls (325,267 in / 138,821 out),
**trial** 235 calls (2,015,435 in / 131,557 out), unknown 6 · `est_usd_saved` **$11.08** vs
claude-sonnet reference (up from $5.43 pre-session — this session's Track 1 benchmark and the
ADK agent's own `local_generate` delegation account for most of the growth). Separately, and more
concretely: **real GCP trial-credit balance dropped $13.30 this session** ($196.72 → $183.42 per
the Studio billing banner) — actual observed cloud spend, not an estimate, the first time this
session's own offload doctrine has a real-dollar number to sit next to the usual
claude-sonnet-equivalent estimate. Legacy zero-token buckets (`model:<name>`-shaped) unchanged at
167 of 353 lifetime calls — same pre-existing, already-tracked undercount.
