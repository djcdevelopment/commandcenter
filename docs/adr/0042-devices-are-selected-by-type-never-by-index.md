# 0042 — Devices are selected by type, never by index

**Status:** Accepted (2026-08-29) — root-caused, fixed, and verified across restarts — **Addendum 2026-09-03:** caught a live instance 2026-09-03: a single-device filter at index 2 selected the iGPU, and the assertion, not the index, decided.

**Companion to:** `docs/adr#0034` (the dual-B70 rung whose boot ceremony carried the defect),
`docs/adr#0041` (co-residency poisons the incumbent — the other silent-degradation ADR from the
same day)

## Context

`fleet/arcserve/serve-arc.cmd` carried `set GGML_VK_VISIBLE_DEVICES=1,2` with an emphatic comment:
*"Vulkan0 is the iGPU on this box — the visibility filter is LOAD-BEARING. Never remove it;
re-verify indices after driver updates."*

On 2026-08-29 the production server's own load report at `-lv 5` showed:

```
- Vulkan0 : Intel(R) Arc(TM) Pro B70 Graphics (32558 MiB)
- Vulkan1 : Intel(R) Graphics                     <- the iGPU, not the second B70
llama_prepare_model_devices: using device Vulkan0 ...
load_tensors: offloaded 49/49 layers to GPU
```

The filter had selected **one B70 plus the iGPU**. llama.cpp then drops the iGPU when a dGPU is
present, leaving a single device, and the entire model landed on it: **17524 model + 12288 KV +
296 compute = 30108 MiB, 92.5% of one 32558 MiB card**, with the second B70 holding nothing.
Confirmed independently by b70tools per PCI BDF (`0000:04:00.0` 29.57 GB / `0000:09:00.0` 0.00 GB)
and by the ~10 GB spill into host memory that appeared whenever anything else touched the cards.

**The indices had not merely gone stale — they are not stable.** Within a single session, an
interactive shell enumerated `[iGPU, B70, B70]` (so `1,2` was correct) while the S4U scheduled task
enumerated `[B70, B70, iGPU]` (so `1,2` selected `[B70, iGPU]`). The operator's experience is that
this reshuffles between runs, "DHCP-lease style", and has caused repeated incidents — which is why
the original note was so emphatic.

A second, related trap sits in the tooling: `common/arg.cpp:2811` splits `--tensor-split` on
`[,/]+`, while `tools/llama-bench/llama-bench.cpp:910` splits on `[;/]+` and treats comma as its
*outer config* delimiter. So `-ts 1,1` means "dual-split" to llama-server and **"two separate
single-card runs"** to llama-bench — silently. Copy-pasting production's flags into the bench
measures something else entirely.

## Decision

**Device selection is by device *type*, never by index. The visibility filter is removed, not
corrected.**

With no `GGML_VK_VISIBLE_DEVICES` set, `ggml/src/ggml-vulkan/ggml-vulkan.cpp:7479-7495` selects
*"all dedicated GPUs"* by `deviceType`, and llama.cpp drops the iGPU at model placement. No index
is involved anywhere, so a reshuffle cannot break it. Verified on a spare port before production:
`using device Vulkan1 (Arc Pro B70)` **and** `using device Vulkan2 (Arc Pro B70)`.

**`-dev`/`--device` is not an acceptable substitute** — its `VulkanN` names are positional too, so
"fixing" the filter that way would have been the same bug wearing a different hat.

**Placement is asserted, never assumed.** Every cell that claims a multi-card placement captures
the per-device `model buffer size` lines and verifies both cards are non-zero before any timing is
trusted; `campaign/ff-probes/ff_census.py` records the enumeration order *that run* saw.

## Consequences

- **Production regained a card.** Per-card footprint went from 30108 MiB on one card to
  14.88 / 15.80 GB across two; headroom from ~1.5 GB to ~16 GB per card; card temperatures
  equalised (56/44 °C → 54/54 °C, then 0 °C spread), which is the independent tell that both cards
  are working.
- **The thermal rule is now unenforceable as written.** *"The hot card gets the lighter model"*
  (ROTATION-PROGRAM constitution) is index-targeted and therefore equally unreliable. Symmetric
  `-ts 1,1` makes ordering harmless, but unequal splits or `--main-gpu` need identity-based
  placement. b70tools' PCI-BDF resolution is the right authority; this is registered as open.
- **Any per-model card pinning (one seat per card) inherits the problem.** The four-venue lap had
  to verify-and-retry: launch, read which BDF actually took the weights, relaunch on collision.
- **Two upstream contributions fall out.** (1) `llama-bench` should accept `[,/]+` for `-ts` like
  the rest of llama.cpp, or error on an under-specified split — the divergence is silent and costs
  a full lap. (2) `llama-bench` has no `-np`, so any batching flag validated only there is not
  validated for a server running slots; that gap caused a bad production promotion the same day.

## Alternatives considered

- **Correct the indices and keep the filter.** Rejected: the indices are not stable, so any correct
  value is correct only for the process that measured it.
- **Switch to `-dev Vulkan1,Vulkan2`.** Rejected: positional, same failure.
- **Pin by PCI BDF.** Not available — llama.cpp exposes no BDF-based selector. Left as the
  upstream-shaped gap for identity-based placement.

## Addendum 2026-09-03 — The assertion caught a live instance: index 2 is the iGPU

This ADR removed the production device filter and said the load report decides. Single-card side
seats cannot avoid `GGML_VK_VISIBLE_DEVICES` (type selection has no lever for "one of the two B70s"),
so `docs/adr#0045` declared every single-card model twice — `<m>-vk1` and `<m>-vk2` — and asserted
each load from the report. The first live proof (window `rot-side-20260903-B`) is the case the rule
was written for:

- With `GGML_VK_VISIBLE_DEVICES=2` the side server's own `-lv 5` log (`hearth/var/swap-logs/
  phi4-vk2.log`) read `Vulkan0 : Intel(R) Graphics (74286 MiB, 103494 MiB free)` and `using device
  Vulkan0 (Intel(R) Graphics)`: **index 2 is the iGPU on this driver**, indices 0 and 1 are the B70s.
  The server was READY and answered a one-token canary in 10.3 s — phi-4 runs fine from shared memory.
  **Liveness and a canary are not placement**; `assert_placement` returned `iGPU holds weights` and
  the lifecycle unloaded it.
- With `GGML_VK_VISIBLE_DEVICES=1` the same model landed on BDF `0000:04:00.0`: one B70 with
  weights, iGPU clean, and the per-BDF commit delta corroborated it (+9.729 GB on that card, 0.0 on
  `0000:09:00.0`). Two independent signals, neither an index.
- The sibling pair is therefore renamed **`<m>-vk0` / `<m>-vk1` (env 0 / 1)** across the yaml, the
  rung, the scheduler suffixes and the sibling map (commit `92f3cd6`). The name says which index was
  tried; the report says where the weights went; the two are never conflated. A running llama-swap
  keeps the old entries until the next ArcServe restart.
- One more shape the assertion had to learn: an entry that was **already resident** before the call
  shows no commit delta. A retry saw `0 BDF(s) rose >= 1.0 GB` and unloaded a correct placement
  (the earlier call had executed although its MCP client session had expired). The lifecycle now
  skips the delta corroboration for a pre-resident entry and says so in the receipt
  (`already_resident: true`); the log report alone decides for it.
- BDF-pinned placement remains the upstream-shaped gap: no llama.cpp selector takes a BDF, so the
  retry lever is still an index candidate list, and the assertion is still what makes it safe.
