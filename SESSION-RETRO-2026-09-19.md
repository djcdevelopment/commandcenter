# Session Retro — 2026-09-19 (fourteen hours, twenty-two laps: the fleet learned to work at 256k, and the disk fooled me twice)

> **Every number that shaped the fleet's long-context policy at the start of the day was a
> Vulkan-on-one-engine number, and by the end most of them had moved by 2–5×** — not from new
> hardware but from an engine switch (SYCL), a cache type (f16), a draft head that was already on disk
> (MTP), and a network trick (ggml-rpc) that let a saved state decode across two hosts. The 27B now
> runs at 262,144 context on the two B70s (8.5 min prefill, 14.4 tok/s), and Flash-Next — parked as
> unworkable a week ago — runs at 262,144 across all four GPUs. The two things I got wrong were both
> the disk, and both times the counter that would have told me was one command away.

Narrative companion, written for reading or a podcast:
[`docs/rnd/JOURNEY-2026-09-19-memsplice-sycl-256k.md`](docs/rnd/JOURNEY-2026-09-19-memsplice-sycl-256k.md).

## What this session was

An **R&D session** (`/rnd`, session `cc-544e4480`, 00:33Z → 14:30Z) that began as "check MemSplice
with the dense 27B" and became a full engine-and-levers campaign against one objective Derek set in
his own units — *total throughput × accuracy of work completed on local hardware, with the B70s kept
decoding* — and one concrete target, the 27B at 256k context. It ran as 22 register rows: MemSplice
laps 7–12c (prefill over the wire, capacity, the across-layouts patch, baked into production), then a
planned SYCL-vs-Vulkan ladder L0–L4, then Derek's research list run to ground (L4c–L4g), then the two
"wild finds" (L4h). Derek steered it at every turn: the scoreboard reframe, "bake it, don't publish,"
"patch prod too," "a little over 3 min still excellent," "instead of saying no, inventory," "run them
all down," "let's do them both," and at the end, "no more long runs."

## What shipped

Ranges **`2f6da9f..73d925e`** (commandcenter, 20 commits, docs/register only — the code lives in the
fork and the memsplice repo) and **`0bd15f2..5b17e02`** (memsplice, 12 commits: drivers + results).

