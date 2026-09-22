@echo off
rem ADR-0048 deep lane. Activate only after AM4 fast/default qualification.
rem The Qwen3.8-27B Q4_K_M file path is supplied by the existing private
rem gateway fragment so the checked-in command contains no machine secret.
call C:\work\commandcenter\hearth\var\gateway.cmd
if not defined QWEN38_27B_GGUF exit /b 2
set SYCL_CACHE_PERSISTENT=1
set ZES_ENABLE_SYSMAN=1
set ONEAPI_DEVICE_SELECTOR=level_zero:gpu
E:\work\llamacpp-knee\build\bin\llama-server.exe --model "%QWEN38_27B_GGUF%" --alias qwen38-27b --host 127.0.0.1 --port 8082 --api-key "%OMEN_ARC_TOKEN%" -c 131072 -np 1 -sm layer -ts 1,1 -ctk f16 -ctv f16 -ub 2048 -b 4096 --no-mmap
