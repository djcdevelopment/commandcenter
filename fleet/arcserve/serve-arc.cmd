@echo off
rem serve-arc.cmd — the omen-arc rung (ADR-0034): Qwen3-30B-A3B dual-B70 llama-server.
rem Run by the ArcServeBoot scheduled task (S4U, boot trigger, ExecutionTimeLimit PT0S,
rem RestartCount 3 @ PT1M — the ADR-0032 hardening pattern). Campaign-proven flags:
rem OMEN-LIMIT-TEST-2026-08 (Q4 CV 2.3%%, zero TDR/WHEA over the soaks).
rem
rem Device rule (Phase 0 finding): Vulkan0 is the iGPU on this box — the visibility
rem filter is LOAD-BEARING. Never remove it; re-verify indices after driver updates.
rem 2026-08-29 (FF Phase 0, ROOT CAUSE): the visibility filter is REMOVED, not adjusted.
rem It was silently costing us a whole card. Measured at -lv 5 in the SCHEDULED-TASK
rem context, GGML_VK_VISIBLE_DEVICES=1,2 selected ONE B70 PLUS THE iGPU:
rem     - Vulkan0 : Intel(R) Arc(TM) Pro B70 Graphics
rem     - Vulkan1 : Intel(R) Graphics          <-- the iGPU, not the 2nd B70
rem The iGPU is then dropped (llama.cpp excludes iGPU when a dGPU is present), leaving
rem ONE device, and all 49/49 layers landed on it: model 17524 + KV 12288 + compute 296
rem = 30108 MiB = 92.5%% of a 32558 MiB card, with the second B70 completely idle.
rem WHY THE INDICES DRIFTED: Vulkan enumeration ORDER IS NOT STABLE ON THIS BOX. Derek
rem has been bitten by this repeatedly -- it reshuffles between runs, DHCP-lease style,
rem which is why the original note was so emphatic. Observed here in one session: an
rem interactive shell enumerated [iGPU, B70, B70] (so 1,2 was correct) while the S4U
rem task got [B70, B70, iGPU] (so 1,2 selected [B70, iGPU]). Testing a filter
rem interactively therefore CANNOT predict what the task will do, and NO index scheme
rem can be made safe -- including -dev/--device, whose VulkanN names are positional too.
rem COROLLARY: any policy that targets a SPECIFIC card by index is equally unreliable,
rem which includes the thermal rule "the hot card gets the lighter model". With -ts 1,1
rem the split is symmetric so ordering is harmless here, but unequal -ts or --main-gpu
rem would need identity-based placement (b70tools resolves cards by PCI BDF).
rem THE FIX: with no filter set, ggml-vulkan selects by DEVICE TYPE, not index
rem (ggml-vulkan.cpp:7479-7495, "Default to using all dedicated GPUs"), and llama.cpp
rem drops the iGPU at model-placement. Verified on a spare port, no env var:
rem     llama_prepare_model_devices: using device Vulkan1 (Arc Pro B70)
rem     llama_prepare_model_devices: using device Vulkan2 (Arc Pro B70)
rem Order-independent, so session context cannot break it.
rem ROLLBACK: git revert this commit, then start ArcServeBoot (NOT a rapid Restart loop).
rem set GGML_VK_VISIBLE_DEVICES=1,2

rem COOPMAT stays ENABLED (Stage 1 finding: the old disable flag cost 2x prompt
rem processing; zero TDRs across the campaign on driver 8974).

rem CONTEXT (2026-08-24): -c 65536 -np 4 -> -c 131072 -np 2, so each slot holds
rem 65536 tokens instead of 16384. Nous Hermes Agent hard-refuses any model
rem offering under 64000 tokens per conversation. Two slots, not four: see below.
rem Raise backends.toml context_bytes AND parallel_slots in lockstep.
rem
rem MEASURED 2026-08-24 — the burn-in ladder does NOT extrapolate to slot depth.
rem The Q3 knee ladder varied total -c by raising -np at a FIXED 8192 tokens/slot,
rem so its 96.7 KiB/token slope captures KV growth but is blind to attention
rem compute buffers, which scale with per-slot DEPTH. Predicting from it gave
rem ~20.1/21.5 GiB per card for -c 262144 -np 4. Reality:
rem     -c 262144 -np 4  -> B70 shared usage 10.24 GB (SPILLED), 86.32 tok/s
rem     -c 131072 -np 2  -> B70 shared usage  0.24 GB (clean),  109.31 tok/s
rem     -c  65536 -np 4  -> (previous config)                   110.15 tok/s
rem Spill costs ~22%, not the ~8x collapse seen with gemma27 — a partial spill is
rem quiet enough to look like normal variance. Always re-run the shared-usage
rem assert after changing -c or -np.
rem
rem --jinja + --metrics restored: the AM4 SYCL rung (serve-moe.sh) carried both and
rem the Windows port dropped them. Tool-calling already worked without --jinja on
rem b10549, but the flag is the documented contract, not a lucky default.
rem
rem Token: shared with the gateway (backends.toml auth_env OMEN_ARC_TOKEN).
rem Sourced from the same gitignored fragment the gateway uses.
call C:\work\commandcenter\hearth\var\gateway.cmd

