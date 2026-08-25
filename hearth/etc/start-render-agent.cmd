@echo off
rem BF6 render agent -- the interactive-session GPU executor.
rem Registered as scheduled task "BF6RenderAgent" (LOGON trigger, OMEN\derek).
rem
rem MUST run in an interactive session. The HEARTH gateway runs in session 0,
rem which has no D3D adapter access at all, so it cannot render -- that split is
rem the whole reason this process exists (ADR-0036). Never register this with
rem /RU SYSTEM or as an S4U task: it will start, report healthy, and be unable
rem to create a device.
cd /d C:\work\commandcenter
set HEARTH_FFMPEG=C:\Users\derek\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe
set HEARTH_FFPROBE=C:\Users\derek\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffprobe.exe
set HEARTH_SCOPE=C:\work\commandcenter;C:\work
if not exist hearth\var\log mkdir hearth\var\log
python -m hearth.media.agent >> hearth\var\log\render-agent.log 2>&1
