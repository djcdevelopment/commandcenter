# 0045 — The scheduler plans a rotating host: OMEN is a stateful Machine, llama-swap owns its lifecycle, and every number comes from a receipt

**Status:** Accepted (2026-09-03) — Derek's decisions in session. Each decision below carries its own
execution state (**LANDED** / **EXECUTED** / **DECIDED, NOT YET EXECUTED** / **IN PROGRESS** / **OPEN**), because
accepting the shape and having cut production over are two different facts and this record refuses
to blur them.

**Companion to:** `docs/adr#0008` (the scheduler advises until the ledgered regret trend earns it
dispatch — intact here), `docs/adr#0027` (a rung with one `model_id` can never clear gate 2),
`docs/adr#0031` (a pin chooses a rung, not a waiver of arithmetic), `docs/adr#0034` (the dual-B70
rung is the door default), `docs/adr#0039` (depth specialists earn pin-only rungs), `docs/adr#0040`
(serving lifecycle is adopted, not built — this is its Phase 2), `docs/adr#0042` (devices are
selected by type, never by index), `docs/adr#0043` (the rung goes cold when idle), `docs/adr#0044`
(a rate is not a scalar; the cutover is a deliberate epoch boundary in its observation log).

## Context

Derek's steer, 2026-09-03: the destination is **scheduling + capacity — the job shop**. The Qwen 3.8
campaign changed the machine: OMEN's 128 GB of fast RAM plus two Arc Pro B70s makes **different
models for different task types, loaded and unloaded on demand**, the viable optimization. Second
steer: **HEARTH is the front door and the manifest of what uses local; mechnet is the workhorse**
(planning loops, build loops, critics, judges, self-learning), neglected since the OMEN board saga.

Three campaigns measured every input a rotation-aware scheduler needs. **None of it was in production
code** — verified against the receipts, not the write-ups, before this record was drafted:

| measured | value | receipt | consumer that did not exist |
|---|---|---|---|
| dio cold load, 30B / 27B | 8.19/8.16 s, 8.24/8.20 s steady; 26.58 / 19.51 s first-in-window | `E:\work\battlemage\rotation-phase1\r2-receipts.jsonl` | OMEN catalog `load_s_steady`, `load_s_first_in_window` |
| KV slot save / restore across restart | 2.68 GB / 1.74 s; 1.19 s, `prompt_n=1` vs 102.8 s re-prefill | `docs/adr#0040` P2/P3 | KV hydration + `{model}.{slot}.{hash}` manifest |
| swap semantics | swaps DRAIN in-flight streams (~27 s) | `docs/adr#0040` P7 | swap latency budget |
| critic quad, 2 per card | 8.3/8.4/11.3/13.3 GB; solo 48.7/45.6/98.3/29.8 tok/s; 4-way 22.5/28.6/52.9/23.9; reload ~5 s | `E:\work\battlemage\rotation-phase1\w1-receipts.jsonl` R6/R5 | side-port model entries |
| co-residency | idle neighbour 0%; inferring neighbour on the same card −8% | `docs/adr#0041` W-B "during" | admission/placement advice |
| task-family verdict (R10) | 27B `line_verbatim` 13/16 vs 30B-A3B 3/16 (see Decision 6 for the tally) | `E:\work\battlemage\rotation-phase1\r10-results.jsonl` | family → model preference |
| depth inversion | 27B 0.687× jobs/hour @512, 2.63× @8K, 5.49× @32K | `docs/adr#0039` | depth override |
| rung health | baseline 106.0 (epoch 2026-08-29T18:22), fail <0.80, warn <0.90; 4 observed levels | `campaign/ff-probes/rate-baselines.json`, `docs/adr#0043`/`#0044` | door-side rung state; watchdog rate spell |
| llama-swap lifecycle | per-model env, groups, path-param unload, `/logs/stream/{model}` | `docs/adr#0040` P5–P7, `E:\work\llama-swap-v251\` | the Phase 2 thin substrate |

The scheduler (`hearth/scheduler/`) already models the problem — setup times, per-card VRAM, a
staging slot, `resident_models` — but it was **hard-wired to AM4** (`hearth/toolsurface/scheduler.py:140-156`
applies `am4-catalog.v1` to a machine named `am4`), and AM4 serves no inference (`docs/adr#0034`:
the B70s left it in the 2026-08-20 rebuild). OMEN, the only stateful host, was invisible to the one
component built to plan around statefulness.

