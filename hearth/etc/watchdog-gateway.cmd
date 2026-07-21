@echo off
rem HEARTH gateway EXTERNAL watchdog (ADR-0024). The in-gateway ops-loop watchdog
rem (ADR-0015) cannot revive the gateway it runs inside -- it dies with it. This
rem is the one liveness loop that must live OUTSIDE the door. Registered as the
rem scheduled task "HearthGatewayWatchdog" on a MINUTE/3 trigger.
rem
rem Design notes:
rem  - Revives through the HearthGatewayRestart task (RL HIGHEST, S4U), NOT by
rem    launching the gateway here. A LIMITED-integrity revive would start a door
rem    that cannot do Hyper-V admin (checkpoint_vm). Going through the restart
rem    task preserves integrity, and its S4U registration lets this medium caller
rem    trigger it without a UAC prompt.
rem  - Debounced: a single failed probe (a transient blip, or a probe caught mid
rem    bounce) must not trigger a restart. Only two consecutive failures act.
rem  - The tick must run even if its own log is momentarily locked, or a watchdog
rem    log-lock would defeat the watchdog -- so the log is best-effort and falls
rem    to NUL rather than aborting the tick.
cd /d C:\work\commandcenter

rem Bound the watchdog log so an every-3-min tick cannot grow it without limit.
for %%A in (hearth\var\gateway-watchdog.log) do if exist hearth\var\gateway-watchdog.log if %%~zA GTR 2097152 del hearth\var\gateway-watchdog.log 2>nul

set "WLOG=hearth\var\gateway-watchdog.log"
(echo.)>> "%WLOG%" 2>nul || set "WLOG=NUL"

C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m hearth.callers.doorcheck --json --facet door >> "%WLOG%" 2>&1
if not errorlevel 1 goto :done

rem First probe says unhealthy. Confirm with a second probe after a short gap
rem before acting, so a transient blip does not bounce a healthy door. `ping` is
rem the sleep (redirection-safe under a detached task; `timeout` is not).
ping -n 4 127.0.0.1 >nul
C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m hearth.callers.doorcheck --json --facet door >> "%WLOG%" 2>&1
if not errorlevel 1 goto :done

echo [%date% %time%] HearthGatewayWatchdog: door down on two probes, triggering HearthGatewayRestart >> "%WLOG%" 2>nul
schtasks /Run /TN HearthGatewayRestart >> "%WLOG%" 2>&1

:done
