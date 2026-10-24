# ADR-0075: Dual-B70 Nemotron-74B and the 3-Tier Holistic Fleet (`bolt74-am4`)

- **Status:** Proposed (2026-10-23)
- **Date:** 2026-10-23
- **Deciders:** Derek, Claude / Antigravity
- **Related:** ADR-0034, ADR-0050, ADR-0060, ADR-0061, ADR-0062

## Context

On `omen-linux`, the two Intel Arc Pro B70 cards (`03:00.0` + `08:00.0`, 64 GB total GDDR6) have
historically served either one 30B MoE + one 27B dense seat (`day`, `two-dense`) or two 27B dense
replicas (`three-dense` alongside `am4-vllm` TP=2 on AM4).

Two hardware and systems developments change the capability frontier of the fleet:

1. **Tier-1 Heavy Reasoner (`omen-nemotron-74b` on Dual B70, `:18080`):**
   `manki-llama-bolt74b-v1.5-Q4_K_M.gguf` (Nemotron-3.5-Bolt-74B-A9B hybrid Mamba-2 + GQA + MoE,
   36.75 GiB weights) fits comfortably across both B70 cards (`--split-mode layer --tensor-split 50,50`)
   with a 32,768-token context window (`q8_0` KV cache) and ~12.4 GB VRAM headroom per card. With the
   native SYCL `_mt_reorder` multi-token MoE routing kernel (`build-sycl-mt-opt`), multi-token prompt
   prefill increases from 54.4 tok/s to **419.4 tok/s** (+671% speedup; `pp128` 250.3 tok/s, `pp512`
   419.4 tok/s) while maintaining **24.6 tok/s** single-stream generation (`tg32`).
2. **Tier-2 Parallel 27B Dense Worker (`am4-vllm` on AM4, `10.44.0.2:18082`):**
   `Qwen3.5-27B-AWQ-4bit` (`dense-tp2` across RTX 5070 + RTX 4070 Ti SUPER over the direct LAN cable,
   49,152 context, ~34 tok/s decode, ~1,200 tok/s prefill) remains continuously online without
   contending for OMEN PCIe or XPU resources.
3. **Tier-0 NPU Context Packager & Router (`:18097` on Intel AI Boost NPU + AST Slicer):**
   At 32,768 context and ~250–419 tok/s prefill on `omen-nemotron-74b`, shipping raw multi-file
   repositories wastes both context budget and prefill latency, and models copying `0042| ` line-number
   prefixes from `numbered=True` packets suffer avoidable `missing` quote rejections. Combining
   resident `:18097` NPU regime classification (`/classify_regime`, 50 ms budget) and candidate block
   ranking (`/rank_candidates`, 120 ms budget) with deterministic Python AST slicing (including
   method-level decomposition of monolithic `>60`-line classes and module-level `const_table`
   retention) reduces prompt context tokens by 50–65% while preserving exact 1-based line numbers.

## Decision

1. **Register `omen-nemotron-74b` and configuration `bolt74-am4`:**
   - Declare `omen-nemotron-74b` in `host/omen-linux/hearth-production/backends-linux.toml` on
     `http://127.0.0.1:18080` (`models = ["nemotron-3.5-bolt-74b-a9b"]`, `engine = "llama.cpp"`,
     `devices = [0, 1]`, `tensor_split = [0.5, 0.5]`, `context_tokens = 32768`, `max_tokens = 16384`,
     `deliberate_max_tokens = 24576`, `parallel_slots = 1`, `tags = ["dense", "quality", "agent", "code", "reasoning"]`).
   - Add `[configuration.bolt74-am4]` to `host/omen-linux/config/lab-configurations.toml` (and
     `host/lab-configurations.toml`) with `omen-nemotron-74b`, `am4-vllm`, `fx99-vllm`, and
     `omen-perception` marked `live`, and `omen-vllm`, `omen-dense-27b`, `omen-dense-27b-b`, and the
     AM4 tool pair marked `absent`. Mark `omen-nemotron-74b` `absent` in all other configurations.
   - Update `[lane.deep]` in `host/omen-linux/hearth-production/local-work-routes-linux.toml` so
     `backends` and `code_backends` are `["omen-nemotron-74b", "omen-dense-27b", "omen-dense-27b-b", "am4-vllm"]`.
   - Update `hearth/scheduler/families.py` and `hearth/toolsurface/backends.py` so family
     recommendations and tag-routed calls fall back cleanly to the active configuration's live
     `default_fallback` backends (`omen-nemotron-74b` and `am4-vllm`) when `omen-vllm` and
     `omen-dense-27b` are absent under `bolt74-am4`.

