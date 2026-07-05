# Session Retro — 2026-07-05 (commander intent lane · matrix harness · AM4-is-Linux)

> **We built the meta-tools that take frontier *out* of the loop** — a local refine↔critique
> "commander intent lane" and a cross-hardware planning-quality matrix generator — proved both
> live on OMEN, then hit an honest wall bringing up the AM4 B70s and **stopped guessing to ask**.
> The through-line: *frontier's job is to build the machine that removes frontier from the next
> hundred runs — and to know when it's thrashing on someone else's hardware.*

## What this session was

A **build** session, front-loaded by a short **triage** and tailed by an honest **recon dead-end**.
It started as "what's left unbuilt across the three plans?" and became two substantial new
capabilities plus a 24-pour idle campaign. Zero commits — everything is uncommitted working tree
(the "commit when asked" default held), so this retro is centered on **artifacts + decisions**, not
a commit table.

## What shipped (uncommitted working tree)

**New capabilities:**
- **Commander intent lane — REFINE slice.** `hearth/commander/{refine,cli}.py` +
  `hearth/toolsurface/commander.py` (HEARTH tools `refine_idea`/`refine_result`) + CLI. A local
  author↔critic convergence loop; **no frontier model in the run loop**. 14 tests green; live
  smoke all-local.
- **Matrix dataset harness.** `hearth/experiments/{matrix,run_pilot}.py` + per-role backend
  routing added to `run_refine` (planner/critic on different boxes) + a held-out critic-panel
  scorer. 237 toolsurface tests green. **Live proof on OMEN:** qwen3-coder:30b planner scored **85**
  vs qwen2.5:14b **65** on a plan-skeleton prompt (held-out judge) — real signal on the first run.
- **Idle speculation campaign.** `campaign/{pour_speculation,status}.py` + manifest — 24 all-local
  fleet pours (2 build slices + a wordcount re-run + 20 speculative planning briefs).

**New artifacts:** `POUR-JS5-ACTUATION-2026-07-05.md`, `POUR-ASSAY-STREAM-ACCEPTANCE-2026-07-05.md`,
three memory files (`project-idle-speculation-campaign`, `project-commander-intent-lane`,
`project-matrix-dataset-experiment`), this retro, ADR-0012.

## The team retro — our collaboration across the seats

**Architect.** The two headline design calls were sound and mutually reinforcing. "Mechnet
shouldn't need frontier to load/retrieve work" → the commander lane's *three modes, local-driven,
frontier-builds-it-once* framing; the matrix as an *orchestration of existing assets* (vllama gates,
b70tools rubric, `run_refine` as the cell engine) rather than a rebuild. The `AskUserQuestion` forks
(interface home, refine engine, first-slice scope; then metric, grid size, prompt set) kept the
builds aimed at what Derek actually wanted. The one architecture miss was operational, not logical:
committing to an AM4 bring-up path before mapping the box.

**Implementer.** Clean, offline-first builds — `run_refine` extended additively (back-compat kept,
existing tests untouched), the matrix cell reusing the same loop, everything injectable so tests run
with a scripted fake and zero network. 21→237 green across the new modules. The rework was all on
the AM4 leg: three failed launch mechanisms (CRLF-mangled wrapper, SSH-detach that the channel
swallowed, logind reaping) before `systemd-run --user` — and even that was moot once Derek said the
serving already exists.

**Reviewer / QA.** Strong test posture: paranoid isolation held (mock model + ssh, no live network),
and the **live smoke was the real reviewer** — it turned "the harness runs" into "the harness
produces a sensible score gradient." Caught early that `run_refine`'s new `backend` kwarg would break
the existing fake, and fixed it in the same pass. What slipped: nothing in code, but a recurring
**guard bug** (three read tools — `schedule_hindsight`, `query_am4_catalog`, `query_capacity` — all
fail with the same "not a registered knowledge tool" error) was surfaced but not fixed.

**Operator / SRE.** This is where the session cost the most and taught the most. I treated AM4 as
Windows because its `vllama`/`b70tools` docs are Windows-native — but the box was **migrated to
Ubuntu**, and the current serving is a Python `oxen-facade.py` over SYCL `llama-server`. I then
thrashed on process persistence (CRLF, SSH detach, logind) instead of first mapping what was
actually running. Derek's correction — *AM4 is Linux; vllama exposes 2 ports, one per B70, backing
most mechnet runs* — revealed I was about to spawn a redundant backend on a box that serves live
work. Right call was to **stop, map, and ask.**

