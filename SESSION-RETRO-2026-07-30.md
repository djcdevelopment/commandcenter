# Session retro — 2026-07-30

**One-line:** A frozen-projection bug turned out to be **two event stores and an unscheduled
bridge**, and fixing it properly meant **closing the learning loop for real** — the first
capability re-earned since the 2026-07-02 corpus overwrite — while a mid-session Ollama outage
exposed an older architectural drift and, twice, my own bad handovers.

## What this session was

A **recover-then-build** session in four acts, each one triggered by the last. Diagnose a
26-day-old silent failure → tidy the repo state the diagnosis exposed → plan and build the real
fix → and, when Ollama died underneath us, diagnose a second, unrelated drift that Derek had been
living with for weeks.

The through-line is one idea: **a system that reports healthy while doing nothing is worse than
one that fails loudly.** It showed up three times — a rebuild that returned `ok` over a frozen
corpus, an Ollama that answered `/api/tags` while no model could run, and a freshness guard I
almost left permanently red.

## What shipped

| Commit | What |
| --- | --- |
| `bcde130` | call-mix dashboard on the six-hour rebuild cadence |
| `decf695` | dashboard states it is a snapshot, not a live query |
| `bd2b86f` | ADR-0026 — cloud deployments are ephemeral by default |
| `007b92e` | nested-claude guard |
| `573db02` | GCP ADK demo agent + engine specs |
| `a5df83c` | 2026-07-29 session retro, buddy-demo audit, plan refresh |
| `009be37` | **fix(projection): close the learning loop — bridge on rebuild, guard staleness** |
| `42eefdb` | ADR-0027 — gateway dispatches are not observations until we say what they observe |
| `360211f` | merge: projection freshness repair |
| `50338fa` | first rebuild through the repaired bridge |
| `009c201` | NotebookLM agentic-systems research + GCP agent implementation plan |
| `90d8f1d` | **feat(projection): acknowledged staleness, so the freshness guard stays readable** |
| `c5711e6` | rebuild through the bridge (corpus 848 → 1013) |
| `623139c` | refactor: move the error taxonomy out of the kernel |
| `bd2655b` | **feat(observation): offload dispatches become capability evidence (ADR-0027 option B)** |
| `16fba44` | ADR-0027 accepted — the evidence semantics, and two waivers retired |
| `611b2ea` | **chore(knowledge): first capability re-earned (count 0 → 1)** |
| `ef84482` | **feat(sentinel): serviceability beside bypass detection + ADR-0028** |
| `354dfd7` | ADR-0028 remediation status — native gateway and loopback done |
| `8e5e11a` | one-command Ollama posture check + repair |
| `04d2aeb` | fix(ops): find the real staged installer, and refuse a bad one |
| `0903ba5` | ADR-0028 — Ollama runtime repaired; record the env-inheritance gotcha |
| `67cf338` | ADR-0028 — correct stale runbook rows 4 and 5 |

**New durable artifacts:** [`hearth/projection/freshness.py`](hearth/projection/freshness.py) ·
[`hearth/observation/`](hearth/observation/) (identity + emit) ·
[`hearth/errortax.py`](hearth/errortax.py) ·
[`knowledge/freshness_ack.json`](knowledge/freshness_ack.json) ·
[`tools/ops/fix-ollama.ps1`](tools/ops/fix-ollama.ps1) ·
[`comfy/fieldlab/scripts/start-comfy-gateway.cmd`](../comfy/fieldlab/scripts/start-comfy-gateway.cmd) ·
[ADR-0027](docs/adr/0027-gateway-dispatches-are-not-observations-yet.md) ·
[ADR-0028](docs/adr/0028-one-door-means-one-host.md) · scheduled task `ComfyGatewayBoot` ·
1034 tests green (from 631 baseline).

## The team retro — our collaboration across the seats

*(The local draft of this section was discarded — see Provenance. It filled two of five seats
with invented self-criticism that inverted the record, so these are frontier-written.)*

