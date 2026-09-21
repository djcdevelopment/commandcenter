# HANDOFF — vLLM-XPU natively on Windows for the B70s (2026-09-20, before the reboot)

Written at the end of session cc-e0fdf649 (2026-09-19 evening → 2026-09-20). OMEN reboots next to finish an Arc
driver install. Read this, then `SESSION-RETRO-2026-09-20.md` (why), then `docs/rnd/vllm-xpu-windows/` (what was
measured). Decisions: `docs/adr/0047-*`. Every re-run command below was used this session and works.

## 1. State of the machine at handoff

| thing | state |
| --- | --- |
| OMEN Arc driver | **32.0.101.9030** (installed 2026-09-20 ~18:30Z via Intel's signed installer; reboot pending to "finalize"; the driver is already active). Old 8974 in the driver store `iigd_dch.inf_amd64_85a415bebe1aa6a6` for rollback. `corpus/vkdevices.py` map unchanged: iGPU = Vulkan0, B70s = Vulkan1/2 |
| production `omen-arc` | restored after the last window, `/health` 200 on 9030; `ArcServeBoot` brings it back at boot — verify after reboot (§2) |
| vLLM lab seats | none running (`kill-seat.ps1 -Port 8097` was the last action on the cards) |
| `hearth\var\arc-maintenance.stop` | removed (no window open) |
| NEO kernel cache `%LOCALAPPDATA%\NEO` | cleared 2026-09-20 for a probe; rebuilds itself; first SYCL launches after reboot JIT once |
| `~/.claude/rnd-mode.json` | this session's entry (`e0fdf649…`) is removed at the end of this handoff; the two older entries belong to other sessions |
| repo | commandcenter master clean of this session's work (docs committed through `015ef8a` + retro/ADR/handoff commit); large unrelated uncommitted changes from other sessions remain — not mine |

## 2. First ten minutes after the reboot

1. Production: `curl http://127.0.0.1:8082/health` → 200 (ArcServeBoot). If not, `schtasks /Run /TN ArcServeRestart`
   from PowerShell (no sentinel present → it restarts). `hearth` door: `/checkmcp`.
2. Driver sanity: `fleet-worker-node\.venv-omen\Scripts\python corpus\vkdevices.py` — expect Vulkan1/2 = B70.
3. **The gate that was never run:** `E:\work\llamacpp-knee\gpu-mem-gate.ps1` (or `b70tools verdict`) before any
   benchmark that will be quoted. Every number in the docs was taken with seats under ~24 GiB, unchecked for WDDM spill.
4. Confirm the vLLM stack still imports on 9030 after the reboot (no window needed, tiny tensors):
   `cmd /c "set ONEAPI_DEVICE_SELECTOR=level_zero:1&& E:\work\vllm-xpu-win\run.cmd python probes\l2_full_import.py"`
   → `FA2 True`, `xe2 True`, three `True`s.

## 3. The lab tree — `E:\work\vllm-xpu-win\`

| path | what |
| --- | --- |
| `.venv` | Python 3.12, torch 2.13.0+xpu, triton-xpu 3.7.2, vLLM (editable from `vllm-src`), vllm-xpu-kernels (editable from `kernels`), SYCL runtime pips 2026.1 |
| `run.cmd <cmd>` | runs anything inside the oneAPI 2026.1 + MSVC + venv env (`sycl-env.cmd` + `CC=cl` + Level Zero SDK path). Triton needs it (JITs a C helper) |
| `build-kernels.cmd <log> basic\|full` | rebuilds the kernel package (6 jobs, default attention configs; `VLLM_XPU_AOT_DEVICES=none` for JIT-only). Always `rm -rf kernels\build` first when CMakeLists changed. Copy `build\temp\csrc\xpu\*\xe_2\*_xe_2.dll`, `…\grouped_gemm\xe_default\grouped_gemm_xe_default.dll` and the compiler's `libmmd.dll` beside the `.pyd`s afterwards (setup.py's extra-lib step looks for `.so`) |
| `serve-30b.cmd <log> [card] [port]` | **the recommended 30B seat**: Qwen3-30B-A3B-GPTQ-Int4, TLA-free stack, XPU graphs sizes 1–8. 82.4 tok/s single / 1,196 @64 |
| `serve-27b.cmd <log> [card] [port] [max_model_len] [args]` | the 27B seat (`UTIL=0.93`, `VLLM_USE_V2_MODEL_RUNNER=0`, `VLLM_XPU_ATTN_PREFILL=64,32,8,1`, `--enforce-eager` for the baseline) |
| `serve.cmd` | generic launcher (0.6B smoke etc.); `kill-seat.ps1 -Port N` stops a seat (matches only this lab's `vllm serve … --port N` and its children) |
| `probes\` | every probe + every build/launch log (`l2_build*.log`, `l2_full*.log`, `l3_serve*.log`, `l4_*`, `l5_*`, `l6_*`, `l7_*`, `l8_cl_ext.py`). Drivers: `l3_chat.py` (correct + T=0 ×3), `l3_conc.py <port> <streams> <tokens>`, `l4_needle.py <port> <tokens>`, `l7_depth.py <port> <tokens> <gen>`, `l6_moe_tune.py [--write]`, `l7_attn_sweep.py` (`SEQ=`), `l4_fa2.py` / `l4_moe.py` / `l7_gdn_warm.py` (the fault probes) |
| `kernels` (branch `windows`, 4 commits on 0.1.14.1) | the Windows build patch set |
| `vllm-src` (branch `windows-xpu`, 5 commits on the fork's v0.29.0) | optional TLA imports; `moe_wna16` on XPU + loader-hook fix; B70 MoE table JSON; GDN → FLA route; attention tiling override; Triton W4A16 as fallback |
| `deps\level-zero-sdk` | Level Zero win SDK 1.33.1 (`ze_loader.lib` the toolkit lacks); `deps\driver\gfx_win_101.9030.exe` (Authenticode-verified) |
| `E:\work\models\hf` | HF cache: Qwen/Qwen3-0.6B; Qwen/Qwen3-30B-A3B-GPTQ-Int4 (16 GB); SergiioB/Qwen3.8-27B-GPTQ-Int4-sym-G128-MTP-BF16 (19 GB) |

## 4. The tenancy window (needed for any real seat — production holds ~14 GB on each card)

```
# open
Set-Content -Encoding ascii C:\work\commandcenter\hearth\var\arc-maintenance.stop "<who/why/when>"
schtasks /Run /TN ArcServeRestart          # stop-only while the sentinel exists; ~25 s; both cards → 31,906 MiB free
# ... lap ...
# close
Remove-Item C:\work\commandcenter\hearth\var\arc-maintenance.stop
schtasks /Run /TN ArcServeRestart          # restarts production; wait for http://127.0.0.1:8082/health = 200
```
Never `Stop-Process` production. Five windows this session, no incident.

## 5. Where the program stands (one paragraph each; numbers in the docs)

- **30B-A3B on one B70, natively on Windows: functional and fast.** Correct, T=0 deterministic, 82.4 tok/s single,
  330 @8, 681 @32, 1,196 @64, ~2.4k prompt tok/s at 12k. Levers that paid: TLA-free stack, XPU graphs for sizes 1–8
  (eager above — captured MoE loses at batch ≥ 32), a swept B70 MoE table. Not done: the SAT-L1 jobs/h shape, two seats
  at once, the `omen-vllm` door stanza (pin-only, :8097, zero code).
- **Qwen3.8-27B (hybrid, 48 GDN + 16 full-attn) at 128k on one card.** 463 tok/s prefill at 13.6k; 161 tok/s prefill /
  6.0 tok/s decode at 64k; needle found. **128k itself not run** (~20–25 min; needs a cue). Prefill is O(n²) in the
  Triton attention kernel; tunables inventoried in `DENSE-27B-DEPTH.md`.
- **The wall:** every CUTLASS-SYCL (`intel/sycl-tla`) kernel faults on Windows — FA2, MoE grouped GEMM, GDN — under
  8974 and 9030, JIT or AOT, both SYCL runtimes; the B70 advertises every extension they need. `LINUX-DELTA.md` has the
  ladder. **E1 (driver 9030) is done and negative.**

## 6. Next laps, with what each needs

| # | lap | needs | est. |
| --- | --- | --- | --- |
| E3 | Linux IGC's ISA under the Windows runtime: dump the FA2 kernel SPIR-V on Windows (`SYCL_DUMP_IMAGES=1` on `l4_fa2.py`), `ocloc compile -spirv_input -device bmg-g31` on **AM4** with the validated profile, load NATIVE on OMEN (`zeModuleCreate` harness or DPC++ persistent cache) | `sudo apt install` on AM4 of the 8 `.deb`s in `kernels\build_script\gpu_runtime_packages.json` → profile `igc-2.34.4-cr-26.18` (Derek's cue; AM4 has icpx 2026.0, 24 cores, 30 GB, 22 GB disk) | ~1 h |
| E2 | registry IGC flags (`HKLM\SOFTWARE\Intel\IGFX\IGC`: `ShaderDumpEnable`, `DisableIGCOptimizations`, `Decompose2DBlockFuncsMode`, `TotalGRFNum=256`) + NEO `GpuFaultCheckThreshold`; env vars are ignored on Windows | admin shell (Derek) | minutes/flag |
| 27B-a | XPU graphs + V2 runner on the 27B now that GDN is on the FLA path (gave 4.4× on the 30B) | window | 20 min |
| 27B-b | MTP speculative decode with the checkpoint's own head (`--speculative-config`); llama.cpp doubled depth decode with MTP on this model | window | 30 min |
| 27B-c | the 128k point; `--kv-cache-dtype fp8`; decode tiling at depth (`TILE_SIZE_DECODE`, split-KV segments) | window + cue | 25 min + |
| 30B-a | SAT-L1 jobs/h at 8×16k vs 3,358; two seats (`ONEAPI_DEVICE_SELECTOR=level_zero:0/1`, ports 8097/8098) | window | 30 min |
| door | `omen-vllm` stanza in `hearth/etc/backends.toml` (pin-only, :8097) + `HearthGatewayRestart` + one pinned `local_generate` proof | tenancy answer: the seat needs production stopped | 20 min |
| upstream | PR `vllm-project/vllm`: the `MoeWNA16Config` loader-hook fix + the B70 MoE config JSON; PR `vllm-xpu-kernels`: the Windows build patch set | Derek's word | — |

## 7. Traps that cost a lap each (all in memory too)

- A directory named `vllm` in the cwd shadows the package (clone is `vllm-src`). The fork's deprecated `api_server`
  entrypoint has a stray `import uvloop` — use `vllm serve`. The banner's block glyphs crash a cp1252 process
  (`PYTHONUTF8=1`). A dead EngineCore leaves the API-server parent holding ZMQ :29550 (`kill-seat.ps1`).
- `serve.cmd`'s arg loop splits on commas — a JSON `--compilation-config` needs its own `.cmd` (`serve-30b.cmd`).
- The captured graph path warms over its first 2–3 runs (28 → 50 → 72 tok/s): sample three times.
- An access violation in a Triton launch (`driver.py:launch`, `_ctypes`) means the **previous** kernel faulted the GPU.
- A killed `-j16` ninja keeps compiling for minutes; check for live `clang-cl.exe` before rebuilding. `until grep`
  loops on a log must also watch the process — a crashed EngineCore never writes the line.
- Bash heredocs mangle `\v`/`\b`/`\w` in Windows paths (three times this session) — write patch scripts with the
  Write tool and run them.
- The oneAPI toolkit's `ocloc` is Windows driver-lineage (`32.0.101.8857 (26.24)`): an AOT build here does **not**
  test Linux IGC codegen. IGC/NEO env knobs are ignored on Windows.

## 8. Open decisions (also in `DECISIONS-PENDING.md`)

- E3's `sudo apt install` on AM4 (services host; the debs don't touch NVIDIA).
- Whether the vLLM seat becomes a door rung before it clears the saturation bar (ADR-0047 §7).
- The two upstream PRs.
- The SYCL MoE determinism policy from the sibling program (still open; vLLM's eager path is deterministic).
