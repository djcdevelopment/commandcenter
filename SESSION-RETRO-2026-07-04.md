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
