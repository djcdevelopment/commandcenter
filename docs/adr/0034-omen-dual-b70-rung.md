# 0034 — The dual-B70 rung lives on OMEN itself: omen-arc is the door default

**Status:** Accepted (2026-08-21); registered and proven through the door the same day

## Context

The 2026-08-20 OMEN rebuild (Core Ultra 9 285K, 128 GB DDR5, new Z890 board/case/PSU)
moved both Arc Pro B70s — and the 4 TB model drive — out of AM4 and into the gateway's
own host. That inverted the fleet's serving geography in one afternoon:

- **am4-moe / am4-oxen** (ADR-0018/0029) can never serve again as declared: their cards
  and their models are gone from that box. AM4 is dark, pending a rebuild as a CUDA node
  around the RTX 5070 that left OMEN.
- **omen-ollama**, the pool default, lost its silicon: the 5070 is gone and stock Ollama
  on Arc silently serves CPU-only (proven May 2026; re-observed 2026-08-20 when its
  resident CPU runners held 65 GB of commit through live benchmarks — the campaign's
  "commit thief" finding).

Rather than guess a replacement config, the **OMEN-LIMIT-TEST-2026-08 campaign** (lab
doc of the same name; artifacts at `E:\work\battlemage\burnin-2026-08\`) burned in the
new box and ran a genuinely open bake-off. The numbers that matter:

| Candidate (dual-split, -fit off) | single-stream | agg @ -np | p95 | VRAM |
|---|---|---|---|---|
| gpt-oss-120b MXFP4, -c 65536 -np 4 | 14.7 tok/s | ~21.6 | 25–32 s | 29.3 + **31.75** GiB (0.9 GiB non-local) |
| **Qwen3-30B-A3B, -c 65536 -np 4** | **95.2 tok/s** | **116.3** | **4.1 s** | 11.4 + 12.1 GiB |
| Llama-3.3-70B, -c 32768 -np 2 | 10.7 tok/s | 16.1 | 21.9 s | 24.8 + 25.1 GiB |

Supporting evidence from the same campaign: FA crossover bracketed at 16k–24k (fa-off
+25–87% pp below it, hard-hang above — the rung's 65k ctx mandates `-fa on`); q8-KV tax
collapsed to ~5–6% on driver 8974 (the old 2–4× trap is gone); the concurrency knee is
**N\*=8 and host-invariant** (reproduced with 78 GB commit free — it lives in the
cards/driver, so 128 GB of RAM does not raise it); COOPMAT re-enabled (2× prompt
processing, zero TDRs across ~6.5 h of soak); symmetric two-server replica scaling
un-retracted at 1.85×; co-tenancy with torch-xpu training measured at 0.99× serve
throughput.

## Decision

**1. `omen-arc` is the new rung and the pool default** (Derek's call, made on the
scorecard): Qwen3-30B-A3B-Instruct-2507 Q4_K_M, llama.cpp b10549 Vulkan, dual-split
`-sm layer -ts 1,1 -fa on -fit off --no-mmap -c 65536 -np 4`, loopback :8082,
`--api-key` bearer (`OMEN_ARC_TOKEN` — `api="openai"` requires a token even on
loopback). It inherits every opportunistic tag the am4 rungs carried
(`big-context`/`research`/`second-opinion`/`reasoning`) plus `default`/`code`.
`context_bytes = 57344` (16k tokens/slot, the same arithmetic as the old moe rung);
`parallel_slots = 4` — the knee study says 8 is the aggregate peak, 4 is held for
latency headroom.

**2. `omen-arc-oss` is declared as banked fire**: gpt-oss-120b MXFP4 on the same cards,
:8083, **no tags, pin-only, not resident**. Lighting it is deliberate
(`fleet/arcserve/serve-arc-oss.cmd`, q8-KV recommended at 65k). **Mutual exclusion with
omen-arc is not enforced** — the AM4 tenancy standoff (B70-VERTICAL-TRACE) taught that
no ceremony guarantees residency; the serve script says so where the operator will read it.

**3. The am4 rungs become tombstones, not deletions.** Tags stripped (nothing routes
there), entries kept as the historical record with ☠ markers; their occupancy probes are
removed (a probe that can only answer "unreachable" is decoration). `fleet/inventory.toml`
marks the am4 node `expect = "down"` pending the CUDA rebuild.

**4. `omen-ollama` is demoted, not removed**: tags stripped, pin-only, hardware profile
bumped to `omen-285k-dual-b70-2026H2` (as is omen-arc's). A pin still works — slowly,
on CPU — until an Arc-accelerated Ollama path is validated as separate work.

**5. Keep-alive is the ADR-0032 triad, cloned**: `ArcServeBoot` scheduled task (boot
trigger, S4U, `ExecutionTimeLimit PT0S`, `RestartCount 3` @ PT1M) running
`fleet/arcserve/serve-arc.cmd`; `ArcServeRestart` (no trigger — UAC-free bounce);
serviceability = `fleet/arcserve/arc-serviceability.ps1`, whose probe is a **real
1-token completion** (`/health` answering is exactly what a zombie also does — the
ADR-0032 lesson). Wiring the serviceability probe into the gateway's timer loop is
follow-up work, noted here so it isn't mistaken for done.

**6. The device rule is law**: on this box `Vulkan0` is the iGPU (74 GB shared budget);
every serving/bench process sets `GGML_VK_VISIBLE_DEVICES=1,2` and asserts the device
banner. The rule lives in the serve scripts and the campaign preamble.

## Consequences

- The door default went from ~54 tok/s CPU-bound single-stream (old ollama on the 5070
  era's successor hardware: CPU-only) to a measured 95 tok/s single-stream / 116
  aggregate with p95 4.1 s — and the offload doctrine's "prefer sunk" now points at
  hardware that was idle capital the day before.
- Routing semantics shift subtly: the default rung now carries the tags, so a busy
  omen-arc tag-route falls through to *itself* as default and waits in llama-server's
  queue instead of degrading to a weaker rung. Tests pin this behavior.
- `knowledge/capacity.json` capability history: `hardware_profile_id` bumps mean new
  observations accrue to `omen-285k-dual-b70-2026H2` — prior AM4 capability rows stay
  attributed to the hardware that earned them (ADR-0027).
- OllamaBoot stays **disabled** (Derek, 2026-08-21: its lifetime ledger — silent tray
  updates, zombie installs, the commit-thief incident — reads net negative). A pin on
  omen-ollama now requires a deliberate `Start-ScheduledTask OllamaBoot` first.
- The projections (`economics.py`, `gemini_pricing.py`, `call_mix_dashboard.py`) know
  the new rungs as `sunk`; `doorcheck` reads both via the standard TCP-informational
  probe for `api="openai"` rungs, with `arc-serviceability.ps1` as the honest check.
- **Thermal reality is part of the rung's operating envelope**: VRAM peaked at 94 °C
  (one card, PCI 04:00.0) during deep-context soak — one degree under the campaign's
  abort line — with an 8 °C card-to-card delta. Sustained serving heats the room it
  lives in (Derek's office, currently mitigated by a cardboard duct of some genius);
  relocation of the box is an open operational question, not an ADR matter.
- Evidence base: `OMEN-LIMIT-TEST-2026-08.html` (stage verdicts, findings F1–F7, charts),
  raw artifacts under `E:\work\battlemage\burnin-2026-08\`, and the campaign retro to
  follow. The full test suite (806) passes with the new pool wiring.
