# /rnd workflow — SYCL vs Vulkan on the B70s: is there a faster engine under the fleet?

Designed 2026-09-19 (session cc-544e4480, plan approved by Derek). Register: `docs/rnd-log.md` (one row per
lap). Results accumulate in the **Results** section at the end of this file; the HTML beside it
(`WORKFLOW.html`) is hand-written from this file — update both.

Scope decided 2026-09-19 (Derek): **Windows-only, llama.cpp SYCL via oneAPI**; an engine that wins may
replace **anything, production `omen-arc` included**; the ladder runs **open-ended until complete**, with a
written verdict either way. vLLM is out of scope with a named reopen condition (below).

## Context

Every number that currently shapes decisions — the B70 prefill knee (dual-card 609 → 443 → 117 tok/s from
16k to 119k), decode at depth (4.9 tok/s at 119k, capped near 6 tok/s aggregate however many sequences),
production's 105 tok/s single-stream and 3,358 jobs/h at `-np 8` — is a *Vulkan-on-Xe2* number from one
engine, Derek's knee fork (`E:\work\llamacpp-knee`, production build 52 / `60cdd25`). The question is
whether a different backend on the same silicon moves those numbers by enough to change what sits
underneath the door, the MemSplice pipeline, and the long-task route.

What the desk research (three read-only sweeps, 2026-09-18/19) settled:

