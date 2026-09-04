# 0040 — Serving lifecycle is adopted, not built

**Status:** Accepted (2026-08-28) — probe-gated ratification; every gate passed same day — **Addendum 2026-09-03:** Phase 2 landed 2026-09-03: production `omen-arc` runs under llama-swap (`docs/adr#0045`).

**Companion to:** `docs/adr#0039` (the rungs this lifecycle serves),
`docs/adr#0008` (advisory-first — unchanged by this ADR),
`docs/adr#0031` (context budgets are arithmetic, not vibes),
`docs/adr#0034` (the dual-B70 rung and its boot ceremony)

## Context

The rotation program's first draft proposed building a production rotation
controller (`rotation-control.ps1`, ~1,000+ lines promoted from the campaign's
`server-control.ps1`): process lifecycle, health gating, queueing, TTL,
mutual exclusion, topology launch. Derek ordered a Phase −1 framework bake-off
before construction: determine what existing inference infrastructure already
solves, and delete the commodity work.

The bake-off (llama.cpp in-tree router read at our pin, llama-swap, SGLang
Model Gateway, Ray Serve, NVIDIA Dynamo as architectural reference, plus a
landscape sweep) found that **~60–70% of the proposed controller is
commodity** — and that the parts worth keeping are precisely the parts no
open-source system ships: bytes-per-card VRAM admission, Intel Arc telemetry,
card-aware eviction, KV scheduling across swaps, and the belief/epoch layer.

Seven probes were defined as the ratification gate and run 2026-08-28 inside
a supervised maintenance window (production stopped and restored under the
campaign sentinel protocol, restoration proved by a real one-token
completion). All passed:

- **P1 — device identity.** Under `GGML_VK_VISIBLE_DEVICES=1,2` the B70s
  enumerate as exactly `Vulkan0`/`Vulkan1`. (The per-process "free" figure is
  budget-view only; cross-process VRAM stays invisible on Windows Vulkan.)
- **P2 — KV save.** A 29,320-token slot on Qwen3-30B-A3B (card 1, `-fa on`)
  saved 2.68 GB in **1.74 s** (~1.66 GB/s). Vulkan readback is a non-issue.
- **P3 — KV restore across a full restart.** Server killed and relaunched
  (6.1 s to healthy), slot restored in **1.19 s**, and the identical 29K
  prompt then re-evaluated **one token** (0.84 s wall vs 102.8 s cold
  prefill) with byte-identical greedy output. **Save+restore ≈ 3 s round
  trip against ~100 s re-prefill: KV-preserving rotation is real on this
  stack.**
- **P4 — cross-model negative control.** Restoring the 30B state file into
  the 27B server was **refused, HTTP 400** (structural guards: layer/KV
  shape). Loud failure confirmed — but the file format carries no model
  identity, so the `{model}.{slot}.{prompt_hash}` naming manifest stays
  mandatory as the real guard.
