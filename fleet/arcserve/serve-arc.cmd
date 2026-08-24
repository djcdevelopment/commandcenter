@echo off
rem serve-arc.cmd — the omen-arc rung (ADR-0034): Qwen3-30B-A3B dual-B70 llama-server.
rem Run by the ArcServeBoot scheduled task (S4U, boot trigger, ExecutionTimeLimit PT0S,
rem RestartCount 3 @ PT1M — the ADR-0032 hardening pattern). Campaign-proven flags:
rem OMEN-LIMIT-TEST-2026-08 (Q4 CV 2.3%%, zero TDR/WHEA over the soaks).
rem
rem Device rule (Phase 0 finding): Vulkan0 is the iGPU on this box — the visibility
rem filter is LOAD-BEARING. Never remove it; re-verify indices after driver updates.
set GGML_VK_VISIBLE_DEVICES=1,2

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