- **vLLM XPU**: officially runs on Arc Pro B60/B70 (V1 engine, `vllm/vllm-openai-xpu` since v0.26,
  recipes.vllm.ai validates Qwen3.5/3.6 on B70), but **Linux only**, no Windows wheel, and on this host the
  WSL2 dual-Arc bridge is kernel-dead (two upstream bugs characterised to source line, ember ADR-0012,
  2026-06). Dual-B70 tensor parallel needs oneCCL env workarounds (vllm#41663) because the two cards have no
  P2P (`zeDeviceCanAccessPeer=0`, dead by silicon on OMEN's root complex). NixlConnector has no XPU path;
  cross-vendor P/D (CUDA prefill → XPU decode) is documented nowhere. **Out of scope by Derek's call.**
- **llama.cpp SYCL**: builds on Windows (`cl` + `icx`, oneAPI setvars). Flash attention now present
  (default on). **PR #25222 (merged 2026-07-15) — oneDNN/XMX flash attention — reports 4.26× prefill at
  80k on a B70 (730 vs 171 tok/s) and +90% at 128k on a dense 27B, prefill only, no decode gain claimed.**
  Against it: the one same-day same-card comparison found (jonathanmann, B70, Qwen3.5-35B-A3B, ~2k ctx,
  Mesa 26.1) has Vulkan ahead on 8-stream decode (170 vs 100 tok/s) and prefill (1,172 vs 979), parity
  single-stream; Derek's own May-2026 Windows test had IPEX-LLM's SYCL 6–13% ahead of Vulkan on short
  decode. Open correctness bugs on Battlemage: weight corruption without `GGML_SYCL_DISABLE_OPT=1`
  (llama.cpp#21893), garbled Windows output on B580 (#20169, closed not-planned), FA corruption on an Xe2
  iGPU (#19276). `-sm row` unsupported; `-sm tensor` "optimised for 2 GPUs" but segfaulted on AM4 in June;
  dual-B70 MoE layer-split ignoring `--tensor-split` → OOM (#22885, closed not-planned). Q8_0 *weights*
  hit a slow kernel on Xe2 on both backends (#21517) — production weights are Q4_K_M, so not in play.
- **Nothing measures both backends' newest kernels on the same card at the same depth.** That is the gap.
- **Vulkan has its own Xe2 FA kernels in flight** (llama.cpp PR #24406, Intel author; +39–83% prefill,
  +7–26% decode at 8k on a Panther Lake iGPU; merge status unconfirmed). If merged, a fork rebase is a
  cheaper win than a backend switch — it gets a lap of its own.
- **Installed**: OMEN has *no* oneAPI toolkit (only the installer stub), no `icx`; driver 32.0.101.8974
  (2026-08-10); the torch-xpu venvs carry pip runtime wheels only. AM4 has the full oneAPI 2026.0 but no
  Intel GPU. IPEX-LLM is archived (2026-01-28) — not a candidate.

Scoreboard (Derek): **total throughput × accuracy of work completed on local hardware, B70s kept
decoding** — not per-request latency. Every lap reports in that unit or says why it cannot.

## The decision chain this feeds

Each lap exists to move one of these. A lap that moves none is not run.

- **D1 — the production rung's engine** (`omen-arc`, the door default). A SYCL build replaces the Vulkan
  knee fork only if it clears production's own measured bar on production's shape: single-stream ≥ 105
  tok/s (the epoch baseline the deep probe reads), `-np 8` × 16k at ≥ 3,358 jobs/h (SAT-L1 Lap 1B,
  tag `prereg-np-sweep-lap1b-20260909`), 30B-A3B dual-split loads at all, correctness (T=0 answers equal
  to Vulkan's on a fixed prompt set; the OMEN-LIMIT-TEST Q4 200-row CV ≤ ~2.3%), and a soak with zero
  TDR/WHEA. Fails any one → SYCL is not the default, whatever else it wins.
- **D2 — MemSplice's shape and the long-task route policy.** Today's policy: prompts over ~8k on the 30B-A3B
  or 27B prefill on AM4; the B70s never prefill at depth (9.8× at 119k). If SYCL prefills the dense 27B at
  ≥ ~500 tok/s at 120k on one B70 (the #25222 claim), the disaggregation multiplier drops to ~3× and the
  wire becomes the larger term; the threshold moves and the streaming detour rises in priority. If SYCL
  moves the **decode** ceiling at depth (~6 tok/s aggregate at 119k), the whole depth economics change —
  that is the number nobody has published.
- **D3 — the depth specialist rung** (`omen-arc-27b`, ADR commandcenter#0039, pin-only). Engine choice is
  per rung, not global: SYCL can be the better engine for the 27B at depth even if it loses D1.
- **D4 — the parked upstream work.** The kv-state across-layouts patch is backend-agnostic and stays. PR
  #27652's Vulkan MMV lever is moot for any rung that moves to SYCL. A Vulkan FA rebase (#24406) competes
  with the whole ladder — it is measured first so the comparison is against the *best* Vulkan.
- **D5 — the control plane / door.** Any candidate seat enters as a `[[backend]]` stanza in
  `hearth/etc/backends.toml` (`api = "openai"`, pin-only, `tags = []`) — zero code — so the bake-off runs
  through the door and lands on the ledger (ADR-0027: dispatches as observations).
- **D6 — the OS question.** Untouched by this workflow. vLLM reopens only if OMEN runs Linux natively or a
  B70 moves back into AM4; WSL2 is not a path on this host (ember ADR-0012).

## The ladder

One edge per lap. Every lap writes a row to `docs/rnd-log.md` (register), a section to the workflow doc,
and stops the ladder with a written verdict if its stop condition fires. Production is only ever down
inside the maintenance ceremony (fx99 timers stop → sentinel → `ArcServeRestart` → … → `ArcServeBoot` →
timers start → one quiet deep-probe tick → `query_rung_state`), the same one used for laps 9–12c.

### L0 — desk (done, this document)
Verdict: vLLM out of scope (Windows); SYCL in; the Vulkan FA rebase is a first-class arm; the correctness
bugs make accuracy a gate, not an afterthought.

### L1 — environment: can the fork build SYCL on Windows at the production commit?
- Probe: install Intel oneAPI (Deep Learning Essentials or Base Toolkit 2025.x/2026.x — the SYCL doc's
  verified table uses 2025.1–2025.3) on OMEN — **a system install of several GB, needs Derek's go**; build
  the fork at `60cdd25` in a NEW tree `E:\work\llamacpp-knee\build-sycl` (`cmake -DGGML_SYCL=ON
  -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=icx -DGGML_SYCL_F16=ON`, Ninja, Release); `sycl-ls` and
  `ONEAPI_DEVICE_SELECTOR=level_zero:*` must enumerate both B70s (the iGPU is also a Level Zero device —
  select by index, and record the mapping).
- Edge: build failure at this commit / device enumeration. Same source as production, so any later
  difference is the backend and nothing else.
- Feeds: go/no-go for everything below.
- Stop: cannot build or cannot see the cards → verdict "SYCL blocked on Windows at 60cdd25", reopen on the
  next oneAPI or driver release.

### L1b — the Vulkan arm: is #24406 merged, and what does a rebase buy?
- Probe: `git fetch origin master`; check whether PR #24406 (Vulkan Xe2/Xe3 FA kernels) merged; if so,
  build a THIRD tree `build-vk-next` from master + cherry-picks of `242c3cd` (MMV cols) and the across-layouts
  patch; llama-bench pp512/pp2048/tg128 on one B70 vs production's Vulkan.
- Edge: a prefill gain on Vulkan without leaving the backend.
- Feeds: D4 first, and it sets the Vulkan side of every later comparison (best-vs-best, not old-vs-new).
- Stop: never stops the ladder; if not merged, record and compare against production Vulkan.

### L2 — single-card sanity and correctness
- Probe: 30B-A3B Q4_K_M and 27B Q4_K_M on ONE B70, SYCL vs Vulkan, same fork, same card, same prompts:
  `llama-bench` pp512 / pp2048 / tg128; then T=0 answers on the lap-7 and lap-9 prompt bodies through
  `bench_omen_depth.py` (the SYCL seat on `:8096`, driver gets an `--omen-server` argument) compared
  token-for-token to Vulkan's. Run with and without `GGML_SYCL_DISABLE_OPT=1`.
- Edge: garbled or divergent output (#21893 / #20169) — and if `DISABLE_OPT` is required, the speed it costs.
- Feeds: D1 (correctness gate), D3.
- Stop: divergent at T=0 on short prompts with no workaround → "correctness-blocked", ladder ends.

### L3 — production's shapes: do they load?
- Probe: 30B-A3B `-sm layer -ts 1,1 -c 131072 -np 8` on SYCL (does #22885 bite — tensor-split ignored,
  one 25 GB allocation, OOM?); 27B dense `-sm layer -ts 1,1`; `-sm tensor` once (expect the AM4 segfault or
  a host-routed path — P2P is dead by silicon).
- Edge: whether the production shape exists on SYCL at all.
- Feeds: D1 (no dual MoE → no production candidacy; the ladder continues for D2/D3 only).

### L4 — the depth ladder (the lap that moves D2)
- Probe: 27B dense, q4_0 KV, the lap-8 prompt bodies (`results/ceiling-body-*.json`, 16k / 30k / 60k /
  119k), on SYCL single and dual vs the Vulkan numbers already on file (single 370/247; dual 609/443/…/117
  tok/s prefill; decode 15.2 / 11.4 / … / 4.9). Prefill via cold requests, decode via restore + 64 tokens.
  Add `GGML_SYCL_ENABLE_MKL_FA=1` (default) with quantized KV — the XMX path only engages with `*_0` KV
  types, batch ≥ 1024, prompt ≥ 1024, so `-ub 1024 -b 2048` is the shape.
- Edge: whether the #25222 claim holds — prefill at 120k near 500 tok/s — and what decode does at depth.
- Feeds: D2 (the route threshold and the "B70s never prefill" rule), D3.
- Stop: never stops; either answer is the result.

### L5 — production's regime: jobs per hour at `-np 8` × 16k
- Probe: the SAT-L1 harness (512-token prompts, `-np 8`, the tag above) against a SYCL seat on `:8096`
  with production's exact flags; through the door via a pin-only stanza `omen-arc-sycl` so the ledger has
  it. Compare to 3,358 jobs/h (Vulkan, `-np 8`). Also `-np 16`: Vulkan regressed 32% there (the MMV
  8-column cliff, `vulkancliff`); SYCL has no such kernel — it may keep scaling.
- Edge: the aggregate ratio at production depth, and the shape of the `-np` curve.
- Feeds: D1 directly — this is the scoreboard's own unit.

### L6 — deep concurrency: does the ~6 tok/s ceiling move?
- Probe: the lap-10/12 ladder (`bench_np_depth.py`, eight 119k contexts, non-unified with the patch, fresh
  restore per round) on a SYCL dual seat.
- Edge: attention-at-depth decode on the SYCL FA kernels vs Vulkan's 4.9 → 6.0 flat aggregate.
- Feeds: D2 (the decode floor at depth), D4.

### L7 — cross-backend KV: does the shipped cache survive an engine swap?
- Probe: restore the CUDA-made 119k q4_0 state (already on `E:\work\battlemage\kv\`) into a SYCL seat;
  decode 96 tokens; compare to the Vulkan-decoded text (lap 9, exact answer) — the slot-state file is
  ggml tensor data, so this should be backend-agnostic; the q4_0 dequant path is the risk.
- Edge: a SYCL-specific quantized-KV restore or attention divergence.
- Feeds: D2 — an engine change must not break the pipeline.

### L8 — soak, bake-off, verdict
- Probe: only if a SYCL shape won L5 or L4: the OMEN-LIMIT-TEST-style soak (hours, `-np 8`, TDR/WHEA
  watch, the 200-row CV), then a written verdict in the ADR register (`docs/adr/`, commandcenter
  register, cite as `commandcenter#NNNN`) — `promote` / `promote as depth specialist only` /
  `do_not_promote`, in the form of the Qwen 3.8 campaign's verdict.
- Feeds: D1 / D3 promotion. Promotion itself is a separate action (omen.yaml → new tree, next ArcServe
  restart) and is Derek's.

## Order and early exits

L1 → L1b → L2 → L3 → L4 → L5 → L6 → L7 → L8. L1 or L2 failing ends the ladder with a verdict; L3 failing
removes D1 and continues for D2/D3. Laps L4–L7 reuse the memsplice drivers and prompt bodies as-is
(one added `--omen-server` argument), so a lap is a seat launch plus a command already on file.

## Baselines the laps compare against (already measured, on file)

- production Vulkan single-stream: 105.33 tok/s epoch baseline (deep probe, 32 tokens), 110 observed
- production `-np 8` × 16k: 3,358 jobs/h at 512-token prompts (`-np 16`: −32%)
- 27B dual-B70 Vulkan prefill: 609 (16k) / 443 (30k) / 117 (119k) tok/s; single 370 / 247
- 27B decode at depth (dual): 15.2 (16k) / 11.4 (30k) / 4.9 (119k) tok/s; aggregate ~6 at 119k for 1–8
- AM4 prefill (the disaggregated alternative): 2239 / 2197 / 1898 / 1462 tok/s at 16k / 30k / 60k / 120k
- OMEN-LIMIT-TEST: Q4 200-row CV 2.3%, zero TDR/WHEA over ~6.5 h

## Constraints and ceremony

- Production down only inside the fx99-timers → sentinel → `ArcServeRestart` … `ArcServeBoot` → timers
  ceremony; never rebuild `E:\work\llamacpp-knee\build` in place (the running server locks the DLLs);
  new trees only. A fresh tree needs `vulkan-1.dll` (Vulkan) beside the exe; a SYCL tree needs the oneAPI
  runtime DLLs on PATH (`setvars.bat` in the launching shell — the launch `.cmd` sources it).
- Experiment seats run outside llama-swap on `:8095` (Vulkan) / `:8096` (SYCL); imagegen shares the
  cards (`arc-maintenance.stop` is a shared lock).
- `pkill -f` on AM4 must be anchored (`^\./llama.cpp-knee/build/bin/llama-server`); the depth laps need
  AM4's `:18090` seat only for new prompt bodies (the existing bodies suffice).
- One edge per lap; a clean sample is a result; a can't-answer-why row stops the lap.

## Deliverables and files

- **This document** (`docs/rnd/sycl-vs-vulkan/WORKFLOW.md`): the ladder, decision chain, baselines, and a
  results section filled lap by lap; `WORKFLOW.html` beside it for reading (hand-written, not generated).
- Per lap: a row in `docs/rnd-log.md`; results JSON under `C:\work\memsplice\results\` (drivers there);
  seat logs in `hearth/var/swap-logs/`; build recipes as `.cmd` wrappers under
  `E:\work\llamacpp-knee\` (`build-sycl.cmd`, `build-vk-next.cmd`) so they survive the session scratchpad.
- Driver change: `bench_omen_depth.py`, `bench_np_depth.py`, `bench_split_prefill.py` gain
  `--omen-server` (default `http://127.0.0.1:8095`).
- Door: a pin-only `[[backend]] name = "omen-arc-sycl"` stanza (`:8096`, `tags = []`, same auth env) in
  `hearth/etc/backends.toml`, plus `HearthGatewayRestart` so the door mounts it (the door runs the code it
  was started with).
- Verdict: an ADR in the commandcenter register; memory update (`project-memsplice-disaggregated-kv`,
  a new `project-sycl-vs-vulkan` file); the date correction (prior attempts were May–June 2026) recorded
  in the level-zero memory.

## Verification

Each lap is verified by running it: the seat's own log (`build N (commit)`, device lines, buffer sizes),
the driver's JSON, and — for anything touching production — the observer (`query_rung_state` at_rate on
a quiet deep-probe tick), never the binary's self-report alone. Correctness is verified by token-for-token
comparison against Vulkan's T=0 output on the fixed prompt bodies; a divergence is a result, not a retry.

## vLLM — parked, with the reopen condition written down

Reopen when either is true: OMEN boots Linux natively (then `vllm/vllm-openai-xpu:latest`, v0.29+, TP=2
with the vllm#41663 env set, Qwen3.6-27B / Qwen3.5-35B-A3B FP8 from recipes.vllm.ai), or a B70 returns to
AM4. Not before: no Windows wheel, WSL2 dual-Arc bridge dead on this host, no XPU KV connector, no
cross-vendor P/D. The one-rig comparison on record (Bentech, 2026-04) has vLLM ~5× llama.cpp on prompt
processing and slower on generation — consistent with the SYCL prefill story above, and untested here.

## Results (filled lap by lap)

See also `LEVERS-256K.md` — the inventory of every asset that could move the 256k objective, ranked.

### L0 — desk research (2026-09-19) — DONE
- Sources: three read-only sweeps (local estate; vLLM XPU; llama.cpp SYCL / IPEX-LLM), summarised in the
  Context section above; full agent reports in the session transcript for cc-544e4480.
- Verdict: vLLM parked (Linux-only; WSL2 dual-Arc bridge dead on this host; no XPU KV connector); SYCL in;
  Vulkan FA rebase (#24406) is a first-class arm; correctness is a gate.
- Correction recorded: the earlier vLLM/SYCL attempts date to May–June 2026 (ember ADR-0009/0012,
  denning P5, intel_ollama findings), not ~Nov 2025.

### L1 — environment — DONE 2026-09-19 06:00Z: builds, both cards enumerate
- oneAPI was already on the box at a custom dir, `E:\omen\tensor\native\toolchain\oneapi` (installed
  2026-09-08; a ghost registry entry made winget refuse). `installer.exe --action modify` added oneMKL 2026.1 and
  oneDNN 2026.0. Intel's `setvars.bat` fails here (after the VS init, `call vars.bat` no longer resolves against
  the cwd) — `E:\work\llamacpp-knee\sycl-env.cmd` reproduces it with full-path calls; `sycl-run.cmd <cmd>`
  launches anything with the runtime on PATH.
- `sycl-ls`: `level_zero:0`, `level_zero:1` = Arc Pro B70; `level_zero:2` = iGPU. Seats pick with
  `ONEAPI_DEVICE_SELECTOR=level_zero:0` (one) or `level_zero:0;level_zero:1` (both). `ZES_ENABLE_SYSMAN=1` for
  true free-memory figures.
- `build-sycl.cmd` → `build-sycl\bin\llama-server.exe` = `build 52 (60cdd25)`, `ggml-sycl.dll` 66 MB; cmake found
  SYCL 20260100, oneDNN (the #25222 XMX FA path), oneMKL 2026.1, Level Zero API on. `--list-devices`: SYCL0/SYCL1
  B70 31,906 MiB, SYCL2 iGPU.
- Edge: none in the build — the edges were all environment (ghost install, setvars, heredoc). Ladder open.
### L1b — Vulkan arm (#24406) — CHECKED 2026-09-19: NOT MERGED
- `origin/master` at `60081bb` (2026-09-18) has no Intel Xe FA shaders (`git log --grep`, shader tree). The Vulkan
  side of every comparison is therefore production's build 52 (`60cdd25`); a master rebase would add general
  Vulkan work (sparse FA #28105, Flash-Next top-k #28032) but no Xe kernels — optional second data point, not the arm.
  Re-check #24406 at each later lap; if it lands mid-ladder, `build-vk-next.cmd` is ready.
### L2 — single-card sanity + correctness — DONE 2026-09-19 06:15Z
- llama-bench, one B70, tok/s (Vulkan → SYCL): 30B-A3B pp512 2383 → 1498, pp2048 2170 → 1880, tg128 121.9 → 111.0;
  27B pp512 756 → 892 (+18 %), pp2048 703 → 1111 (+58 %), tg128 23.9 → 21.5 (−10 %).
- Cold 16k T=0, 27B: Vulkan prefill 375 tok/s / decode 15.25; SYCL 810 tok/s (2.16×) / 12.23. **Generation
  byte-identical** (397 chars) with the optimized SYCL kernels — no garbling.
- Cold 16k T=0, MoE: Vulkan 520 tok/s / 18.85, deterministic; SYCL 1215–1401 tok/s (2.3–2.7×) / 18.8–20.6 —
  **non-deterministic**: four runs (default ×2, `GGML_SYCL_DISABLE_OPT=1` ×2), four different fluent answers.
  Not a correctness failure in the garbling sense (the SYCL answers were grounded), but T=0 reproducibility is
  gone on the MoE path. `DISABLE_OPT` changes neither determinism nor speed.
- **Edge:** SYCL MoE is non-deterministic at T=0; dense 27B is exact. Byte-equality is the wrong gate for a
  MoE across backends; D1 now needs (a) the accuracy/CV suite and (b) a determinism policy call (the control
  plane's replay/verify story assumes T=0 reproducibility). D3 (27B depth specialist) has a strong candidate.
- Not sampled: SYCL MoE with `-fa off`; Vulkan MoE across `-ub`; the Vulkan↔Level-Zero physical card mapping.
### L3 — production shapes — DONE 2026-09-19 06:35Z: all load
- MoE dual layer-split `-c 131072 -np 8`: loads with `-ts 1,1` honoured (8976 / 8549 MiB, production's split);
  single-stream decode 104.9 tok/s vs production Vulkan 105–110 — parity. #22885 absent at this commit.
- `-sm tensor` on the 27B: **loads and runs** (meta backend, graph splits = 2) — Vulkan has no such mode. No gain at
  ≤16k (decode 19.1 short / 12.3 at 16k vs single-card 21.5 / 12.2; prefill 622 vs single 810) because every layer
  round-trips through the host; output matches Vulkan's. Re-tested at 119k in L4.
- Trap: cmd splits `.cmd` arguments on `;` and `,` — quote the device selector and `-ts`.
### L4 — depth ladder — DONE 2026-09-19 07:05Z: the knee is gone; tensor split moves decode at depth
- SYCL dual layer-split (27B, q4_0 KV, both B70s), prefill tok/s at 16k / 30k / 60k / 119k: **813 / 817 / 736 / 614**
  vs Vulkan dual 609 / 443 / — / 117. 119k prefills in 194 s vs 1,015 s (5.2×). Decode 14.0 / 10.5 / 7.0 / 4.24 vs
  Vulkan 15.2 / 11.4 / — / 4.9.
- `-sm tensor` at 119k: prefill 482 tok/s, **decode 6.74 tok/s** (+59 % over SYCL layer, +38 % over Vulkan) — the
  first thing to move the depth-decode ceiling; output identical to layer-split.
- D2: the MemSplice multiplier at 119k drops from 9.8× to 1.9× against SYCL-local; the route threshold moves, the
  scoreboard rationale (B70 prefill time is lost decode time) stands. D3: the depth specialist's engine candidate is
  SYCL tensor-split.
### L4c — f16 KV and the ubatch knob — DONE 2026-09-19 08:25Z: decode at depth 2.2–2.6×
- Source read (`fattn.cpp`): the XMX/oneDNN path is prefill-only (≥32 query tokens); decode is TILE/VEC; quantized
  KV is dequantized per ubatch (prefill) and per step (decode); f16 is native.
- f16 KV, SYCL dual 27B at 256k (8.2 GB KV per card): 119k decode **9.41 tok/s** (q4_0 4.24, Vulkan 4.9); 248k
  decode **5.89** (q4_0 2.24). Prefill unchanged (597 / 435) — the cache conversion was not the deep-prefill wall.
- `-ub 2048 -b 4096`: prefill +16 % at 119k (694); `-ub 4096 -b 8192`: 713. Best 248k run: **512.7 s (8.5 min), 485
  tok/s, decode 5.89 tok/s**, correct answer.
- T=0 output changes with `-ub` (batch-shape sensitivity) — reproducible only for a fixed (backend, ubatch).
- Levers still untried: f16 on tensor split; f16 on Vulkan; MTP at depth; the oneDNN-for-decode gate; RPC pipeline.

### L4d — decode levers without new hardware — DONE 2026-09-19 10:22Z
- f16 + tensor split at 119k: decode 10.24 tok/s (layer f16 9.41, +9 %), prefill 555 (layer 713, −22 %) — the two
  decode gains do not stack; **layer split + f16 is the all-round seat**.
- MTP on 256k f16 `-ub 4096` overfilled a B70: **WDDM spilled 24.8 GB to system RAM silently** (denning's cliff, live);
  `gpu-mem-gate.ps1` now checks the adapter counters before a run. Budgeted MTP seat (`-c 131072 -ub 2048`) launched
  clean but not measured (interrupted).
- Incident inside the window: a runaway Explore-agent `grep -r` over `E:\workattlemage` (the models dir) saturated
  E: for ~20 min; two production loads died at llama-swap's 5-min timeout; misdiagnosed as WDDM, cards reset for
  nothing. Rule recorded in memory: disk counters first; no recursive searches over model trees.
- **B2 MTP** (`draft-mtp`, n_max 3, layer f16 `-ub 2048`): 119k decode **19.98 tok/s** (9.41 without, 2.1×; 76 % accepted;
  output identical over 128 tokens); **248k decode 14.37 tok/s** (5.89 without, 2.4×), prefill 442, correct answer.
- **B3 n-gram** (`ngram-cache`): 6.30 tok/s at 119k — worse than none (12 % accepted on explanatory prose). Negative.
- Depth-decode arc on the same two cards tonight: 119k 4.24 → 9.41 → **19.98**; 248k 2.24 → 5.89 → **14.37**.
- Denning detour (read, not run): its spill cliff, admission-control conclusion, `-fit` finding and restore-vs-re-prefill
  ratios all transfer directly; PDH `non_local` is nearly blind under SYCL — use `b70tools verdict` with that caveat.

### L4e / L4f — Derek's research list — DONE 2026-09-19 11:35Z
- **Asymmetric KV** (q8_0/q4_1 and f16/q4_0) on SYCL at 119k: decode 5.84 / 5.80 (f16 9.41, q4_0 4.24) — a memory lever
  (3.4 GB/card at 256k), not a speed lever; f16/q4_0 also loses 44 % prefill (fails the oneDNN type gate).
- **AOT (`GGML_SYCL_DEVICE_ARCH=bmg-g31`)**: identical to JIT within noise. Negative.
- **RPC four-GPU pipeline** (AM4's pair + the B70s, 1 GbE): loads and runs; decode **11.7 tok/s at 119k (+24 %)**,
  prefill 498 (−28 %); one rpc-server for both remote cards halves the wire crossings vs two. On this link it is a
  decode lever only. Flash-Next shard 1 is a 0-byte file — no MoE+MTP lap until it is re-downloaded.
- **MTP** was already done (L4d). oneDNN FA was already on.

### L4g — the rest of the list — DONE 2026-09-19 11:55Z: restore into four devices; 24.9 tok/s at 119k
- Vulkan f16 at 30k: prefill +28 %, decode 10.10 (q4_0 11.4) — the f16 decode unlock is SYCL-only.
- SYCL q8_0/q8_0: 4.15 tok/s — the KV ladder on SYCL: q8 < q4 < q8/q4_1 ≈ f16/q4 < **f16 9.41**.
- An f16 119k state saved on the B70s (16 s, 7.97 GB) **restores into the four-device RPC cache** (40 s) and
  decodes with MTP: 21.6 tok/s; with `-ts 2,2,1,1` **24.9 tok/s** — the fleet's best at depth. n_max 6 is worse (15.0).
- fx99 as a fifth device: blocked on CUDA arch (AM4's build has no sm_75); `build-sm75` building.

### L4h — 256k on four devices; Flash-Next — DONE 2026-09-19 14:30Z
- The 27B's 248k f16 state restores into the four-device cache (34–62 s) and decodes with MTP at 13.1–14.0 tok/s —
  **parity with the dual B70s (14.37)**, not a win: at 256k the NVIDIA cards cannot carry the layer share that won at 119k.
- **Qwen3.8-Flash-Next (qwen4exp, 512×10 MoE, hybrid) runs across all four GPUs at 262144 context** on the master build:
  `-ts 4,5,20,19 -ub 512`, B70s at 29.8/29.1 GB (under the cliff, no spill); `--load-mode none --lazy-mode off` pins the
  27 GB per-layer embedding in host memory (the mmap default page-faults it from disk: 98 → 287 tok/s cold vs warm at 16k).
  16k: 287 tok/s prefill, 21 tok/s decode, correct answer, T=0 non-deterministic (SYCL MoE).
  **248k body: 51 min prefill (81 tok/s average, falling with depth), decode 3.5 tok/s (two samples), coherent grounded
  answer; the hybrid state is 6.5 GB (28 KB/token) and saves in 28 s.** Nothing is saturated at depth — can't-answer-why
  row. The 27B (8.5 min / 14.4 tok/s with MTP) remains the better 256k worker today.
- First 256k launch refused loudly (RPC0 compute buffer 6.2 GB at `-ub 1024`) — the fix was the split + ubatch, not memory.
- Incident, again: five other sessions' `grep -r … /e/work` sweeps (the models dir) had E: at 199–317 % during the runs; killed.

### L5 — jobs/h at -np 8 × 16k — PENDING
### L6 — deep concurrency — PENDING
### L7 — cross-backend KV — DONE 2026-09-19 07:20Z (answered inside L4b)
- The CUDA-made 119k q4_0 slot state restored into a SYCL seat (`n_restored=119203`, 1.2 s) and the B70s
  continued from it correctly. The slot-state file is vendor- and backend-agnostic; an engine swap does not
  break the pipeline.

### L4b — 256k (Derek's question) — DONE 2026-09-19 07:25Z: minutes, not an hour; not three
- 27B at `-c 262144`, both B70s, SYCL layer split, q4_0 KV: loads (2.3 GB KV per card). SYCL alone prefills
  248,515 tokens in **552.9 s (9.2 min, 449 tok/s)**; decode 2.24 tok/s at 248k; correct answer.
- Hybrid (AM4's 119k state + 121k tail on the B70s): **433 s (7.2 min)** — the tail runs at 366 tok/s and is the wall.
- Bar was ~3 min. Path to it: a smaller weight quant on AM4 (IQ4_XS) so the NVIDIA pair holds 256k and does the
  whole prefill at ~1,200 tok/s (~3.5 min), wire hidden by the streaming detour. Not attempted tonight.
### L8 — soak / verdict — PENDING
