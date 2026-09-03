@echo off
rem serve-arc-swap.cmd -- the omen-arc rung under llama-swap (ADR-0045 / plan P13, Derek's 2026-09-03 call).
rem PARKED: cutover.ps1 -Live copies this file over serve-arc.cmd as its first live step, so an
rem ArcServeBoot fired by anything else (imagegen recovery, a reboot) keeps the pre-cutover shape until
rem the ceremony has run.
rem Run by the ArcServeBoot scheduled task (S4U, boot trigger, ExecutionTimeLimit PT0S, RestartCount 3
rem @ PT1M -- unchanged). Before 2026-09-03 this file launched llama-server directly; that launcher is
rem preserved verbatim as serve-arc-direct.cmd and is the rollback.
rem
rem WHAT CHANGED: llama-swap v251 now owns the process lifecycle (ADR-0040 Phase 2). It listens on
rem 127.0.0.1:8081 and, via hooks.on_startup.preload, starts the SAME production command line as before
rem (fleet\arcserve\llama-swap\omen.yaml, production entry) with --host 127.0.0.1 --port 8082 fixed
rem behind a per-model proxy. Every existing :8082 consumer -- the door's omen-arc rung, the fx99
rem keep-alive (warm-arc.ps1), ff_ratecheck.py, occupancy.probe_omen_arc_slots, the ETW/keep-alive
rem readers -- stays byte-identical. Side seats (phi4/qwen14b/gptoss20b/mistral24b, two Vulkan-index
rem candidates each) and the 27B depth specialist load on demand through :8081 (the omen-swap rung).
rem
rem SECRETS: llama-server reads LLAMA_API_KEY from the environment (arg.cpp), so the api key never
rem appears in the YAML. Sourced from the same gitignored fragment the gateway uses.
rem
rem Device rule (ADR-0042): NO GGML_VK_VISIBLE_DEVICES for the dual-split production entry -- ggml-vulkan
rem selects by device type and llama.cpp drops the iGPU. Side entries carry index envs ONLY as sibling
rem candidates; placement is asserted from the -lv 5 load report, never assumed.
rem
rem Shape (2026-08-24, unchanged): -c 131072 -np 2 -ub 1024 -- raise backends.toml context_bytes AND
rem parallel_slots in lockstep; re-run the shared-usage assert after any -c/-np change.
rem
rem ROLLBACK: copy serve-arc-direct.cmd over this file, drop hearth\var\arc-maintenance.stop, run
rem ArcServeRestart (stop-only), delete the sentinel, then schtasks /Run /TN ArcServeBoot.

call C:\work\commandcenter\hearth\var\gateway.cmd
rem STEP 2 of 2 (2026-08-24): opt in to the widened mul_mat_vec crossover (inert at -np 2; arms
rem the moment -np rises; clamps silently above 16).
set GGML_VK_MMV_MAX_COLS=16
rem The upstream servers inherit this instead of an --api-key literal in the config.
set LLAMA_API_KEY=%OMEN_ARC_TOKEN%

set SWAP_EXE=E:\work\llama-swap-v251\llama-swap.exe
set SWAP_CFG=C:\work\commandcenter\fleet\arcserve\llama-swap\omen.yaml
set SWAP_LOG=C:\work\commandcenter\hearth\var\arc-swap.log
set SWAP_LISTEN=127.0.0.1:8081

rem Warm step in the background: waits for the preloaded production upstream to answer /health, then
rem fires one 1-token completion so the rung is warm before the keep-alive's next tick (ADR-0043).
start "" /B powershell -NoProfile -ExecutionPolicy Bypass -File C:\work\commandcenter\fleet\arcserve\warm-swap.ps1

rem Foreground, like the old llama-server line, so the scheduled task stays 'Running' and
rem schtasks /End still tears down the tree. logToStdout: both in the YAML puts upstream logs here too;
rem the production server additionally writes its own --log-file (arc-serve.log) as before.
"%SWAP_EXE%" -config "%SWAP_CFG%" -listen %SWAP_LISTEN% > "%SWAP_LOG%" 2>&1