| Where | What |
| --- | --- |
| `E:\work\llamacpp-knee` (fork) | across-layouts restore patch (`src/llama-kv-cache.cpp`); SYCL trees `build-sycl`, `build-sycl-rpc`, `build-sycl-aot`; `sycl-env.cmd`, `sycl-seat.cmd`, `sycl-rpc-seat.cmd`, `sycl-rpc-seat-master.cmd`, `build-*.cmd`, `gpu-mem-gate.ps1`, `reset-b70s.ps1` |
| `E:\work\llamacpp-knee-pr` | master worktree (60081bb + patch) with `build-sycl-rpc` — the Flash-Next engine; the upstream PR, **parked** by Derek's call |
| AM4 | patch baked (`5d3ef7cf8`, build 10583); master `ggml-rpc-server`; `build-sm75` started; ufw rule for RPC |
| **OMEN production** | rebuilt on the patched fork (`60cdd25`, build 52), verified `at_rate` 110.6 tok/s |
| `C:\work\memsplice` | `bench_cold_answer.py` (new), `--omen-server` on every OMEN-side driver, `--hot-suffix`, `--model-tag`, `--glob`, `build_body.py`; 40+ result JSONs |
| `docs/rnd/sycl-vs-vulkan/` | `WORKFLOW.md/.html` (the ladder with results L1–L4h), `LEVERS-256K.md/.html` (nine levers, measured statuses) |
| `docs/rnd-log.md` | 22 rows, laps 7 → L4h, including one incident row and one can't-answer-why row |
| `E:\work\battlemage\kv\` | reusable states: 27B 119k q4_0 / f16, 27B 248k f16 (16.45 GB), Flash-Next 248k (6.52 GB) |
| memory | `project-sycl-vs-vulkan`, `project-memsplice-disaggregated-kv`, `feedback-check-the-disk-before-blaming-the-gpu`, `feedback-no-long-runs-without-a-cue` (new) |

**Not shipped, deliberately:** the llama.cpp PR (Derek: not today); L5/L6 of the ladder (jobs/h and
deep concurrency — the two laps that decide whether SYCL replaces Vulkan under production); the
fx99 fifth device; a door stanza for the SYCL seat.

## The team retro — our collaboration across the seats

*(Seat reads drafted on `gcp-gemini` from the journey document, then edited — see Provenance.)*

**Architect** *(Derek set the destination and re-set it twice; I drew the ladders.)* Two structures
held all day. MemSplice — prefill on the NVIDIA pair, ship the cache, decode on the B70s — was proven
for the dense 27B (9.8× at 119k against the B70s' own Vulkan prefill) and then *demoted by our own
next result*: SYCL's XMX attention took the B70s' prefill at 119k from 117 to 614 tok/s, so the wire
became the larger term and disaggregation fell to 1.9×. That is the right kind of demotion — the
ladder was designed so L4 would move the MemSplice policy, and it did. The four-GPU RPC pipeline was
the surprise: a decode lever (+24% at 119k, 24.9 tok/s with MTP from a restored state) and a prefill
loss on 1 GbE, and at 256k it collapses to parity because the 12 GB cards cannot hold their share. The
call I would make again: the across-layouts patch, baked into every build on the fleet within an hour
of Derek's word. The one I would not: launching the 51-minute Flash-Next prefill without stating its
length first.

**Implementer** *(Windows + oneAPI + a fork + upstream master, all in one day.)* The environment was
the tax: winget refused oneAPI (ghost registry entry), the installer wanted `--action modify`, the
toolkit was in a custom path, and Intel's `setvars.bat` failed — `sycl-env.cmd` calls each component's
`vars.bat` by full path and is the reason the rest of the day existed. The traps that cost a lap each
are now in the register: `.cmd` needs CRLF for `goto`; cmd splits on `;` and `,` so device lists are
quoted; Git Bash strips those quotes, so seats launch from PowerShell; a new build tree needs
`vulkan-1.dll` beside it; never rebuild production's `build\` in place. Flash-Next needed upstream
master (`qwen4exp`), whose `--no-mmap` became `--load-mode` and whose default `--lazy-mode auto`
memory-maps a 27 GB embedding — `--lazy-mode off` pins it. The patch itself is small and
backend-agnostic, and the proof that a CUDA-made cache restores into a SYCL seat (L7) came for free.

**Reviewer / QA** *(Exact answers are the gate; one sample is a hypothesis.)* Correctness was
verified the only way that counts here: T=0 outputs compared token-for-token. The 27B dense is
byte-identical between SYCL and Vulkan and identical with MTP on; the MoE is **not** deterministic on
SYCL — two coherent, different answers to the same 16k prompt — and that is recorded as a policy
question, not papered over. Two-sample discipline held for the headline numbers (Flash-Next decode
3.58/3.50; RPC output 259/259 identical) and was stated where it did not (Flash-Next's 248k prefill is
one run). The reading I got wrong: I attributed the first 256k run's 78 tok/s to the lazy-mapped
embedding when five foreign `grep -r` processes had the disk at 199–317%; the fix I made
(`--lazy-mode off`) is still right — the 16k cold/warm pair (98 → 287) predates the greps — but the
256k number was confounded and the row says so. When the disk went quiet and the rate kept falling,
the row became can't-answer-why rather than a story.

**Operator / SRE** *(Production was down for hours by ceremony, and once by accident.)* Every seat
ran inside the maintenance ceremony (fx99 timers → sentinel → `ArcServeRestart` → seats → `ArcServeBoot`
→ timers → `query_rung_state`), and production came back each time, verified through the door. The
accident: two production loads died at their 5-minute timeout, I blamed WDDM, and Derek approved a
`pnputil` card reset that was not needed — an Explore agent's `grep -r` over `E:\work\battlemage`
(the models directory) had E: at 350 MB/s. The same shape recurred at 13:18Z from five other sessions'
sweeps, and this time `Get-Counter '\Process(*)\IO Read Bytes/sec'` found it in one call. The other
operational find was Derek's: 91 GB of system RAM in Task Manager was a B70 paging 24.8 GB over the
WDDM budget with no error anywhere; `gpu-mem-gate.ps1` now runs before every benchmark. `reset-b70s.ps1`
exists and is for a real wedge, not a first move.

**Product** *(Did the day move Derek's scoreboard?)* Yes, on the axis he named. The 27B at the depth
it is best at: a 256k context loads on the two paid-for cards, prefills in 8.5 minutes (bar: 3; his
ruling: "a lil over 3 min still excellent" — we are at 2.8× that), and decodes at 14.4 tok/s with
answers that are exact. The fleet's best depth decode went from 4.9 to 24.9 tok/s at 119k. Every
lever on his research list was tried, four paid, five closed cleanly — "don't just decide it's not
worth it" was honoured by measurement. Flash-Next at 262,144 is the wild find and, honestly, a batch
planner: 51 minutes to load a 248k context once, 28 seconds to save it, 30 to restore it, 3.5 tok/s to
think. His last steer — no more hour-long runs — is the product constraint for next time: the
interactive loop is worth more than any single number.

### Two seats, two views

**Claude's seat.** The day's shape was right: measure, record, next probe, and let Derek's sentences
end deliberation. The two errors were the same error — I reasoned about a slow disk from the model's
behaviour instead of reading the counter that names the process — and the second time I had the
memory entry from the first time in front of me. The fix is not "remember harder"; it is the one-liner
in the memory file and the rule that any prefill number is read *after* per-process I/O. The other
thing I would change: I let a 51-minute run start because the estimate (15 min at the warm 16k rate)
was optimistic and I never said the number out loud. Derek's "I'm gonna get bored" is the honest cost.

**Derek's seat** *(reconstructed from his messages, not his words about the day).* He set the unit of
the scoreboard, refused "no" as an answer ("inventory what we have"), spotted the spill from a
screenshot before any counter did, parked the PR without hesitation, and asked for both wild finds in
either order. His steers were all one sentence long. The one thing he had to say twice across
two days was about long runs — the first version ("i'm pretty sure fx99 has a keep warm process") was
a warning about a cold timer that never came, the second was about his own attention. That is the
constraint to carry.

## Last time's lessons — follow-through

Previous retro: [SESSION-RETRO-2026-09-04.md](SESSION-RETRO-2026-09-04.md). Fifteen days and a
different subsystem, so graded only where this session touched the same ground.

| Lesson | Status |
| --- | --- |
| L-2026-09-04-1 — an instrument that reports a constant is not reporting | **acted-on** — `gpu-mem-gate.ps1` reports the live shared/dedicated split, and the PDH blindness under Level Zero is stated on it; `query_rung_state` was read as `stale` (a varying verdict), not as health |
| L-2 — a test that skips reports success | pending — no test work this session (R&D mode, by design) |
| L-3 — an in-process lock cannot protect a shared resource | n/a |
| L-5 — a decision made on a diagnosis dies with it | **violated, then repaired** — the `pnputil` reset was a decision made on a wrong diagnosis (WDDM); the incident row records the real cause and the reset as unnecessary |
| L-6 — one sample is not a regime, in either direction | **acted-on** — second decode sample taken for Flash-Next before the number was reported; single-run numbers labelled as such |
| L-8 — check the receipt that records the fact, not a nearby one | **violated, then repaired** — I read the seat's page faults (a nearby fact) instead of per-process disk I/O (the fact); the second incident was found by the right counter |
| L-9 — a gate that only fires on a shape you have already tested is untested | **acted-on** — the spill gate was run on the shape that had actually spilled (the MTP overfill) before being trusted |
| L-10 — an outside reader catches what you have stopped seeing | **acted-on** — Derek's Task Manager read (91 GB) and the other agent's notes (`--load-mode`, `hc_attn_*`, per-layer embedding on CPU) both landed |

## Lessons learned

1. **L-2026-09-19-1 — Read the per-process disk counter before any story about why a run is slow.**
   Twice in one day a recursive `grep -r` over the models directory saturated E:; the first cost a
   needless GPU reset, the second cost twenty minutes of a wrong story about a lazy-mapped embedding.
   `Get-Counter '\Process(*)\IO Read Bytes/sec'` sorted descending names the process in one call. It is
   now in the memory file with the command.
2. **L-2026-09-19-2 — A loud refusal is a one-relaunch fix; a silent spill needs a human.** The 256k
   Flash-Next launch that refused (6.2 GB compute buffer on a 12 GB card) was fixed with a split and a
   ubatch. The MTP run that paged 24.8 GB to system RAM was found by Derek reading Task Manager. Gate
   for the silent one; welcome the loud one.
3. **L-2026-09-19-3 — Say the run length out loud before starting it.** The 51-minute prefill started
   on a 15-minute estimate that I never stated. Anything over ~10 minutes gets an estimate and Derek's
   cue first; depth samples come from saved states (30 s restore), not re-prefills. Standing directive.
4. **L-2026-09-19-4 — Read the kernel source before declaring a wall.** The f16 decode unlock
   (2.2–2.6× at depth) came from reading `fattn.cpp` — XMX is prefill-only, quantized KV dequantizes
   every decode step, f16 hits native SDPA — and then running it. "Decode at depth is ~5 tok/s" had been
   treated as physics for a week.
5. **L-2026-09-19-5 — Inventory beats verdict.** "Instead of saying no, we're inventorying what we
   have" produced nine levers with costs; four paid (f16, MTP, RPC decode, `-ub`), five closed cleanly
   (n-gram, AOT, asymmetric KV, Vulkan-f16, q8 KV). Every negative is a row that saves a future lap.
6. **L-2026-09-19-6 — Exact answers are the correctness gate, and they are cheap.** T=0
   token-for-token comparison caught SYCL MoE non-determinism in one lap and proved MTP, RPC, the
   cross-backend restore and the patch all exact. Nothing else was needed.
7. **L-2026-09-19-7 — The same measurement that proves a technique can demote it.** MemSplice's 9.8×
   became 1.9× the moment SYCL removed the B70s' prefill knee. The ladder was designed so L4 would move
   that policy; a design that lets your own next result demote your last one is working.
8. **L-2026-09-19-8 — Same commit, same KV type, both ends — and now the same build variant.** The
   Flash-Next lane needed a master `ggml-rpc-server` on AM4 to match the master server on OMEN; a
   state saved by the master build is only restorable by it. The slot-state file still carries no model
   or build identity — that gap is still open.
9. **L-2026-09-19-9 — A pinned split that fits at 119k does not fit at 256k.** The NVIDIA-weighted
   `2,2,1,1` won at 119k (24.9 tok/s) and cannot exist at 256k (the cards cannot hold layers + cache);
   the equal split is parity with the B70s alone. Depth changes the shape, not just the size.
10. **L-2026-09-19-10 — Other sessions' background work is part of this session's environment.** The
    second disk incident was five `grep -r` processes from other agents' sweeps. A running benchmark
    should be preceded by a look at what else is reading the disk, and any recursive search touching
    `/e/work` is killable on sight.

## Docs / plans

- `docs/rnd/sycl-vs-vulkan/WORKFLOW.md` + `.html` — results filled L1 → L4h; L5, L6, L8 still pending
  (they decide D1, the production engine). `LEVERS-256K.md` + `.html` — measured statuses on every
  lever.
- `docs/rnd-log.md` — 22 rows this session; one can't-answer-why row (the Flash-Next depth limiter)
  and one incident row (the grep that took production down).
- `docs/rnd/JOURNEY-2026-09-19-memsplice-sycl-256k.md` — the narrative record (podcast source).
- Memory: `project-sycl-vs-vulkan` (L4h added), `feedback-check-the-disk-before-blaming-the-gpu`
  (recurrence + the one-liner), `feedback-no-long-runs-without-a-cue` (new), index trimmed under its
  size limit.
- Open for Derek: the SYCL MoE determinism policy; whether L5/L6 run (SYCL under production); the
  upstream PR's timing; the 10 GbE port.

## Provenance

Commandcenter **`2f6da9f..73d925e`** (20 commits) and memsplice **`0bd15f2..5b17e02`** (12), plus this
retro and the journey document. **Offloaded:** the five seat reads and ten candidate lessons were
drafted on **`gcp-gemini` / `gemini-3.5-flash`** (`req_5dbce59f…`, 7,845 tokens in / 1,921 out,
36 s) from the journey document packed via `files=`; then edited — **`edit_verdict: minor-fixes`**
(it expanded SDPA as "spatial attention", credited "strict telemetry standards" that were in fact
violated twice, and reached for "robust platform" / "mathematical equivalence"; the structure and the
numbers it quoted were faithful, no invented events). The journey document, the follow-through
grading, the lessons as written, and every judgment are frontier. No `--fleet` second opinion was
dispatched. The HEARTH door was healthy throughout the retro (production `READY` at 219 ms after the
window closed).
