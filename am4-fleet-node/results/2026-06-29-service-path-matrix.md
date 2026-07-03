# AM4 Hermes Service-Path Matrix

Date: 2026-06-29
Host: `am4`
Model: `Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf`
Path: backend `llama-server` on `:8080` through Hermes facade on `:8090`

## Runs

All runs used:

- alias: `vllama-planner`
- context: `131072`
- KV cache: `q8_0 / q8_0`
- generation cap: `32`
- one warm-up serve-readiness call before measured requests

## Summary table

| Placement | Parallel | Prompt target | Real prompt tokens | Backend ready | Serve ready | Non-stream total | Prompt tok/s | Gen tok/s | Stream TTFT | Artifact |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `single0` | `1` | `65536` | `57228` | `14.0s` | `0.52s` | `213.3s` | `275.69` | `5.69` | `5.43s` | `service-path-2026-06-29T115854Z-single0-ctx131072-depth65536.json` |
| `single1` | `1` | `65536` | `57228` | `14.0s` | `0.50s` | `217.1s` | `270.71` | `5.68` | `5.43s` | `service-path-2026-06-29T120533Z-single1-ctx131072-depth65536.json` |
| `layer` | `1` | `65536` | `57228` | `16.0s` | `0.91s` | `211.1s` | `279.04` | `5.37` | `5.79s` | `service-path-2026-06-29T120935Z-layer-ctx131072-depth65536.json` |
| `single0` | `1` | `32768` | `28651` | `14.0s` | `0.50s` | `66.0s` | `456.64` | `9.92` | `2.91s` | `service-path-2026-06-29T121346Z-single0-ctx131072-depth32768.json` |
| `layer` | `1` | `32768` | `28651` | `16.0s` | `0.92s` | `65.0s` | `464.54` | `9.86` | `2.94s` | `service-path-2026-06-29T121514Z-layer-ctx131072-depth32768.json` |
| `single0` | `2` | `32768` | `28651` | `14.0s` | `0.50s` | `65.3s` | `461.99` | `9.92` | `2.91s` | `service-path-2026-06-29T122135Z-single0-ctx131072-p2-depth32768.json` |
| `layer` | `2` | `32768` | `28651` | `16.0s` | `0.92s` | `64.2s` | `470.43` | `9.86` | `2.94s` | `service-path-2026-06-29T122303Z-layer-ctx131072-p2-depth32768.json` |
| `single0` | `4` | `32768` | `28651` | `14.0s` | `0.51s` | `65.3s` | `461.59` | `9.92` | `2.90s` | `service-path-2026-06-29T122444Z-single0-ctx131072-p4-depth32768.json` |
| `layer` | `4` | `32768` | `28651` | `16.0s` | `0.91s` | `64.4s` | `469.05` | `9.87` | `2.94s` | `service-path-2026-06-29T122612Z-layer-ctx131072-p4-depth32768.json` |
| `single0` | `1` | `16384` | `14342` | `14.0s` | `0.50s` | `23.4s` | `671.72` | `15.74` | `1.66s` | `service-path-2026-06-29T122858Z-single0-ctx131072-p1-depth16384.json` |
| `layer` | `1` | `16384` | `14342` | `16.0s` | `0.91s` | `23.3s` | `677.49` | `15.51` | `1.68s` | `service-path-2026-06-29T122754Z-layer-ctx131072-p1-depth16384.json` |

## Observations

1. `single0` remains the best default operating point so far.
2. `single1` is close, but consistently behind `single0`.
3. `layer` improves prompt ingest throughput slightly at every measured depth.
4. That prompt-ingest win is small relative to its costs:
   - slower backend startup
   - slower serve-readiness probe
   - slightly worse generation rate
   - slightly worse streamed TTFT
5. At `~57k` realized prompt tokens, `layer` cuts about `6.3s` off total prompt ingest versus `single0`, but loses roughly `0.36s` on the streamed follow-on request and adds about `2s` to backend bring-up.
6. At `~28.6k` realized prompt tokens, `layer` saves about `1.1s` total on the non-stream request, which is marginal in service terms.
7. Increasing `PARALLEL` from `1` to `2` to `4` did not materially change single-request service behavior in this harness. The backend advertises more slots, but these runs are still one-request traces.
8. At `~14.3k` realized prompt tokens, `single0` and `layer` are effectively tied on total request time. `single0` keeps the startup/readiness advantage; `layer` keeps a small ingest advantage.

## Harness notes

- The failed artifact `service-path-2026-06-29T122754Z-single0-ctx131072-p1-depth16384.json` was a port-collision artifact from launching two backend-owned runs at the same time. It is not a model or placement failure.

## Current conclusion

For the Linux AM4 Hermes service path, multicard `layer` is functional but not yet compelling. It is a valid benchmark and stretch path, not the present default deployment mode.

If the operating goal is lowest friction and strongest single-stream served behavior, use `single0`.

If the operating goal is exploring the edge where prompt ingest dominates enough to justify multicard overhead, keep testing `layer` at deeper prompts and with true concurrent client traffic.
