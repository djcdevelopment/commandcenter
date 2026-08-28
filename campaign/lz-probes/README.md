# lz-probes — probe kit for the Level-Zero leverage experiments

Experiment cards: `docs/LZ-EXPERIMENT-CARDS.md`. Findings source:
`docs/LEVEL-ZERO-LEVERAGE-BRIEF.md`. Receipts land at
`E:\work\battlemage\lz-probes\lz-receipts.jsonl` (machine rows + w-style verdict rows).

## kit/ — rescued tools (provenance)

Copied byte-for-byte 2026-08-28 from session a58fb70c's scratchpad
(`%LOCALAPPDATA%\Temp\claude\C--work-commandcenter\a58fb70c-...\scratchpad\`), where every
W0–W3 rotation-phase1 probe tool lived un-versioned. These generated the numbers on the
rotation board — treat as frozen references:

- `probe_openai.py <port> <alias> [n_tokens] [ignore_eos]` — the canary. Its fixed prompt
  IS the 22-token prompt behind the `flash-ot` 11.7 tok/s prefill receipt.
- `probe_completion.py <port> <prompt-file> [n_predict]` — /completion wrapper.
- `r2_ladder.ps1 -Label -ModelPath -Device -Port` — the R2 load-timing harness
  (load_to_health_s = Start-Process → first healthy /health, 500 ms poll).
- `probe-prompt.txt` — the ~29K-token corpus used for the W0 KV fill.

## Lap-0 scripts (no GPU window needed; all coexist with production)

| Script | Card | What it decides |
|---|---|---|
| `lz3_d2d.py` (run in `E:\work\xpu-train\.venv`) | LZ3 | is the 2.29 vs 5.05 GB/s D2D asymmetry real (ABAB×3 sizes) |
| `lz4a_readladder.ps1` | LZ4a | unbuffered-read GB/s vs chunk size → gate for the PR #26014 port (≥6 GB/s) |
| `lz2_filewrite.ps1 -TargetDir <dir>` | LZ2 | the pure file term of KV save (residual = D2H+serialization floor) |
| `lz2_kv.ps1 -SavePath <dir> -LabelSuffix <tag>` | LZ2 | real slot save/restore on NVMe vs RAM disk |
| `lz1_stage0.ps1` | LZ1 | op-offload engagement proof (signed 22-tok regression + `1.off` tag) |
| `lz_prefill_probe.py <port> <label> <~tokens> [n_predict] [reps]` | LZ1 | unique-prefix prefill probe, `cache_prompt:false` |

Windowed cards (LZ1 headline, LZ6, LZ7) and the LZ4b port are specified in the cards doc;
they reuse these same tools.

Rules: env vars latch at process start; never time rep 1; internal server timings only;
every receipt row records co-residency; `--no-repack` on every `-ot` run; no
`ZE_AFFINITY_MASK`, ever.
