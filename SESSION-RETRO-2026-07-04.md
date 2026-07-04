# Session Retro — 2026-07-04 (wave-2 curation · conductor patches · fleet recovery)

> One-line: dispatched and **curated all five wave-2 streams onto `master` (tests 177 → 264)**,
> lifted the cc-builder-1 quarantine on real evidence, applied one conductor fix and **retracted a
> wrong one after the fleet falsified it**, built an OMEN-side fleet health tool, and **recovered
> claudefarm1** by finding a static-MAC collision. The through-line: *the fleet's signals don't
> mean what they look like they mean* — and the lab is only trustworthy because we refused to take
> them at face value.

---

## What this session was

A build-and-curate session on the running lab (not a design session). It started from a plain
"what's our build status?" and turned into: pour wave-2, curate it honestly, fix what the pours
revealed, and recover a downed node. Heavy use of the conductor over SSH and Hyper-V over PowerShell
from OMEN.

## What shipped (master `13ba046` → this docs commit)

| Commit | What |
|---|---|
| `74ceb80` | **G2 opened** — first real GPU physical telemetry (`runs/g2-validation/`, RTX 5070) |
| `cb60065` | **fleet-ping** — OMEN-side reachability inventory (`fleet/inventory.toml`) + stdlib CLI + 15 tests |
| `ae46a35` | **D1** — derived economy + `economy_influence` (landed clean) |
| `3944f01` | **E1** — idle events + operating-budget contract + `budget_check` (landed clean) |
| `c656cfe` | **F1** — snapshot-diff (`project_diff.py`) + curated tests |
| `a3986e7` | **Ga** — worth-realized projector + curated tests (**caught a latent `KeyError` crash**) |
| `b5ad8ca` | **Gc** — calibration, **synthesized** (neither lap landable; wrote the missing function + tests) |
| `f256d38` | **Step 4** — belief re-projection: cc-builder-1 quarantine LIFTED, cc-builder-2 → known_good |
| `c75f0ab` | followups: finding #1 applied, **finding #2 retracted** |
| `b199757`, `643f91b` | claudefarm1 diagnosed then **RECOVERED** (MAC-collision fix) |
| conductor `63935ee` | **finding #1 live** — allow-list overrides `exclude_from_build_pool` |
| (this) | docs: ADRs, quickstart, howto correction, POUR-STATUS, this retro |

New durable artifacts: `docs/adr/{0001,0002,0003}`, `fleet/`, `runs/g2-validation/`,
`runs/regression-probe-ccb1/`, `QUESTIONS-D1.md`, `DECISION-NEEDED-Ga.md`, `BUILD-NOTES-Gc.md`.

## Key findings / lessons (the durable ones → ADRs)

1. **The assay is a regression gate, not an acceptance oracle** ([ADR-0001](docs/adr/0001-assay-acceptance-gap.md)).
   It crowned unlandable "winners" 4+ times: missing-test laps (F1, Ga), a `NameError` lap graded 64
   (Gc), and a bare module graded 70 over it. **Winner ≠ landable; always curate.** Curation caught
   two real defects the assay was blind to.
2. **`agent_timed_out` is not a completeness signal.** cc-builder-1 timed out on all five wave-2
   pours yet produced the winning complete code each time. This *falsified* the finding #2 timeout
   grade-cap I'd recommended a few hours earlier — so I retracted it rather than ship it. The correct
   fix is stream-scoped deliverable acceptance in the assay layer.