**Architect.** The good call was refusing the obvious seam twice. The bridge went into
`rebuild.main()` rather than inside `rebuild_knowledge()`, because that function has a
byte-untouched-on-failure contract and appending to an event store is a write-side concern —
ADR-0010's direction preserved rather than quietly crossed. Then the same instinct paid again
when a Plan agent overturned my emit-point design: putting observation-birth in the gateway
wrapper would have been ADR-0010's forbidden pattern wearing option B's clothes, *and* would have
emitted nothing for the eleven in-process callers that bypass the door. What I'd change: I
proposed `builder_id = caller.id`, which would have made gate 1 and gate 2 test the same axis and
manufactured a capability out of gate arithmetic. Derek picked the workflow-identity option I'd
argued against, and his choice was fine — **my mapping was the actual error**, and I only found it
because I had to defend the objection.

**Implementer.** Volume was real and mostly clean: 23 commits, 1034 tests green, three
conflict-resolved merges. The heartbeat filter keyed on `caller.id`/`profile` rather than tool
names, which is right because `mechnet_watchdog.*` is already five tools and growing. Two
mechanical errors, both mine, both self-inflicted: I edited `ledger_adapter.py` in the **main
repo** instead of my worktree, and my `git worktree remove` — which failed because my own shell
was inside the directory — had already unlinked it, so a follow-up `checkout --detach` landed on
the main repo and detached it from `master`. Both reverted with nothing lost, but both were
avoidable by checking where I was standing first.

**Reviewer / QA.** This seat carried the session, and mostly not as me. The Plan agent's critique
overturned **three** of my design choices and every one of them checked out when I verified it —
including that my planned double-count fix was unnecessary (no belief projector reads raw events
as evidence) and that my premise about which gate was binding was backwards (gate 3 is satisfied
everywhere; gate 2 is the wall, because every rung declares one model on one host). My own new
kernel-free test also failed on its first run and was right to: it caught that `errortax`'s
docstring *mentions* `hearth.kernel`, which pushed the check from substring-grep to the AST import
graph — a better test than the one it was mirroring. And writing the ack mechanism surfaced that
the ack file would be scanned as a document and reported stale for the very watermark it
acknowledges. What slipped: I left ADR-0028's status table claiming step 4 was OPEN after Derek
had already done it and I had verified it — an artifact whose status lags the work is what sends
the next reader redoing finished steps.

**Operator / SRE.** Not a clean session, and the failures were informative. A real ~30-minute
silent Ollama outage, found **by accident** while chasing something else — which is precisely the
gap the new serviceability check closes. The branch prune didn't hold: 61 of 70 deleted branches
reappeared within half an hour with Jul 5–18 commit dates, re-pushed from off-box; I correctly did
**not** re-delete them, because that's a treadmill until the source is found. `ComfyGatewayBoot`
could not be registered with `HearthGatewayBoot`'s S4U/Highest/boot shape (access denied, needs
elevation), so it's Interactive/Limited/logon — a real divergence, recorded rather than hidden.
And going native cost comfy_gateway Docker's `restart: unless-stopped`, a persistence loss that
was **not** in my runbook and that I only noticed because I stopped to ask how it should survive a
reboot.

**Product / planning.** The best decision was not building the thing that was asked for. The task
said "confirm `capability_count` becomes non-zero"; memory said that zero was a *deliberate,
documented* state after the A3 evidence-hunt found the destroyed capability was half fiction. So I
stopped, wrote ADR-0027 as a proposal, and asked — and the capability was later earned
legitimately from real dispatches instead of forced. Pacing was decent: I asked at four genuine
forks and otherwise kept moving. What I'd change: I front-loaded the Ollama story as "reinstall
Ollama" and Derek had to correct me that it was plainly installed. He was right, and the framing
mattered — the app layer and the inference layer are separate, and my wording reproduced the exact
blind spot that had hidden the bug for thirty minutes.

### Two seats, two views

