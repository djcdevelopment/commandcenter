@echo off
setlocal
rem Build the zello_p2p_copy port against the installed Level Zero SDK with MSVC.
rem Run from PowerShell:  cmd /c campaign\lz-probes\build_zello_p2p.cmd
cd /d "%~dp0"
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "SDK=C:\Program Files\LevelZeroSDK\1.32.0"
if not exist build mkdir build
cl /nologo /EHsc /std:c++17 /O2 /I"%SDK%\include" zello_p2p_port.cpp /Fo:build\ /Fe:build\zello_p2p_port.exe /link "%SDK%\lib\ze_loader.lib"
exit /b %ERRORLEVEL%
