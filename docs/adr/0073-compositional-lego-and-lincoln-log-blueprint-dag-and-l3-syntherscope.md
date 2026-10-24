# ADR-0073 — Compositional Lego Bricks & Lincoln Logs Blueprint DAG and L3 Landscape #5 (`landscape.syntherscope.valheim_complexity_cinemagraph`)

**Status:** Accepted and implemented 2026-10-23 (`AMUX-140`..`AMUX-168`, `88/88` Passes 1–5 gates green; `verify_blueprint_corpus.py --all` `6/6` green).

## Context

Across 12 constellations, 89 repositories, 9,784 commits, and 18.4M+ line changes, the primary tax on solo-operator and multi-agent collaboration is structural amnesia: forgetting where a hardened binary codec or memory-splicing substrate lives relative to a domain sub-assembly, a runnable studio, or a theater landscape. Furthermore, when visualizing multi-dimensional lab complexity inside Valheim (`ComfyEra17.db`), earlier iterations spawned artificial structural piece ZDOs (`wood_wall`, `piece_chest_wood`) that created visual clutter and obscured the game engine's native terrain shader, grazing sunlight, weather, and ocean reflections.

Over Passes 1–5 (`AMUX-140`..`AMUX-168`), we executed 8 controlled experiments across 5 workspaces (`W1` YouTube Studio 60-Cell Sympathy, `W2` JEPA v3 160 Counterfactuals, `W3` 56-Blueprint Compositional Atlas, `W4` 27B Lever Ledger 759 Families, `W5` MechSuit NPU/eBPF Telemetry) to test whether pure natural terrain features (`_TerrainCompiler` height displacements and natural paint channels alone, with `0` artificial piece ZDOs) combined with grazing-angle sun (`16.2°–18.5°`), atmospheric mist, and slow cinemagraphic camera drift could encode system complexity with higher perceptual separability and zero visual clutter.

## Decision

1. **Five-Class Compositional Ontology (`56` Blueprints, `0` Cycles, `99/99` SHA-256 Bindings):** Formalize the lab's reusable building blocks in `sovereign_core/blueprints/registry.yaml` across five compositional ranks:
   - `L0_BRICK` (`20` Lego Bricks): Atomic functional primitives (`brick.wire.motion_50b`, `brick.spatial.tcdata_gzip_codec`, `brick.jepa.state_shaper_ast`, etc.) with zero import-time side effects.
   - `L0_LINCOLN_LOG` (`8` Lincoln Log Substrates): Sub-second iteration beams (`lincoln.memsplice.zdo_ram_splicer` `10,000x`, `lincoln.hotpatch.v37_db_sector` `3,169x`, `lincoln.filemap.gpu_instance_80b` `1,000x`, `lincoln.routing.jepa_7ms_ranker` `2,000x`) averaging `2,582.1x` cycle-time compression over traditional restart/rebuild loops.
   - `L1_WALL` (`11` Walls): Domain sub-assemblies, including `wall.spatial.valheim_perceptual_palette` (`sovereign_core/spatial/valheim_perceptual_palette.py`).
   - `L2_HOUSE` (`12` Houses): Complete runnable services, client mods (`house.mod.comfy_network_sense`), and studio workbenches, including `house.ui.valheim_cinemagraph_syntherscope` (`valheim_syntherscope.html`).
   - `L3_LANDSCAPE` (`5` Landscapes): Multi-system operational theaters and executive vision capabilities, adding **L3 Landscape #5 (`landscape.syntherscope.valheim_complexity_cinemagraph`)**.
2. **Pure Natural Terrain Encoding (`0` Artificial Piece ZDOs):** `landscape.syntherscope.valheim_complexity_cinemagraph` encodes complexity strictly through Valheim's native `_TerrainCompiler` `65x65` vertex height deltas (`smooth_delta_h` in `[-12.0, +16.0] m`), natural surface paint (`64x64` RGBA: `#FF0000` Dirt/Stone slope transition, `#00FF00` Cultivated Meadow, `#0000FF` Paved Geodesic Trail, `#000000` undisturbed biome grass), natural `y = 30.0 m` water-table intersection fjords (`smooth_delta_h = -5.5 m` at `34.0 m` base terrain), and natural `38°` shader cliff-rock exposure (`>= 0.78 m/vertex` gradient). Zero structural piece ZDOs (`wood_wall = 0`, `piece_chest_wood = 0`) are emitted in pure-terrain mode.
3. **Cross-Lit Grazing Sun & Cinemagraphic Camera Law:** Complexity relief is illuminated by cross-lit grazing sun (`m_debugTime = 0.30` Dawn `16.2°` or `0.68` Golden Hour `18.5°`, cross-lighting azimuth `90° +/- 15°`), yielding `3.84x` ridge relief contrast (`3.40x–4.60x` across `W1..W5`) vs noon (`65.0°`). Camera trajectories run on `brick.wire.motion_50b` at `60 Hz` with `0.35 m/s` horizon-locked drift and `46°` cinematic FOV.
4. **4-Way Structural Decision Gate (`REUSE | UPDATE | MINT | REASSESS`):** Every blueprint carries a `human_proofing_protocol` (`33` Auto-Golden Locked requiring `0` human minutes; `23` bounded `2–5 min` human touch-and-feel surfaces routed through `/executive_hub.html` `SESSION-01`..`SESSION-07`).

## Consequences

- `python3 tools/verify_blueprint_corpus.py --all` enforces `100%` Tier-1 file coverage (`28/28`), `99/99` live SHA-256 code-binding checks, `0` DAG cycles across all `56` blueprints, and `16/16` (`100%`) 4-way Decision Engine accuracy (`< 2 ms` latency).
- Executive Hub (`/executive_hub.html`), Blueprint Atlas (`/blueprint_atlas.html`), Valheim StageCraft Studio (`/valheim_syntherscope.html`), and the Global Sovereign Command Bar (`sovereign-nav-bar.js`, `Alt+1`..`Alt+8`) share a single live registry source of truth.
