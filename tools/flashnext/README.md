# tools/flashnext — Flash-Next and lever-lap tooling (2026-10-15)

Scripts used for the Linux lever day (flash `docs/rnd-log.md` rows 2026-10-15; ledger revision 16, slice 17).
The llama.cpp build itself lives at `~/opt/llama.cpp-flashnext` (cookbook pin `52d4268` + the two patches here,
F16 math SYCL build); these are the drivers, copied verbatim so the recipes survive the build directory.

| File | Role |
|---|---|
| `launch-and-check.sh` | guarded launch: VRAM cliff (31.4 GiB), health timeout (8 min), first-token floor (10 tok/s), kill by pid; `RUNNER=` picks the launcher |
| `run-flashnext.sh` | Flash-Next on both B70s (cookbook flags); `-ot` on the command line replaces the built-in override; `--mtp` adds the draft |
| `run-27b.sh` | Qwen3.8-27B Q4_K_M on one B70 under llama.cpp SYCL (the engine A/B arm) |
| `bench.py` | prefill/decode cells from llama-server timings; `--spec on|off` (ignored by this build), `--out` jsonl |
| `bench_engine.py` | engine A/B through `/v1/completions` streaming on vLLM or llama.cpp; TTFT = first token; tokenizer-sized prompts |
| `toolprobe.py` | sandboxed list/read/grep multi-hop tool-calling probe with optional long background pad |
| `prefix_probe.py` | Hermes-style fixed prefix: cold/warm/concurrent TTFT on a vLLM seat |
| `vision_probe.py` | perception fixtures through a vision-enabled seat, manifest-graded |
| `grade_delivery.py` | form grader for a delivery (words, verbatim quotes, line numbers) |
| `arm-bench.sh`, `vision-arm.sh`, `vision-arm2.sh` | campaign commands for `fleet/experiment_linux.py` fenced arms (they source the seat key inside the process) |
| `zz-lv7-mbt16k.conf.staged`, `zz-lv12-30b-wna16.conf.staged`, `zz-lv15-vision.conf.staged` | the staged seat-1 drop-ins the arms used (live copies in `~/.config/systemd/user/omen-vllm@1.service.d/`) |
| `qwen4exp-mtp-draft-head.patch`, `sycl-fused-mmvq-mt.patch` | the cookbook patches applied to the pin |

The recipe that held for Flash-Next: `launch-and-check.sh <log> -- 128k --mtp -ot 'per_layer_token_embd=CPU,blk\.(20|21|22|23|44|45|46|47)\.ffn_(gate|up|down)_exps=CPU' --ubatch-size 1024 -b 1024`.
Protocol (Derek, 2026-10-15): load once, prove the first token, print a status line every minute, never `pkill -f`, never pipe live output through `cut`.
