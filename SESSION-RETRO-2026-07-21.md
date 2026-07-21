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
