@echo off
rem Bounded D1 gateway cutover launcher.  This file lives in the clean worktree
rem deliberately: it must never source runtime code or pool configuration from
rem the dirty commandcenter checkout.
setlocal
cd /d C:\work\commandcenter-flash-foreman

rem Load credentials without printing or copying them.  The fragment is private
rem and remains owned by the existing production gateway deployment.
if exist C:\work\commandcenter\hearth\var\gateway.cmd call C:\work\commandcenter\hearth\var\gateway.cmd >nul 2>&1

rem Pin the process, rather than relying on its working directory, so every
rem request and pool_config_hash use the qualified D1 declaration.
set "HEARTH_BACKENDS=C:\work\commandcenter-flash-foreman\hearth\etc\backends.toml"
set "HEARTH_SCOPE=C:\work\commandcenter;C:\work"
set "HEARTH_CLEAN_D1_LOG=C:\work\baseline\fieldlab\runs\gateway-clean-d1-20260922.log"

start "HEARTH clean D1 gateway" /b C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m hearth.kernel.gateway --callers C:\work\commandcenter\hearth\var\callers.json --providers hearth.toolsurface.fs,hearth.toolsurface.git,hearth.toolsurface.testing,hearth.toolsurface.knowledge,hearth.toolsurface.summon,hearth.toolsurface.inference,hearth.toolsurface.execution_control,hearth.toolsurface.local_work,hearth.toolsurface.task_lane,hearth.toolsurface.fleet_harvest,hearth.toolsurface.patrol,hearth.toolsurface.masters_pet,hearth.toolsurface.dream,hearth.toolsurface.scheduler,hearth.toolsurface.am4,hearth.toolsurface.commander,hearth.toolsurface.build_requests,hearth.toolsurface.media_render,hearth.toolsurface.image_generate,hearth.toolsurface.media_generate,hearth.toolsurface.rotation,hearth.toolsurface.rungstate >> "%HEARTH_CLEAN_D1_LOG%" 2>&1
endlocal