**From Claude's seat.** The thing I got most right was treating the ask's own hypotheses as
falsifiable rather than as instructions. All four suggested causes were wrong, and the real one —
two bounded contexts with a hand-run bridge stuck at line 3 — was only findable by ignoring the
framing and following the data. The same discipline paid on `capability_count: 0`, where the
"bug" was a decision. Where I under-reached: I accepted my own partial observations as complete
twice. The `head -10` truncation is the sharp one, but the ADR status-table row is the same
error in a different costume — I verified something, then didn't propagate the verification into
the artifact. What I'd want to know next time: which of my "verified" claims are verified *now*
versus verified *once*. The freshness guard I built for the projections is exactly the discipline
I failed to apply to my own reporting.

**From Derek's seat** *(my reconstruction from his stated preferences and this session's signals —
to be corrected, not taken as his words)*. The projection work is the kind of thing he'd want done
without narration: find it, fix it, prove it, and don't ask permission for the obvious parts. He'd
approve of the ADR-first pause on capability — it's his own guardrail working — and he'd have
little patience for the branch-prune treadmill, which is someone else's machine misbehaving. The
part that visibly cost him was the handover quality: two broken pastes at ~4am, then a wrong
installer path, on top of an Ollama problem he'd already fixed once thirteen days earlier and
been quietly bitten by since. His "TIRED TIRED of pasting broken powershell" is the session's
most actionable feedback, and it isn't about PowerShell — it's that I was optimizing my own
output over his execution cost. He'd also, I think, want the auto-updater thread pulled properly
rather than left as a note.

## Last time's lessons

| Lesson (2026-07-29) | Status |
| --- | --- |
| L-2026-07-29-1 — the failure mode of a *faithful* offloaded summary is omission; only reading the source catches it | **acted-on, and vindicated harder than expected.** Applied throughout: I re-verified every claim from the Plan agent (3 of my design choices overturned, all checked) and from the Explore agents. This session also extends it — today's draft failed by *fabrication*, not omission, so source-reading catches both. |
| L-2026-07-29-2 — diagnose prompt-shape before diagnosing the model | **acted-on.** Today's draft got a factsheet plus a 40-line in-context exemplar; the failures that remained were not prompt-shape ones. |
| L-2026-07-29-3 — one warm call is a data point, not a reliability finding | **acted-on.** Deliberately resisted twice: the first capability is recorded at `medium/0.5` confidence on 2 samples, and ADR-0027 states plainly that exactly one capability forms and why. |

## Lessons learned

