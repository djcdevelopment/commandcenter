# Session Retro — 2026-07-18 (oxen gets a brain: swap → ze_peer → resident 120B → gambit → real work)

> **We gave mechnet a resident brain and proved it before trusting it** — the D2 swap landed
> by Derek's hand, the P2P link measured at full wire speed, gpt-oss-120b made a permanent
> tenant of both B70s behind a goodput-routed HEARTH rung, a six-sweep gambit that found *no
> breaking point*, and then four real backlog items run through the new brain the same night.
> The through-line: *build → characterize → then depend — and know which half of every task
> belongs to the machine.*

## What this session was

A **build-and-prove** session in five acts, each gated on the previous: capture (swap receipt),
measure (O1 ze_peer), decide (the resident-MoE design question), build (am4-moe rung), stress
(the gambit), use (real workload). One overnight arc, ~05:30→09:30. Three root-gated
interventions from Derek (remount-rw, NTFS ACL, ufw :8082) — and **zero course corrections**.

## What shipped

| Commit | What |
| --- | --- |
| `a6b3ada` | Plan v1.8 — O1 swap/zram landed (Derek's hand); successor receipt `4d91410a` done 5/5 |
| `8a52e70` | O1 ze_peer results — 14.3 GB/s uni = full x8 wire, no P2P penalty; copy engine free; raw logs |
| `ecd009f` | Plan v1.9 — O1 bench done |
| `10c12a3` | feat(oxen-moe) — am4-moe rung + slot/KV goodput probe; oxen-planner pin-only; 527 tests |
| `384d9fb` | merge to master |
| `15c8089` | Plan v2.0 + oxen-moe results — resident bring-up numbers, wait-in-line proven |
| `a07b8cc` | Gambit results — 453/453, no breaking point; instruments committed rerunnable |
| `a77ce19` | Plan v2.2 + real-work artifacts — capacity facts, porcelain design, sync rec, O4 draft |

**Durable artifacts:** 4 build receipts closed with full evidence (`4d91410a` 5/5, `4f780d5d`
5/5, `50a1d2f7` 6/6, `fe90b0be` 6/6); `b70-moe.service` resident on AM4;
[capacity-facts-2026-07-18.json](am4-fleet-node/results/capacity-facts-2026-07-18.json) (O5
seed); [o4-windows-delta-draft.md](docs/drafts/o4-windows-delta-draft.md) (D4 unblocking);
[DECISIONS-PENDING-SYNC-2026-07-18.md](docs/DECISIONS-PENDING-SYNC-2026-07-18.md); the
rerunnable gambit instruments; [ADR-0018](docs/adr/0018-resident-moe-steady-state-tenant.md);
this retro. In flight: the porcelain implementation chip (started by Derek in a separate
session).

## The team retro — our collaboration across the seats

**Architect.** The load-bearing calls were made *before* their proof arrived, and held: the
VRAM arithmetic said `--n-cpu-moe 4` was a fit requirement before the download started; the
residency handover (planner/critic → pin-only, revive *removed* because an auto-revive would
start a model into the moe's VRAM) was decided at design time, not discovered as an incident;
the occupancy probe was designed as a registry precisely because the fuser probe's semantics
("someone holds the cards") are wrong for a rung whose own server always holds them. The one
architecture miss was sequencing, not logic: the three root gates on the 4TB path (fstab-ro,
NTFS ACL, ufw) were discovered serially — the runbook's own "read-only-ish" and the :8090
ufw precedent were sitting there, and a five-minute storage/network preflight would have
batched all three asks into one Derek window.

**Implementer.** Zero-sudo throughout on a box we don't own: local libpng into `~/o1/prefix`,
the `BUILD_ZE_PERF_TESTS_ONLY` escape hatch, a CMake-4 policy shim, a manifest-driven download
script that absorbed the single-file-vs-shards surprise without editing. The serve unit was
staged before the model existed so launch was one command. Rework was small and real: the
probe registry override bug (one round), a serve-script filename fix. The gambit driver —
streaming TTFT, slot/mem/temp sampler, crash-safe JSONL — worked on its first full campaign.

**Reviewer / QA.** The strongest seat tonight, and the busiest. The test suite caught the
probe-registry bug *as designed* — five occupancy tests suddenly making real SSH calls showed
up as assertion failures before merge, not as flaky CI later. Human review caught the moe's
wrong porcelain status characters (`*`/`=`, not `+`/space), flash's fabricated narrative twice
(a "disk pressure during compilation" story; "Ubuntu 24.04"), and the local retro drafts'
inverted causality (below). The capacity-facts A/B doubled as a review instrument: two models,
one brief, verifiable numbers. What slipped: nothing into production — but two moe calls
burned on timeouts that were predictable from published numbers (see Operator).

**Operator / SRE.** Four receipts closed with evidence; the receipt lane's immutability held
against me (close-then-update both refused on the blocked predecessor) and the successor-
receipt pattern kept the audit trail honest. The three root gates were handed to Derek as
exact paste-able lines, each verified before proceeding. The server survived everything the
gambit threw — including a 19.3 GiB swap peak with decode intact — and llama-server's
cancel-on-disconnect was verified so abandoned door calls don't leak slots. The seat's real
lesson: I learned the timeout physics (door 120s default, MCP client 300s idle vs ~27 tok/s
including reasoning) by burning two calls, when the ceiling was *derivable from the gambit
numbers I had just committed*. The scorecard also exposed a rung-onboarding gap: `am4-moe`
missing from the economics cost-class map (fixed this retro, live on next gateway restart).

**Product / planning.** Derek's cues were honored beat for beat: a screenshot became captured
state; a design question got an assessment and *stopped* (no unrequested build); two "make it
so"s became receipted builds; "real work, not synthetic" became four backlog items with his
decision register deliberately untouched and the O4 draft staged for his edit, not published.
Scope discipline held under temptation — the sync table recommends, the article awaits, the
porcelain design became a chip rather than a tangent. The pacing signal worth keeping: zero
corrections across a ~4-hour arc that crossed infra, benchmarking, integration, and writing.

### Two seats, two views

**From Claude's seat.** The judgment I'm keeping: knowing which half of each task was mine.
Verification, receipt closure, domain-fact checking, and every repo-coherent write stayed
frontier; drafting, serving, and benching went to the machines — and when the drafts came
back wrong (flash's fabrications, the moe's porcelain slip, the retro drafts' inversions),
review was the value-add, exactly as ADR-0004 predicted. My miss: I dispatched twice into
timeout ceilings I could have computed from my own published data — 27 tok/s × 120 s was
sitting in the gambit table. The physics of the machine you just measured applies to *you*
first. Next session: derive the dispatch budget before the call, not after the burn.

**From Derek's seat** *(my reconstruction — correct me).* "This is the session the lab became
a colleague. I typed four sentences and three sudo lines all night, and the system did the
rest — receipted, tested, honest about what broke and what it made up. The brain answering as
mechnet's router by morning, on hardware everyone writes off, is the thesis working. Two
things I'd watch: you found the 4TB gates one at a time when the runbook hinted at all three,
and you burned tokens learning a ceiling your own benchmark had already published. Batch my
hand; trust your data."

## Last time's lessons — follow-through (2026-07-05)

| Lesson | Status |
| --- | --- |
| L-2026-07-05-1 — Map running services before starting one | **acted-on** — the moe bring-up opened with a full inventory sweep + runbook read; `:8080/:8081` claimants respected, `:8082` chosen |
| L-2026-07-05-2 — Windows-heritage docs ≠ current deployment | **acted-on** — B70-CARD-MANAGEMENT.md (Linux truth) was the build's source of record |
| L-2026-07-05-3 — Build the tool that removes frontier from the loop | **acted-on** — the resident brain is this pattern's largest instance yet; gambit instruments committed rerunnable |
| L-2026-07-05-4 — Second interruption means off-track | **not exercised** — zero corrections this session |
| L-2026-07-05-5 — One guard root-cause blocks three tools | **acted-on** — knowledge read path lifted in the G-track; `query_capacity`/`query_offload` live on the door |

## Lessons learned

1. **L-2026-07-18-1 — Closed receipts are immutable; completion after closure = successor
   receipt.** `close(done)` returned `duplicate_close` without applying; `update` refused.
   The lane protecting its own history from me is the lane working. *(→ practice; memory done)*
2. **L-2026-07-18-2 — Derive dispatch budgets from the physics you already measured.** Two
   moe calls burned on timeouts computable from the gambit's own table (27 tok/s × timeout =
   output ceiling ≈ 2.5k tokens/call). *(→ practice; capacity facts updated)*
3. **L-2026-07-18-3 — Servers may silently degrade rather than refuse.** The 17.5k over-limit
   prompt truncated with no error. Door-side budgets are load-bearing, not advisory. *(→ ADR-0018)*
4. **L-2026-07-18-4 — A resident tenant needs its own occupancy semantics.** "Someone holds
   the hardware" reads a resident server as permanently busy; slot/KV state is the truth. And
   a probe registry must never override an injected cache's probe (five tests made real SSH
   calls until it didn't). *(→ ADR-0018 + practice)*
5. **L-2026-07-18-5 — Batch the root-gated asks.** Preflight storage/ACL/firewall on a
   foreign box *before* the build: one Derek window instead of three serial gates. *(→ practice)*
6. **L-2026-07-18-6 — Task-shape doctrine for the two drafting rungs.** Brain = reasoning-
   dense, short-output (≤~2.5k tok/call); flash = bulk long-form but its thinking clips ~2k-token
   expansions at an 8192 cap (two-pass it); verify *context claims* from both, not just numbers.
   *(→ ADR-0018; memory done)*
7. **L-2026-07-18-7 — Pressure-test the architecture the day it lands.** The D2 swap was
   proven under 18 GiB of fire hours after Derek built it. "No breaking point" is a measured
   claim with committed instruments, not a vibe. *(→ practice)*
8. **L-2026-07-18-8 — Local drafts of self-referential prose invert causality.** Tonight's
   role-read draft turned discovered-and-handled facts into "we overlooked" narratives even
   from a tight factsheet. Grade, then rewrite; the factsheet discipline stays. *(→ practice,
   reaffirms ADR-0004)*
9. **L-2026-07-18-9 — New-rung onboarding is a checklist, not a vibe.** Tonight touched
   backends.toml, the occupancy registry, timeout guidance — and *missed the economics
   cost-class map* until the scorecard exposed `am4-moe` as `unknown`. Checklist now lives in
   ADR-0018's consequences. *(→ ADR-0018 + doc)*

## Provenance

Git range: **`6a66e4f..a77ce19`** (8 commits, all pushed). Offloaded: role-read first pass
(`am4-moe`, 41.9s, 1054 tok — **edit_verdict: hallucinated**, causal inversions; structure
salvaged, prose rewritten frontier) + timeline/lessons candidates (`gcp-gemini` flash, 21.8s,
646 tok — **edit_verdict: minor-fixes**). Frontier: factsheet, all synthesis, ADR-0018, this
file. Derek's-seat is a reconstruction. `--fleet` not used; no cc-conductor writes. The
porcelain chip runs in a separate session (not this retro's to grade).

## Offload scorecard (S6)

Lifetime through the door: **212 calls, 203k in / 45k out, est. $1.01 saved** (claude-sonnet
reference). Reported ratio **0.69** — artificially low: `am4-moe` (9 calls tonight, 22.4k
in / 13.9k out, ok_rate 1.0) projected as `unknown` because the cost-class map predated the
rung. Fixed this retro (`economics.py` → `am4-moe: sunk`; live on next gateway restart);
corrected ratio **1.0**. Trial burn remains far inside the 200M runway.
