# AM4 Hermes Service-Path Ladder

Date: 2026-06-29
Host: `am4`
Model: `Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf`
Mode: served path through Hermes facade on `:8090`

## Scope

This is the service-path depth ladder for the two placements that still matter:

- `single0`
- `layer`

All runs used:

- context: `131072`
- parallel slots: `1`
- generation cap: `32`
- KV cache: `q8_0 / q8_0`

## Ladder table

| Placement | Prompt target | Real prompt tokens | Non-stream total | Prompt tok/s | Gen tok/s | Stream TTFT | Artifact |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `single0` | `8192` | `7208` | `9.8s` | `863.49` | `22.44` | `1.03s` | `service-path-2026-06-29T123357Z-single0-ctx131072-p1-depth8192.json` |
| `layer` | `8192` | `7208` | `9.9s` | `855.60` | `21.80` | `1.05s` | `service-path-2026-06-29T123313Z-layer-ctx131072-p1-depth8192.json` |
| `single0` | `16384` | `14342` | `23.4s` | `671.72` | `15.74` | `1.66s` | `service-path-2026-06-29T122858Z-single0-ctx131072-p1-depth16384.json` |
| `layer` | `16384` | `14342` | `23.3s` | `677.49` | `15.51` | `1.68s` | `service-path-2026-06-29T122754Z-layer-ctx131072-p1-depth16384.json` |
| `single0` | `32768` | `28651` | `66.0s` | `456.64` | `9.92` | `2.91s` | `service-path-2026-06-29T121346Z-single0-ctx131072-depth32768.json` |
| `layer` | `32768` | `28651` | `65.0s` | `464.54` | `9.86` | `2.94s` | `service-path-2026-06-29T121514Z-layer-ctx131072-depth32768.json` |
| `single0` | `65536` | `57228` | `213.3s` | `275.69` | `5.69` | `5.43s` | `service-path-2026-06-29T115854Z-single0-ctx131072-depth65536.json` |
| `layer` | `65536` | `57228` | `211.1s` | `279.04` | `5.37` | `5.79s` | `service-path-2026-06-29T120935Z-layer-ctx131072-depth65536.json` |

## Readout

1. The served path behaves the way the raw ladder hinted it would: prompt ingest falls off hard as depth rises, and generation also decays with depth.
2. `single0` wins the shallow rung outright at `8k`.
3. `single0` and `layer` are nearly tied at `16k`.
4. `layer` takes a small ingest lead at `32k` and `64k`, but not enough to produce a meaningfully better service profile.
5. The multicard `layer` cost still shows up outside raw ingest:
   - slower backend bring-up
   - slower readiness probe
   - slightly worse generation rate
   - slightly worse stream TTFT

## Current operating read

Use `single0` as the default Hermes service placement.

Keep `layer` as the long-context stretch path to revisit when either:

- true multi-request concurrency becomes the bottleneck, or
- deeper prompts than the current `~57k` realized service probe dominate the workload.