1. **L-2026-07-30-1 — A from-zero rebuild cannot detect its own staleness; the check must come
   from outside the replay, against something that moves.** Replaying a frozen corpus succeeds,
   and the staging dir a rebuild validates against starts empty, so the regression guard has
   nothing to compare. Every internal signal read healthy for 26 days. *(→ ADR — landed as
   ADR-0027's framing + `freshness.py`)*
2. **L-2026-07-30-2 — A guard that is always red is a guard nobody reads, so "known-stale" must be
   expressible without going quiet.** Replacing a silent failure with an ignorable alarm is not a
   fix. The mechanism needs three properties or it rots: the acknowledgement pinned to the exact
   observed value, a mandatory expiry measured against evidence rather than the clock, and the
   load-bearing checks made un-acknowledgeable. *(→ ADR — landed as `freshness_ack.json`)*
3. **L-2026-07-30-3 — Never draw a conclusion from output I truncated myself.** `head -10` on a
   22-line listing hid `updates_v2/` at line 21; I did it twice and then reasoned as though I'd
   seen the directory. Worse, the tool's own log named the authoritative path, and I read it only
   after the failure — I promoted circumstantial filesystem evidence over an authoritative record.
   *(→ memory: [[feedback-dont-conclude-from-truncated-output]] — written)*
4. **L-2026-07-30-4 — If a handover needs pasted fragments, the handover is the defect.** A
   `python -m` with no working directory and PowerShell one-liners with fragile escaping both
   failed on paste, at 4am, on top of a recurring problem. The fix wasn't better instructions, it
   was one script with no arguments and no cwd dependency, tested from the directory that broke
   the last version. *(→ memory, same file)*
5. **L-2026-07-30-5 — "Installed" and "able to work" are different facts, and every visible signal
   usually reports the first.** Ollama's tray icon, Start-menu entry, `/api/tags` and
   `/api/version` all reported the app layer; the missing `lib/ollama` runtime was invisible to
   all of them. Reachability is not serviceability, and a liveness probe that never asks the
   system to *do* its job will read green through a total outage. *(→ ADR-0028 + the sentinel's
   serviceability check)*
6. **L-2026-07-30-6 — Local-model seat reads invent self-criticism, and this is now a pattern
   across three retros.** 07-21 and 07-29 both graded seat reads down (mechanics instead of reads);
   today's draft actively *inverted* the record — the Reviewer seat claimed it caught nothing in
   the session where an agent critique overturned three design choices, and Product called the
   session's central correct judgment a missed opportunity. A factsheet plus an exemplar was not
   enough. Next retro should either offload seat reads with the *evidence per seat* attached, or
   stop offloading that section and keep the timeline + lessons extraction, which are consistently
   usable. *(→ practice, escalate to the offload doctrine if it recurs a fourth time)*
7. **L-2026-07-30-7 — In-process `local_generate` calls are invisible to the offload economics.**
   They bypass the gateway wrapper, so nothing reaches the kernel ledger: today's live test
   dispatches appear in the observation artifacts but not in `offload.json`, and `omen-ollama`
   still shows `ok_rate 1.0` despite my failed attempts against it. The scorecard measures
   *door traffic*, not *offload*, and the two have now measurably diverged. *(→ doc — worth naming
   in the offload doctrine; not yet an ADR)*

## Provenance

**Git range:** `cc13217..67cf338` — 23 commits, 74 files, +8852/−407, all pushed to
`origin/master`. Working tree carries a **concurrent session's** `CLAUDE.md` edit (a Lumberjacks
decision-record section) plus timer-driven `knowledge/` churn — neither authored here, both left
untouched.

**Offloaded:** one `mcp__hearth__local_generate` call, unpinned (`routed_by: default` →
`omen-ollama` / `qwen3-coder:30b`, 3,362 in / 712 out, 105.9 s) for the timeline, five seat reads,
and candidate lessons — with a factsheet and a 40-line style exemplar in the prompt.
**Edit verdict: `hallucinated`.** Four lessons were usable and are folded in above. Discarded: the
entire seat-reads section (Reviewer/QA claimed it "caught no critical bugs" in the session where a
Plan agent overturned three of my design choices; Product called the deliberate ADR-0027 pause a
"missed opportunity"), an invented filename `freshening.py`, and a reversion to "5 projections"
where the factsheet says eleven. Incidentally this call is the end-to-end proof that the repaired
`omen-ollama` rung works through the door.

**Frontier:** the whole diagnosis, all five seat reads, both POV sections, every ADR, the
verification of the Plan agent's three overturns, and this file's structure and judgment. No
`--fleet` draft was requested; **no plan_ids were pending from 2026-07-29**, so there is no second
opinion to resolve.

**Not done, deliberately:** the Ollama auto-updater half-applies on a loop (`app.log` hourly;
`repair-install-2026-07-17.log` shows Derek already repaired this once). Stopping it means pinning
a version and updating on purpose — a real decision, deferred rather than made at 6am.

## Offload scorecard (S6)

`knowledge/offload.json` — watermark `2026-07-30T09:38:53Z`, fresh.

| | |
| --- | --- |
| offload_ratio | **1.0** (1,172 calls) |
| sunk | 616 calls · 365,640 in / 155,927 out · **$0** |
| trial | 550 calls · 2,140,275 in / 172,890 out · **$6.09** |
| est_usd_saved | **$12.45** vs `claude-sonnet` reference |

Every rung at `ok_rate 1.0`. `am4-oxen` 332 calls and `am4-moe` 236 carry the sunk side;
`gcp-gemini-pro` is $5.28 of the $6.09 real spend, which is the premium-reach rung behaving as
designed. Caveat per L-7: this measures door traffic, and today's in-process dispatches are
absent from it.
