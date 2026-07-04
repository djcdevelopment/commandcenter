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
