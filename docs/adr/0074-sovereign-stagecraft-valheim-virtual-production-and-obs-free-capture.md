# ADR-0074 — Sovereign StageCraft: Valheim Virtual Production Volume, Diegetic Audio, and OBS-Free Steam Capture

**Status:** Accepted and implemented 2026-10-23 (`AMUX-169`..`AMUX-173`, `20/20` Pass-6 gates and `108/108` cumulative gates green; `0.0 MiB` B70 GPU VRAM contention).

## Context

When storytelling on `yt-studio` for a podcast or creating a technical visualization, generating synthetic backgrounds via ComfyUI diffusion pipelines consumes heavy GPU compute, produces flat 2D plates without real 3D parallax or natural ambient audio, and cannot react in real time to out-of-world narrative feedback. Simultaneously, traditional OBS screen-capture workflows introduce encoding overhead and require manual scene switching. Meanwhile, the OMEN dual Intel Arc Pro B70 GPUs are frequently reserved for long-running 27B/MoE inference benchmarks and must experience `0.0 MiB` VRAM contention from visualization control planes.

## Decision

1. **Valheim as a Real-Time 3D "Sovereign StageCraft" Virtual Production Volume:** Instead of generating diffusion backgrounds via ComfyUI on local GPUs, we use the Valheim Unity engine (`ComfyEra17.db` + `ComfyNetworkSense.dll`) as an interactive, multi-layered 3D virtual production volume:
   - **Layer 1 (`15–35 m` Foreground):** Natural cultivated meadow terraces (`#00FF00`), paved geodesic footpaths (`#0000FF`), and `y = 30.0 m` submerged anomaly fjord water-table reflections.
   - **Layer 2 (`40–140 m` Midground):** `65x65` `_TerrainCompiler` ridge relief (`[-12m, +16m]`), `38°` natural cliff rock exposure, and cross-lit grazing sun (`16.2°–18.5°`, `3.84x` relief shadow contrast) with valley mist pooling.
   - **Layer 3 (`150–500 m+` Horizon):** Volumetric biome skybox, scattering clouds, sun disc, and distant mountain silhouettes.
2. **Diegetic Game-Engine Audio (`AudioMan` + `EnvMan`):** Harvest the game engine's native 3D spatial audio—wind shear across ridges, biome ambience, distant thunder, rain particles, and `y = 30.0 m` fjord shoreline water lapping—directly from `EnvMan` weather and `AudioMan` without external Foley synthesis.
3. **Out-of-World MCP Feedback -> `< 21 ms` In-World Atmosphere Hot-Swap (`0.0 MiB` B70 GPU VRAM):** Out-of-world `yt-studio` podcast story beats and telemetry events are routed through the Intel NPU (`:18097` `/dev/accel/accel0`, `0.0 MiB` B70 VRAM) via `compile_yt_studio_story_beat_cue()` (`20.6 ms < 50.0 ms` SLA) into `ComfyNetworkSense.dll` (`ApplyCinemagraphAtmosphere` + `TeleportTo(stance)` + `FlushPendingSectorPokes()`), hot-swapping `EnvMan.m_debugTime`, `m_debugEnv`, `GameCamera.m_fov = 46°`, `Hud.m_userHidden = true` (`CleanPlateHudOff`), and local player mesh culling (`HideLocalPlayer`) on the Unity main thread with zero console commands.
4. **OBS-Free Steam / PipeWire + VA-API Hardware Capture Harness:** Drive recordings without OBS via `build_obs_free_stagecraft_capture_spec()` (`sovereign_core/spatial/live_dataset_ingestor.py`), combining Steam Game Recording / PipeWire window-ID DMA-BUF capture (`valheim.x86_64`) + hardware VA-API HEVC (`hevc_vaapi`, `60 FPS`, `qp=18`) + PipeWire node audio sink (`valheim.x86_64.audio`, `48 kHz` 24-bit stereo) + `50B` UDP camera pilot sidecar (`:2459`).

## Consequences

- All `45/45` `_TerrainCompiler` ZDOs (`9` per workspace across `W1..W5`, `0` artificial piece ZDOs) are spliced into `/home/derek/work/xtech-search10-rnd-20261015/runs/gis-world/ComfyEra17.{db,fwl}` (`3,513,045 B`, `45/45` GZip `TCData` SHA-256 roundtrip verified, `0` duplicates).
- `ComfyNetworkSense.dll` (`Release` build, `0` warnings, `0` errors, SHA-256 `89c1c94d0988c7da...`) executes main-thread safe `Heightmap.Poke(false)` draining (`<= 4` zones/frame) and clean-plate camera piloting.
- B70 GPU benchmarks run at `100%` isolation (`0.0 MiB` VRAM contention, `0` HTTP requests to B70 ports) while podcast storytelling and 3D cinematography operate through NPU `:18097` and the StageCraft volume.
