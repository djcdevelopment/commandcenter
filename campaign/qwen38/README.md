# Qwen 3.8 HEARTH replacement campaign

This directory is the version-controlled source for the August 2026 Qwen 3.8
campaign. Runtime state is deliberately kept outside the repository at
`E:\work\battlemage\qwen38-bench-2026-08`.

The campaign has two lanes:

- **Qwen3.8-27B** is a production-replacement candidate. It must clear every
  correctness, quality, residency, latency, throughput, and soak gate.
- **Qwen3.8-Flash-Next** is quarantined experimental fire. It is not eligible
  to become the default in this campaign.

The scripts never alter `fleet/arcserve/serve-arc.cmd` or
`hearth/etc/backends.toml`. Entering maintenance explicitly stops the
`ArcServeBoot` process tree and writes a restore receipt. Restoring starts the
known-good task and proves a real one-token completion with
`fleet/arcserve/arc-serviceability.ps1`.

## Before the hardware window

From `C:\work\commandcenter`:

```powershell
python campaign/qwen38/qwen38_campaign.py validate
powershell.exe -NoProfile -ExecutionPolicy Bypass -File campaign/qwen38/scripts/prepare-engine.ps1
python campaign/qwen38/qwen38_campaign.py init --force
powershell.exe -NoProfile -ExecutionPolicy Bypass -File campaign/qwen38/scripts/preflight.ps1 -Mode Offline
```

The guarded runner captures the pinned Gemini reference before maintenance.
It can also be prepared or resumed independently, without occupying the B70s:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File campaign/qwen38/scripts/prepare-frontier.ps1
```

Preview the revision-pinned model acquisition without downloading anything:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File campaign/qwen38/scripts/acquire-models.ps1 -WhatIf
# Add -IncludeFlash, and optionally -IncludeFlashQ2, when the quarantined lane is wanted.
```

Run the same command without `-WhatIf`, or populate the paths in
`config/artifacts.json` by another audited method. Qwen3.8-27B, its MTP file,
and its vision projector are required; both Flash quants are optional. After
acquisition, lock the exact bytes and verify that nothing drifted:

```powershell
python campaign/qwen38/qwen38_campaign.py lock
python campaign/qwen38/qwen38_campaign.py verify-lock --rehash-artifacts
powershell.exe -NoProfile -ExecutionPolicy Bypass -File campaign/qwen38/scripts/preflight.ps1 -Mode Hardware
```

`lock` refuses missing required files and writes SHA-256, byte size, model
revision, engine revision, configuration hash, and task-set hash into the
runtime manifest. Hardware preflight also proves the Vulkan-index-to-BDF map,
driver versions, engine parent revision, binary hash, full model hashes,
commit headroom, the pinned Gemini 3.1 Pro rung/credentials, and the absence of
the iGPU after filtering.

## Campaign order

1. `prepare-frontier.ps1` (production remains online)
2. `00-enter-maintenance.ps1 -ConfirmOutage`
3. `01-baseline.ps1`
4. `02-qwen27-compatibility.ps1`
5. `03-qwen27-performance.ps1`
6. `04-qwen27-quality.ps1`
7. `05-flash-quarantine.ps1`
8. `06-final-soak.ps1`
9. `99-restore.ps1`
10. `07-score.ps1` (blind cloud judging runs only after restore is proved)

The guarded entry point runs that chain and always restores ArcServeBoot:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File campaign/qwen38/scripts/run-campaign.ps1 -ConfirmOutage
```

Use `-SkipFlash` when the optional day-zero artifacts are absent or when the
maintenance window is reserved for the 27B replacement decision only.

Each stage is resumable. A stage is complete only when its receipt has
`status: "passed"`; a partial JSONL file is never treated as completion.
Transport/judge-infrastructure retries append evidence under the same request
ID; gates and summaries use the latest attempt while preserving earlier rows.
`99-restore.ps1` is safe to run after any failure.

## Result shape

Every request is written independently to `results/requests/*.jsonl`. Summary
and verdict files are derived outputs:

```text
results/
  requests/                 one immutable row per request attempt
  telemetry/                b70tools, residency, commit, and watchdog streams
  serverlogs/               stdout/stderr per server process
  quarantine/               invalid, corrupt, crashed, or spilled legs
  receipts/                 resumability and stage disposition
  summaries/                per-configuration aggregates
  promotion-verdict.json    deterministic gate result
```

Raw output is retained even when invalid. Invalid completions contribute zero
successful jobs and are never included in throughput or preference rates.
Every launch command, embedded GGUF chat template, artifact/engine revision,
quant, placement, and device assignment is appended to
`state/server-launches.jsonl`. Watchdog samples carry energy counters, host RAM,
temperatures, shared-memory growth, commit headroom, and event status,
correlated to request rows by run ID. Cross-process local VRAM is explicitly
marked unobservable on this Windows/Vulkan stack; the campaign does not turn
the known-blind PDH/Vulkan gauges into fake residency numbers. Shared growth is
the authoritative spill signal.

Performance legs use streaming responses, so TTFT is measured at the client;
the server's prompt/decode timings and current `draft_n` / `draft_n_accepted`
MTP counters are retained separately. Closed-loop wall time is reconstructed
from per-client active time, so a resumed leg cannot accidentally count an
overnight pause as benchmark time.

## Deliberate operator decisions

- The quality assay is balanced across nine families (eight tasks each), not
  weighted by historical HEARTH traffic.
- A single campaign outage is permitted. Ordinary traffic must not share the
  benchmark servers.
- MTP is always tested after the non-MTP base path and is promoted only on net
  successful-output goodput.
- Long-context cells use buried-code retrieval and leave output headroom; a
  nonempty but context-blind answer does not count as a successful job.
- Flash-Next tries IQ4 first. Q2 is allowed only after IQ4 misses residency,
  correctness, goodput, or latency, and neither quant earns specialist status
  without a 65% order-consistent frontier win rate.
- Blind judges receive the original prompt, mechanical acceptance criteria,
  tool calls, multi-turn transcript, and image, while model identities and
  output order remain hidden.
- Advertised HEARTH capacity comes only from a measured, spill-free topology;
  native model context is not routing metadata.

A passing verdict says `eligible_for_pin_only_canary`; it does not edit HEARTH
routing or make the candidate default. That preserves the separate 48-hour
canary and human review boundary from the campaign plan.