rem STEP 2 of 2 (2026-08-24): opt in to the widened mul_mat_vec crossover.
rem
rem ⚠ MEASURED INERT AT THE CURRENT -np. Live A/B on this box, single stream,
rem 3 reps each, same prompt/n_predict/temperature:
rem     stock b10549            tg ~104.5 tok/s
rem     knee build, default 8   tg ~108.2 tok/s   (+3.5%, from 32 upstream builds)
rem     knee build, mmv=16      tg ~107.9 tok/s   (no change -- within noise)
rem WHY: the gate is `n <= mul_mat_vec_max_cols`, where n is the number of
rem sequences decoding together. With -np 2 the batch can never exceed 2, so
rem `n <= 8` already holds and raising the cap to 16 changes no dispatch. The
rem knob only does anything once -np goes ABOVE 8 -- that is the cliff the
rem vulkancliff campaign measured, and this rung cannot reach it at 2 slots.
rem
rem It is left set deliberately: harmless, and it arms the moment -np rises.
rem Clamped to [1, 16] by the compile-time max -- setting 32 here would SILENTLY
rem clamp to 16, not error. To A/B, change this value and ArcServeRestart; to
rem revert to stock dispatch, delete this line (default is 8).
set GGML_VK_MMV_MAX_COLS=16

rem BINARY SWAP 2026-08-24, step 1 of 2: b10549 prebuilt -> the local knee build
rem (b10581-2-g242c3cd = upstream e85caa8 + the GGML_VK_MMV_MAX_COLS patch).
rem Step 1 deliberately sets NO env var, so mul_mat_vec_max_cols defaults to 8 and
rem dispatch is historically identical -- this step changes the BINARY only.
rem Pre-evidenced by vulkancliff data-correctness.md #2: patched-at-default vs the
rem b10549 prebuilt, 14B sanity bench, within +-3%.
rem ROLLBACK: git revert this file to the previous commit (the b10549 path), then
rem   schtasks /Run /TN ArcServeRestart
rem UBATCH 2026-08-29: -ub 1024 was PROMOTED AND THEN RETRACTED THE SAME DAY.
rem DO NOT RE-APPLY without a clean -np 2 measurement. The A/B that produced these
rem numbers is NOT trustworthy -- see the withdrawal note below.
rem     -ub 512  (default) : 104.26 / 104.84 / 103.40   <- matches the 109.31
rem                           recorded here on 2026-08-24
rem     -ub 1024           :  27.03 /  25.93 /  21.95
rem
rem CAUSAL CLAIM WITHDRAWN (ADR-0041, same day, later). This file previously
rem asserted that -ub 1024 CAUSES a ~4x decode regression at -np 2. It does not
rem say that any more, because the two arms were not measured at the same machine
rem state: ub512 was measured on a FRESH server (104) and ub1024 was measured
rem AFTER co-resident Flash work (22-27). Co-residency persistently degrades this
rem server by ~3.7x until it is restarted, which reproduces the entire "4x gap"
rem with the ubatch value held constant. The measured spread is real; the
rem attribution to -ub is not.
rem THE REVERT STILL STANDS, on different grounds: ub512 is the historical default
rem and matches the 109.31 recorded here on 2026-08-24. It is the known-good
rem value, not the winner of a valid A/B.
rem TO RESOLVE: re-run both arms FRESH AFTER RESTART (ADR-0041 rule 2), with
rem ff_ratecheck pre/post on each. Until then this is an open question, not a
rem settled finding.
rem
rem THE LESSON THAT DOES SURVIVE: llama-bench measured -ub 1024 as +5.7% prefill
rem and decode-neutral, so it looked free -- but llama-bench HAS NO -np and tests
rem ONE slot. A flag validated only where the harness can reach is NOT validated.
rem That gap was explicitly noted at promotion time and shipped anyway. Any flag
rem touching batching MUST be A/B tested against the live server before promotion.
E:\work\llamacpp-knee\build\bin\llama-server.exe ^
  -m E:\work\battlemage\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf ^
  --alias qwen3-30b-a3b ^
  -ngl 99 -sm layer -ts 1,1 ^
  -fa on ^
  --no-mmap -dio -fit off ^
  -c 131072 -np 2 ^
  --host 127.0.0.1 --port 8082 ^
  --slots --jinja --metrics ^
  --api-key %OMEN_ARC_TOKEN% > "C:\work\commandcenter\hearth\var\arc-serve.log" 2>&1