**Live state when the decisions were taken:** production `omen-arc` = pid 20416 on `127.0.0.1:8082`,
the epoch the ETW manifest pinned on 08-30 07:40Z, unbroken; `:8081/:8083/:8084` free; commit
95.8/135.3 GB; keep-alive deep probes since 09-02: 34 of 304 degraded (INC-2026-08-30-A still
recurring — WATCH, DO NOT POKE); tests 1243 → 1410 passing across the build. **Live state when this
record was written (later the same day):** another lane's imagegen session holds the
`omen-b70-pool` tenancy and production `:8082` is **stopped under it** (maintenance sentinel present).
That is why Decision 3 is decided and scripted but not executed — see its marker.

## Decision

**1. Catalog values come only from receipts; a value with no receipt stays `null`; `omen-catalog.v1`
is frozen.** — **LANDED** (`faaec77`).

`hearth/contracts/omen-catalog.v1.schema.json` shares `am4-catalog.v1`'s required keys so
`ModelSpec` loads it unchanged; `cards[]` are keyed by **BDF** (`0000:04:00.0`, `0000:09:00.0`) and
`index` is a solver slot only; every measured field is nullable; every model carries
`receipts: {field: path#selector}`. `knowledge/omen_catalog.json` (gathered 2026-09-03T09:51:09Z)
holds the 30B at 14.52/15.44 GB per card, `expected_gen_tps` 106.0 with its `rate_epoch`,
`load_s_steady` 8.175 (r2 `dio-cold-2/3`) and `load_s_first_in_window` 26.5 (`dio-cold-1`); the 27B at
17.67 GB, decode 23.4, prefill 41.0, loads 8.22 / 19.51; the quad (phi4 8.3, qwen14b 8.4, gptoss20b
11.3, mistral24b 13.3 GB; solo 48.7/45.6/98.3/29.8) from w1 R6 — and for the quad `load_s_*`,
`prefill_tps` and `rate_epoch` are **`null`**, because R6 measured sizes and rates, not loads. Gates
(`commit_min_free_gb` 6.0, `vram_headroom_gb` 0.5, abort 95 °C / resume 80 °C, `shared_growth_abort_gb`
2.0) cite `campaign/qwen38/config/campaign.json`. Out by construction: llama-bench numbers,
Flash-Next, anything gathered over SSH. Constitution / R8: never a llama-bench number beside a
server number.

**2. OMEN is a stateful, rotating `Machine` that the ADVISORY scheduler plans over. `docs/adr#0008`
is intact; a `rotation_plan` is a proposal, never an actuation.** — **LANDED** (`fa657bb` ontology /
solve; `4ec76db` `propose_schedule` appends `omen-inference` + `rotation_plan`,
`hearth/tests/toolsurface/test_scheduler_omen.py`).

`ModelSpec` gains `load_s_steady`, `load_s_first_in_window`, `kv_hydrate_s`, `unload_drain_s_max`,
`exclusive_group`, `receipt`; `setup_s(default, cold=False)` prefers `load_s_first_in_window` when the
machine is cold. `Machine` gains `roles` and `cold`; `Job` gains `task_family`, `prompt_tokens`,
`kv_state_available`. `propose_schedule(..., omen_catalog_path=..., omen_resident=...)` **appends**
`Machine(name="omen-inference", stateful=True, cards=[{index, bdf, vram_gb}], roles=["inference"],
token_cost_weight=0)` — appended, **not** placed in `load_machines` (see Alternatives). Jobs that cannot
fit go to `rotation_plan.blocked` with the numbers rather than collapsing the whole proposal to
INFEASIBLE. `rotation_plan` = `{machine, steps:[{t_s, action: load|kv_restore, model_id, swap_entry,
cards:[bdf], placement, est_s, est_s_first_in_window, evidence, serves:[plan_id]}], blocked, assumptions}`
rides as a **sibling** key of the proposal — never inside `decision_record`, whose schema is closed.
Absent catalog → byte-identical proposals to today's.