- **P5 — heterogeneous co-residency via llama-swap.** llama-swap v251
  (Windows binary) with per-model `env:` ran the proven command lines
  byte-for-byte: 30B@card1 and 27B@card2 simultaneously resident, and under
  **concurrent** fire held 99.2 tok/s (95% of solo) and 21.6 tok/s (100% of
  solo, matching the campaign's 23.0 replica-per-card row).
- **P6 — Intel telemetry.** llama-swap `/metrics` reports host memory only;
  no per-GPU VRAM for Arc. **Arc telemetry is confirmed BUILD.**
- **P7 — swap semantics.** With a 2048-token `ignore_eos` generation
  mid-flight, a swap request for the other model **drained** — the stream
  completed intact (all 2048 tokens) and the swap waited (~27 s), then
  loaded. Swaps never cut in-flight work; swap latency budgets must include
  drain time bounded by active slots' `max_tokens`.

## Decision

1. **llama-swap v251 owns model process lifecycle** on OMEN's B70s: spawn,
   health gating, queueing behind loads, TTL/persistent residency, groups as
   *enforced* mutual exclusion (retiring the unenforced omen-arc/omen-arc-oss
   hazard), per-model `env:` for card pinning. One entry per proven command
   line; the production `resident-default` entry is today's `serve-arc.cmd`
   line verbatim. ArcServeBoot re-points at llama-swap in Phase 2; the
   maintenance sentinel protocol is honored unchanged.
2. **The in-tree `llama-server` router is plan B**, already present in our
   b10581 pin. It loses on per-model env (impossible), card-blind LRU, and
   pinning (commented out upstream). Its preset-INI cascade is the borrow
   source for topology config. The router-CLI-overrides-preset trap is
   recorded: per-model flags live only in config files, never on a router
   command line.
3. **KV hydration is a first-class swap step**: save before unload, restore
   after ready, via `--slot-save-path` + `/slots/{id}?action=save|restore`
   through llama-swap's `/upstream/:model` passthrough. File naming
   `{model_id}.{slot}.{prompt_hash}`; a cross-model restore attempt is a
   bug even though the server 400s the common case.
4. **HEARTH builds only the verified absences**: bytes-per-card VRAM
   admission (commit floor + thermal + WHEA gates ported from
   `server-control.ps1`, run before *every* load), Arc VRAM telemetry,
   card-aware eviction advice, restart/backoff policy, and the entire
   belief/quality/epoch-scheduling layer (JS7b has no open-source peer;
   Dynamo's VirtualConnector shape is the advisory→executor contract).
5. **In-server prefix affinity before external routing**: `-sps`,
   `--cache-ram`, `--cache-reuse` tuning (Phase 1 R2c) is the first lever on
   the 306× penalty; an external prefix router only pays with multiple
   instances of the same model, which this topology does not have.

## Consequences

- `rotation-control.ps1` as designed is dead; the Phase 2 build shrinks to a
  thin admission/telemetry/hydration layer (~200–300 lines) plus llama-swap
  YAML.
- The P3 result changes the scheduler's cost model: with ~3 s KV round trips,
  **deep-context state survives rotation**, so JS7b's setup-time term splits
  into weight-load (8–40 s, measured) + KV-hydrate (~1–3 s/slot at 26K,
  measured) instead of a prohibitive re-prefill (~100 s). Continuous rotation
  is back on the table for KV-manifested callers; epochs remain the shape for
  Flash-Next (R0: its 60.4 GB commit still does not fit beside production).
- llama-swap becomes a tracked dependency (`E:\work\llama-swap-v251`,
  v251, SHA-verifiable release binary); upgrades are deliberate, like
  `docs/adr#0032` holds for Ollama.
- Probe receipts live in `ROTATION-PROGRAM.html`'s phase board and the
  session plan dossier; the probe scripts and configs remain in
  `E:\work\llama-swap-v251\` (p5/p7 YAML) for re-verification after any
  llama-swap or llama.cpp upgrade.

## Addendum 2026-09-03 — Phase 2 landed: production runs under the adopted lifecycle

The thin substrate this ADR bought instead of building is now the production shape, not a probe
shape (`docs/adr#0045`, plan P6/P13, commit `26a1d66`):

- **Production `omen-arc` runs under llama-swap since 2026-09-03 12:45:02–12:45:25.** `llama-swap.exe`
  on `127.0.0.1:8081` owns the process lifecycle; the production entry keeps `--host 127.0.0.1 --port
  8082` behind a per-model `proxy: http://127.0.0.1:8082` in its own `persistent` group, so every
  `:8082` consumer (the door's `omen-arc` rung, the fx99 keep-alive, `ff_ratecheck.py`, the ETW
  readers) is byte-identical. `ArcServeBoot` still runs `fleet/arcserve/serve-arc.cmd`; that file is
  now the llama-swap launcher, `serve-arc-direct.cmd` the rollback (exercised twice, 36 s each, by
  the two aborted ceremony attempts), `restart-arc.cmd` tears down the whole tree.
- **Side seats are door tools, inside ledgered windows** (`hearth/toolsurface/rotation.py`:
  `rotation_window` → `rotation_load` → … → `rotation_unload` → close; ADR-0044 exclusion spans).
  The first proof through the door (window `rot-side-20260903-B`) loaded `phi4-vk1` on BDF
  `0000:04:00.0` in 3.344 s (warm file cache; 20.14 s after another model evicted it) and
  `qwen14b-vk1` in 57.469 s (cold), each asserted from the side server's own `-lv 5` log and a
  per-BDF commit rise (+9.729 / +9.621 GB on one card, 0.0 on the other), with production
  `at_rate` at 107–109 tok/s throughout.
- **The W0 KV finding holds through the door and across a model swap:** slot 0 saved with 1239
  tokens (253,768,028 bytes) → unload → load `qwen14b-vk1` → unload → reload `phi4-vk1` → restore
  in 168.7 ms; the replayed prompt cost `prompt_n=1` against `cache_n=1238`. A restore into the
  other model was refused before any request (the file carries no identity — `hearth/rotation/kv.py`
  manifest, P4 shape). Two defects on the way: `save_slot` must process the prompt before saving
  (the first save captured one canary token), and an entry already resident before the call cannot
  show a commit delta (`hearth/rotation/lifecycle.py` skips that corroboration for it).
- **A running llama-swap keeps the entries it was started with.** The yaml on disk changed the same
  night (`<m>-vk2` → `<m>-vk0`, see `docs/adr#0042` addendum); it takes effect only at the next
  ArcServe restart. Until then a pin on a renamed id gets a fast 404 refusal from `wait_ready`.
- Still true, still load-bearing: the bare `POST /api/models/unload` unloads **everything**,
  production included — the path form only; llama-swap stays on loopback (its admin endpoints are
  unauthenticated).
