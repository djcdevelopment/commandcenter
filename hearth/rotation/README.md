# hearth/rotation — the model-rotation substrate over llama-swap

[ADR-0040](../../docs/adr/0040-serving-lifecycle-is-adopted-not-built.md) Phase 2 /
[ADR-0045](../../docs/adr/0045-the-scheduler-plans-a-rotating-host.md) P3–P4. HEARTH builds only the
verified absences; **llama-swap owns the process lifecycle** (`fleet/arcserve/README.md`). Nothing in
this package addresses production `:8082` (`swapclient.PRODUCTION_PORTS = (8082, 8083, 8084)` are
refused by every actuator), and `qwen3-30b-a3b` is never unloaded by these tools.

## Module map

| module | what it does | seams |
|---|---|---|
| `swapclient.py` | `LlamaSwapClient` for llama-swap v251 on `http://127.0.0.1:8081`: `/health`, `/running`, `/upstream/{id}/health` (triggers a load), `/upstream/{id}/completion`, `/slots`, `/logs`, `unload(id)` = **path form** `/api/models/unload/{id}`, `unload_all()` = the bare endpoint (unloads production too; named so nobody calls it by accident). `wait_ready` is READY only when `/health` is 200 **and** a 1-token completion returns `timings`. | `fetch`, `clock`, `sleep` |
| `placement.py` | pure parser + verdict over the `-lv 5` load report: which `VulkanN` got weights, B70 vs `Intel(R) Graphics`, `expected_cards`, optional per-BDF commit-delta corroboration. Unparseable → **not ok** (fail closed). | — |
| `kv.py` | KV slot save/restore with the naming manifest `hearth/var/kv-manifest.json` (`kv-manifest.v1`). | injected client |
| `windows.py` | named rotation windows: a jsonl row in `hearth/var/rotation-windows.jsonl` (the span a health reader can exclude via `rung_state(windows=)`) plus a schema-valid workflow event for the caller to record. | path |
| `telemetry.py` | `commit_free_gb()` (Windows commit headroom) and `b70_snapshot()` (one b70tools capture; cards keyed by **BDF**). Missing telemetry is `None`, never 0. | `runner` |
| `admission.py` | pure bytes-per-card gates: `commit_min_free_gb 6.0`, `vram_headroom_gb 0.5`, abort at 95 °C, `shared_growth_abort_gb 2.0` — ported from `campaign/qwen38/config/campaign.json` (the 0.5 GB headroom from the scheduler's `_VRAM_HEADROOM_GB`), **not re-measured**. Unknown → refuse. Card choice = most free VRAM, ties to the cooler card; advice only. | — |
| `lifecycle.py` | `load_with_assertion`: fence → telemetry → admission → load → `wait_ready` → read the server's own log → assert placement (+ delta) → ok; on mismatch unload and try the sibling entry. Every step emits a receipt row. | `snapshot`, `fence`, `logs`, `on_event` |
| `preflight.py` | the four gates that must hold before a window opens — **G0** fence clear, **G1** the entries the window needs are declared by the RUNNING llama-swap, **G2** production `at_rate`, **G3** the door is not older than the code it mounts. Pure gate functions + live readers; `python -m hearth.rotation.preflight` prints GO/NO-GO per gate with its remedy and exits 1 on NO-GO. Unreadable input is a NO-GO, never an assumed pass. | `fence`, client, `live_rung_state`, log path, `git` |

## The door tools

`hearth/toolsurface/rotation.py` (P9, `a53fe8b`; registered `3462687`):

- capability `query`: **`rotation_status`** (llama-swap `/running`, production rung state, the
  tenancy fence, open windows, catalog ids, `last-load.json`), **`recommend_rung`** (task-family
  advice from `hearth.scheduler.families`; never dispatches).
- capability **`rotation_admin`** (operator + unrestricted only — `hearth/etc/profiles.toml`,
  `hearth/kernel/capabilities.py`): **`rotation_window`**, **`rotation_load`**, **`rotation_unload`**,
  **`rotation_kv_save`**, **`rotation_kv_restore`**.

`hearth/toolsurface/rungstate.py`: **`query_rung_state`** — the omen-arc verdict (`at_rate | warn |
degraded | stalled | stale | unreachable | no_baseline | unknown`) read passively from the keep-alive
tail and `campaign/ff-probes/rate-baselines.json` (ADR-0044). Never probes.

## The window protocol

```
rotation_window(action="open", name, reason, models)     -> jsonl row + assay.started event
rotation_load(model_id, window, expect_cards=1)          -> receipt (also hearth/var/rotation/last-load.json)
rotation_kv_save / rotation_kv_restore / rotation_unload (all take window=)
rotation_window(action="close", name, outcome=passed|failed|aborted) -> jsonl row + assay.passed|failed
```

Every actuator refuses without an **open, named** window (`error_code: no_open_window`). Window state
lives in `hearth/var/rotation/windows/<name>.json`. The tool returns the workflow event
(`workflow_id = wf-rotation-side-port`, `run_id = <window>`, `assay_id = rotation-window:<window>`) —
**the caller records it with `record_event`**; this package never writes the corpus. The close event
carries `candidate_id` (`8f301bc`): the ontology's `assay.passed` requires it and `record_event`
refused the first close without one. `hearth.health.rungstate.rung_state()` takes a `windows=` list and excludes
rows inside one, and `live_rung_state()` reads them from `hearth/var/rotation-windows.jsonl` (fixed in the
2026-09-03 close-out after the proof's own probes had read as production's regime);
`campaign/lz-probes/etw11_recurrence.py` does not read them yet.

## The placement assertion

Placement is never inferred from a Vulkan index (ADR-0042). `lifecycle` reads the **side server's
own `--log-file`** (`hearth/var/swap-logs/<id>.log`, last load report only; llama-swap's `/logs` is a
~10 KB tail and only a fallback), parses it, and corroborates with the **per-BDF commit delta**
from a b70tools snapshot before/after the load (`min_delta_gb 1.0` on exactly the expected cards).
Mismatch → unload → try the sibling entry (`-vk1` ↔ `-vk0`) once. An entry that was **already
resident** shows no delta, so the log report alone decides and the receipt says `already_resident`
(2026-09-03: a retry had unloaded a passing placement on that 0 GB delta).

## The tenancy fence

`lifecycle.default_fence()` reads `GpuTenancyStore.active_image_session("omen-b70-pool")`. An active
image session → **refuse before anything is touched**; an unreadable store → refuse (fail closed).
The fence is read-only: rotation never claims the pool, and the owner literal `'imagegen'` in
`hearth/execution/coordination.py` is an OPEN item in `DECISIONS-PENDING.md`. While the imagegen
lane holds the pool, production is stopped under it and every rotation/door call refuses — expect it.

## KV manifest

Slot files carry **no model identity**, so identity lives in the name,
`<model_id>.<slot>.<prompt_hash>.bin` (`E:\work\battlemage\kv\`, every side entry's
`--slot-save-path`), and in `hearth/var/kv-manifest.json`. `save_slot` prefills the prompt
(`cache_prompt`) before saving — the bare save captured whatever the slot last held (1 canary token,
205 KB, on 2026-09-03). `restore_slot` refuses a cross-model restore **before any HTTP call**
(`CrossModelRestore`) and verifies by replaying the prompt (`prompt_n` ≈ 1 = hydrated).

## The 2026-09-03 proof (window `rot-side-20260903-B`, opened 00:33:47Z, closed `passed` 01:03:54Z)

- `rotation_load phi4-vk1` → BDF `0000:04:00.0`, 3.344 s wall (warm file cache; 20.14 s after eviction), 1 B70, iGPU clean, 9.7 GB, +9.729 GB commit on `0000:04:00.0` / 0.0 on `0000:09:00.0`; `qwen14b-vk1` → same card, 57.469 s (cold), +9.621 GB.
- `rotation_kv_save phi4-vk1` slot 0 → `phi4-vk1.0.e9480f7e3f3cf3d6.bin`, 1239 tokens, 253,768,028 bytes; unload → load qwen14b → unload → reload phi4 → `rotation_kv_restore` 1239 in 168.7 ms, replay `prompt_n=1 cache_n=1238`; restore into `qwen14b-vk1` refused before HTTP.
- Production `at_rate` before and after the pour (107.3 / 109.18 / 107.99 tok/s = 101–103% of the 106.0 baseline), with a dip on the three deep probes taken during it — 17:47:06 74.36, 17:52:11 71.14, 17:57:12 73.33 tok/s, `decode_degraded`, prompt_ms 14.8–15.3 vs 10.0 — while the pour's held-out `omen-arc` judge calls hit production beside a decoding side model; back to 107.99 at 18:02:12 with no intervention; the two causes were not separated, and the probes sit inside the ledgered window; teardown `/running == [qwen3-30b-a3b]`. Receipts: `hearth/var/rotation/windows/rot-side-20260903-B.json`, `hearth/var/rotation/last-load.json`, `hearth/var/kv-manifest.json`.

## Pre-flight: run the gates, don't remember them

```
python -m hearth.rotation.preflight                          # the two-card proof's seats
python -m hearth.rotation.preflight --models gptoss20b-vk0   # any other window
```

Four gates, each failing closed with its own remedy; exit 0 = GO, 1 = NO-GO.

**G1 and G3 are the same question asked of two processes: *did it restart after the change landed?***
A running llama-swap keeps the entries it booted with, and the gateway runs the code it was started
with. Neither is answerable from the change itself — G1 asks llama-swap's `/v1/models` what it will
actually accept, and G3 compares the gateway's last start (from its own log) against the newest
commit touching `hearth/rotation`, `hearth/health` or `hearth/toolsurface`. Both were paragraphs in
this file before they were gates, and both cost live attempts on 2026-09-03.

G3 caught a real one on its first live run: the door had restarted at 22:20 on 09-03, but the
close-out landed `hearth/health/rungstate.py` at 01:38 on 09-04 — so the running door still had the
old rung-state reader, the one that never excluded rotation windows.

## The next window: two side models on two cards (staged 2026-09-04)

The first co-residency attempt. On 2026-09-03 `env=1` mapped every side model to `0000:04:00.0`, so
phi-4 and qwen14b ran **sequentially on one card**; the `-vk0` entries exist to put them on both.

**Waits on** an ArcServe restart (any lane's — the imagegen lane's restore path does one). Do **not**
restart production for this alone: INC-2026-08-30-A is watch-do-not-poke (Derek, 2026-09-04).

**To take the pool back deliberately**, see [POOL-HANDOVER.md](POOL-HANDOVER.md): G0 now reports
whether the holder is *busy* or *idle-but-held*, and `stop_image_session(force=False)` drains and
restores a warm ArcServe. Never kill the imagegen processes.

| # | step | expect |
|---|---|---|
| 1 | ArcServe restarts (peer lane) | `-vk0` entries live |
| 2 | `schtasks /Run /TN HearthGatewayRestart` (PowerShell), then doorcheck | door mounts current code |
| 3 | `python -m hearth.rotation.preflight` | four GO |
| 4 | `rotation_window` open | window id on `rotation-windows.jsonl` |
| 5 | `rotation_load phi4-vk0` | `card_bdf 0000:09:00.0`, `bdf_corroborated: true` |
| 6 | `rotation_load qwen14b-vk1` | `card_bdf 0000:04:00.0`, `bdf_corroborated: true` |
| 7 | `query_rung_state` **during** the window | `excluded_windows` **non-empty** |
| 8 | `rotation_unload` each (path form), close the window | `/running == [qwen3-30b-a3b]` |

**Accept when** both receipts say `bdf_corroborated: true` on **different** BDFs with
`igpu_with_weights: false`, production stayed listed throughout, and the window closed
`assay.passed` carrying its `candidate_id`.

Step 7 is not in the original handoff sequence. It is the first live exercise of the window-exclusion
fix (`0cb5275`) — the bug that let the 2026-09-03 pour's own probes read as production's regime. A fix
that has not been run live is a hypothesis.

Optional, only if 5–8 pass clean and the fence is still free: the W1 R6 critic quad
(`gptoss20b-*`, `mistral24b-*` on the other seats).

**If the enumeration shifted again** the sibling retry finds the other card and the assertion fails
closed — read `hearth/var/swap-logs/<id>.log` before touching anything. Never trust a Vulkan index.

## Gotchas

- **The door runs the code it was started with.** Two proof attempts failed on a reader that was
  not in the running gateway. After landing anything the door mounts: `schtasks /Run /TN
  HearthGatewayRestart` from PowerShell (not Git Bash), then `/checkmcp` (doorcheck).
  This is now **gate G3** of `python -m hearth.rotation.preflight` — check it instead of remembering it.
- **In-process harnesses need the launcher's env.** `hearth.toolsurface.inference` returns `no auth
  token for omen-swap` unless `OMEN_ARC_TOKEN` is set: run the harness under a wrapper `.cmd` that
  `CALL`s `hearth\var\gateway.cmd` (never echo it), from PowerShell — the Git Bash `cmd /c "call ...
  && ..."` chain fails with "The system cannot find the path specified".
- **`omen-swap` `context_bytes` 14336 is the MIN over members** (`-c 4096 × -np 1 × 3.5`); it refused
  5 of 8 bench tasks for a pin although phi-4 runs `-c 8192`. **Decided 2026-09-04 (Derek): per-member
  budgets with the rung value as fallback** — `context_bytes_by_model` in `[backend.settings]`.
  Not implemented yet; still the MIN in the code.
- **Index 2 is the iGPU** on this driver; siblings are env 0/1 (`92f3cd6`). The `-vk0` entries activate
  at the next ArcServe restart; until then a `-vk0` load gets a fast 404 refusal and env=1 puts every
  side model on `0000:04:00.0`. Ask **G1**, not the yaml: the file on disk says `-vk0` already.
- A call whose MCP client session had expired still executed door-side (and passed); check
  `rotation_status` before retrying a load.
- Gateway dispatches are bridged with `task_kind` = the tool name, so door calls never feed the
  `offload-generate` bucket (ADR-0027 gate 1); the second workflow for `omen-swap` had to be an
  in-process caller with its own `DispatchIdentity` (`rotation-proof`). **Decided 2026-09-04 (Derek):
  bridge `local_generate` only, other tools stay tool-named** — but a prerequisite is still unanswered
  (was the dispatch-time producer's silence a defect or a semantics gap?), so the mapping is unchanged
  in the code. Read that path before editing it; see the ADR-0027 addendum.
