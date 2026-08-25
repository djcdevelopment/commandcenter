@echo off
rem BF6 pipeline EXTERNAL watchdog. One bounded pass every minute; the task
rem exits after each observation, so it cannot fate-share with the workers.
cd /d C:\work\commandcenter

rem Keep wrapper output bounded. Structured current truth and transitions live
rem under hearth\var\render\pipeline-watchdog.*.
for %%A in (hearth\var\bf6-pipeline-watchdog-task.log) do if exist hearth\var\bf6-pipeline-watchdog-task.log if %%~zA GTR 2097152 del hearth\var\bf6-pipeline-watchdog-task.log 2>nul

python -m hearth.media.watchdog --json >> hearth\var\bf6-pipeline-watchdog-task.log 2>&1
exit /b %errorlevel%
