@echo off
rem bankedfire-drain scheduled-task wrapper (Banked Fire P5 . Delta4 idle-drain).
rem One drain tick: ARM state -> occupancy (P2 probe) -> operating budget
rem (knowledge/operating-budget.json) -> highest-worth not-yet-run candidate
rem (knowledge/candidate_worth.json minus knowledge/experiment_results.json) ->
rem single-in-flight gated dispatch through the existing P3 submit_task path.
rem Every tick (dispatch or no-op) ledgers a bankedfire_drain.tick event.
rem Default DISARMED: arm explicitly with
rem   python -m fleet.bankedfire_drain --arm "reason" --authored-by derek
rem and disarm with
rem   python -m fleet.bankedfire_drain --disarm "reason" --authored-by derek
rem Same login-start posture as start-hearth-gateway.cmd / mechnet-watchdog.cmd.
rem Register every 30 min (see the schtasks command in
rem HEARTH-BANKED-FIRE-STRATEGY.html, P5 row):
rem
rem   schtasks /Create /TN "BankedfireDrain" /SC MINUTE /MO 30 /RL LIMITED /F ^
rem     /TR "C:\work\commandcenter\fleet\bankedfire-drain.cmd"
rem
cd /d C:\work\commandcenter
echo [%date% %time%] bankedfire-drain tick starting >> hearth\var\bankedfire-drain-task.log
C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe -m fleet.bankedfire_drain --json >> hearth\var\bankedfire-drain-task.log 2>&1
echo [%date% %time%] bankedfire-drain tick exited with %errorlevel% >> hearth\var\bankedfire-drain-task.log
