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

E:\work\llamacpp-b10549-vulkan\llama-server.exe ^
  -m E:\work\battlemage\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf ^
  --alias qwen3-30b-a3b ^
  -ngl 99 -sm layer -ts 1,1 ^
  -fa on ^
  --no-mmap -dio -fit off ^
  -c 131072 -np 2 ^
  --host 127.0.0.1 --port 8082 ^
  --slots --jinja --metrics ^
  --api-key %OMEN_ARC_TOKEN% > "C:\work\commandcenter\hearth\var\arc-serve.log" 2>&1
