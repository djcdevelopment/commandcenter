# Session retro — 2026-09-19/20 · vLLM on the B70s, natively on Windows

**One-line:** Derek brought a Windows vLLM port that turned out to be CUDA-only; over nine laps we **assembled a native
vLLM-XPU on Windows anyway, got the production MoE to 82 tok/s single-stream and 1,196 tok/s aggregate on one B70 and
the hybrid 27B to 128k context on one card, and traced every remaining wall to one thing: the CUTLASS-SYCL kernels fault
under the Windows graphics compiler** — a driver update (E1) did not change it, and the session ends on a reboot with the
discriminating experiment (E3, Linux IGC's ISA under the Windows runtime) staged.

## What this session was

An R&D ladder (`/rnd` posture, `~/.claude/plans/federated-waddling-willow.md` approved 2026-09-19 evening): a reopen of
the vLLM question the SYCL-vs-Vulkan program had parked ("OMEN boots Linux, or a B70 returns to AM4"), under an amended
condition — *a native Windows build path exists*. Three `/goal`s from Derek shaped it: "get it functional for our use
cases", "improve speed", then "the dense 27B at 64–128k", and one mid-turn idea — mine the leading-edge Linux stack for
decisions to port — that became its own lap and the session's last experiment. Build + measure, not design: nine laps,
roughly 21:30Z → 18:45Z next day with a night break, every lap ending on one loud refusal or one number.

## What shipped

Commandcenter (`f672a13..015ef8a`, 9 commits, docs only — the code lives on two lab branches):

| Commit | What |
|---|---|
| `7d5db7b` | Lap 1: port is CUDA-only; native assembly serves Qwen3-0.6B from a B70 (13.7k tok/s @256 streams, latency flat) |
| `e307965` | Lap 2: all five `vllm-xpu-kernels` extensions build on Windows; the 30B loads, the SYCL-TLA attention kernel faults |
| `f9bb20f` | Lap 3: the production model family runs — TLA-free stack (`moe_wna16` + Triton MoE + Triton attention + oneDNN int4), 599 tok/s @64 |
| `9d5d589` | Lap 4: `torch.compile` works on Windows (+30 %); XPU graph capture faults; compile loses determinism; donor MoE table negative |
| `c73295c` | Lap 5: the step was launch-bound (GPU idle 85 %); XPU graphs for decode sizes 1–8 → 71.5 tok/s single (3.8×) |
| `6b670c1` | Lap 6: a swept B70 MoE kernel table (`BLOCK_SIZE_M=16` wins everywhere on Xe2) → 82.4 single, 1,196 @64 |
| `d5da53f` | Lap 7: Qwen3.8-27B (hybrid) at 128k on one card; GDN routed to FLA Triton; attention tiling 3.06× → 463 tok/s prefill at 13.6k, 64k point measured |
| `b2f4822` | Lap 8: the Linux delta — a compiler-line gap, not a capability gap; experiment ladder E1–E5 |
| `015ef8a` | Lap 9: E1 negative — Arc driver 32.0.101.9030 installed, kernels fault identically |

Lab branches (not in this repo): `E:\work\vllm-xpu-win\kernels` branch `windows` (4 commits, ~110 lines: the
Windows build of `vllm-xpu-kernels` 0.1.14.1 — `icx` for both languages, `/FI`, Level Zero win SDK, export-all-symbols,
`__restrict` mangling, JIT-only `none` sentinel) and `E:\work\vllm-xpu-win\vllm-src` branch `windows-xpu` (5 commits on
the fork's v0.29.0: optional TLA imports, `moe_wna16` on XPU with the loader-hook fix, the B70 MoE config JSON, the FLA
GDN route, the attention-tiling override, the Triton W4A16 linear as a fallback, a timer knob left off).

Durable artifacts: `docs/rnd/vllm-xpu-windows/WORKFLOW.md` (+ `.html`), `DENSE-27B-DEPTH.md`, `LINUX-DELTA.md`; nine
rows in `docs/rnd-log.md`; `E:\work\vllm-xpu-win\` (venv, `run.cmd`, `build-kernels.cmd`, `serve-30b.cmd`,
`serve-27b.cmd`, `kill-seat.ps1`, 25 probe scripts, every build and launch log); two checkpoints under
`E:\work\models\hf` (Qwen3-30B-A3B-GPTQ-Int4 16 GB, SergiioB/Qwen3.8-27B-GPTQ-Int4 19 GB); the 9030 driver installer
(Authenticode-verified) in `deps\driver`; ADR-0047; four memory files touched; `HANDOFF-vllm-xpu-windows-2026-09-20.md`.

## The team retro — our collaboration across the seats

*(Seat reads drafted on `gcp-gemini` from the three workflow docs, then edited — see Provenance.)*

**Architect** *(Derek set the destination four times; I drew the ladders.)* The L0 desk check — the port is CUDA-only,
its Intel question unanswered since January — was the cheapest decision of the session and the one everything else stood
on: instead of installing a wheel that could never see a B70, we combined the fork's Windows shims with upstream's
pure-Python XPU platform and a kernel package we built ourselves. The second structural call came out of a fault, not a
plan: when the CUTLASS-SYCL kernels kept killing the device, the stack was re-routed around them — oneDNN for the int4
linears, vLLM's Triton kernels for MoE and attention, FLA Triton for the 27B's gated-delta layers — and that "TLA-free
stack" is what serves today. Two boundaries were established rather than assumed: no XCCL on Windows torch-xpu, so the
dual-card shape is one seat per card; and the profile's verdict that decode is launch-bound, which turned "graphs or
tables?" into "graphs for small batches, eager above, and a table for both". What I would decide differently: I spent
lap 3 on the JIT-vs-AOT theory when lap 8's `ocloc --version` would have shown the AOT path used the same Windows
compiler — the discriminating experiment (Linux ISA under the Windows runtime) should have been designed the moment the
first `DEVICE_LOST` appeared.

**Implementer** *(eighteen build attempts, sixteen launch attempts, one loud refusal each.)* Windows charged its usual
tax and every item is now in a file: `icpx` is the GNU-like driver CMake cannot drive (use `icx`); GNU `-include`
silently becomes a second source file; a backslash path breaks `file(REAL_PATH)`; the oneAPI toolkit ships no
`ze_loader.lib`; `-j16` OOMs the device compile of 600 attention variants; `shutil.which`'s `icx.EXE` versus the
toolchain's `icx.exe` reads as a compiler change and empties the cmake cache every re-configure; SHARED SYCL libraries
export nothing without `CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS`; MSVC mangles `__restrict` into the symbol. On the engine side,
a directory named `vllm` shadowed the package, the fork's deprecated entrypoint has a stray `import uvloop`, a dead
EngineCore leaves its parent holding ZMQ :29550, and the banner's block glyphs crash a cp1252 process. The one real
upstream bug — `MoeWNA16Config` rebuilding its inner GPTQ config per layer so the loader's hooks never reach it — is
not Windows-specific and belongs in a PR. Two rebuilds were wasted: a killed `-j16` ninja kept compiling underneath two
later attempts, and the attention-config sweep's grep pipe block-buffered for ten minutes while I thought it hung.

**Reviewer / QA** *(exactness as the gate, isolation as the method.)* T=0 token-for-token identity was checked at every
lap and caught the one regression that mattered — `torch.compile` without graphs loses determinism; graphs restore it.
The needle-in-haystack at 6k/12k/64k made "correct at depth" a one-number fact. The faults were localised by isolation
rather than argument: the int4 GEMM standalone at M = 1…8192 clean, `flash_attn_varlen_func` standalone `DEVICE_LOST`;
the FLA norm clean on the exact in-engine shape, so the crash upstream of it was the GDN kernel. What slipped: the
`gpu-mem-gate.ps1` spill check the plan promised before every benchmark was never actually run this session — the
numbers stand because the seats stayed under 24 GiB, not because the gate said so. And the 64-stream compiled figure was
reported once as 500 before a re-sample showed 592: the captured path warms over its first runs, and the "one sample is
not a regime" rule applies to warm-up too.

**Operator / SRE** *(the cards belonged to production; every lap borrowed them.)* Production holds ~14 GB on *each*
card, so every real seat needed a tenancy window: write `hearth\var\arc-maintenance.stop`, run `ArcServeRestart` (stop-
only with the sentinel), lap, delete the sentinel, run it again — five windows, each restored to `/health` 200, no
incident. The one operational change is permanent: OMEN now runs Arc driver 32.0.101.9030, installed elevated through
`Start-Process -Verb RunAs` with the UAC prompt on Derek's desktop, `vkdevices.py` map unchanged, old driver retained
in the store — and the session ends on the reboot the installer asked for. Housekeeping worth knowing: the NEO
persistent kernel cache (`%LOCALAPPDATA%\NEO`, 228 MB) was cleared for a probe and rebuilds itself; two stale
background loops from a crashed launch had to be killed by hand; the venv's SYCL runtime pips were moved to 2026.1
(harmless, noted).

**Product / planning** *(three `/goal`s, each answered with a number.)* "Functional": the production family serves on
one B70 natively on Windows, correct and deterministic. "Speed": 18.9 → 82.4 tok/s single-stream (4.4×) and 599 → 1,196
aggregate at 64 streams (2×) — 78 % of the dual-card llama.cpp seat's single-stream on one card, with a concurrency
figure no llama.cpp seat has. "The 27B at depth": 128k fits on one card; 463 tok/s prefill at 13.6k, 161 tok/s and
6.0 tok/s decode at 64k, the tunables inventoried with evidence. Pacing held to the standing rule — the 25-minute kernel
build, the 35-minute sweep, the 128k run and the driver install each got an estimate and a cue (the 128k run is the one
that never got its cue). Scope creep was real but cued: the Linux-delta idea was Derek's and it earned its lap. What is
not done: the SAT-L1 jobs/h comparison (the saturation program's actual axis), the `omen-vllm` door stanza, and the
upstream PRs.

### Two seats, two views

**From Claude's seat.** The session's engine was the execution-first rule: run the physical step, read the refusal,
fix the one thing — 34 refusals, none argued with. Where I over-reached: the hour-long tuner sweep and the 27B loop were
started under a `/goal` without re-stating their length; the timer-resolution patch was a theory I acted on before
testing it standalone (it was refuted in ten minutes, cheaply, but the order was wrong). Where I under-reached: I told
Derek "the installer needs you" when `Start-Process -Verb RunAs` puts the UAC prompt on his desktop — he corrected it in
one line. Two theories I held too long: "AOT vs JIT" (lap 3) and "the oneDNN op cannot capture" (lap 4) — both died to
a standalone probe that could have run first. What I'd want next time: the discriminating experiment designed at the
first fault, and the disk/spill gates run rather than promised.

**From Derek's seat** *(my reconstruction from his cues and directives; correct it).* He brought the seed (a real port,
found by him), accepted the CUDA-only finding without re-litigating it, and cued three escalating goals as each landed —
which is how he scales: intuit the direction, let the instrument run, offload. The Linux-delta thought was the
session's best question and he asked it mid-lap; he would expect it pursued to a verdict, which it was (E1) and isn't
yet (E3). He typed exactly one thing — the UAC approval — and pointed out I could have driven the installer from the
tool. He will read the two big numbers (82 tok/s and 128k on one card) as "the B70 does more than Windows was letting
it", which is the thesis of the whole estate, and will want the E3 answer before believing "structural".

## Last time's lessons — follow-through

| Lesson | Status | Note |
|---|---|---|
| L-2026-09-19-1 disk counter before blaming the GPU | pending | no disk story arose; the counter was not run pre-benchmark either |
| L-2026-09-19-2 loud refusal = one relaunch; silent spill needs a human | acted-on | 34 loud refusals, one relaunch each; the one silent failure (`DEVICE_LOST` surfacing downstream) was found by isolation |
| L-2026-09-19-3 say the run length out loud | acted-on (mostly) | kernel build, driver, 128k got estimate + cue; the 35-min sweep was cued by `/goal`, its length stated after |
| L-2026-09-19-4 read the kernel source before declaring a wall | acted-on | the attention tiling (`BLOCK_Q=2`), the loader hook, the GDN route — all from reading source |
| L-2026-09-19-5 inventory beats verdict | acted-on | tunables tables in both depth docs; five clean negatives recorded |
| L-2026-09-19-6 exact answers as the gate | acted-on | T=0 identity every lap; caught the compile regression |
| L-2026-09-19-7 the same measurement can demote a technique | pending | no occasion |
| L-2026-09-19-8 same commit/KV type both ends | pending | not applicable (no MemSplice lap) |
| L-2026-09-19-9 depth changes the shape | acted-on | 128k KV budget vs chunk size; captured MoE loses at batch ≥ 32 |
| L-2026-09-19-10 other sessions' work is your environment | acted-on | checked for live compilers before every rebuild — found my own stale ninja instead |

## Lessons learned

1. **L-2026-09-20-1 — A "native port" is a claim about a backend; read the README before the wheel.** `vllm-windows`
   is CUDA-only; the useful half was its build shims. *[practice]*
2. **L-2026-09-20-2 — On this stack an access violation in a Triton launch means the previous kernel faulted the GPU.**
   Standalone the norm was clean; the GDN kernel before it was the killer. Isolate the op before the op that crashed.
   *[memory — written]*
3. **L-2026-09-20-3 — The decode step on Windows/Level-Zero eager is launch-bound; graphs are the lever, but only for
   the batch sizes you capture.** GPU idle 85 %; sizes 1–8 captured, eager above (a captured MoE loses at batch ≥ 32).
   *[adr — 0047]*
4. **L-2026-09-20-4 — Kernel tables tuned for other silicon are worse than defaults on Xe2; sweep on the card.** The
   Strix Halo MoE table lost; `BLOCK_SIZE_M=16` won at every M; the attention tiling gained 3.06×. *[memory — written]*
5. **L-2026-09-20-5 — Ask the driver what it advertises before theorising about what it lacks.** Twenty lines of ctypes
   showed every needed extension present; the delta is codegen, not capability. *[practice]*
6. **L-2026-09-20-6 — Know which compiler produced the binary before calling an experiment decisive.** The toolkit's
   `ocloc` is Windows driver-lineage; "AOT faulted too" tested nothing about Linux IGC. *[doc — LINUX-DELTA.md]*
7. **L-2026-09-20-7 — Elevated steps go through `Start-Process -Verb RunAs`; the UAC prompt lands on Derek's desktop.**
   Don't hand him a command to type. *[memory — written]*
8. **L-2026-09-20-8 — A tenancy window is a recipe, not a judgment call.** Sentinel + `ArcServeRestart` in, delete +
   `ArcServeRestart` out, `/health` 200 before the lap is closed; five windows, zero incidents. *[adr — 0047]*
9. **L-2026-09-20-9 — Loader bugs read as "unquantized" layers.** A quant config rebuilt per layer never sees the
   loader's hooks; the symptom was a missing `.data` attribute three frames away. *[practice]*
10. **L-2026-09-20-10 — Compile without graphs costs determinism; graphs restore it.** Pick per use, and re-check T=0
    identity whenever the execution mode changes. *[memory — written]*
11. **L-2026-09-20-11 — Background loops that wait on a log must also watch the process.** A crashed EngineCore never
    writes the sentinel line; the `until grep` loop ran for an hour. *[practice]*
12. **L-2026-09-20-12 — A gate you promised and did not run is a hypothesis.** `gpu-mem-gate.ps1` was in the plan and
    never executed; the numbers are not spill-checked. *[practice — carried to the handoff as a first action]*

## Docs / plans

- `docs/rnd/vllm-xpu-windows/WORKFLOW.md` + `.html` — the ladder L0–L8 with results; `DENSE-27B-DEPTH.md` — the 27B
  baseline and tunables; `LINUX-DELTA.md` — the delta, E1 negative, E2–E5 staged.
- `docs/rnd-log.md` — nine rows (laps 1–9), each with the re-run command and the uncertainty list.
- `docs/adr/0047-vllm-on-windows-runs-tla-free-one-seat-per-card.md` (new); `docs/adr/README.md` index.
- `HANDOFF-vllm-xpu-windows-2026-09-20.md` — the reboot handoff (state, verification, next laps, cues needed).
- `DECISIONS-PENDING.md` — four open decisions appended.
- Memory: `project-vllm-xpu-windows` (laps 1–9 + E1), `project-omen-arc-rung` (driver 9030),
  `project-sycl-vs-vulkan` (vLLM reopened), `feedback-elevated-steps-via-runas` (new), index lines.

## Provenance

Commandcenter **`f672a13..015ef8a`** (9 commits, docs) plus this retro, ADR-0047, the handoff and the register; code on
the lab branches `kernels/windows` (4 commits) and `vllm-src/windows-xpu` (5). **Offloaded:** timeline, five seat reads
and eleven candidate lessons drafted on **`gcp-gemini` / `gemini-3.5-flash`** (`req_2b9c51eb…`, 59,711 tokens in /
2,456 out, 28 s) from the three workflow docs + the rnd-log + the previous retro packed via `files=`; then edited —
**`edit_verdict: minor-fixes`** (it imported a "4× silent paging" lesson and a `-ts "1,1"` lesson from the sibling
program's retro, said the spill gate was "instituted" when it was never run, called a process crash a "console crash",
and dated the reboot as done; its numbers and structure were faithful). Follow-through grading, the lessons as written,
the ADR, the handoff and every judgment are frontier. No `--fleet` second opinion dispatched.

**Offload scorecard (S6):** `knowledge/offload.json` re-projected at the close (watermark 2026-09-21T02:48Z) —
offload_ratio **0.9991** over 1,569 calls; sunk 427,088 in / 176,007 out, trial 7,965,992 in / 386,704 out;
est_usd_saved **$33.62** (real trial spend $16.63). This session's own bucket (`cc-e0fdf649`): one call, 59,711 in /
2,456 out, ≈ $0.22 saved — the session was almost entirely physical steps and reading, which is the right shape for an
R&D ladder; the one offload was this retro's draft.
