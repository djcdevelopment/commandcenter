@echo off
rem serve-arc-night.cmd -- the NIGHT shape of OMEN's B70s (Derek's 2026-09-12 call; nightshift guest epochs).
rem Runs llama-swap with fleet\arcserve\llama-swap\omen-night.yaml: NO production entry, Qwen3.8-Flash-Next
rem (full placement, fork binary) and Qwen3.8-27B (knee binary) in one exclusive swap group. Everything on
rem :8082 is down for the epoch by design (ADR-0040 "epochs remain the shape for Flash-Next").
rem
rem Run by the ArcServeNight scheduled task (same principal as ArcServeBoot, NO boot trigger -- a night
rem epoch is always a deliberate, Derek-called state). Ceremony, all from PowerShell:
rem   1. ssh fx99 sudo systemctl stop arc-keepalive.timer arc-keepalive-deep.timer   (the deep unit's
rem      ExecStartPost can boot the day shape over this one via ArcServeRestart)
rem   2. echo nightshift-epoch %DATE% %TIME% > hearth\var\arc-maintenance.stop   (holds every other
rem      day-shape boot: watchdog, imagegen recovery; serve-arc.cmd refuses to start under it)
rem   3. schtasks /Run /TN ArcServeRestart      (stop-only; wait until llama-swap.exe and llama-server.exe are gone)
rem   4. schtasks /Run /TN ArcServeNight        (this file)
rem   MORNING: schtasks /Run /TN ArcServeRestart -> del hearth\var\arc-maintenance.stop -> schtasks /Run /TN
rem   ArcServeBoot -> ssh fx99 sudo systemctl start arc-keepalive.timer arc-keepalive-deep.timer.
rem
rem Unlike serve-arc.cmd this launcher does NOT run hearth\execution\maintenance.py as a gate: the sentinel is
rem SUPPOSED to be present during a night epoch (it is what keeps the day shape from booting underneath).
rem It refuses instead if the day shape is still running (llama-swap on :8081 / llama-server on :8082).
rem
rem SECRETS: the bearer is sourced from the same gitignored fragment the gateway uses; never echoed.

setlocal
netstat -ano | findstr /R /C:"127.0.0.1:8082 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo serve-arc-night: the day shape is still listening on :8082 -- run ArcServeRestart first and wait for it to stop
  exit /b 2
)
netstat -ano | findstr /R /C:"127.0.0.1:8081 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo serve-arc-night: something is still listening on :8081 -- run ArcServeRestart first and wait for it to stop
  exit /b 2
)

call C:\work\commandcenter\hearth\var\gateway.cmd
rem Same performance hack as the day shape (llama.cpp PR ggml-org#27652): both binaries carry the override.
set GGML_VK_MMV_MAX_COLS=16
rem The upstream servers inherit this instead of an --api-key literal in the config.
set LLAMA_API_KEY=%OMEN_ARC_TOKEN%

set SWAP_EXE=E:\work\llama-swap-v251\llama-swap.exe
set SWAP_CFG=C:\work\commandcenter\fleet\arcserve\llama-swap\omen-night.yaml
set SWAP_LOG=C:\work\commandcenter\hearth\var\arc-swap-night.log
set SWAP_LISTEN=127.0.0.1:8081

rem Foreground so the scheduled task stays 'Running' and schtasks /End (via ArcServeRestart) tears down the tree.
"%SWAP_EXE%" -config "%SWAP_CFG%" -listen %SWAP_LISTEN% > "%SWAP_LOG%" 2>&1