**3. llama-swap owns the process lifecycle on OMEN, and production cuts over to it — with the
production entry keeping `:8082` behind a per-model proxy so every consumer stays byte-identical. The
INC-2026-08-30-A epoch ends deliberately and is recorded as a boundary.** — **EXECUTED**
2026-09-03 12:45:02–12:45:25, window `rot-cutover-20260903-1245` (23 s, `assay.passed`), commit
`26a1d66`. Receipt: placement asserted from the server's own `-lv 5` log — 2 B70 `using device`
lines, 0 iGPU, 2 Vulkan model buffers, 0 CPU buffers, 49/49 layers offloaded; api key enforced (bare
request → 401); `ff_ratecheck` PASS as the warm burst; keep-alive resumed inside the window
(12:45:21 ok); epoch boundary stamped 12:45:25, baseline 106.0 preserved. Scripts `cbeaf69`,
`ce32632`, `dfc8479`, `f9409b2`, `a83b395`, `5178f37`. Two earlier attempts (12:35, 12:43) aborted on
ceremony defects — llama-swap `/logs` is a ~10 KB tail, so the `using device` lines had scrolled out
(placement is now read from the server's own `--log-file`); then a `ReadAllText` sharing violation on
that file (now a shared-mode read) — and rolled back to the direct launcher in 36 s each: the
rollback path is proven twice.

Derek's call was **cut over now, not side-port-first**. The one concern was stated once and is
encoded rather than argued: the cutover ends the epoch every INC-2026-08-30-A row was taken in, and the
keep-alive must resume from a *warm* rung (`docs/adr#0043`) — so the ceremony warms immediately
(the `ff_ratecheck` burst right after load is the valid measurement, ADR-0043 rule 1), the boundary is
written into `docs/adr#0044`'s observation log ("Deliberate epoch boundary — cutover to llama-swap"),
and `campaign/ff-probes/rate-baselines.json` carries an `epoch_boundaries` row with `ts: null` until
`cutover.ps1 -Live` fills it — **baseline 106.0 preserved**, never silently re-baselined.

The shape (`fleet/arcserve/llama-swap/omen.yaml`, `c6370b0`): llama-swap v251 listens on
`127.0.0.1:8081`; the production entry `qwen3-30b-a3b` is today's `serve-arc.cmd` line verbatim with
`--host 127.0.0.1 --port 8082` **fixed** and `proxy: http://127.0.0.1:8082`, in its own
`{persistent: true, swap: false, exclusive: false}` group, preloaded by `hooks.on_startup`. So the
door's `omen-arc` rung, the fx99 keep-alive (`warm-arc.ps1`), `ff_ratecheck.py`,
`occupancy.probe_omen_arc_slots`, and the ETW/keep-alive readers **need no change**. The production
restart happens **once**, inside a ledgered window; only the ceremony may drop the maintenance
sentinel or run `ArcServeRestart`; rollback is `serve-arc-direct.cmd` (the pre-cutover launcher,
body byte-identical). Abort criteria: placement not dual, ratecheck FAIL/WARN twice, keep-alive not
resuming within 3 ticks. `cutover.ps1` is dry-run by default and `-Live` refuses unless every
pre-flight item passed in the same invocation.

**Verified live (2026-09-03, later the same day).** The cutover survived a peer lane without an
operator: another lane's imagegen sessions took the B70 pool repeatedly (epochs 3..27), and at 12:59
that lane stopped ArcServe through the new `restart-arc.cmd` — which tears down the whole llama-swap
tree — and its restore path booted production **unattended under llama-swap**. Verified 17:31:
`/running` ready, keep-alive ok, rung state `at_rate` 107.3 tok/s = 101% of the 106.0 baseline. The
HEARTH gateway was restarted by another lane and now mounts `hearth.toolsurface.rotation` +
`hearth.toolsurface.rungstate` (door tools `rotation_status`, `recommend_rung`, `query_rung_state`,
`rotation_window/load/unload/kv_save/kv_restore`; capability `rotation_admin` on operator +
unrestricted). Live door checks passed: `rotation_status`; `query_rung_state` (summary "omen-arc
at_rate …"); `recommend_rung('quote_retrieval', 40000 bytes)` → `qwen38-27b` on `omen-arc-27b` with
the R10 evidence. The first rotation proof (window `rot-side-20260903-A`, `assay.failed`) found
`--slot-save-path E:\work\battlemage\kv` missing — the side servers exited at launch (fixed
`039f68a`). **The second, `rot-side-20260903-B`, PASSED through the door** (opened 2026-09-04T00:33:47Z,
closed `passed` 01:03:54Z; `assay.started`/`assay.passed` in `hearth/var/rotation-windows.jsonl`;
fix-ups `92f3cd6`, suite 1646 passed). Receipt line: `rotation_load phi4-vk1` → entry `phi4-vk1` on
BDF `0000:04:00.0`, 3.344 s wall (warm file cache; 20.14 s on a later reload after the other model
evicted the cache), placement from the server's own `-lv 5` log — 1 B70 with weights, iGPU clean,
9.7 GB — corroborated by +9.729 GB commit on `0000:04:00.0` and 0.0 on `0000:09:00.0`, canary
timings present; `rotation_load qwen14b-vk1` → same BDF (env=1 maps there), 57.469 s wall (cold file
cache), +9.621 GB corroborated; `rotation_kv_save phi4-vk1` slot 0 → `phi4-vk1.0.e9480f7e3f3cf3d6.bin`,
1239 tokens, 253,768,028 bytes; unload phi4 → load qwen14b → unload → reload phi4 →
`rotation_kv_restore`: `n_restored` 1239 in 168.7 ms, replayed prompt `prompt_n=1 cache_n=1238` (vs a
1239-token re-prefill); negative control `rotation_kv_restore` into `qwen14b-vk1` refused before any
HTTP (`CrossModelRestore`); production `qwen3-30b-a3b` on `:8082` stayed listed after every step and
`at_rate` before and after the pour (107.3 / 109.18 / 107.99 tok/s = 101–103% of the 106.0 baseline), with a dip on the three deep probes taken during it — 17:47:06 74.36, 17:52:11 71.14, 17:57:12 73.33 tok/s, `decode_degraded`, prompt_ms 14.8–15.3 vs 10.0 — while the pour's held-out `omen-arc` judge calls hit production beside a decoding side model; back to 107.99 at 18:02:12 with no intervention; the two causes were not separated, and the probes sit inside the ledgered window; keep-alive
unbroken); teardown `/running == [qwen3-30b-a3b]`. The M1 evidence pour ran the same night and opened
gate 2 (`capability_count` 1 → 2 — see Consequences).

**Lessons from the first live proof** (2026-09-03; every defect fixed in `92f3cd6`):

1. **`GGML_VK_VISIBLE_DEVICES=2` is the iGPU on this driver — `docs/adr#0042` caught it live.** The
   side server's own `-lv 5` report read "Vulkan0 : Intel(R) Graphics"; it was READY and answered the
   canary in 10 s — liveness is not placement. Indices 0 and 1 are the B70s, so the single-card
   siblings are now `<m>-vk0` / `<m>-vk1` with env 0 / 1 (yaml, rung, scheduler suffixes, sibling map,
   tests, docs). The running llama-swap keeps the entries it was started with: the `-vk0` entries go
   live at the next ArcServe restart (any lane's); until then a `-vk0` pin gets a fast 404 refusal,
   not a 240 s poll, and env=1 puts every side model on `0000:04:00.0` (the pour ran them sequentially).
2. **The door runs the code it was started with.** The gateway (started 11:42) predated the swap-log
   reader (landed 13:00), so two attempts read "no placement lines" and failed closed on a defect that
   was not in the running tree; restarted 17:39 and 17:50. The earlier note blaming llama-swap's
   `/logs` tail for the side entries was wrong — they already read their own `--log-file`. Lesson: a
   gateway restart is part of landing code the door mounts.
3. **A call whose MCP client session had expired still executed door-side and passed.** The retry
   found phi-4 already resident, saw a 0 GB commit delta, and unloaded a correct placement — the
   lifecycle now skips the delta corroboration for an already-resident entry and the receipt carries
   `already_resident`.
4. **`kv.save_slot` saved whatever the slot last held** (1 canary token, 205 KB) — it now prefills the
   prompt (`cache_prompt`) before saving, and restore verifies with the same prompt.
5. **The bench harness runs in-process and needs the launcher's env** (`OMEN_ARC_TOKEN`) — run it under
   a wrapper that `CALL`s `hearth/var/gateway.cmd`; from Git Bash the cmd chain failed, from PowerShell
   it works.

**Gate-1 mechanism note (`docs/adr#0027`).** Gateway dispatches are bridged with `task_kind` = the tool
name, so door calls never join the `offload-generate` bucket; the second workflow that gate 1 needs had
to be an in-process caller with its own `DispatchIdentity` (`rotation-proof`). The plan's "two door calls
from claude-frontier" could never have closed gate 1.

**4. Placement is asserted from the `-lv 5` load report plus the per-BDF commit delta; the sibling
entries `-vk1`/`-vk0` are the retry lever; an index is never trusted.** — **LANDED** (`b85e32e`,
`2e5935b`, `c6370b0`).

`docs/adr#0042` cost a whole card for days by filtering devices by index. Here every single-card side
model is declared **twice** in `omen.yaml` — `<m>-vk1` (`env` Vulkan index 1) and `<m>-vk0` (index 0) —
in a per-model `{swap: true, exclusive: false}` group; dual entries carry **no env**.
`hearth/rotation/placement.py` parses what the server itself reported (`- VulkanN : <name>`, `using
device VulkanN`, `<dev> model buffer size = N MiB`) and `assert_placement` passes only if exactly
`expect_cards` B70s hold weights, the iGPU holds none, and exactly `expect_cards` BDFs rose ≥ 1 GB in
b70tools' `local_committed`; **unparseable → fail closed**. `hearth/rotation/lifecycle.py`
`load_with_assertion` runs fence → telemetry snapshot → admission → `wait_ready` (health 200 **and** a
1-token completion carrying `timings` — port-open ≠ model-ready) → `model_log` → assert; on mismatch
it unloads and tries the sibling **once**, emitting every step as a receipt row. Admission
(`hearth/rotation/admission.py`) refuses on fit, commit floor, thermal, and on **`None` telemetry**
("unknown" is a refusal, not a pass).

**5. Rung health is three things — baseline epoch + observed rate + acceptance envelope — read
passively from the keep-alive. Liveness never becomes health. No regime names.** — **LANDED**
(`36306b9`, the pure module; `0ae8acf`, the gaps / patrol / watchdog wiring, P7b).

`hearth/health/rungstate.py` yields `at_rate` only from rows with `predicted_n >= 8` (the 32-token
deep probe) inside 720 s; 1-token pings give liveness and `prefill_stall` and nothing else; rows
inside a ledgered window are excluded and named (`excluded_windows`); a rung with no deep sample
inside those 720 s is **`stale`**, never `at_rate`, however many pings answered. Every state carries the note "epoch-scoped reference, not capacity;
restart discriminator not applied; no regime names" — the module classifies nothing beyond
`at_rate | warn | degraded | stalled | stale | unreachable | no_baseline`, per `docs/adr#0044`'s
refusal to name regimes.

**6. Task-family preferences are authored intent with cited evidence, and a recommendation only.** —
**LANDED** (`78350a5`, `hearth/etc/routing-families.toml` = `routing-families.v1`,
`hearth/scheduler/families.py`).

`quote_retrieval` → `qwen38-27b` on `omen-arc-27b` (pin required; `min_prompt_tokens` 4096); the nine
assay families → `qwen3-30b-a3b` on `omen-arc` (`docs/adr#0039` do_not_promote at 512) with a
`depth_override` to the 27B at ≥ 8192 prompt tokens (2.63× @8K, 5.49× @32K); the vision families →
`gcp-gemini` because there is no verified local vision rung. `backend_hint` and `pin_required` are
derived from `backends.toml` at recommendation time, never authored. Nothing here routes, pins, loads
or dispatches.

⚠ **The R10 label is corrected here.** The plan and earlier notes cited the verdict as *"27B verbatim
recall 11/14 @8k, 2/2 @32k vs 30B 3/14, 0/2"*. The receipt
(`E:\work\battlemage\rotation-phase1\r10-results.jsonl`, 32 rows) has **16 rows per model = per
document (an 8k pack and a 32k pack) one full-prompt row + seven cached-prefix depth-sweep rows**. The
"14" is the **fourteen sweep rows across both documents**; the "2" is the **two full-prompt rows**. The
`@8k` / `@32k` labels were misassigned. The true tally, `line_verbatim`:

| model | 8k document (`prompt_n` 9681 / 9565) | 32k document (`prompt_n` 27548 / 26634) | sweep rows | full-prompt rows | overall |
|---|---|---|---|---|---|
| Qwen3.8-27B | full 1/1, sweep **7/7** | full 1/1, sweep 4/7 | **11/14** | **2/2** | **13/16** |
| Qwen3-30B-A3B | full 0/1, sweep 2/7 | full 0/1, sweep 1/7 | 3/14 | 0/2 | 3/16 |

The verdict's direction is unchanged — the dense 27B recalls the line where the MoE paraphrases — and
`routing-families.toml` carries the same correction in its `receipt_note`. What the receipt does
**not** support is any claim that the 27B is perfect at 32k: it dropped three of seven sweep rows
there.

**7. Rotation reads the GPU tenancy fence; it cannot claim it.** — **OPEN**.

`GpuTenancyStore` (`hearth/execution/coordination.py:261-263`) hard-codes the owner literal
`'imagegen'`. This build only **reads** the fence — `active_image_session("omen-b70-pool")` non-None →
refuse to load (`hearth/rotation/lifecycle.py` `default_fence`, `hearth/toolsurface/rotation.py`) —
and never acquires tenancy under its own name. Parameterizing the owner so a rotation load can hold
the pool as a peer tenant belongs to the imagegen lane, whose file it is; until then rotation and
imagegen are mutually exclusive on the pool **by read-only convention**, which is exactly the state
that has Decision 3 blocked as this record is written. Registered in `DECISIONS-PENDING.md`.

## Consequences

- **`omen-swap` is the gate-2 unlock.** `hearth/etc/backends.toml` declares `omen-swap` on
  `http://127.0.0.1:8081`, `tags = []` (pin-only), `lifecycle = "llama-swap"`, `context_bytes = 14336`
  (`docs/adr#0031` arithmetic: MIN over members, `-c 4096 × -np 1 × 3.5 B`), and **nine** `model_id`s
  (`phi4-vk1`/`-vk0`, `qwen14b-vk1`/`-vk0`, `gptoss20b-vk1`/`-vk0`, `mistral24b-vk1`/`-vk0`,
  `qwen38-27b-dual` — the `-vk0` names replaced `-vk2` in `92f3cd6`, see the lessons above).
  `docs/adr#0027` found every rung declared exactly one model, so `model_id` and `backend` were the
  same fact and gate 2 could never vary; one endpoint serving several `model_id`s is the first rung
  that can. The evidence pour that turns this into `capability_count` 2 (plan M1) is **DONE 2026-09-03**:
  doc/ADR bench (`e44b726`) arms `omen-swap:phi4-vk1` + `omen-swap:qwen14b-vk1`, tasks
  `adr-0042-vs-launcher` + `adr-0041-claims-vs-receipts`, held-out judges `gcp-gemini:gemini-3.5-flash`
  + `omen-arc:qwen3-30b-a3b`; phi4 mean 92.5 (2/2 cells, ~26.8 s), qwen14b mean 88.25 (2/2, ~29.3 s);
  datasets `hearth/var/experiments/doc-adr-bench-20260904T005139Z-sweep` + `-20260904T005728Z-sweep`.
  `knowledge/capabilities.json` `capability_count` 1 → 2 (`capability:task_kind=offload-generate|backend=omen-swap`,
  qualified resources (omen, phi4-vk1) + (omen, qwen14b-vk1), confidence medium, workflows
  `wf-hearth-offload-experiment-doc-adr-bench` + `wf-hearth-offload-rotation-proof`, watermark
  `2026-09-04T01:02:55Z`). Residual: the single MIN budget refused 5 of the 8 tasks for a pin although
  the chosen members could carry them — per-member context budgets are registered in `DECISIONS-PENDING.md`.
- **Windows are ledgered before the first GPU touch.** `hearth/rotation/windows.py` emits
  schema-valid workflow events (`assay.started`, `assay.passed|failed`,
  `workflow_id = "wf-rotation-side-port"`, `run_id = <window name>`) and appends
  `hearth/var/rotation-windows.jsonl`; `rung_state(..., windows=...)` excludes rows inside a window
  and names them, the way the 04:29 window was excluded by hand. ⚠ `campaign/lz-probes/etw11_recurrence.py`
  is the other intended reader and does **not** consume the jsonl yet — until it does, a proof window
  contaminates the recurrence count unless excluded manually. The cutover ceremony itself runs inside
  such a window (`rot-cutover-*`).
- **The rotation tools exist and are inert until the port answers.** `hearth/toolsurface/rotation.py`
  (`a53fe8b`): `rotation_status`, `recommend_rung`, `rotation_window`, `rotation_load`,
  `rotation_unload`, `rotation_kv_save`, `rotation_kv_restore`. `rotation_load` refuses under an active
  image session, without an open window, for a model not in `omen-swap`, on admission failure, and
  hard-refuses the production ports; unload uses the **path form** `/api/models/unload/{model}` — the
  bare endpoint unloads *everything*, production included. Cross-model KV restore is refused before any
  HTTP call because the slot file format carries no model identity. Registration in `TOOL_CAPABILITY` /
  `profiles.toml` / the gateway's `--providers` list (plan P10) is **not** on master at acceptance
  (landed later the same day, `3462687`; door mounts verified — see *Verified live*);
  the capability for the actuators is `rotation_admin`, operator + unrestricted only.
- **VM builders reach the B70s only through an authenticated proxy plus one operator firewall rule.**
  llama-swap's admin endpoints (`/api/models/unload*`) are unauthenticated, so binding it on `0.0.0.0`
  or `172.19.240.1` would let any VM unload production, and the Default Switch prefix drifts across
  reboots. Shape (designed, not built): llama-swap stays loopback; an authenticated reverse proxy on
  `omen.mshome.net` (the `OmenOllamaTracingProxy :11435` pattern) forwards `/v1/*` to `:8081` with the
  bearer; one inbound rule scoped to the `vEthernet (Default Switch)` interface is Derek's action; then
  `cc-builder-2/3` `runner.json` is re-pointed.
- **Fleet dispatch is uncued and the drain timer stays held.** "Leverage the dual B70s on OMEN first"
  was not a dispatch cue: the mechnet exerciser (plan M6) stays `--dry-run`; the drain timer is armed
  against dead candidate backends and would dispatch unattended on its first tick, so it is not lifted.
- **The recurrence record splits at the cutover, by decision rather than accident.** Rows before and
  after are two epochs; the ETW manifest's `server_pid 20416` goes stale at execution; the baseline is a
  reference, not capacity (`docs/adr#0044`); the first post-cutover keep-alive rows are warm-state rows
  because the ceremony warms before it measures (`docs/adr#0043`).
- **The catalog will say `null` more often than a dashboard likes.** That is the point: a `null`
  `load_s_steady` on phi4 is a prompt to measure, not a hole to fill from memory.
- ⚠ **What acceptance does not claim.** No side model has been loaded through this substrate on the
  live box; the parser is tested against quoted `-lv 5` lines, not a captured phi4 load report; the
  admission gates are ported numbers, not re-measured ones; and the scheduler's `rotation_plan`
  (Decision 2) is a specification here, not a landed function. Each of these has a receipt path
  named for it and none of them has the receipt yet. **Superseded 2026-09-03 by proof B** (see *Verified
  live*): phi-4 and qwen14b have been loaded through this substrate on the live box beside production;
  the parser has read two captured side-server load reports (and refused the iGPU one); `rotation_plan`
  landed (`4ec76db`). Still ported rather than re-measured: the admission gate numbers.

## Alternatives considered

- **Side-port-first, production untouched.** Rejected by Derek: it leaves two lifecycles on one pool
  (ArcServeBoot's llama-server beside llama-swap's), the exact unenforced mutual-exclusion hazard
  `docs/adr#0040` adopted llama-swap to retire, and postpones the only cutover that can ever be
  ledgered as a boundary rather than suffered as an incident.
- **OMEN in `load_machines`.** Rejected: hindsight replays past runs over the machine list, and a
  builder run replayed onto `omen-inference` would score a schedule that never existed. Appending the
  machine at proposal time with `roles=["inference"]` keeps builder jobs off it by eligibility, not by
  luck.
- **llama-swap listening on `0.0.0.0` (or the Default Switch address) so the VMs can reach it.**
  Rejected: the admin endpoints carry no auth, so exposure equals letting every VM unload production;
  see the proxied follow-up above.
- **Trusting the `-vk1` index and skipping the assertion.** Rejected on `docs/adr#0042`'s evidence — a
  Vulkan index is positional per process, and the iGPU sits in the same enumeration.
- **Letting the keep-alive or the rotation tools restart production when the rung reads degraded.**
  Rejected, as in `docs/adr#0043`/`#0044`: R10 forbids reading a post-restart recovery as caused by it,
  and a restart from an actuator would consume the dwell-time record the watch posture exists to
  accumulate.