2. **Enforce `serving-profile.v2` Qualification for Pooled Delivery Admission:**
   - Under ADR-0061 and ADR-0062, pooled delivery admission in `[lane.deep]` is bound to the exact
     `serving-profile.v2` SHA-256 hash (`selected_model`, `engine`, `devices`, `tensor_split`,
     `context_tokens`, `parallel_slots`, `max_tokens`, `deliberate_max_tokens`, `kv_cache_dtype`).
   - Non-delivery code work (`unified_diff`, `whole_file`) routes immediately across
     `code_backends = ["omen-nemotron-74b", "omen-dense-27b", "omen-dense-27b-b", "am4-vllm"]` via
     `lane_code_backend` (skipping backends marked `absent` in the active lab configuration).
   - Delivery briefs (`brief.v2`) route to `omen-nemotron-74b` in the pooled deep lane once its
     `serving-profile.v2` hash has accumulated `min_accepted_briefs >= 2` in
     `knowledge/delivery_procedures.json` (earned via pinned qualification runs); until then, pooled
     auto-delivery routes to qualified `am4-vllm` while explicit pins (`procedure="carry" | "one_call" | "jepa_ast_sliced"`)
     remain available for qualification.

3. **Integrate Tier-0 NPU+AST Context Slicing (`jepa_ast_sliced`) and Line-Number Quote Hygiene:**
   - `hearth/delivery/sourcemap.py` provides `render_jepa_ast_sliced_for_model()`, which queries
     `:18097` `/classify_regime` and `/rank_candidates` (falling back deterministically to AST +
     subtoken overlap if `:18097` is unreachable), splits monolithic classes (`>60` lines) into
     `class_header` + individual `method` spans, retains module-level `const_table` assignments, and
     renders kept spans with exact 1-based line numbers.
   - `sourcemap.locate()` strips leading `0042| ` line-number prefixes copied from `numbered=True`
     packets and records a `normalized` repair rather than failing as `missing`.
   - Callers opt in via `procedure="jepa_ast_sliced"`, `brief={"jepa_pack": true}`, or
     `HEARTH_JEPA_PACK=1|auto|true`.

4. **One-Command Profile Switch and Rollback (`omen-profile`):**
   - Host lifecycle between Dual-B70 Bolt74B and Dual-27B / MoE+27B is controlled by
     `~/bin/omen-profile status | switch bolt74 | switch three-dense | switch two-dense`, which manages
     the mutual-exclusion systemd units (`omen-llama-bolt74b.service` vs `omen-vllm.service` /
     `omen-dense-27b*.service`), writes `~/.config/hearth/lab-configuration`, and verifies backend
     readiness.

## Consequences

- **Positive:** OMEN gains a 74B-class hybrid Mamba-2/MoE reasoning and coding tier across Dual B70
  at 419 tok/s prefill and 24.6 tok/s decode, paired with a simultaneous 27B TP=2 worker on AM4 and
  sub-150 ms NPU+AST context slicing on `:18097`.
- **Bounded Risk:** Rollback to `three-dense` or `two-dense` is a single deterministic command
  (`~/bin/omen-profile switch three-dense`), and all existing configurations explicitly mark
  `omen-nemotron-74b` as `absent`.