3. **Don't feed the belief layer infra/harness noise** ([ADR-0002](docs/adr/0002-belief-layer-excludes-infra-failures.md)).
   Materializing the wave-2 outcomes would have false-quarantined the best builder and false-`known_bad`
   a good model (omen-worker-1's F/0s were claudefarm1 being down). We materialized only the clean
   regression-probe signal; withholding is recorded, not silent.
4. **Static-MAC collisions masquerade as dead guests.** claudefarm1 (Hyper-V display name changed to
   `cc-worker-1`; guest hostname unchanged) was knocked off the network because cc-builder-4, cloned
   off its golden image, inherited its static MAC (`00155D0CC700`). `KVP: No Contact` + no host-visible
   IP looked like guest-network-death but was host-side and fixable without a console. **Fix:** unique
   MAC + restart. **Prevent:** regenerate NIC MAC on every clone.
5. **The A2 corpus-regression override worked in anger** — a legitimate finding retirement (18→17)
   accepted via an authored, scoped, audited override; the guard correctly blocked it until then.

## What worked (process)

- **Pull-and-verify-live before patching.** The live `conductor_maf.py` differed from my snapshot
  (the reverted `repo_path` threading); catching that before editing avoided a broken patch.
- **Curation-before-landing** and **`promote:false`** kept `master` clean and caught real bugs.
- **Surfacing the contradiction** (retracting finding #2) instead of executing an approved-but-wrong
  patch. "Make it so" doesn't override "don't ship code you've proven wrong."
- **fleet-ping paid for itself immediately** — it framed the claudefarm1 diagnosis and confirmed the
  recovery.

## What to change

- **Build stream-scoped acceptance** (ADR-0001 item 2) so curation isn't the only safety net.
- **Add MAC-regeneration to the VM-clone procedure** (done in memory; fold into the runbook on the
  conductor).
- Consider re-homing `omen-worker-1`'s shell off claudefarm1 (its Ollama backend is independently
  reachable; the coupling is why it dies with claudefarm1).

## On Derek's desk (needs authored judgment, not buildable by Claude)

- `QUESTIONS-D1.md` — economics answers (attention economy · credit split · leased default · attendance).
- `DECISION-NEEDED-Ga.md` + `BUILD-NOTES-Gc.md` — numeric worth values.
- Whether to fire the **omen-worker-1 build lap** now that claudefarm1 is back (would re-earn `build|ollama`, close **G3**).

## How to resume

Read `MEMORY.md`, then this retro, then `POUR-STATUS.md` (gate + landing state) and `docs/adr/`.
Fleet health: `python fleet/fleet_ping.py` from OMEN. The lab is green (264 tests), the belief layer
is healthier than it started (a recovered builder, a promoted one, and it refused to be poisoned),
claudefarm1 + omen-worker-1 are back. Next substantive moves: the omen-worker-1 build lap (→ G3),
stream-scoped acceptance (ADR-0001), and Derek's three decision docs. He sets when it fires.

---

# Session Retro — 2026-07-04 (addendum · the `/retro` skill · a tool that writes this)

> One-line: a small, self-referential build — a question about whether `submit_task` could
> carry a retrospective's context **turned into the tool that writes them**: a `/retro` Claude
> Code skill that assembles the story frontier-side, offloads the draftable prose to
> HEARTH/mechnet, and keeps every repo-coherent write local. Shaken down by running it on itself.

## What this session was

A tight design-and-build session (not curation, not recovery). It started from a pointed
question — *is `submit_task` smart enough to hand off the conversation, tool calls, and files
touched for a retro?* — and the honest "no" became the design spec for a new skill. One artifact
shipped: [.claude/skills/retro/SKILL.md](.claude/skills/retro/SKILL.md). No commits yet (working
tree; Derek paces the commit).

## What shipped

| Change | What |
|---|---|
| `.claude/skills/retro/SKILL.md` (untracked) | **`/retro`** — a playbook skill: gather factsheet (frontier) → offload draft prose (HEARTH `local_generate`, opt-in `--fleet` `submit_task`) → write retro + ADRs + docs + memory (frontier) → ledger via `record_event`. Multi-role engineering-team retro + dual Claude/Derek POV. |
| this addendum + ADR-0004 + memory | the skill's own first run, writing itself up |

*Not this session:* the untracked `fleet/bankedfire_drain*` files in the working tree are
concurrent work from another session — explicitly **not** claimed here (honest scoping is the
whole point of the tool).

## The team retro — our collaboration across the seats

*(First-pass role reads drafted by qwen3-coder:30b on omen-ollama via HEARTH `local_generate` —
8.3s, occupancy available — then edited against the factsheet. The offload itself was the live
integration test.)*

- **Architect** — The design call was sound and cheap to reach: because `submit_task` is a dumb
  pipe (proven by reading [task_lane.py](hearth/toolsurface/task_lane.py), not assumed), the skill
  had to split work by *who can see what* — conversation/why is frontier-only, file-truth is
  git-reconstructable. Modeling `/retro` as a pure `SKILL.md` playbook (matching `checkmcp`/`checkmechnet`)
  rather than new code kept blast radius near zero. What we'd watch: the offload/frontier boundary is
  a judgment call the skill *describes* but can't *enforce* — discipline lives in the playbook prose.
- **Implementer** — Low-friction: study two existing skills + the retro/ADR house style, then write
  one file. No rework, nothing fought us. The build's honesty was load-bearing — reading the actual
  `submit_task` source before answering, and catching that the `bankedfire_drain` files weren't ours,
  are the difference between a retro and a fabrication.
- **Reviewer / QA** — The shakedown *is* the test, and it earned its keep: it exercised the real
  HEARTH path (`ok:true`, ledgered), forced the append-don't-overwrite guardrail (today's retro
  already existed), and surfaced the scoping trap (foreign working-tree files). Gap: there are no
  automated tests for a prose skill — its correctness is only ever observed by running it, which is
  exactly what we did here.
- **Operator / SRE** — The door was up and warm enough (8.3s incl. model turnaround, `occupancy:
  available`, `routed_by: default` → omen-ollama). `local_generate` returning routing + occupancy
  in-band is what let this retro *cite its own provenance* honestly. No infra incidents; no
  cc-conductor writes (the `--fleet` path stayed unused, so nothing touched the shared box).
- **Product / planning** — Right thing, right size. Derek asked a scoping question first ("is it
  smart enough?") before asking to build, so the tool was shaped by a real limitation instead of a
  guess. Scope held — one skill, no gold-plating — and pacing was deliberate (built, then immediately
  dogfooded, no premature commit).

## Two seats, two views

**From Claude's seat.** I like that the answer to "can the fleet do this?" was a clean split rather
than a yes/no — naming what only I can see (the conversation, the *why*) versus what git already
holds made the design fall out almost for free. Where I could over-reach on future runs: the "update
docs/plans/README" phase is the dangerous one — it's the easiest place to sweep too wide, so I bounded
it to the diff-affected set in the playbook and will hold that line. What I'd want next time: a cheap
way to diff *my* tool-call log against git, so the factsheet's "files touched" half is mechanical
instead of recalled.

**From Derek's seat** *(my reconstruction of your view — correct me).* You asked the sharp question
first because you wanted to know the *shape of the limitation* before spending tokens on a tool — the
`submit_task`-is-a-dumb-pipe framing is the kind of mechanical-sympathy read you reach for. You'd
value that the skill leaves artifacts everywhere (ADDP) and captures many perspectives in one pass,
and that it offloads grunt drafting to your own idle mechnet rather than burning frontier tokens. You'd
want the commit to wait on your cue, and you'd probably want an HTML mirror only when a retro is worth
publishing, not by default.

## Lessons learned

1. **`submit_task` carries only its prompt string — retrospection context is frontier-assembled, not
   transport-provided.** → **ADR-0004** (the reusable principle behind `/retro` and any future
   offload-of-authoring).
2. **A prose-generating skill's only test is running it.** The recursive shakedown (run `/retro` on the
   session that built `/retro`) is the cheapest honest test and should be the default for skill work. →
   doc/practice note (this retro).