**Product / planning.** We built the right things: the commander lane directly answers the
bottleneck Derek named (frontier-as-dispatcher), and the matrix serves the wind-tunnel doctrine. The
pacing miss was mine — I should have asked "how are the B70s normally served?" before the first
launch attempt, saving ~6 tool-calls of thrash. Derek drove scope and paced via corrections; I held
the whole and did the instrumenting.

### Two seats, two views

**From Claude's seat.** I'm happy with the *build* judgment — reuse over rebuild, offline-first,
frontier-builds-the-meta-tool-then-exits. My failure was **acting before mapping** on unfamiliar
infra: I let the Windows-heritage docs set my prior and pushed launch after launch instead of running
`ss`/`ps`/`systemctl` first. The tell was there early (no `llama-server` process on my *first* check)
and I explained it away. Next time, on any box I don't own: map the running services before I try to
start one, and when a user interrupts twice, treat it as "you're off-track," not "try harder."

**From Derek's seat** *(my reconstruction — correct me).* "Good — you built the two things I asked
for and they work. But you spent too long fighting my hardware. AM4 isn't fragile and it isn't
Windows anymore; the B70s are already served — you should have looked at what's running before trying
to run your own. When I say a thing is leveraged in every run, don't spawn a second one." The
standing preferences this echoes: infra is production-grade (don't treat it as risk), do exactly
what's asked, and he holds the hardware truth I lack.

## Last time's lessons — follow-through

| Lesson (2026-07-04) | Status |
| --- | --- |
| `submit_task` carries only its prompt — context is frontier-assembled | **acted-on** — every pour + speculation brief written as a self-contained contract |
| Scope a retro to *this* session's diff, not the whole tree | **acted-on** — this retro excludes the pre-existing `M` files (WATCHFIRE/dream/patrol/masters_pet), centers on this session's `??` artifacts |
| Token-tier delegation works when the brief is the contract | **acted-on** — 24 pours to Sonnet/local; frontier only designed + built the meta-tools |
| Anything SSH-shaped in tests gets paranoid isolation | **acted-on** — commander + matrix tests mock model/ssh, zero live network |
| One boundary or the dataset fragments (ADR-0005) | **pending** — the commander/matrix runs ledger the intent+result, but per-turn author/critic calls aren't yet on the ledger |

## Lessons learned

1. **L-2026-07-05-1 — Map the running services before starting one, on any box you don't own.**
   The AM4 thrash came from launching against a mental model (Windows, dual-card :8080) instead of
   the ground truth (`ss`/`ps`/`systemctl` = Linux facade up, backend down, config mismatch). *(→ practice)*
2. **L-2026-07-05-2 — Windows-heritage docs are not the current deployment.** `vllama`/`b70tools`
   describe a Windows origin; AM4 now runs Linux (`oxen-facade.py` + SYCL `llama-server`). Docs
   describe where a thing *came from*, not where it *runs*. *(→ memory)*
3. **L-2026-07-05-3 — Frontier's highest-leverage move is building the tool that removes frontier
   from the loop.** The commander lane + matrix harness are meta-tools: expensive to build once,
   they make every future refine/sweep run with no frontier session. *(→ ADR-0012)*
4. **L-2026-07-05-4 — A second user interruption means "you're off-track," not "push harder."**
   Two corrections in a row (32GB caution → "AM4 is rock solid"; launch attempts → "it's Linux, 2
   ports exist") were the signal to stop and ask. *(→ practice)*
5. **L-2026-07-05-5 — One guard root-cause blocks three tools.** `schedule_hindsight`,
   `query_am4_catalog`, `query_capacity` all fail the knowledge-write guard as read tools; one fix
   unblocks the JS5 regret gate *and* catalog/capacity reads. *(→ doc / follow-up pour)*

## Provenance

Git range: **none** (zero commits; working tree carries all new artifacts). Offloaded: role-reads +
lessons first pass (`local_generate`, qwen3-coder:30b, 14.2s, 702 tok) — **edit_verdict:
minor-fixes** (faithful, mildly generic, role labels refined; no factual inventions). `--fleet` not
used. Frontier: factsheet, all synthesis + judgments, ADR-0012, this file. Derek's-seat is a
reconstruction. Live smoke + AM4 recon done over SSH from OMEN; no cc-conductor writes beyond the
campaign's inbox drops.
