@echo off
rem serve-arc-oss.cmd — BANKED FIRE (ADR-0034): gpt-oss-120b MXFP4 on the dual B70s.
rem NOT a scheduled task; light it deliberately, pin backend="omen-arc-oss", douse it after.
rem
rem !! MUTUAL EXCLUSION IS NOT ENFORCED (the am4 lesson, B70-VERTICAL-TRACE):
rem !! stop the resident omen-arc first or both tenants degrade:
rem !!   schtasks /End /TN ArcServeBoot   (then taskkill /IM llama-server.exe /F)
rem !! and bring omen-arc back after:  schtasks /Run /TN ArcServeBoot
rem
rem Bake-off note (Stage 6): at -c 65536 fp16-KV this rides one card to 31.75 GiB
rem with ~0.9 GiB non-local. q8-KV clears it at a measured ~5-6%% throughput tax.
set GGML_VK_VISIBLE_DEVICES=1,2
call C:\work\commandcenter\hearth\var\gateway.cmd

E:\work\llamacpp-b10549-vulkan\llama-server.exe ^
  -m E:\work\models\gpt-oss-120b\gpt-oss-120b-MXFP4.gguf ^
  --alias gpt-oss-120b ^
  -ngl 99 -sm layer -ts 1,1 ^
  -fa on ^
  -ctk q8_0 -ctv q8_0 ^
  --no-mmap -dio -fit off ^
  -c 65536 -np 4 ^
  --host 127.0.0.1 --port 8083 ^
  --slots ^
  --api-key %OMEN_ARC_TOKEN%