3. **Scope a retro to *this* session's diff, not the whole working tree.** Foreign untracked files
   (`bankedfire_drain*`) were present; claiming them would have been a fabrication. The guardrail earned
   its place on first run. → already codified in the skill's guardrails.
4. **Offloading is also an observation.** The `local_generate` draft landed on the ledger by construction
   — the retro's own authoring is now a captured signal (capture-first). → memory + `record_event`.

## Provenance

Git range: since `5573979` (last retro commit); this session added only
`.claude/skills/retro/SKILL.md` (untracked). Offloaded: the five role-read first-passes
(`local_generate`, qwen3-coder:30b, edited after). Frontier: factsheet, all repo writes, ADR wording,
this whole edit. `--fleet` not used (no independent draft). Derek's-seat section is a reconstruction.

---

# Session Retro — 2026-07-04 (addendum 2 · Banked Fire P1–P5 · the wind tunnel armed)

> One-line: from "where are we at" to **stage-6 autonomy armed in one session** — mothballed the
> external pitch, found and fixed the door that had died with a closed console window, wrote the
> Banked Fire strategy on verified probes, and shipped **P1–P5 of bankedfire** (backend pool →
> occupancy → task lane → watchdog → idle-drain) with the drain live-ARMED — while Fable never
> built a line itself: **Sonnet agents built, qwen drafted, the frontier designed and reviewed.**

## What this session was

A design→delegate→ship session, and a deliberate economics experiment in its own right: after the
strategy and review work, Derek's constraint ("I can't afford your tokens to build this") forced
the token-tier split that the rest of the session then proved out. Concurrent with (and interleaving)
an independent builder session that owned the oxen rename + P1.

## What shipped (this session's commits; `584d816`/`8c0a75a` are the concurrent session's)

