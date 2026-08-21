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

rem Token: shared with the gateway (backends.toml auth_env OMEN_ARC_TOKEN).
rem Sourced from the same gitignored fragment the gateway uses.
call C:\work\commandcenter\hearth\var\gateway.cmd

E:\work\llamacpp-b10549-vulkan\llama-server.exe ^
  -m E:\work\battlemage\models\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf ^
  --alias qwen3-30b-a3b ^
  -ngl 99 -sm layer -ts 1,1 ^
  -fa on ^
  --no-mmap -dio -fit off ^
  -c 65536 -np 4 ^
  --host 127.0.0.1 --port 8082 ^
  --slots ^
  --api-key %OMEN_ARC_TOKEN%
