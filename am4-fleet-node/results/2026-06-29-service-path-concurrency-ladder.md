# AM4 Hermes Concurrent Service-Path Ladder

Date: 2026-06-29
Host: `am4`
Model: `Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf`
Path: Hermes facade on `:8090` with one resident backend per run

## Scope

True concurrent client traffic at `16k` service depth.

All runs used:

- context: `131072`
- prompt target: `16384`
- realized prompt: about `14342` prompt tokens
- generation cap: `32`
- backend slot count matched client concurrency
- placement set to either `single0` or `layer`

## Ladder table

| Placement | Slots | Clients | Batch elapsed | TTFT min | TTFT median | TTFT max | Elapsed max | Artifact |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `single0` | `1` | `1` | `22.9s` | `22.9s` | `22.9s` | `22.9s` | `22.9s` | `service-path-concurrency-2026-06-29T124030Z-single0-ctx131072-p1-c1-depth16384.json` |
| `single0` | `2` | `2` | `45.3s` | `44.5s` | `44.9s` | `45.3s` | `45.3s` | `service-path-concurrency-2026-06-29T124107Z-single0-ctx131072-p2-c2-depth16384.json` |
| `single0` | `4` | `4` | `91.0s` | `88.3s` | `89.8s` | `91.0s` | `91.0s` | `service-path-concurrency-2026-06-29T124207Z-single0-ctx131072-p4-c4-depth16384.json` |
| `single0` | `8` | `8` | `181.5s` | `117.8s` | `178.2s` | `181.5s` | `181.5s` | `service-path-concurrency-2026-06-29T124353Z-single0-ctx131072-p8-c8-depth16384.json` |
| `layer` | `1` | `1` | `22.8s` | `22.8s` | `22.8s` | `22.8s` | `22.8s` | `service-path-concurrency-2026-06-29T124718Z-layer-ctx131072-p1-c1-depth16384.json` |
| `layer` | `2` | `2` | `44.8s` | `44.0s` | `44.4s` | `44.8s` | `44.8s` | `service-path-concurrency-2026-06-29T124758Z-layer-ctx131072-p2-c2-depth16384.json` |
| `layer` | `4` | `4` | `90.1s` | `87.3s` | `88.9s` | `90.1s` | `90.1s` | `service-path-concurrency-2026-06-29T124900Z-layer-ctx131072-p4-c4-depth16384.json` |
| `layer` | `8` | `8` | `179.3s` | `116.1s` | `175.8s` | `179.3s` | `179.3s` | `service-path-concurrency-2026-06-29T125047Z-layer-ctx131072-p8-c8-depth16384.json` |

## Readout

1. The concurrent curve is nearly linear with client count.
2. TTFT is almost the same as full request completion time at `1`, `2`, and `4` clients.
3. At `8` clients, a few requests start earlier, but the median request still does not receive first token until very late in the batch.
4. That means prompt work is dominating under load, and the current served path is not giving useful early streaming behavior at this depth.
5. `layer` is slightly ahead of `single0` at every concurrent rung, but only by a small amount:
   - about `0.1s` at `1`
   - about `0.5s` at `2`
   - about `0.9s` at `4`
   - about `2.3s` at `8`

## Current conclusion

Concurrent traffic does not change the placement decision yet.

`layer` becomes a little more defensible under load, but not enough to overturn the operational simplicity of `single0`.

The bigger issue is architectural: at `~14k` prompt tokens, streaming usefulness collapses under concurrent prompt ingest. If this node is meant to serve overlapping long-context requests, the next optimization target is not just placement. It is request scheduling, prompt caching strategy, and possibly admission control.

## Notes

- There is an earlier smoke artifact for `single0` at `2` clients:
  - `service-path-concurrency-2026-06-29T123859Z-single0-ctx131072-p2-c2-depth16384.json`
  The later ladder run is the canonical one.