| Commit | What |
|---|---|
| `de19082` | gateway wrapper observability (the silent console-death fix's first half) |
| `ec2ca27` | Proving Ground proposal **mothballed** to `archive/` (priority → in-house MCP + mechnet) |
| `322ab7d` | **doorcheck CLI** (`--revive` relaunches DETACHED; kill/revive proven live) + `/checkmcp` + `/checkmechnet` skills + gateway check in fleet inventory |
| `3ddc8e5` | **HEARTH-BANKED-FIRE-STRATEGY.html** — Δ4 made concrete on verified probes; two lanes, no second scheduler, P1–P5 |
| `a825adf` | **P4 mechnet watchdog** committed (concurrent session's build; tests re-verified 8/8) |
| `7bd6441` | **P2 occupancy** — AM4 render-owner serve-truth over SSH, 30s cache, fail-open, `Lease` helper (Sonnet agent) |
| `7b55fb7` | **P3 task lane** — `submit_task`/`task_status`, conductor inbox drops, zero conductor changes (Sonnet agent) |
| `ea71d93` | codename **bankedfire** ratified; P1–P4 doc status (Sonnet agent) |
| `f790ec4` | **P5 idle-drain** — tick: arm→occupancy→budget→highest-worth candidate→dispatch; authored arm toggle; `BankedfireDrain` 30-min task; **ARMED** after a clean supervised busy-no-op cycle (Sonnet agent) |

New durable artifacts: `hearth/callers/doorcheck.py`, `hearth/toolsurface/{occupancy,task_lane}.py`,
`fleet/{mechnet_watchdog,bankedfire_drain}.py` (+ cmd wrappers + tests), `.claude/skills/{checkmcp,checkmechnet}/`,
two registered scheduled tasks (`MechnetWatchdog`, `BankedfireDrain`), `archive/`, the three-plane
doctrine (memory + strategy-doc panel), ADR-0005/0006 (below). Tests 148 → 226; gateway 21 → 23 tools.

## The team retro — our collaboration across the seats

*(First passes drafted by qwen3-coder:30b via `local_generate` and edited against the factsheet —
it predictably credited Derek with hand-rolling the client and building doorcheck; those were
Claude's. Corrected here; the seat-level judgments are frontier.)*

- **Architect** (Claude proposed, Derek + another Claude sharpened) — The load-bearing calls were
  *reconciliation* calls, not inventions: Banked Fire is Derek's own Δ4 with plumbing, the task lane
  rides the existing conductor instead of growing a second scheduler, and the three-plane review
  adopted an outside model only after verifying its factual claims in code and amending where it
  oversimplified (control-surface roles; conductor-FS demoted to admission signal; layered revive).
  What to change: the strategy doc was written before discovering the AM4-MCP tools existed — a
  five-minute capability inventory of the *whole* mechnet before writing would have gotten P2's
  design right the first time.
- **Implementer** (three Sonnet agents, ~444k of their tokens) — Precise zero-context briefs turned
  out to be the real interface: all three agents shipped on-spec with tests (31+12+27 new) and none
  needed a second round-trip. The one real defect (the `subprocess.run` default-arg binding that
  defeated a test mock) was the agent's own, caught by the agent, reported honestly. What to change:
  briefs should mandate dry-run/mock-verification *before* any code path can touch a shared box —
  the 7 stray inbox items were harmless but real.
- **Reviewer / QA** (Claude, with the live system as witness) — Verification-before-endorsement
  carried the session: the three-plane suggestion's tool claims were checked in source before
  adoption, both kill/revive loops were proven against the *live* gateway rather than asserted, and
  the P5 supervised cycle was accepted on a busy-no-op — the *correct* boring outcome — instead of
  forcing a dispatch for a prettier demo. What slipped: the stray-items incident reached the real
  conductor before tests caught it; test isolation for anything SSH-shaped needs to be paranoid by
  default.
- **Operator / SRE** (Claude on OMEN; the mechnet itself) — Two genuine incidents, both closed with
  root cause: the gateway's silent death was a console-lifetime kill (task never registered — the
  script's own comment claimed otherwise), fixed structurally with DETACHED relaunch + watchdog; and
  the B70s' real contention stalled the P3 acceptance run, which is not a failure but the P5
  admission gap demonstrating itself on schedule. Self-healing went from zero to two registered
  watchdog layers in one session. What to change: thermal/power ceilings are declared in the budget
  but not yet measured — Δ2 telemetry is now the obvious next operator investment.
- **Product / planning** (Derek's seat, exercised exactly as designed) — Derek made every
  irreversible call (mothball, name, codename, delegate, arm) and spent zero time on reversible
  ones; the pacing directive held (strategy stayed draft until the cue). Scope grew once —
  "strategy" became "strategy + build" — but by explicit ratification, not drift. The token
  constraint was the session's best product decision: it forced an operating model (below) that is
  itself reusable.

## Two seats, two views

**From Claude's seat.** The session's quiet lesson is that my highest-value output was *briefs and
verification*, not code — every line shipped was written by a cheaper tier against a spec I could
hold to account, and nothing needed rework. Where I nearly over-reached: I was one keystroke from
building P2 inline before Derek's cost correction; the delegation produced the same outcome at a
fraction of the spend. Where I under-reached: I recommended wiring the HEARTH MCP into session
config in my first hour and then let it sit — the whole session ran on a hand-rolled client, which
worked but left the CLAUDE.md directive unexecutable for any session that doesn't think to do the
same. What I'd want next time: the factsheet's "files touched" half assembled mechanically from my
tool log instead of recalled.

**From Derek's seat** *(my reconstruction of your view — correct me).* You got the thing you've been
circling for weeks: the wind tunnel actually armed, on your own hardware, under your own authored
budget, with every crossing on one ledger — and you got it without burning frontier tokens on grunt
work, which was the point of HEARTH all along. The three-plane doctrine landing as a ratified ADR
matters to you because it's the argument you'll reuse when the next shiny "second connector" idea
shows up. You'd flag two things: the stray-items incident is exactly why agents don't get cleanup
authority on shared boxes without asking, and the first unattended overnight drain is the real
acceptance test — tonight's ledger is the artifact you actually care about reading tomorrow.

## Lessons learned

1. **A comment claiming "registered as scheduled task" is not a registered scheduled task.**
   Services must be *provably* detached from consoles and watched by something that heals them.
   → done structurally (doorcheck DETACHED + two watchdog layers); runbook note in `/checkmcp`.
2. **Verify an outside reviewer's factual claims in source before adopting the design.** The
   three-plane suggestion was right *because* `render_owners`/`start_oxen_backend` were real; the
   review's value was the verification plus the three amendments. → practice, recorded here.
3. **One boundary or the dataset fragments.** → **ADR-0005**.
4. **Autonomy is earned by a boring supervised cycle, not granted by a flag.** The drain got its
   ARMED state because it correctly did *nothing* on busy hardware. → **ADR-0006**.
5. **Token-tier delegation works when the brief is the contract.** Frontier designs/reviews, mid-tier
   builds, local drafts — three agents, zero rework rounds. → memory (`feedback-token-tier-delegation`).
6. **Anything SSH-shaped in tests gets paranoid isolation.** Default-arg binding of `subprocess.run`
   silently defeated a mock and leaked 7 items to a shared box. → brief-template note (5's memory).
7. **The B70 stall was the thesis proving itself** — the conductor dispatches without checking
   occupancy; P5's admission gate is the fix and it was already designed. → no action, satisfaction.

## Provenance

Git range `8c8213b..f790ec4` (+ this docs commit); `584d816`/`8c0a75a` are a concurrent session's,
scoped out. Offloaded: timeline/role-read/lessons first passes (`local_generate`, qwen3-coder:30b,
~35s, edited against the factsheet — attribution errors corrected) and 5 commit-message drafts
across the session. Fleet second opinion: `--fleet` dispatched as plan
`hearth-retro-2026-07-04-a2c55db0` (am4-worker-1; check `task_status` later — B70s were under real
load at dispatch time). Frontier: factsheet, all repo writes, ADR wording, both seat views; Derek's
seat is a reconstruction. Builds credited above were Sonnet-agent work against frontier briefs.

**Correction (same evening).** The P3 acceptance stall and the `--fleet` retro dispatch were NOT
slow-grinding under B70 load — both had crashed the conductor's fan-out (`FanOutEdgeGroup` requires
≥2 targets; single-builder `submit_task` was incompatible). Another session fixed the task lane
(`e287059`) and wrote stub results to clear the phantom in-flight state. Lesson 7 above is therefore
half-right: the occupancy probe's busy reading was real (llama-server did hold both render nodes),
but the stall itself was this bug. Both requests were re-fed through the fixed lane:
`hearth-retro-2026-07-04-r2-36f26f8d` and `hearth-occupancy-risks-r2-c140665f` (the fix visibly pads
dispatch with a second builder). Phantom-busy from crashed runs was itself flagged in P2's recon as
the #1 false-busy trap — it caught our own dispatches.

---

# Session Retro — 2026-07-04 (addendum 3 · the fan-out fix → the guard dog learns to see)

> One-line: a question about a stuck board became a **root-caused fan-out crash fixed live**, which
> became **Watchfire** — a coherence-watching sense that turns the fleet's own lies into signal, built
> rules-first as Slice 0 (`preflight` + `remediate`) and **wired into the watchdog so it now patrols
> and self-heals the obvious autonomously.** The through-line, a fourth time today: *the fleet's
> signals don't mean what they look like* — so we taught the guard dog to notice when they disagree.

## What this session was

A diagnose-then-build session that started from one blunt operator question — *"are the jobs actually
running? I see them entering the board but not moving"* — and ended with the mechnet watchdog patrolling
for **incoherence**, not just death. Heavy SSH into the conductor, three gateway reloads, and a design
shaped almost entirely in conversation (Derek intuiting, Claude instrumenting).

## What shipped (`8c0a75a` → `b75f28a`, this session's commits only)

| Commit | What |
|---|---|
| `e287059` | **fix** — `submit_task` pads to the conductor fan-out minimum (≥2 local builders). The bug that made every single-builder HEARTH job crash on dispatch. Pushed. |
| `2384934` | **WATCHFIRE-FLARE-DESIGN** v0.2 (→ v0.3) — NPU charm for the guard dog; one engine, two sensitivities; SVG diagram |
| `c3495d0` | **Slice 0 · preflight** — `hearth/health/gaps.py` (the spellbook) + `hearth.preflight` coherence gap-detector (pure observer) |
| `82f4e35` | **Slice 0 · remediate** — the first *healing* spell: auto-heals obvious+reversible gaps, flags ambiguous; + a fix so a heal *resolves* a gap, never relabels it |
| `b75f28a` | **watchdog patrols coherence** — `remediate` on every 15-min tick; Watchfire goes autonomous |

New durable artifacts: `WATCHFIRE-FLARE-DESIGN-2026-07-04.html`, `hearth/health/`,
`hearth/toolsurface/{preflight,remediate}.py`, `docs/adr/0007`. ~28 new tests; hearth suite 196 green,
fleet 55 green. *(Concurrent other-thread commits `a353c09`/`a3dc3a9`/`a2a310b` are explicitly excluded.)*

## The team retro — our collaboration across the seats

*(Role-read first-passes drafted by qwen3-coder:30b via HEARTH `local_generate` — 16s — then edited
against the factsheet.)*

- **Architect** — The strongest call was structural, and it was Derek's: *don't build a new watcher —
  hand the guard dog we already have (`mechnet_watchdog`) a charm.* That collapsed a whole "new system"
  into "new spells on P4," and reframed the goal precisely — from *liveness* (is it down?) to
  *coherence* (do the sources agree?). Every failure today was a coherence gap no single view caught.
  What to watch: the auto-heal/flag-only boundary is a policy the code *describes* but can't *prove* —
  discipline lives in `AUTO_HEAL_KINDS`.
- **Implementer** — Low rework, high leverage. The fan-out fix was small and surgical; Slice 0 was a
  clean pure-rules core (`gaps.py`) behind an SSH-gathering tool, which made everything unit-testable
  without a network. The code fought us exactly once, and it was instructive (see QA).
- **Reviewer / QA** — Two real defects, both caught by *running the thing live*, not by tests: (1) the
  original fan-out crash slipped through because the P3 tests mocked the conductor and never exercised
  the real fan-out; (2) my own detector re-flagged the runs it had just healed (a heal-stub is still a
  stub) — caught seconds after the first live heal, fixed so a heal resolves. Lesson: mock-only tests
  hide integration truth, and *dogfooding on live state is the real test.* 28 new tests lock the fixes.
- **Operator / SRE** — This is the seat that changed most. The watchdog now does two things per tick —
  revive down services (liveness) *and* auto-heal phantom runs (coherence) — with liveness kept as the
  health gate so a coherence-sweep failure never fails the patrol. Three gateway reloads to ship live
  tools; the scheduled `MechnetWatchdog` task verified registered and firing. Every heal is reversible
  (delete the stub) and ledgered.
- **Product / planning** — Right thing, and it compounded. We didn't set out to build a self-healing
  sense; we set out to answer "are the jobs running?" — and each honest answer opened the next brick
  (crash → fix → why-did-nothing-warn-us → a sense that warns). Scope stayed disciplined (rules-first,
  NPU deferred until there are labeled reps) and pacing was Derek's throughout.

## Two seats, two views

**From Claude's seat.** The satisfying part was that the diagnosis *became* the design: the four false
signals (in-flight-but-crashed, B/70-but-empty, ran-but-stale, busy-but-idle) are literally the gap
kinds `gaps.py` now checks. Where I had to stay honest: the `stale_checkout` and false-B/70 findings
were tempting to auto-fix, but they're ambiguous — I kept them flag-only, which is the whole point.
What I'd want next: the "fans digitized" spell (AM4 GPU-util vs a running claim) isn't built yet, and
it's the one that most directly encodes Derek's own instrument.

**From Derek's seat** *(my reconstruction — correct me).* You heard the fans stay quiet while the board
claimed work, and trusted the fans — that mechanical-sympathy read is the seed of the entire design,
and you said it felt like a gift a remote dashboard can't give. You'd want the obvious decisions made
*for* you (it's a research lab; act, document, undo), the ambiguous ones left as eyes not authority,
and the whole thing to ride the guard dog you already trust rather than a new daemon. You paced every
commit and never let scope run.

## Lessons learned

1. **Single-builder `submit_task` crashes the conductor fan-out; the task lane must dispatch ≥2.** The
   HEARTH pad is the stopgap; the clean fix (conductor single-builder → direct-assign) is a deferred
   follow-up. → git `e287059` + open follow-up (not yet an ADR — decision not yet made).
2. **Mock-only tests hid a live-integration crash.** The P3 suite passed while the lane was broken in
   production. Dogfooding on live state is the real acceptance test. → practice note (this retro).
3. **The guard dog should watch coherence, not just liveness** — every failure today was a
   claim-vs-truth gap. → **ADR-0007**.
4. **Auto-heal the obvious + reversible, flag the ambiguous, document everything** (research-lab
   policy). A heal must *resolve* a gap, never relabel it. → **ADR-0007**.
5. **A healing tool can manufacture the gap it closes** — validate remediation against the *next* scan,
   not just the action. → codified in `gaps.py` + tests.

## Provenance

Git range `8c0a75a`→`b75f28a` (this session; concurrent `a353c09`/`a3dc3a9`/`a2a310b` excluded).
Offloaded: the five role-read first-passes (`local_generate`, qwen3-coder:30b, edited after). Frontier:
factsheet, all repo writes, ADR-0007, this section, every diagnosis and fix. `--fleet` not re-run here
(the earlier fleet draft is blocked on stale `~/commandcenter-src`, a separate thread). Derek's-seat
section is a reconstruction. 4 commits unpushed at write time.

---

# Session Retro — 2026-07-04 (addendum 4 · the job shop scheduler · the 2019 article was the map all along)

**One-line:** Derek revealed his 2019 OR-Tools job-shop article as the north star — and in one
session the lab **became the scheduler**: JS1–JS7 built by a tiered agent fleet, integration-first,
live on the door, with a 250-job synthetic assay proving the CP-SAT formulation beats FIFO 19→0
on deadline misses.

## What this session was

A revelation, then a sprint. The doorcheck was routine; the pivot was Derek linking his
7-year-old article ("Solving the Job Shop Problem", Google OR-Tools CP-SAT) and naming it the
next level for HEARTH/mechnet/Watchfire — with the recognition that *"it always seemed to come
together... almost like I was recreating another system I lived in."* Everything since (ledger,
assay, capacity instrumentation) was, in hindsight, building the solver's input dataset. Derek
then waived the staged gates ("skip to the end and the integration test works... that's a huge
win"), so this became a build-to-integration session with the plan doc as contract.

## What shipped (`8d438a6` → `9674e32`, 24 commits, 43 files, +5118 lines)

| Commit | What |
|---|---|
| `73a1d0a` | JS1 — ledger events carry `task_class` + `model` (additive schema, sqlite migration, `_ledger_model` lift) |
| `4c80301` | JS2 — capacity projection → `knowledge/capacity.json` (p50/p90 buckets, legacy-tolerant) |
| `be7bbd9` | JS3 — CP-SAT shadow scheduler (`hearth/scheduler/`, `propose_schedule`, two-economies objective, emits scheduler-decision.v1) |
| `20c8781` | JS6 — Watchfire `schedule_divergence` spell (flag-only, >2× capacity p90) |
| `1cf7fc5` | JS4 — `schedule_hindsight` regret replay |
| `9d3ba94` | **Integration fix**: conductor success runs have NO `status` key — completion = no-status + winner; verified live on 50 real runs |
| `be722ec` | JS7a — AM4 catalog ingest (vllama `models.json` + b70tools warmups → `knowledge/am4_catalog.json`) |
| `4b7d70c` | JS7b — setup-aware CP-SAT: model residency, shared load intervals, DDR4 staging NoOverlap, per-card VRAM budgets |
| `037fc1a` | **Integration fix**: `required_model` = hard eligibility (jobs were escaping to stateless/frontier machines); `est_out_tokens` in token objective |
| `a364642`–`9674e32` | U1–U6 surgical upgrades (5 haiku agents): `est_duration_s`, patrol auto-refresh + regret ledgering, imagegen-250 assay promoted to fixture, null-p90 guard, `_ledger_task_class` lift |

New durable artifacts: [JOB-SHOP-SCHEDULER-PLAN.html](JOB-SHOP-SCHEDULER-PLAN.html),
`hearth/scheduler/` (ontology · solve · decision · hindsight · experiments/imagegen_250),
`hearth/toolsurface/{scheduler,am4}.py`, contracts `capacity.v1` + `am4-catalog.v1`,
`knowledge/{capacity,am4_catalog}.json` (now patrol-refreshed), ADR-0008/0009 (this retro).
Gateway ends HEALTHY, 32 tools.

## The team retro — our collaboration across the seats

- **Architect** (Derek's intuition, Claude's math — the how-derek-scales engine verbatim):
  the two big calls were both Derek's and both right: naming the JSP frame (which turned scattered
  lab instruments into *solver inputs*), and the AM4 insight that **load time + prerequisites,
  not runtime**, is the real scheduling problem on 64GB-VRAM/32GB-DDR4 — classic
  sequence-dependent setups + a single staging resource. Claude's contribution was recognizing
  both as textbook CP-SAT shapes and finding the pre-existing `scheduler-decision.v1` contract so
  we extended rather than invented. Advisory-first (shadow → hindsight → gated actuation) held
  even with gates waived: JS5 actuation remains unbuilt *by design*.
- **Implementer** (sonnet slices, opus for the solver, haiku for surgery; frontier merged):
  slice briefs as contracts worked — every agent shipped green tests on the first pass. The
  solver formulation (shared load intervals via OR-literals, staging NoOverlap, per-card charge)
  came back from opus correct and deterministic. Where code fought us: nothing in the math —
  all three real bugs were *world-model* bugs (wrong assumptions about conductor data shapes).
- **Reviewer/QA** (frontier, deliberately): integration-first caught what unit tests
  structurally couldn't — the no-status-key success shape, the eligibility escape, the token
  fallback. All three were faithful implementations of wrong beliefs; only contact with real
  data exposed them. The imagegen-250 assay is now a 0.1s regression guard encoding the
  headline result (3 loads / 0 misses vs FIFO's 24 / 19).
- **Operator/SRE**: two clean gateway reloads (kill + doorcheck --revive, ~15s each); ortools
  9.15 installed first-try on the Windows venv (de-risked before JS3 was briefed). The known
  flaky Windows temp-teardown test wandered as usual; ignored deliberately, documented.
  One self-inflicted scar: `git add -A` during conflict resolution committed the generated
  `knowledge/*.json` — harmless (patrol now refreshes them) but against JS2's own rule.
- **Product/planning**: pacing was Derek's explicitly this session — he waived his own gates
  and set the tier budget ("sonnet builds", "haiku surgical", "opus to fit"). Scope stayed on
  the plan's rails even at speed; the one unplanned addition (imagegen assay) was Derek's ask
  and became the session's best evidence.

## Two seats, two views

**From Claude's seat.** The thing I'd bottle: *integration as the review*. Three bugs, none
findable by the agents that wrote them, all found within minutes of touching real data — the
"skip to the end" instruction was the highest-leverage decision of the session and it wasn't
mine. Where I under-reached: I trusted scout reports as ground truth when writing briefs (the
status-key claim propagated into two agents' code); scouts confirm *existence*, only live data
confirms *shape*. Where the cheap tier bit: 3 of 5 haiku worktree agents branched from stale
bases and rebuilt existing code from scratch — one recreated the whole scheduler, one made
`task_class` required (would have broken every legacy event). My merge review caught all of it,
but that review is load-bearing; budget for it whenever haiku writes code.

**From Derek's seat** *(my reconstruction — correct me)*: seven years ago I wrote down the
math for this exact system; today it scheduled 250 jobs optimally on my own hardware using
durations my own lab measured. The lab finally paid off as designed — capacity → ontology →
solver → talk at the schedule level. The tier economics worked: frontier briefed and merged,
everything else was sonnet/opus/haiku/qwen on sunk compute. What I'd watch: the haiku
stale-base mess means "surgical" briefs aren't actually cheap if Fable has to hand-reapply
them — either fix worktree branching or stop pretending haiku can touch shared files.

## Lessons learned

1. **Scout reports confirm existence, not shape** — any data contract a brief depends on must be
   verified against one live sample first. → folded into ADR-0009.
2. **Integration-first catches world-model bugs that unit tests faithfully encode.** Three for
   three this session. → ADR-0009 (build protocol).
3. **The scheduler stays advisory until the ledgered regret trend proves it** — patrol now accrues
   that trend automatically every 15 min. → ADR-0008.
4. **Haiku worktree agents may branch from stale bases and rebuild existing code** — merge
   strategy: `checkout --ours` for rebuilt files + hand-apply the small intent; frontier merge
   review is mandatory, not ceremonial. → ADR-0009 + memory.
5. **Setup times dominate on AM4** (32GB DDR4 staging vs 64GB VRAM) and the B70 stack already
   measured them (`warmup.wall_ms`) — reuse of hard-won data beat new instrumentation. → memory
   (already written).
6. Cold-load vs first-inference split is still unmeasured (open item in Derek
's own
   PICKUP-battlemage.md) - the one instrumentation gap left. -> doc note, no ADR.

## Provenance

Git range `8d438a6` -> `9674e32` (24 commits; all this session's). Offloaded: timeline/role-read/
lessons first-pass (`local_generate`, qwen3-coder:30b, 20.3s, edited - it misattributed the plan
doc to Derek and garbled the scout targets; corrected against the factsheet). `--fleet` second
opinion dispatched: plan_id `hearth-retro-2026-07-04-jobshop-35fc03d7` (poll `task_status`).
Frontier: factsheet, all judgments, ADR-0008/0009, every repo write. Derek's-seat is a
reconstruction. Suite green at write time (272 tests + assay 6); commits unpushed.

---

# Addendum 5 — the CQRS fan-out session (advisory)

**One-line:** Derek asked one sentence — *standardize toward classic event sourcing, fan out
as needed* — and the suit **ran a full scout → three-lens review → synthesis lap** producing a
ratifiable 10-step plan, zero code touched; then Derek named what he was watching: Phase 7.

## What this session was

Pure advisory/design — no commits, no code edits. A structured architecture review of the
HEARTH event/ledger/projection layer against classic CQRS/event-sourcing, executed as a
multi-agent fan-out: 1 Sonnet explorer mapped the terrain, then 3 parallel reviewers
(Opus ES-purist, Sonnet pragmatic-migration, Opus query-side/projections), then frontier
synthesis. Followed by Derek articulating **Phase 7 of the Mech Suit Methodology** live.

## What shipped

No commits. Durable artifacts:
- [docs/CQRS-ES-STANDARDIZATION.md](docs/CQRS-ES-STANDARDIZATION.md) — full synthesis:
  unanimous calls, sharpest findings, don't-bothers, 10-step execution order, open decisions.
- Memory: Phase 7 articulation added to `reference-mech-suit-methodology`; new
  `project-cqrs-es-review` memory.
- Fleet second opinion in flight: `hearth-retro-2026-07-04-cqrs-fanout-d164cdf2`.

## The team retro — our collaboration across the seats

- **Architect** (Derek intent, Claude structure): the fan-out design was right-sized — three
  *genuinely different* lenses (purist/migration/query-side) rather than three redundant
  reviewers, and they converged independently on the big calls (two bounded contexts, rebuild
  button, corpus_guard demotion). Convergence across diverse lenses is the signal; identical
  prompts would have proven nothing. The Three Chairs pattern is now an operational move, not
  a one-off artifact.
- **Implementer**: idle by design. The output is deliberately fleet-briefable — each of steps
  2–4 is a self-contained builder prompt per the token-tier protocol. Nothing was built that
  Derek hadn't paced (fleet-handoff-pacing honored: plan drafted, release on his cue).
- **Reviewer/QA**: the reviewers spot-checked code rather than trusting the explorer's map
  wholesale — the query-side agent's verification pass surfaced the session's sharpest
  finding (the unguarded `capacity.json` bare `write_text` bypass), which the map alone had
  only hinted at. Lesson from Addendum 4 applied: scout reports confirm existence, reviewers
  confirmed shape.
- **Operator/SRE**: the door behaved — `local_generate` (qwen3-coder, 24.7s) and `submit_task`
  both clean on first call; no revives needed. The retro's own offloads landed on the ledger
  per capture-first.
- **Product/planning**: exactly the asked-for deliverable, no scope creep into building.
  The one addition (Phase 7 memory capture) was Derek's own articulation, worth durable
  storage on its face.

## Two seats, two views

**From Claude's seat.** The clean part: the lap cost Derek one sentence, and the synthesis is
decision-ready rather than a wall of agent output — the convergence check (what did all three
agree on?) did most of the compression work. What I'd repeat: sequencing the explorer *before*
the fan-out so all three reviewers shared one verified map instead of each re-exploring.
What I'd watch: advisory sessions produce recommendations that *feel* done — nothing here is
real until steps land and the golden determinism test passes.

**From Derek's seat** *(my reconstruction — correct me)*: this is what the suit is for. I said
one sentence about a pattern I care about and got back a fact-checked, sequenced, fleet-ready
plan that found a live blind spot in code I thought was guarded. And the session itself was
the demonstration of Phase 7 — pieces became roles, I scouted/drove/posted up/collected. The
part that isn't transferable is that I actually care whether the ledger tells the truth;
that's why the review was worth running at all.

## Lessons learned

1. **Lens diversity, not reviewer redundancy, is what makes fan-out reviews trustworthy** —
   independent convergence across different framings is the acceptance signal. → memory
   (folded into mech-suit Phase 7 note); candidate ADR if ratified as review protocol.
2. **Advisory output must be fleet-briefable to be real** — each recommendation step sized and
   scoped as a zero-context builder prompt, or it's just an essay. → practice, no ADR.
3. **A guard that must fire to prevent data loss is a design smell** — the corpus_guard
   demotion principle generalizes: replace conventions with mechanisms, keep guards as
   tripwires. → becomes an ADR when Derek ratifies the CQRS plan.
4. **The `capacity.json` bypass shows guards rot silently as new writers appear** — new
   projection writers didn't inherit guard coverage. Interim: watch for it; structural fix is
   plan steps 2/4. → doc (captured in CQRS-ES-STANDARDIZATION.md).

## Provenance

Git range: none (zero commits; working tree clean at ea0b62a throughout). Offloaded:
timeline/role-reads/lessons first pass (`local_generate`, qwen3-coder:30b, 24.7s, 906 tok —
edited; draft was faithful, mildly fluffy, no factual inventions this time). `--fleet` second
opinion dispatched: plan_id `hearth-retro-2026-07-04-cqrs-fanout-d164cdf2` (verifies the
blind-spot claim against code; poll `task_status`). Frontier: factsheet, synthesis doc, all
judgments, this addendum. Derek's-seat is a reconstruction.
