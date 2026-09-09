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

## ETW lane — from a continuous ring back to the analyzers

`etw2_join.py` / `etw3_perqueue.py` / `etw4_depth.py` all read one `report.json` shape, and
until now the only producer of it was `etw1_feasibility.ps1` (elevated, short Sequential
sessions). The continuous recorder — `etw6_session.ps1` (elevated, owns the `lz_dxgk_ring`
session) plus `etw6_watch.py` (unelevated, snapshots to `cap-<stamp>-<tag>.etl` + a
`cap-*.json` manifest) — had no path back into them.

- `etw10_package.py --manifest cap-X.json --requests rows.jsonl (--dump D.xml | --etl cap-X.etl) --out report.json`
  closes that gap: pure post-processing, **unelevated** (tracerpt on an existing `.etl`
  needs no elevation — measured, see the module docstring), read-only over captures. Arms
  come from a load-harness JSONL (`campaign/qwen38/qwen38_campaign.py load` rows), one per
  `run_id` (or per client with `--group-by client`). `--describe` prints a dump's time span
  and the groups a request log would produce without writing anything.
  Fixtures: `test_etw10_package.py`.

Two things to know before using it. The analyzers derive the arm label from the dump
**filename** (`"etw-" + dump.split("-r")[-1].split(".")[0]`), so a trace's name is the join
key and the packager materialises one `-rN.dump.xml` name per arm over the single
underlying XML — N traces are N *windows* on one capture, not N captures. And a 19 GB ring
snapshot converts to far more XML than fits anywhere comfortable, so `--max-dump-mb` stops
the converter at a budget and repairs the partial XML to well-formed; a budgeted dump holds
only the **earliest** part of the trace, and the packager refuses arms that fall outside the
dump's span rather than reporting them as 100% starved.
