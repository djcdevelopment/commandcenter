# Prediction Card — SAT-L1 : the B70 saturation surface under concurrent intake

`[prereg tag: prereg-saturation-lap1-20260909 — TAGGED 2026-09-09 on Derek's ack; predictions final]`

- **Committed:** `2026-09-09` (tag `prereg-saturation-lap1-20260909`)  **Advisor ack:** `2026-09-09, Derek` — ack and tag; Phase 2 `-np` restarts authorized, interleaved; capture the render-lane reference before the first cell; build the ring→report packager before depth-0 cells
- **Maps to:** R&D program *"saturate the B70s with concurrent work, then solve residency"*, Lap 1;
  loss channels (a) intake, (b) submission gaps, (d) over-admission, (e) idle decay
- **Format:** copied from `E:\work\denning\prereg\TEMPLATE-prediction-card.md`. Predictions below are
  written before any cell runs and are not edited afterwards; results are appended beneath them.

## Hypothesis (falsifiable, one sentence)

On the production dual layer-split server, completed jobs/hour under concurrent intake is capped by
the server's slot count at shallow depth and by per-slot context at deep depth — **the cards are not
the binding constraint at `-np 2`** — and the binding channel is identifiable per cell from slot
occupancy, board duty cycle, and live VRAM budget recorded together.

## Mechanism (why we expect it)

- `-np 2` is a hard admission cap: llama-server holds two slots and queues the rest, so client
  concurrency above 2 adds queueing delay, not GPU work. Two slots of a 3B-active MoE cannot fill two
  B70s that the render lane drives to sustained high power.
- KV is ~96 KB/token FP16 for this model (48 layers × 4 KV heads × 128 dim × 2 bytes × K,V; agrees
  with denning's cost model). At fixed `-c 131072`, raising `-np` shrinks per-slot context
  (64K → 32K → 16K), so deep cells become inadmissible by *configuration* before any memory knee.
- denning I-4b measured a single-card knee at **N\*=8** (goodput 8/8 at 229 t/s; **0/10 at 67.5 t/s**
  — a cliff, not a trade) at 2K ctx. Dual layer-split pipelines each token across both cards; batching
  hides part of the bubble, so the dual-split knee at `-np 8` is expected below 8, not at 16.
- ADR-0043: a rung idle >~60 s is not at its known-good rate; unwarmed cells measure the decay.

## Quantitative prediction (direction + threshold + prior)

| # | prediction | prior |
|---|---|---|
| **P1** | At `-np 2`, prompt 512 / max 200: jobs/hour rises N=1→2, then is **flat within ±10% from N=2 through N=24**. | ~85% |
| **P2** | The N=1→2 gain at 512 is **1.3×–1.8×** jobs/hour (denning: aggregate decode 60.9→80.6 t/s at 1→2; prefill batches better than decode). | ~70% |
| **P3** | At `-np 2`, p95 latency exceeds **1.5× the N=2 baseline by N=4** and grows ~linearly with N/2 thereafter — the waiting term, measured directly. | ~85% |
| **P4** | At `-np 2`, 32K-prompt jobs/hour is **≤ 1/50** of the 512 value (a 32K job ≈ one 32K prefill at ~140 tok/s + 200 tokens at ~10 tok/s ≈ 250 s, vs ≈ 2 s at 512). | ~75% |
| **P5** | Across the whole `-np 2` surface, board duty cycle (FF6 definition) stays **< 50%** while `/slots` shows both slots busy **≥ 90%** of wall at N≥4 — *the server saturates, the cards do not*: channel (a) by configuration, not (b). | ~70% |
| **P6** | At `-np 8`, prompt 512: the SLO-met knee (TBT median ≤ 50 ms, TTFT ≤ 2 s, denning's definition) lands at **N\* = 4–6**, below denning's single-card 8, and jobs/hour at N=8 is **≥ 2×** the `-np 2` value. | ~55% |
| **P7** | Warm-then-measure holds cell repeats within the FF6 noise floors (1.5% pp512, 0.64% pp2048, 1.2% tg128); an unwarmed rep-1 reads **65–90%** of warm (ADR-0043's 68/69/74/92). | ~90% |
| **P8** | At fixed `-c 131072`, 32K prompts are admissible at `-np ≤ 4` and **rejected or truncated at `-np 8`** (16K/slot). Near-deterministic; recorded so it is a protocol fact, not a surprise. | ~90% |

P5 is the one that shapes the program: refuted (duty cycle already high at two slots) means the cards
are compute-bound now and the feeder lane (Lap 2) is not the next step.

## Measurement protocol (exact)

**Box / build / model** — OMEN; production binary `E:\work\llamacpp-knee\build\bin\llama-server.exe`
(record exe sha256 + `git -C E:\work\llamacpp-knee rev-parse HEAD`); model
`Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf` (sha256 from `campaign/qwen38/config/artifacts.json`);
driver as reported by b70tools at run time; the production entry in
`fleet/arcserve/llama-swap/omen.yaml` verbatim: `-ngl 99 -sm layer -ts 1,1 -fa on --no-mmap -dio -fit off
-c 131072 -np 2 -ub 1024 --slots --jinja --metrics`, `ttl: 0`.

**Load generator** — `campaign/qwen38/qwen38_campaign.py load`, pointed at production directly
(`--endpoint http://127.0.0.1:8082`; round-robins clients across endpoints; streams
`/v1/chat/completions` with `stream_options.include_usage`; no maintenance guard at code level):

```
python campaign/qwen38/qwen38_campaign.py load \
  --run-id sat-l1-np2-p<depth>-c<N>-r<rep> --endpoint http://127.0.0.1:8082 \
  --candidate production --topology dual-split --model qwen3-30b-a3b \
  --concurrency <N> --prompt-tokens <512|8192|32768> --max-tokens 200 \
  --requests-per-client 3 --seed 38027 --disable-thinking
```

Rows land in `E:\work\battlemage\qwen38-bench-2026-08\results\requests\<run-id>.jsonl` with
`started_at`, `completed_at`, `latency_s`, `ttft_s`, and llama-server `timings`. `summarize_rows`
gives jobs/hour (**busiest-client wall**, per its definition) and nearest-rank p50/p95/p99.
If `:8082` answers 401, set `QWEN38_API_KEY`; it is not expected to.

> **Correction 2026-09-09 (verified live before any cell):** `:8082` answers `/health` 200 but
> `/slots` and `/metrics` **401 "Invalid API Key"** — production requires a bearer for everything
> except health. The bearer is the ArcServe token, which lives only in the gitignored
> `hearth\var\gateway.cmd` and must never appear in a transcript or receipt. The cell-runner
> obtains it through `hearth\etc\with-gateway-env.cmd` (invoked as
> `cmd /c "hearth\etc\with-gateway-env.cmd <command>"` from PowerShell, never Git Bash) and passes
> it to the harness as `QWEN38_API_KEY` and to the `/slots` poller as `Authorization: Bearer`.
> Receipts record the bearer's *source variable name and length*, never its value.

**Cells** — Phase 1 (no restart, co-resident): `-np 2` × depth {512, 8192, 32768} × N {1, 2, 4, 8, 16, 24}.
Phase 2 (each `-np` value is a **production restart** — edit `omen.yaml`, then
`schtasks /Run /TN ArcServeRestart` from PowerShell, never Git Bash — **a tenancy call, Derek's**):
`-np` {4, 8} × depth {512, 8192, and 32768 only where admissible} × N {2, 4, 8, 16, 24}.
**Repeats:** ≥3 per cell; ≥5 for the cells that decide P5 and P6.

**Order** — depth blocks outermost, N ascending within a block, so a warm rung is never measured cold.

**Gates, every cell:**
1. *Warm-then-measure (ADR-0043):* at the block's depth run N=2 until three consecutive
   `decode_tokens_per_s` agree within ±2%; discard; then measure.
2. *Production:* `guard.gate("cell start")` must read `at_rate`; `guard.wait_for_fresh` after each
   depth block (the keep-alive cadence is ~5 min, so per-cell fresh samples are not available);
   `degraded` **stops the run**.
3. *Admission (denning H1 / I-4a):* b70tools snapshot before and after — `commit_free_gb`, per-card
   `local_committed_gb`, temps; the live `QueryVideoMemoryInfo` budget where the tensor lab's
   `device_budget` path can read it. A cell whose budget headroom goes negative is `over_admitted`:
   kept in the receipt, excluded from the surface.

   > **Correction 2026-09-09 (finding A12, `campaign/ff-probes/ff_cell.py`):** `local_committed`
   > is an activity-window counter that reads ~0.00 on an *idle* server even with the model resident
   > and serving — 22 of 25 archived adapter-probe captures were `indeterminate` for this reason. The
   > "before" sample is therefore taken **inside the warm-up traffic window** (gate 1), never on an
   > idle rung, and the "after" sample inside the cell's last request. The cell-runner is built on
   > `ff_cell.py`'s ordering (rate → sample-inside-window → gate → one cell → rate → one receipt row),
   > and every Phase 2 receipt records the incumbent **epoch** (ADR-0044) — a `-np` restart starts a
   > new one, so its rate baseline is re-established by `ff_ratecheck` after the restart, never
   > carried over.
4. *In-flight, unelevated:* poll `http://127.0.0.1:8082/slots` at 1 s during the cell → slot-busy
   fraction; scrape `/metrics` before and after.
5. *Board duty cycle (FF6):* `saturation_duty_cycle` and `time_to_saturation` against the frozen
   render-lane reference, gated by `symmetry_check`. **Resolved 2026-09-09, verified at source:**
   - The symmetry gate **exists** — `corpus/verdict.py` (lines 186–212), a CLI over a b70tools
     `events.jsonl` stream: `ratio = min(samples)/max(samples)` across adapters sharing a
     description, warn below `0.5`. It was never called by any FF harness; this card calls it per
     cell and records `ratio`. Below 0.5 the row is `partially_scored`.
   - The render-lane reference profile, `saturation_duty_cycle` and `time_to_saturation` **do not
     exist** — prose in the FF6 card only; present in 0 of 410 receipt rows. Until the reference is
     captured (one BF6 render-lane run with b70tools recording per-card power; freeze p50), the
     power-derived fields are `null` on every row and **P5's duty-cycle half is scored *untested***,
     never inferred from `/slots`. Capturing it is a named ops item, Derek's call — **acked
     2026-09-09: capture first.**

   > **Reference-capture protocol, added 2026-09-09 (verified at source, not relayed):**
   > - `submit_render` takes **no** tenancy fence — zero references to `arc-maintenance`, `fence`,
   >   `tenancy` or `image_session` under `hearth/media/`; only the imagegen lane writes the sentinel.
   >   The capture is therefore **co-resident with production**, not an outage. It contends, so it
   >   runs under the same guard as a cell: `at_rate` on a fresh sample before, `wait_for_fresh` after,
   >   `degraded` recorded as a finding about co-residency rather than hidden. No render-lane
   >   telemetry has ever been captured; this is the first.
   > - **Power is derived, not sampled.** The real IGCL collector emits only cumulative
   >   `gpu.energy_j_counter` / `vram.energy_j_counter` / `card.energy_j_counter`; `reference_p50_power`
   >   is the p50 over ticks of ΔJ/Δt per card at 1 Hz (`b70tools --run --ticks N --cadence-ms 1000
   >   --flush-every-tick`). `gpu.power_w` exists only in the fake collector and is never read.
   > - **Oracle validity condition.** The BF6 lane is QSV media-engine encode; ADR-0036's two-lane
   >   proof measured ~28–30% *media-engine* utilization, and fixed-function encode need not drive
   >   board power to what Vulkan compute reaches. The capture therefore records absolute p50 W per
   >   card for the render run **and** for a compute control (a pp8192 prefill burst on production at
   >   N=2, same capture cadence). If render p50 < compute-control p50, the oracle is inverted:
   >   `saturation_duty_cycle` stays `null`, P5's duty-cycle half is scored *untested*, and the
   >   reference question is re-opened with the compute control as the candidate oracle. This is
   >   decided by the numbers, before any Lap 1 cell is scored.
   > - **The lane's own calibration already points that way** (`list_render_lanes`, calibrated
   >   2026-08-25, read 2026-09-09): during a real QSV encode each B70's engine profile is
   >   `videodecode` 77.2 / 76.9%, `3d` 12.5 / 20.3%, **`compute` 0.0 / 0.0%**. The encode does not
   >   exercise the compute engines at all, and the coexistence acceptance measured its effect on
   >   production at −0.44%/+1.24% (one lane) and −1.34%/+0.87% (two lanes) — consistent with that.
   >   The compute control is therefore expected to *be* the oracle; the render capture is still
   >   taken, so the comparison is measured rather than argued.
   > - **Executor dependency:** the gateway cannot render (session 0); `interactive_executor_available`
   >   is `false` with the render agent's heartbeat ~11.9 days stale. The render half of the capture
   >   needs the agent started in an interactive session first — Derek's step. The compute-control
   >   half needs no executor and can run first.
   > - **Corrected same day:** `hearth/var/render/PAUSED` reads "Hatchet production authority; legacy
   >   workers held for rollback" (2026-08-28). The door's render lane is the *legacy* path, paused by
   >   design; the live authority is Hatchet, whose intake (`:18777`) is **not listening on OMEN**. The
   >   render half is not drivable without operator action on either authority.

   > **RESULT — compute control captured 2026-09-09 08:54:37Z** (`campaign/ff-probes/
   > sat_reference_capture.py --live --ledger`; receipt `E:\work\battlemage\sat-l1\reference\
   > ref-20260909T085437Z\receipt.json`; ledger rows `SAT-L1-REFERENCE` and the post-burst
   > `FF-RATECHECK`). **Regime:** Qwen3-30B-A3B Q4_K_M, dual layer-split `-ts 1,1`, `-np 2 -ub 1024`,
   > epoch 2026-08-29T18:22; N=2 clients, **pp6033** per request (the 8192 target tokenized denser;
   > the receipt carries the true `prompt_n`), `cache_prompt: false`, 22/22 requests over 94 s,
   > prefill 1540.8 tok/s median (server timings), request wall p50 8.43 s.
   >
   > | per-card GPU-tile power (ΔJ/Δt, 1 Hz, `gpu.energy_j_counter`) | ambient p50 | **burst p50** | burst p95 | burst max | ticks / irregular |
   > |---|---|---|---|---|---|
   > | bus 9 · `0000:09:00.0` · b70b | 26.67 W | **159.92 W** | 184.74 | 191.81 | 140 / 0 |
   > | bus 4 · `0000:04:00.0` · b70a | 26.60 W | **114.05 W** | 164.37 | 192.83 | 140 / 0 |
   >
   > Symmetry 0.964 (788 vs 760 samples). `card.energy_j_counter` was emitted once per capture in
   > this b70tools build and is not differenceable; the reference is the GPU-tile counter, stated as
   > such. `vram.` is absent.
   >
   > **Oracle decision (per the condition above):** the compute control reaches ~6× ambient and
   > ~192 W peak on both cards; the render lane's own calibration shows `compute 0.0%` during encode
   > and the lane is paused under the Hatchet cutover. **The compute control is the reference.**
   > `reference_p50_power` is **per card** (159.92 / 114.05 W); `saturation_duty_cycle` for a cell is
   > the fraction of wall time each card spends above 0.9 × its own reference p50, reported per card.
   > The render half is scored *not applicable — lane paused, compute idle during encode*, not
   > "untested".
   >
   > **Two observations recorded, not claimed** (one capture, one regime):
   > 1. **Layer-split prefill is power-unbalanced at N=2** — a 46 W gap in p50 between the cards while
   >    both peak at ~192 W. Consistent with pipeline bubbles (one card holds while the other works);
   >    it is the waiting thesis showing up as power, and it is a hypothesis for Lap 1 to test, not a
   >    result.
   > 2. **The keep-alive deep probe fired 28 s into the burst and read 3.56 tok/s (3.4% of baseline).**
   >    Post-burst `ff_ratecheck`: **104.90 tok/s = 99%** (reps 104.48 / 104.46 / 105.75, spread 1.23%,
   >    PASS). So that reading was queueing behind two full slots — P3's waiting term, landing on the
   >    lab's own probe — not damage. Protocol consequence: a rung sample counts as "after" only if taken
   >    after the burst **ended**; a sample inside the burst is kept as `rung_during`. The script was
   >    corrected accordingly the same hour.
   >
   > **FROZEN 2026-09-09 on Derek's ack:** the compute control above is the SAT-L1 saturation
   > reference. The render half is *not applicable* (lane paused under the Hatchet cutover; compute
   > idle during encode). P5's duty-cycle half is now testable on every Lap 1 cell.
   - *In-flight proxy, verified live through the wrapper:* `/slots` (per-slot `is_processing`,
     `n_prompt_tokens`, `next_token`) and `/metrics` (`llamacpp:n_busy_slots_per_decode`,
     `llamacpp:predicted_tokens_seconds`, 15 series) both answer 200 with the bearer. Slot-busy
     fraction comes from both — client-side poll and the server's own busy-slots series — and is
     recorded on every cell.
6. *Depth-0 fraction (ETW4):* the analyzers consume `report.json`, and the only producer today is the
   **elevated** short-session path (`etw1_feasibility.ps1`); the circular ring (`etw6`) has no
   packager. Protocol: one elevated capture per depth block at N=2 and N=16, run by Derek (or by a
   built ring→report packager, a named build item). Cells without a capture carry `null`, recorded not
   dropped; the channel-(b) verdict for those cells rests on `/slots` + duty cycle and is flagged
   lower-confidence.

   > **Resolved 2026-09-09 — the packager exists and the elevation dependency shrinks to one step.**
   > `campaign/lz-probes/etw10_package.py` (47 tests) turns an `etw6` ring snapshot plus the load
   > harness's per-request rows into the `report.json` that `etw4_depth.py` / `etw7_verdict.py` consume;
   > verified end to end on the 2026-09-01 INC-A capture (non-null `f0` on every queue; healthy arms
   > agree) and, on the archived 2026-08-30 trace, reproduced the campaign's frozen healthy floor
   > (`mean_depth` 3.895/3.903 vs 3.9075; `f_depth0` 0.121/0.115 vs 0.118).
   > - **`tracerpt` on an existing `.etl` needs no elevation** — measured by the builder and re-run by me
   >   as `OMEN\derek`, `IsInRole(Administrator)=False`: exit 0, 70,148,788 bytes, byte-identical in
   >   size to the elevated script's dump. Only the ETW *session lifecycle* is privileged.
   > - **Depth-0 protocol, revised:** Derek starts the ring **once** for all of Lap 1
   >   (`etw6_session.ps1 -Start`); per depth-0 cell the runner, unelevated, snapshots the ring right
   >   after the cell, converts with `tracerpt`, packages with the cell's **real** harness rows as arms
   >   (never synthetic in a scored cell), runs `etw4_depth.py`, and records `f0` per queue. No session
   >   → the cell carries `null`; the runner never starts or stops tracing.
   > - **Two traps the builder measured, now fixtured in the packager:** a dump's span must be taken over
   >   DxgKrnl events only — session-header events from a wrapped ring make the naive bound ~178,646 s
   >   against a 164 s trace (a ~1000× error that would license an arm anywhere in two days); and the
   >   archived `etw1-20260830-043447.json` carries a `start_epoch` 28,800 s (8 h) wrong, masked only
   >   because the readers re-derive epochs from the ISO strings — the packager re-reads every epoch it
   >   emits through `etw2_join.epoch` before writing.
   > - **Bounds:** the ring wrapped at ~48 min, not the 42 estimated; a full 19 GB conversion is ~2 h
   >   and ~20–25 GB of XML, so per-cell snapshots are converted with `--max-dump-mb`, promptly.
   > - **Ring state on 2026-09-09:** no `lz_dxgk_ring` session running (`lz_dxgk_ring.etl` last
   >   written Sep 3); `session-manifest.json` dates from Aug 30 with `server_pid 20416` — a stale
   >   epoch, and the pinned per-process queue handles with it. Derek's one elevated step
   >   (`etw6_session.ps1 -Start`) must therefore write a fresh manifest; the Aug 30 `healthy_floor`
   >   is re-licensed on the new epoch, not carried over. Until then depth-0 fields are `null`.

   > **The cell runner exists (2026-09-09):** `campaign/ff-probes/sat_cell_runner.py` composes
   > `ff_cell.py` (one additive `--json-out` flag) with the qwen38 `load`; 83 tests; dry run for
   > `np2-p512-c2-r1` verified by me — every step printed, bearer value absent, nothing written.
   > `--dry-run` is the default; `--live --one-cell <cell>` runs one cell; `--sweep` is explicit.
   > Machine state is pinned to the live checkout, so live cells and every Phase 2 restart run from
   > `C:\work\commandcenter`, never a worktree. Reference thresholds are read from the frozen receipt,
   > never hardcoded; duty cycle is keyed by PCI BDF.

> **RESULT — first live cell `np2-p512-c2-r1` (repeat 1 of 5), 2026-09-09 ~09:43Z** — receipt
> `E:\work\battlemage\sat-l1\cells\np2-p512-c2-r1\receipt.json`; runner at `04837d9`, fixes at
> `b982a1e`. **Regime:** Qwen3-30B-A3B Q4_K_M, dual layer-split, `-np 2 -ub 1024 -c 131072`
> (64K/slot), incumbent epoch 2026-09-08T11:37:28-07:00, placement both-B70 by BDF (14.96 / 16.03 GB),
> N=2 clients × 3 requests, 512-token prompts, 200-token output. **Status: scored.**
>
> | field | value |
> |---|---|
> | completed jobs/hour | **2,334** (6 jobs; busiest-client wall ≈ 9.25 s) |
> | latency p50 / p95 / p99 | 3.03 / 3.20 / 3.20 s; TTFT p50 39 ms, p95 74 ms |
> | decode per request, p50 | 66.8 tok/s at N=2 (single-stream baseline 106) |
> | `ff_cell` incumbent rate, pre → post | **98.6% → 98.9%** of the 106.0 baseline — production unaffected, measured |
> | guard before → after | `at_rate` 106.32 → `at_rate` 109.50 (fresh) |
> | warm (gate 1) | flat at 0.98% after **1** iteration; unwarmed rep-1 104.13 (the rung was already warm) |
> | in-flight (gate 4) | 19 polls: any slot busy 84%, **both slots busy 47%**, busy-slot fraction 0.658; `n_busy_slots_per_decode` Δ 0.035 |
> | board duty cycle (gate 5) | **0.0% on both cards** above 0.9× their reference p50 (143.9 / 102.6 W); cell-window p50 **73.1 / 73.2 W**, ambient 26.7; symmetry 0.999 |
> | admission (gate 3), re-scored | headroom 16.1 / 15.0 GB (budget 31.05 GiB − committed 14.96 / 16.03), `over_admitted` **false** — see defect 1 |
> | depth-0 (gate 6) | **null** — ETW session not live; see defect 2 |
>
> **Read against the predictions:** at N=2/512 the cards sit at ~73 W against a 160 / 114 W prefill
> reference — duty 0.0 — while the two slots are simultaneously busy under half the time. One repeat
> of one cell scores nothing yet (the card asks ≥5 here); it is consistent with P5's direction and
> establishes the noise-floor cell runs end to end under all six gates.
>
> **Two defects the receipt exposed, fixed before repeat 2 (`b982a1e`):**
> 1. *Admission gate vacuous by construction.* It used DXGI `CurrentUsage`, which is **per-process**:
>    through b70tools it reported b70tools' own 4,096 bytes on a card holding 16 GB, so "headroom"
>    read ~31 GB on any cell and could never fire — the A12 idle counter in a new coat, a green gate
>    proving nothing. The term is now the adapter-wide `gpu.adapter.vram.local.bytes_committed`
>    (agrees with the placement evidence to the byte), `available_for_reservation` beside it as a
>    cross-check, cadence stated (once per capture). r1's admission is re-scored above from its own
>    stream; the original receipt's 31 GB "headroom" stands as recorded and superseded.
> 2. *Depth-0 copied a dead ring.* The Aug 30 manifest and the Sep 3 ring both *existed*, so a
>    file-exists check passed, 19.3 GB were copied (15.7 s) and `tracerpt` ran before the packager
>    correctly refused a Sep 9 arm against a Sep 4 span. Liveness is now the ring's mtime; a dead ring
>    records `null` with its age and never gets copied. **The ring is dead (5.2 days); Derek's one
>    elevated `etw6_session.ps1 -Start` is the step that makes any depth-0 cell scoreable.**

> **RESULT — noise-floor block 1, `np2-p512-c2` repeats 1–5, 2026-09-09 09:38–10:46Z** — receipts
> `E:\work\battlemage\sat-l1\cells\np2-p512-c2-r{1..5}\receipt.json`; reduction
> `E:\work\battlemage\sat-l1\noise-floor\np2-p512-c2-block1-cached-prefix.{json,md}` by
> `campaign/ff-probes/sat_noise_floor.py` (merged `03549e1`, 45 tests). Same regime as r1 above;
> runner builds `d18ab09` (r1), `b982a1e` (r2), `5534179` (r3–r5) — gate scoring changed between
> them, the load path did not. **Status: all five scored; block is an instrument-validation block,
> not the surface's noise floor — see the three defects below.**
>
> | repeat | jobs/h | p50 / p95 s | decode p50 | both slots busy (span) | duty | incumbent pre → post | guard | symmetry |
> |---|---:|---|---:|---:|---|---|---|---:|
> | r1 | 2,334.0 | 3.032 / 3.205 | 66.8 | 0.474 | 0 / 0 | 98.6% → 98.9% | at_rate / at_rate | 0.999 |
> | r2 | 2,284.1 | 3.149 / 3.172 | 64.5 | 0.474 | 0 / 0 | 99.5% → 99.5% | at_rate / at_rate | 0.998 |
> | r3 | 2,285.1 | 3.143 / 3.162 | 64.2 | 0.526 | 0 / 0 | 99.3% → 96.4% | at_rate / at_rate | 0.996 |
> | r4 | 2,188.6 | 3.158 / **3.531** | 63.4 | 0.526 | 0 / 0 | 100.1% → 99.8% | at_rate / at_rate | 0.993 |
> | r5 | 2,285.5 | 3.142 / 3.165 | 64.3 | 0.474 | 0 / 0 | 99.0% → 99.5% | at_rate / at_rate | 0.996 |
>
> Jobs/hour mean **2,275.5**, bootstrap 95% CI **[2,227.2, 2,314.2]** over repeats, spread
> **6.39%**, cv 2.08%; p95 spread 11.38% (all of it r4); decode 63.4–66.8; board p50 73.1–73.9 W on
> both cards (spread < 1%); admission headroom 15.0 / 16.1 GB on r2–r5 (r1's 31.0 GB stands as the
> superseded per-process figure, defect 1 above). Production `at_rate` on a fresh sample after every
> repeat; the one soft post-cell reading (r3, 96.4%) was above the warn line and recovered by r4.
>
> **P7 as the reducer scores it:** half 1 (repeat spread) **refuted on the borrowed floor** —
> 6.39% against the 1.5% pp512 figure, with the caveat the card itself owes: the FF6 floors are
> single-stream `llama-bench` spreads and this is a six-job concurrent wall, where one 0.45 s wait
> moves jobs/hour 6%. Half 2 (unwarmed rep-1 at 65–90% of warm) **untested** — every repeat's rep-1
> read 0.995–1.000 of warm because back-to-back cells never let the rung idle. The kill gate
> ("repeats drift beyond the floor → fix the gate, then resume") is taken literally: nothing from
> this block enters the surface, and the block is re-run on the fixed instrument.
>
> **Three instrument defects the block exposed (fix in flight, one builder):**
> 1. **Prefill is served from the prompt cache.** The load harness sends a byte-identical prompt on
>    every request and never sets `cache_prompt`; across r4's and r5's windows the server's
>    `prompt_tokens_total` moved **64** while `prompt_tokens_cached_total` moved **3,073** (six
>    requests × 440 tokens = 2,640; the rest is the rate probes). Per request the server processed
>    ~1 token in ~16 ms (27k tok/s "prefill"), which the harness's own cache check already nulls
>    out of the prefill rate — so every receipt's prefill p50 is `null`. At 512 the cached prefix
>    is ~9% of a 3.1 s job; at 8K and 32K it is the whole size axis, and P4's arithmetic assumes
>    real prefill. Fix: the harness gains `--no-cache-prompt` (`cache_prompt: false`, the same
>    mechanism the runner's readiness probe already uses), the runner passes it on every cell, and a
>    **seventh gate `prefill_real`** requires uncached prompt tokens ≥ 0.9× the prompts sent.
>    Concern stated once: cache-off is the pessimistic bound for callers that share a system-prompt
>    prefix; the cached regime is a second, cheaper regime, and block 1 is its only record.
> 2. **Gate 4 is span-diluted.** `metrics_before/after` and the `/slots` poller bracket the whole
>    `ff_cell` span, which includes its own single-stream pre- and post-rate probes (that is the
>    ~650 surplus predicted tokens per window). "Both slots busy 47–53%" is over that span; within
>    the load both slots were busy essentially throughout. P5's `/slots ≥ 90%` half must be read
>    over the load window (`min started_at … max completed_at` from the load rows). Fix: the
>    receipt keeps every poll sample and reports load-window figures beside the span figures.
> 3. **The r4 outlier is a waiting term with no attributable cause.** Its last request did normal
>    server-side work (prompt 24.9 ms + predicted 3,020.7 ms) but waited **0.510 s** for a slot
>    (TTFT elsewhere 20–90 ms); no HEARTH ledger dispatch touched production in that window; per-poll
>    samples were discarded (`slots_poll.polls` = 19), so the slot's occupant is unrecoverable.
>    Same fix as 2. This is the thesis in miniature — the loss was a wait, not a kernel.
>
> **Protocol additions (dated, additive):** the unwarmed half of P7 needs a deliberate idle — one
> extra repeat per depth block is preceded by ≥ 120 s with no traffic, so rep-1 can read cold; the
> cached-prefix regime is named as such wherever block 1 is cited.
>
> **The fix's premise, measured before the re-run (2026-09-09 ~11:05Z)** — receipt
> `E:\work\battlemage\sat-l1\probes\cache-prompt-oai-verdict-20260909.json`, probe kept beside it.
> `cache_prompt` is a llama.cpp *native*-endpoint field and the harness posts to
> `/v1/chat/completions`; whether the OpenAI-compatible handler forwards it was an assumption the
> whole re-run rides on, so it was measured: two arms of four identical requests each, own nonce
> prefix, `/metrics` bracketed, co-resident with production.
>
> | arm | payload | uncached / sent | per-request `prompt_n` | prefill |
> |---|---|---:|---|---|
> | A (today's harness) | no `cache_prompt` key | 456 / 1,812 = **0.25** | 453, then **1, 1, 1** | 10.7–10.9 ms |
> | B (the fix) | `cache_prompt: false` | 1,828 / 1,828 = **1.00** | 457, 457, 457, 457 | 224.5–225.4 ms |
>
> **`cache_prompt: false` is honoured on the OAI endpoint.** It also gives the first real
> single-stream prefill figure at this depth: **457 tokens in 224.5 ms ≈ 2,035 tok/s**, dual
> layer-split, co-resident, N=1 — the regime named, not a capacity claim. Production `ff_ratecheck`
> after the probe: 104.75 tok/s, **99% of baseline, PASS**.
>
> **Prior art found 2026-09-09, and a Phase 2 risk it raised, resolved.** `E:\work\vllama` (Derek's
> own, June 2026, the stage after b70tools — .NET 9 lifecycle + OpenAI facade over `llama-server`,
> last commit `c130d76` 2026-06-17) already holds two contracts this campaign re-derived:
> - **vllama ADR-0007 Decision 1: "readiness means *can serve*, not *process up*."** A control
>   endpoint resolves the alias exactly as the proxy does and issues a real one-token generation;
>   a resident-but-wedged model fails loudly with `reason` and `remedy`. That is the same finding as
>   this campaign's "HTTP 200 ≠ serving" (the co-resident canary that held `/health` at 200 while
>   production ran at 10.3%), written three months earlier from a different failure — a judge that
>   503'd mid-run. `hearth/health/guard.py`'s rung-state gate is an independent re-derivation, and
>   the citation belongs to vllama ADR-0007.
> - **vllama ADR-0007 Decision 4** names the per-card co-residency VRAM gate as deferred, and names
>   the b70tools field for it (`per_adapter_vram.local_last_gb`) — an independent arrival at the
>   same term this card's admission gate now uses (`gpu.adapter.vram.local.bytes_committed`).
>
> Its Decision 3 also flagged a latent **`n_parallel × n_ctx` KV over-allocation** in the June build.
> Phase 2 raises `-np` to 4 and 8 at fixed `-c 131072` on a restarted production server, so whether
> `-c` is per-slot or total decides whether those restarts are routine or a large over-allocation on
> a rig whose own docs record an OOM cascade that once cost a BIOS reflash. **Settled from the
> running server's log, no restart needed** (`hearth/var/arc-serve.log`):
>
> ```
> llama_context: n_ctx = 131072   n_ctx_seq = 65536   n_seq_max = 2   kv_unified = false
> srv load_model: initializing, n_slots = 2, n_ctx_slot = 65536
> llama_kv_cache: size = 12288.00 MiB (65536 cells, 48 layers, 2/2 seqs)
> llama_kv_cache: Vulkan0 KV buffer 6400.00 MiB   Vulkan1 KV buffer 5888.00 MiB
> load_tensors:   Vulkan0 model 8975.63 MiB   Vulkan1 model 8548.79 MiB
> sched_reserve:  Vulkan0 compute 712.08 MiB    Vulkan1 compute 712.08 MiB
> ```
>
> Read at source, not relayed: `ArgVectorBuilder.cs:41` carries the comment *"llama-server's
> n_parallel=auto picks 4 and allocates 4x the KV cache (ADR-0007)"* — so on the June build KV
> **did** scale with `n_parallel`. This build does not. That is a version-dependent behaviour, which
> is exactly why the Phase 2 restart check below is a real check and not paranoia.
>
> **A lever we are not using, found in the same file.** vllama launches with `-ctk q8_0 -ctv q8_0`
> (`ArgVectorBuilder.cs:65`, `config/models.json:20,66` — `kv_type` defaults to `q8_0`), proven on
> this rig in June. Production runs **unquantized KV**: its log says `K (f16) 6144.00 MiB,
> V (f16) 6144.00 MiB`. Quantizing KV to q8_0 would cut the 12,288 MiB roughly in half and free
> **~3 GB per card** — headroom that could fund a deeper `-c` (the size axis), more slots, or a
> co-resident side model (the rotation lane's binding constraint). Recorded as a named lever with
> its numbers, **not** taken: changing production's KV type is a tenancy call and a quality
> question, and Lap 1's surface must be measured at one KV type. It belongs to part 2's residency
> work, or to a deliberate Phase 2 arm if Derek wants one.
>
> **`-c` is the total and the build divides it**: `n_ctx_slot = 65536` at `-np 2`, so P8's premise
> (64K/32K/16K per slot at `-np` 2/4/8) is the build's own arithmetic, not an assumption. The
> per-card sums — 16,088 and 15,149 MiB — reconcile with the admission gate's committed readings
> (16.03 / 14.96 GB), so that gate is now cross-checked against the server's own declared
> allocation. **Open and answerable at the first Phase 2 restart, not before:** whether total KV
> stays 12,288 MiB when `-np` rises at fixed `-c`. The restart's log lines above are captured into
> the cell receipt and compared; a KV total that scales with `-np` is a stop condition for `-np 8`.
>
> **Derived expectation, recorded before the re-run data exists:** block 1's jobs carried ~10.8 ms
> of prefill; real prefill adds ~214 ms per job to a 3.14 s job, so the re-run's jobs/hour should
> land **~5–8% below** block 1 — roughly **2,100–2,160** — with p50 near 3.35 s, decode unchanged,
> and duty still ~0 (224 ms of prefill per 3.4 s job cannot lift a card to 0.9× a prefill-burst
> reference). If the re-run instead falls far more than 8%, prefill is contending for the cards and
> the size axis matters sooner than P4 assumes; if it does not fall at all, the flag did not reach
> the load path and the gate 7 evidence must be read again.

> **RESULT — first cell on the fixed instrument, `np2-p512-c2-r6`, 2026-09-09 ~11:07Z** — receipt
> `E:\work\battlemage\sat-l1\cells\np2-p512-c2-r6\receipt.json`; runner `defa9e0`; same regime as
> block 1, now with `cache_prompt: false`. **Status: scored, all seven gates.** The derived
> expectation recorded above, before this data existed, was 2,100–2,160 jobs/hour and p50 ≈ 3.35 s.
>
> | field | block 1 (cached) | r6 (real prefill) | expectation |
> |---|---:|---:|---|
> | jobs/hour | 2,275.5 | **2,097.4** | 2,100–2,160 |
> | p50 latency | 3.14 s | **3.344 s** | ≈3.35 s |
> | p95 latency | 3.25 s | 3.648 s | — |
> | TTFT p50 | 0.043 s | **0.332 s** | — |
> | decode p50 | 64.6 | 66.1 | unchanged |
> | prefill p50 | `null` (every cell) | **1,465.0 tok/s** | — |
> | duty, both cards | 0.0 | **0.0** | ~0 |
>
> **Gate 7 passes with zero cached tokens**: 3,136 uncached against 2,640 asked for (the 496 surplus
> is `ff_cell`'s own rate probes, reported as `probe_contribution`, nothing subtracted). The prompt
> cache is defeated, and prefill is a measured number for the first time in this campaign rather
> than a `null`. It lands at **1,465 tok/s at N=2** against the **2,035 tok/s** the single-stream
> probe read — two slots prefilling together cost per-request rate, as expected; the regimes are
> named and neither is a capacity claim. TTFT rising 0.043 → 0.332 s is that same prefill becoming
> visible where it always belonged.
>
> **Gate 4, now scored where it means something.** Over the load window: **10 polls, 10.30 s, both
> slots busy 1.00, busy-slot fraction 1.00**. Over the old span: 20 polls, both slots busy 0.50. The
> dilution was almost exactly 2×, as the r4 replay predicted. **P5's `/slots` half is measurable at
> last, and at N=2 it reads 100%.**
>
> **What that already implies for the sweep.** With both slots saturated at N=2, more clients at
> `-np 2` cannot raise slot occupancy — it is pinned at 1.00. So P1 (jobs/hour flat from N=2) and P3
> (p95 growing with N) are two readings of the same fact, and the `-np 2` block's shape is close to
> determined before it is run. Meanwhile the cards sit at 73–74 W against a 160/114 W reference:
> **duty 0.0 while the server is 100% occupied** — P5's exact signature, visible at N=2 rather than
> the N≥4 it was predicted for. Not scored until the block is complete and the sweep is run.
>
> Production 99.5% before and after; guard `at_rate` both ends; symmetry 1.000; headroom 15.0 /
> 16.1 GB. Warm rep-1 105.93 against 105.61 warm (ratio 1.003) — still no idle, so P7's second half
> remains untested until the deliberate-idle repeat.

> **FINDING — the noise floor at this cell is bimodal, and the mode has a mechanism
> (2026-09-09).** Across the fixed-instrument repeats, jobs/hour splits into two values rather than
> scattering: 2,097.4 / 2,100.0 (r6, r7) against 2,203.7 (r8), a 5% step. The cheap reading is
> "noise". It is not.
>
> Every affected cell has exactly one slow round, and in that round the two requests do not run
> together. Their server-reported work says so directly: prefill runs at **1,995 tok/s — the
> single-stream rate measured earlier — instead of the 1,468 two concurrent prefills get**, and
> decode falls from 66.7 to 58.5 tok/s. The round costs ~11%; the cell ~5%.
>
> **Where the gap is, from the server's own launch records** (`hearth/var/arc-serve.log`, per-round
> interval between the two slots' `new prompt` lines, 440-token requests only):
>
> | cell | round 1 | round 2 | round 3 |
> |---|---:|---:|---:|
> | r7 | 0.2 ms | 0.2 ms | **222.0 ms** |
> | r8 | 0.2 ms | 0.2 ms | 0.1 ms |
> | r9 | 0.2 ms | 0.2 ms | **222.4 ms** |
>
> Two occurrences, **222.0 and 222.4 ms**. *(Corrected same day: a third, r6, measures **250.7 ms**,
> so "quantized" was an overstatement resting on n=2. The event is large and consistent — hundreds of
> milliseconds against a 0.2 ms norm — but not a single fixed value. See the launch-skew instrument
> result below.)* And it is **not the client**: the
> harness's own rows put the two requests' `started_at` within **0.0–12.5 ms** in every cell,
> including the affected ones. The server released the previous round's two slots within 1 ms of
> each other and then re-launched one slot 10 ms later and the other 222 ms later.
>
> **A hypothesis this refuted, recorded because it was mine.** I first supposed the campaign's own
> instrument was the co-tenant — that `ff_cell`'s single-stream rate probe or the discarded warm
> load still held a slot when the measured load began, serializing the first round. The log says
> otherwise: in both cells checked, the lone warm request *released* **24–32 ms before** the
> measured pair launched, and the pair then launched within 0.2 ms. The instrument was not
> contending with itself; the delay is inside the server.
>
> **What follows.** This is channel (b) — a submission-side gap — observed inside `llama-server`
> rather than at the client, on the very cell meant to establish the floor. It means the floor here
> is **not Gaussian and must not be averaged**: the honest statistic is how often a round is delayed,
> not the spread of cell means. Averaging would erase a real mechanism and inflate the floor that
> every later comparison is judged against. **Protocol addition (dated):** each cell records
> per-round launch skew, so every point on the surface reports whether it hit this event instead of
> having it smeared into a variance. Cause not yet named — a quantized 222 ms is a timeout or a
> periodic service, not contention — and naming it is a separate, bounded probe, not a blocker for
> the sweep.

> **RESULT — noise-floor block 2 complete, `np2-p512-c2` repeats 6–10, 2026-09-09 11:03–11:31Z** —
> five repeats on the fixed instrument; reduction
> `E:\work\battlemage\sat-l1\noise-floor\np2-p512-c2-block2-real-prefill.{json,md}`. All five
> scored, all seven gates, gate 7 with **zero cached tokens** every time. The reducer's regime
> filter keeps block 1's five cached-prefix receipts out automatically (5 included, 5 excluded).
>
> | | min | max | mean | spread | cv |
> |---|---:|---:|---:|---:|---:|
> | jobs/hour | 2,097.4 | 2,203.7 | **2,128.3** | 5.00% | 1.84% |
> | p50 latency | 3.270 | 3.344 | 3.311 s | 2.26% | 0.88% |
> | p95 latency | 3.277 | 3.648 | 3.552 s | 10.45% | 3.93% |
> | decode p50 | 66.06 | 67.49 | 66.79 tok/s | 2.14% | 0.75% |
> | both slots busy, **load window** | 1.00 | 1.00 | **1.00** | 0% | 0% |
>
> Bootstrap 95% CI on the mean **[2,102.0, 2,167.3]**. Production `at_rate` on a fresh sample after
> every repeat; duty **0.0** on both cards throughout.
>
> **P7 half 1 — repeat spread: REFUTED, and the refutation is informative.** 5.00% against the
> borrowed 1.5% pp512 floor. But the p50 spread is 2.26% and decode 2.14% while p95 spreads 10.45%:
> the variance lives entirely in the tail, and the tail is the quantized 222 ms event above. Three
> of the five repeats carry one delayed round; two do not. The floor is a **two-state distribution,
> not a scatter**, so a single spread number misrepresents it — which is why per-round launch skew
> is becoming a recorded field rather than something inferred afterwards from a log.
>
> **P7 half 2 — unwarmed rep-1: UNTESTABLE as configured, with the reason and the price.** The
> repeat was deliberately preceded by a **200 s** window with no traffic from this campaign
> (04:23:15 → 04:26:35 local). Rep-1 still read **106.71 tok/s against 106.26 warm — a ratio of
> 1.004**, the highest of its three reps. The rung had not decayed because **it is never idle**: the
> server log shows a 1-token completion arriving every **~31 s** without interruption through the
> whole window, and `hearth/var/arc-keepalive.jsonl` confirms it — 04:29:28, 04:29:59, 04:30:30,
> 04:31:01, 04:31:32, exactly 31 s apart, with an occasional deep row (108.89 tok/s at 04:31:49).
>
> That is the ADR-0043 keep-alive, and it is the same signal `hearth/health/rungstate.py` and
> `guard.py` read to produce the rung verdict that gates **every cell in this campaign**. **The
> instrument that lets the campaign gate on rung state is the same one that prevents it from
> observing idle decay.** Every "unwarmed rep-1" figure in this campaign so far therefore describes
> a rung that was never allowed to go cold, and ADR-0043's own 68/69/74/92 readings deserve
> re-reading against whether the keep-alive was running when they were taken.
>
> > **CORRECTION, same day, on Derek's recollection.** I first wrote this half up as *untestable as
> > configured*. That was wrong, and the error was mine: I identified the prober from its log
> > without looking for a way to pause it. Derek recalled setting the keep-alive up on **fx99**
> > "because it was needed to keep performance up" — which is ADR-0043's reason — and that is
> > exactly what it is. Verified: the host is **`ai-1`, 192.168.12.220**, running
> > `arc-keepalive.timer` (~30 s) and `arc-keepalive-deep.timer` (5 min), both `active`, reaching
> > OMEN's `:8082` over the LAN; passwordless `ssh` from OMEN works.
> >
> > **A pause mechanism already exists in this repo and is precedented** —
> > `campaign/ff-probes/ub_ab.py:106` (`"""Stop/start the fx99 keep-alive timers over SSH. Best
> > effort, never fatal."""`), and the same helper in `b3_topology_crossover.py`,
> > `b4_flash_coresidency.py` and `b5_dense_vs_moe.py`: `sudo systemctl {stop|start}
> > arc-keepalive.timer arc-keepalive-deep.timer`. Four sibling probes in this campaign family
> > already stop it for the duration of a measurement and start it again afterwards.
> >
> > So P7's second half is **testable**, at a stated cost: while the timers are stopped, `guard.py`'s
> > passive rung state goes stale, so gate 2 is blind for that window — which is precisely why the
> > cold reading is possible. It is also strictly smaller and more reversible than the Phase 2
> > production restarts already authorized. Scored below when run; the restore is verified by the
> > timers reading `active` again **and** by `arc-keepalive.jsonl` resuming, not by the `ssh` exit
> > code alone.
>
> **Eliminated, with evidence:** the keep-alive is *not* the cause of the 222 ms event. Its 1-token
> tasks are ~22 ms and appear at 1001.12 and 1001.43 of server uptime, while r9's delayed round is
> at 1001.19 — no keep-alive task is in flight when the delay occurs.

> **RESULT — the N=1 control, `np2-p512-c1-r1`, 2026-09-09 ~11:35Z** (repeat 1 of 3). All seven
> gates; gate 7 passes with **zero cached** tokens; duty 0.0; production `at_rate` both ends.
>
> | | N=1 (this cell) | N=2 (block 2 mean) | ratio |
> |---|---:|---:|---:|
> | jobs/hour | **1,515.0** | 2,128.3 | **1.405×** |
> | p50 / p95 latency | 2.373 / 2.387 s | 3.311 / 3.552 s | — |
> | decode p50, per request | **93.4** tok/s | 66.8 | 0.72× per request, **1.43× aggregate** |
> | prefill p50, per request | **1,991** tok/s | 1,470 | 0.74× per request, **1.48× aggregate** |
> | both slots busy, load window | **0.00** | 1.00 | — |
>
> **A cross-check worth keeping.** The cell's single-stream prefill, **1,991 tok/s**, lands within
> **2%** of the **2,035 tok/s** measured by the standalone `cache_prompt` probe hours earlier
> through a different code path (raw `/v1/chat/completions`, its own prompt, its own reduction).
> Two independent methods agreeing at 2% is the strongest evidence so far that the prefill numbers
> now mean what they say.
>
> **P2 — the N=1→2 gain, predicted 1.3×–1.8× jobs/hour: on track at 1.405×**, inside the band, but
> **not scored**: the card requires 3 repeats at N=1 and one is in hand. Both prefill and decode
> gain from the second slot in the same proportion (1.48× and 1.43×), which is why the jobs/hour
> ratio sits where it does. `both_slots_busy` reading exactly **0.00** at N=1 and **1.00** at N=2 is
> also the gate-4 instrument validating itself at the two extremes it should bracket.

> **RESULT — P7 half 2 SCORED at last, and it is refuted on the severe side (2026-09-09 ~11:45Z).**
> Receipts `E:\work\battlemage\sat-l1\probes\p7-half2-cold-rung-20260909.json` and
> `remediation-20260909.json`. Method: stop the fx99 timers, verify stopped, idle, measure, restore
> in a `finally` proven two ways — the mechanism the sibling probes already use.
>
> **The idle was real, for the first time in this campaign.** `arc-keepalive.jsonl` shows a single
> **299 s gap** (04:38:45 → 04:43:43) and nothing else touched `:8082`.
>
> | reading | tok/s | vs baseline 106.0 |
> |---|---|---:|
> | cold measure, 3 reps | 41.97 / 28.00 / 28.19 | **40% → 27%** |
> | second measure, immediately after | 30.06 / 25.81 / 26.30 | 28% → 25% |
> | after 24 sustained concurrent requests | 72.0 / 50.06 / 30.44 (spread **81.8%**) | **48%, unstable** |
> | after one restart, first warm iteration | 106.45 / 106.25 / 106.19 (spread **0.24%**) | **100%** |
>
> **P7's prediction was an unwarmed rep-1 at 65–90% of warm. Observed ~33% of baseline** — the decay
> is far *deeper* than ADR-0043's own 68/69/74/92 readings, which is consistent with those having
> been taken against a rung the keep-alive never let go fully cold. **Refuted, on the side of more
> decay, not less.**
>
> **A rule I was carrying is wrong, and this falsified it.** I had been applying "a degraded rung is
> a stop condition; restart is not the remedy — warm instead." Here **warming did not work**: 24
> sustained concurrent requests moved it 33% → 48% and left it wildly unstable (81.8% spread across
> three reps, each *lower* than the last). **One restart restored 100% on the first warm iteration
> at 0.24% spread.** The correct rule was already recorded and I had over-generalized past it: a rung
> collapsed by a real idle, with the keep-alive now pinging it, is held near 40% rather than
> recovered — *restart first, then let the keep-alive hold*. `ff_ratecheck`'s own guidance names the
> restart as the **discriminator**: cleared by one ⇒ ADR-0043 idle collapse; survives one ⇒
> INC-2026-08-30-A class. It cleared, so this is classified idle collapse.
>
> **A defect in my own probe, recorded.** Its scoring compared cold rep-1 against a "warm" reference
> measured six requests later — which was *itself still collapsed* — and printed "no decay
> observed". A wrong verdict from a right measurement. Any cold-rung probe must reference a
> baseline established **before** the idle, never a recovery sample taken after it.
>
> **Cost, stated plainly.** I degraded production for roughly 7 minutes (≈04:39–04:46 local) to run
> this. **No real traffic was affected** — the gateway ledgers carry no dispatch in that window —
> and the keep-alive logged two `ok:false` rows during the restart before recovering. The intact
> restore was verified by `systemctl is-active` reading `active/active`, a fresh keep-alive row, and
> production measuring 106.30 tok/s.

> **The launch-skew instrument, landed and reading (merged `9f9fa23`, 291 tests).** Per-round
> admission skew is now a receipt field on every cell, with the reducer counting how many repeats
> carried a delayed round. Two of my own claims are corrected by it:
> - **Not quantized.** A third instance (r6) measures **250.7 ms**, against 222.0 and 222.4. The
>   event is large and consistent — hundreds of milliseconds against a 0.2 ms norm — but it is not a
>   single fixed value, and "quantized" rested on n=2.
> - **Four of five, not three.** r10 had never been analysed. Per-round TTFT asymmetry in the
>   harness rows — a route independent of the server log — gives r6 [267, 13, 1], r7 [0, 0, 239],
>   r8 [1, 1, 1], r9 [0, 0, 237], r10 [242, 12, 1] ms. Only **r8** is clean.
>
> The discriminator is crisp and needs no log at all: **every delayed cell peaks at the
> single-stream prefill rate (1,982–1,995 tok/s) with minimum decode 58.5–59.4**, while clean r8
> peaks at 1,471 with minimum decode 67.3. The four delayed cells land at 2,097–2,125 jobs/hour
> against r8's 2,203.7.
>
> ⚠ **Operational limit found while verifying: `llama-server` truncates `hearth/var/arc-serve.log`
> on every start** — the restart above took it from 78 MB to 569 KB, erasing r6–r10's launch
> records. The instrument therefore only ever sees the current epoch. That is sufficient (a cell
> always runs after any restart) but it means **Phase 2's `-np` restarts destroy prior skew
> evidence**, and the log-free TTFT-asymmetry route above is what survives.
>
> **RESULT — N=1 reproduces to 0.08% *across a production restart*, 2026-09-09 ~11:55Z.**
> `np2-p512-c1-r2` on the post-restart epoch (`2026-09-09T04:45:43`, runner `9f9fa23`) against r1 on
> the old one:
>
> | | r1 (old epoch) | r2 (new epoch) | spread |
> |---|---:|---:|---:|
> | jobs/hour | 1,515.0 | 1,516.2 | **0.08%** |
> | p50 latency | 2.373 s | 2.369 s | 0.17% |
> | decode p50 | 93.4 | 93.6 | 0.21% |
> | prefill p50 | 1,991 | 1,990 | 0.05% |
>
> Two things follow. First, the restart did not move the machine: an epoch change that leaves every
> figure inside 0.21% is evidence for comparability across it, not merely an assumption of it.
> Second, and more useful: **the N=1 cell reproduces at 0.08% while the N=2 cell spreads 5.00%.**
> The variance at N=2 is therefore not general measurement noise — it is specific to admitting a
> second concurrent request, which is exactly what the delayed-round event is. `launch_skew` says so
> itself on this cell, refusing to report a number with the reason that a round of one request has
> no skew to measure and every value would be zero by construction.

> **RESULT — the N=1 cell complete (3 repeats), P2 SCORED SUPPORTED, and the kill gate resolved.**
> `np2-p512-c1-r{1,2,3}`, 2026-09-09 ~12:00Z. Mean **1,514.1 jobs/hour**, CI **[1,511.0, 1,516.2]**,
> spread **0.34%**. All three scored, gate 7 zero cached each, duty 0.0, production `at_rate`.
>
> **P2 — "the N=1→2 gain at 512 is 1.3×–1.8× jobs/hour" (prior ~70%): SUPPORTED.**
> 2,128.3 / 1,514.1 = **1.406×**, with the repeats the card asks for on both sides (3 at N=1, 5 at
> N=2). Decomposed: the second slot buys **1.43×** aggregate decode and **1.48×** aggregate prefill,
> at a per-request cost of 0.72× and 0.74× respectively. First prediction on this card scored.
>
> **The kill gate is resolved, and it does not fire.** Its text: *"repeats drift beyond the noise
> floor at the N=2 / 512 cell after warm-up → the warm gate is not holding; no reading from the
> block is valid; fix the gate, then resume."* The N=1 cell is the control that discriminates
> those two explanations, and it is unambiguous:
>
> | cell | spread | P7 half 1 |
> |---|---:|---|
> | N=1, 512 | **0.34%** | supported (inside the 1.5% floor) |
> | N=2, 512 | **5.00%** | refuted |
>
> Same rung, same warm procedure, same instrument, same day — one client is stable to a third of a
> percent. **The warm gate is holding.** The drift at N=2 is therefore not a gate failure but a
> property of the system under concurrency, so the block's readings stand and the run continues.
> What the card should have said, and now does, is that a drift at the floor cell has two possible
> causes and needs the N=1 control to tell them apart.

> **RESULT — N=4, repeat 1 of 5 (`np2-p512-c4-r1`), 2026-09-09 ~12:05Z.** The cell where P1 and P3
> stop being predictions. All seven gates; gate 7 zero cached; duty 0.0.
>
> | | N=1 (3 reps) | N=2 (5 reps) | **N=4 (rep 1)** |
> |---|---:|---:|---:|
> | jobs/hour | 1,514.1 | 2,128.3 | **2,124.2** |
> | p50 latency | 2.373 s | 3.311 s | **6.553 s** |
> | p95 latency | 2.387 s | 3.552 s | **7.214 s** |
> | TTFT p50 | 0.243 s | 0.329 s | **3.577 s** |
> | decode p50 | 93.4 | 66.8 | 66.6 |
> | prefill p50 | 1,991 | 1,470 | 1,473 |
> | both slots busy, load window | 0.00 | 1.00 | **1.00** |
> | board duty, both cards | 0.0 | 0.0 | **0.0** |
>
> **Doubling the clients moved throughput by 0.2% and doubled latency.** Jobs/hour 2,124.2 against
> 2,128.3 — inside the N=2 cell's own 5% floor and far inside P1's ±10%. p50 rose 1.98×, p95 2.03×,
> against a client count that rose 2.00×. That is the waiting term, measured directly and almost
> exactly linear, and TTFT carries it: **0.329 s → 3.577 s, a 10.9× rise** in time spent before the
> first token, while decode and prefill *per request* are unchanged to within 0.3%.
>
> - **P1** ("flat within ±10% from N=2 through N=24"): **on track**, 0.2% at the first test point.
> - **P3** ("p95 exceeds 1.5× the N=2 baseline by N=4, then ~linear in N/2"): **on track and
>   quantitatively so** — 2.03× at N=4 where 1.5× was the bar, and the linear form holds at the only
>   ratio available so far.
> - **P5's signature holds at N=4**: both slots busy 100% of the load window while both boards sit at
>   **duty 0.0** against a 160/114 W reference. The server is saturated; the cards are not.
>
> Neither is scored yet — the card asks 5 repeats here and this is one.
>
> **A refinement to the delayed-round finding, from the skew instrument's first multi-round cell.**
> N=4 gives 6 rounds instead of 3, and **2 were delayed** (245.6 ms and 236.2 ms) against 1 of 3 at
> N=2. Per round the rate is unchanged (~1 in 3), but the load window also doubled, 10.3 s → 20.3 s.
> **Both cells are consistent with roughly one event per 10 seconds of load rather than one per three
> rounds** — a time-periodic source, not a per-request one. Two points is a hypothesis, not a
> finding; the N=8/16/24 cells lengthen the window further and will separate the two readings
> cleanly.

> **FINDING — prefill runs in three separable modes, and which mode a cell falls into is what
> moves its throughput (2026-09-09 ~12:15Z).** Each request's own `prompt_tokens_per_s` is a
> log-free classifier: it needs no server log, no uptime anchor, and survives the truncation a
> restart causes. Across all ten real-prefill cells the per-request values do not scatter — they
> land in three tight bands with empty space between them:
>
> | mode | per-request prefill | interpretation |
> |---|---:|---|
> | **alone** | 1,982–1,995 tok/s | nothing else on the card. Every N=1 request, and the first of a pair. |
> | **beside a decode** | 1,840–1,890 tok/s | this slot prefills while the other decodes |
> | **batched** | 1,460–1,476 tok/s | both slots prefill in the same pass — slower each, **~2,940 aggregate** |
>
> **The association across cells is clean:**
>
> | cell | jobs/hour | requests not batched | median prefill | median decode |
> |---|---:|---:|---:|---:|
> | N=1 ×3 | 1,514 | 100% (by construction) | 1,990 | 93.4 |
> | N=2 r8 | **2,203.7** | **0%** | 1,469 | 67.5 |
> | N=2 r6, r7, r9, r10 | 2,097–2,125 | 33% | 1,466–1,475 | 66.1–67.1 |
> | N=4 r1 | 2,124.2 | 33% | 1,474 | 66.7 |
> | N=4 r2 | **1,998.5** | **100%** | 1,861 | 59.3 |
>
> **The cell that batched everything is the fastest; the cell that batched nothing is the slowest.**
> The single clean N=2 repeat and the fully-staggered N=4 repeat are the two extremes, 10% apart.
>
> **Mechanism, proposed and consistent with all ten cells.** Two requests admitted together prefill
> together, decode together, finish together, and the next pair arrives together — a synchronised
> loop that keeps prefill batched. A delayed admission (the 222–250 ms event) knocks the pair out of
> phase; thereafter each prefill lands beside the other slot's decode instead of beside its prefill,
> and **the offset persists** because nothing re-synchronises them. That is why one delayed round
> costs a whole cell ~5% rather than one round ~11%: it is not the delay that is expensive, it is
> the phase change the delay causes.
>
> This is a hypothesis with strong support, not a proven mechanism: the modes and the association
> are measured, the persistence is inferred. It is also directly actionable if it holds — keeping
> arrivals in phase is a submission-side lever worth ~5–10% here, which is Lap 2's territory, and it
> predicts that batched-prefill fraction, not client count, is the variable to control.

> **QUANTIFIED — batching is worth 10.1%, and it, not the client count, is what varies
> (2026-09-09 ~12:25Z).** Reducing all twelve real-prefill cells to one number each — the fraction
> of a cell's requests that shared a prefill pass — against their jobs/hour:
>
> | batched | cells | jobs/hour mean | range | which cells |
> |---:|---:|---:|---|---|
> | **100%** | 2 | **2,199.5** | 2,195.3 – 2,203.7 | N=2 r8, **N=4 r3** |
> | **67%** | 6 | **2,118.4** | 2,097.4 – 2,148.6 | N=2 r6/r7/r9/r10, **N=4 r1/r4** |
> | **0%** | 1 | **1,998.5** | — | N=4 r2 |
>
> **Fully batched against fully unbatched: 2,199.5 vs 1,998.5 — batching is worth 10.1%.**
>
> The decisive detail is *which* cells share a row. The 100% group holds one N=2 cell and one N=4
> cell, 0.4% apart. The 67% group holds four N=2 cells and two N=4 cells, all inside 2.4%. **Client
> count does not separate the groups; batching mode does.** Decode confirms it independently: 66.1–
> 67.8 tok/s in every batched-or-partly-batched cell regardless of N, and 59.2 in the one unbatched
> cell.
>
> This is a better account of the surface than P1's own wording. Jobs/hour *is* flat in N — and the
> ±5–10% that would otherwise be written off as noise is a **discrete mode variable**, not scatter.
> ⚠ The 0% group is a single cell; the 100% group is two. The ordering is monotone and the mechanism
> is physical, but the end points are thin and the remaining N=8/16/24 blocks are what thicken them.
> Reducer: `E:\work\battlemage\sat-l1\probes\solo_fraction.py`.

> **RESULT — the N=4 cell complete (5 repeats), 2026-09-09 ~12:30Z.** Mean **2,127.1 jobs/hour**,
> CI [2,058.5, 2,175.8]. All five scored, gate 7 zero cached each, both slots busy **1.00** of the
> load window in all five, duty **0.0** on both cards throughout.
>
> | | N=2 (5 reps) | N=4 (5 reps) | change |
> |---|---:|---:|---:|
> | jobs/hour | 2,128.3 | **2,127.1** | **−0.06%** |
> | p95 latency | 3.552 s | **7.006 s** | **×1.97** |
> | TTFT p50 | 0.329 s | 3.632 s | ×11.0 |
> | both slots busy | 1.00 | 1.00 | — |
> | duty, both cards | 0.0 | 0.0 | — |
>
> **Doubling the clients changed throughput by six hundredths of a percent and almost exactly
> doubled p95.** Five repeats a side.
>
> - **P1** ("jobs/hour flat within ±10% from N=2 through N=24"): **holding, and far tighter than the
>   band** — 0.06% at the first doubling. Not scored until N=24.
> - **P3** ("p95 exceeds **1.5×** the N=2 baseline **by N=4**, then ~linear in N/2"): its first
>   clause is **SUPPORTED with the required repeats** — 1.97× where 1.5× was the bar. The linear
>   form also holds exactly at this point (N/2 = 2, ratio 1.97), but "thereafter" needs N=8/16/24
>   and stays open.
>
> Note the two spreads are not the same kind of number: jobs/hour across the *cell means* moves
> 0.06% between N=2 and N=4, while *within* the N=4 cell it spreads 9.25% — because the repeats
> land in different batching modes. The mode is the variance; the client count is not.
>
> **N=4's five repeats, by mode** (adding r5 at 100%): 100% → 2,195.3 and 2,168.8; 67% → 2,124.2 and
> 2,148.6; 0% → 1,998.5. Monotone again, within a single cell this time.

## Analysis plan

- Per cell: jobs/hour, p50/p95/p99 latency and TTFT (nearest-rank, as the harness computes them),
  slot-busy fraction, duty cycle or `null`, depth-0 fraction or `null`, budget headroom, guard verdict.
- Noise floor: the N=2 / 512 cell's ≥5 repeats; a between-cell difference smaller than that floor is
  "within noise", never a finding. CI: bootstrap over requests for latency, over repeats for jobs/hour.
- Binding channel per region by the signature table in the program plan (slots busy + duty low →
  (a); duty low + depth-0 high with work queued → (b); jobs/hour collapses with N → (d); repeats drift
  → (e), and the reading is invalid).
- Every number carries its regime: model, depth, N, `-np`, placement. The claim register is updated
  for any figure the surface corrects.

## CLOSING BLOCK — Lap 1 at `-np 2` is closed, 2026-09-09 ~12:40Z

Derek stopped this lap: *"it feels like we're performance testing now instead of R&D verification
that helps us plan how we're going to increase local AI lab throughput."* He is right, and the
card's own numbers say so. **What is scored, what is not, and why — nothing is quietly dropped.**

| prediction | verdict | evidence |
|---|---|---|
| **P1** flat within ±10%, N=2→24 | **supported through N=8** | 2,128.3 / 2,127.1 / 2,163.6 jobs/h — ±1.7% |
| **P2** N=1→2 gain of 1.3–1.8× | **supported** | 1.406× (3 repeats at N=1, 5 at N=2) |
| **P3** p95 > 1.5× by N=4, then linear in N/2 | **supported** | 1.97× at N=4; linear across three doublings — 3.552 → 7.006 → 13.684 s |
| **P5** *the server saturates, the cards do not* | **supported at every N** | both slots busy **1.00**, duty **0.0**, boards ~73 W vs their own 160/114 W reference |
| **P7** half 1, repeat spread vs the FF6 floor | **refuted, informatively** | 5.00% vs 1.5%; the variance is a discrete batching mode, not scatter |
| **P7** half 2, unwarmed rep-1 at 65–90% of warm | **refuted, on the severe side** | ~33% of baseline after a verified 299 s idle |
| **P4** 32K ≤ 1/50 of 512 | **unrun** | needs the 32K regime, which now gets measured on the deep card where it matters |
| **P6** SLO knee at `-np 8` | **unrun — and reinstated** | see the `-np` pre-registration below |
| **P8** 32K inadmissible at `-np 8` | **unrun — and reinstated** | same |

**Cancelled with cause:** N=16 and N=24 at 512, and the dual-split 8K/32K depth blocks. At `-np 2`
the server admits two requests; every additional client queues, which P3 already measures exactly.
Another ~40 minutes of machine time would re-confirm flatness.

**The finding that ends the lap.** Across N=1, 2, 4 and 8 the two boards never left ~73 W against a
frozen 160 W / 114 W prefill reference — **duty 0.0 at every point**, while `/slots` showed both
slots busy 100% of every load window. The server is fully occupied and the hardware is at roughly
half the power it demonstrably draws. That is channel **(a), intake starvation by configuration**,
and it means the lever is not more clients, and not a feeder: it is **what runs on the cards**.

---

## PRE-REGISTRATION — the `-np` sweep (Lap 1B), committed before any data

**Why this is not more performance testing.** `-np` is named in this card's own measurement
protocol as *"the one production knob that caps concurrent admission, never swept server-side"*, and
it was never swept. Worse, the axis interacts with a patch this lab authored: **PR 27652**
(`ggml-org/llama.cpp`, ours, open) replaces Vulkan's hardcoded `mul_mat_vec_max_cols = 8` with the
runtime override `GGML_VK_MMV_MAX_COLS`. Past that width the decode dispatch leaves the mul-mat-vec
path for matmul, measured on a B70 at **40.7 s/pass against MMV's 3.1–5.8 s** — a cliff.

Verified at source 2026-09-09: **production runs the patched binary**
(`E:\work\llamacpp-knee\build\bin\llama-server.exe`) and `fleet/arcserve/serve-arc.cmd:34` already
exports **`GGML_VK_MMV_MAX_COLS=16`**, inherited by llama-swap and every server it spawns. **So the
decode batch may be 16 columns wide, and production runs `-np 2`.** Eight-fold headroom, bought and
never used. The whole N sweep above varied *clients*, which only queue, instead of *slots*, which
are what widen the batch.

**Matrix.** `-np` ∈ {4, 8, 16} at fixed `-c 131072` (per-slot context 32,768 / 16,384 / 8,192),
prompt 512, clients matched to slots (N = `np` and N = 2·`np`), 3 repeats per cell. `-np 2` is the
control and is already measured.

| # | prediction | prior |
|---|---|---|
| **Q1** | Board duty cycle **rises above 0.0** by `-np 8` and is **≥ 0.25** on at least one card at `-np 16`. It has been exactly 0.0 in all 16 cells so far. | ~70% |
| **Q2** | Aggregate jobs/hour at the best `-np` is **≥ 1.5×** the `-np 2` ceiling of ~2,130. | ~65% |
| **Q3** | Per-request decode falls with `np` while aggregate decode rises — the batching trade, not a regression. | ~85% |
| **Q4** | At `-np 16`, a control repeat with `GGML_VK_MMV_MAX_COLS=8` is **materially worse** than the same cell at 16 — the cliff is real under serving load, not just llama-bench. | ~75% |
| **Q5** | P8 restated: at `-np 16` a slot holds 8,192 tokens, so **32K prompts are refused or truncated**. Near-deterministic; recorded so it is a protocol fact. | ~90% |

**Q4 is the one that pays twice.** PR 27652's maintainer named per-vendor measurement as the
blocker, and nothing in the knee campaign measured the knob under **llama-server concurrency** — it
was llama-bench and frame pacing. This would be its first serving-load evidence.

**Gates:** the same seven, unchanged. Plus, at every restart, capture the server's own
`llama_kv_cache: size` and per-card buffer lines — **a KV total that scales with `np` is a stop
condition** (the June build multiplied KV by `n_parallel`; this one divides `-c`, and that must be
re-confirmed per epoch, not assumed).

**Kill gate:** if duty is still 0.0 at `-np 16` with the batch at the window's edge, then this
configuration cannot load the cards at all, `-np` is exhausted as a lever, and the partition is the
only remaining move — which is a result, not a failure.

### Lap 1B operational notes, dated 2026-09-09

> **⚠ `ArcServeRestart` is STOP-ONLY. It took production down and left it down.** The `--set-np 4`
> sequence edited the yaml (token-exact, backed up), issued `schtasks /Run /TN ArcServeRestart`, and
> then failed at the ready marker. Checked immediately: **no listener on 8081 or 8082**, the serve
> log's last write predating the restart, and the yaml reading `-np 4`. `serve-arc.cmd`'s own header
> states the procedure — *"ArcServeRestart (stop-only), delete the sentinel, then `schtasks /Run /TN
> ArcServeBoot`"* — and following it brought llama-swap back immediately and production ~3 minutes
> later (`model loaded`, `listening on http://127.0.0.1:8082`, llama-swap's own health check passed).
> An earlier restart in this session appeared to self-recover, which is consistent with
> `ArcServeBoot`'s `RestartCount 3` firing rather than with `ArcServeRestart` restarting anything.
> **`sat_cell_runner.set_np()` assumes the restart brings production back; it does not, and the
> runner must issue `ArcServeBoot` after the stop.** Fix before the next `-np` change.
>
> **✅ The KV stop condition does NOT fire — measured, not assumed.** At `-np 4` the new epoch's own
> load report reads `llama_kv_cache: size = 12288.00 MiB (32768 cells, 48 layers, 4/4 seqs)`,
> `Vulkan0 6400.00 + Vulkan1 5888.00 MiB`, `n_slots = 4, n_ctx_slot = 32768`. **Byte-identical KV
> total to `-np 2`.** So `-c` is the total and the build divides it; KV does not scale with `np` on
> this build, and the June-build behaviour vllama recorded does not apply here. Compute buffers
> actually *fell*, 712.08 → 456.08 MiB per card. Re-baselined: **106.25 tok/s, spread 0.07%** — the
> same single-stream rate as `-np 2`, so the regime change costs nothing at N=1.

> **RESULT — the throttle mattered. `np4-p512-c4-r1`, 2026-09-09 ~13:04Z.** First cell of the new
> epoch, all seven gates, gate 7 zero cached, no delayed rounds.
>
> | | `-np 2` (best of 16 cells) | **`-np 4`, N=4** |
> |---|---:|---:|
> | jobs/hour | 2,163.6 | **2,918.1** — **×1.37** |
> | p95 latency | 13.684 s (at N=8) | **4.944 s** |
> | decode per request | 66.8 | 44.3 |
> | **aggregate decode** | 133.6 | **177.2** — ×1.33 |
> | prefill per request | 1,470 | 1,050 |
> | slots busy, load window | 1.00 (of 2) | **1.00 (of 4)** |
> | board power, p50 | ~73 / ~73 W | **99.01 / 74.35 W** |
> | board duty | 0.0 | 0.0 |
>
> **Adding slots did what adding clients could not.** Throughput rose 37% against the ceiling that
> sixteen cells of client-sweeping could not move by more than 1.7%, and p95 *fell* — 4.94 s at four
> clients on four slots against 13.68 s at eight clients on two.
>
> - **Q3 supported**: per-request decode falls 66.8 → 44.3 while aggregate rises 133.6 → 177.2.
>   Exactly the batching trade, not a regression.
> - **Q2 on track, not yet met**: 1.37× against a predicted ≥1.5×. This is `-np 4` at its matched
>   client count; `-np 8` and `-np 16` are where the prediction is actually tested.
> - **Q1 not yet**: duty still reads 0.0 — but **board power moved 73 → 99 W on one card, +36%**.
>   ⚠ **This exposes a limitation in the duty metric itself**: its threshold is 90% of a *prefill
>   burst* reference (143.9 W), which is the wrong yardstick for a decode-heavy cell. Power is the
>   honest signal here and it is rising; duty may stay 0.0 while the cards genuinely load. Raw watts
>   are now reported beside duty rather than behind it.
> - **New observation:** power is **asymmetric for the first time** — 99.01 vs 74.35 W, where every
>   `-np 2` cell read ~73/73. The wider batch is landing unevenly across the pair.
>
> **Thermal headroom, checked before pushing further.** The corpus records the replica-per-card
> experiment being **quarantined at 96 °C on VRAM of `0000:04:00.0`** at only p512-c4, and the 131K
> context tier failing the same way. Every receipt in this campaign already carried
> `gpu.temperature_c` and `vram.temperature_c` and I had not been reading them. Measured across the
> `-np 2` cells: VRAM p50 **58–64 °C**, max **68 °C** at N=8; GPU max **65 °C**. The hotter card is
> consistently `adapter_00016def` = **`0000:04:00.0`** — the same card the corpus flagged, by 2–4 °C.
> **~28 °C of margin to the abort point**, rising ~1 °C per doubling of load. Safe to continue, and
> a thermal gate is now warranted before the partition step, which *is* the configuration that hit
> 96 °C.

> **THERMAL PROTOCOL — set 2026-09-09 on Derek's call, and the ambient confound he named.**
>
> Derek, on the 96 °C abort precedent: *"the last time i tested this it was one the hotter weeks of
> late summer and now it's approaching fall, so the natural cooling of the weather is assisting in
> the thermals also."* **Measurement confirms it, and more strongly than expected.** Splitting each
> capture into its idle leading quarter and its busy window:
>
> | cell | card | idle | busy p90 | max | **Δ** |
> |---|---|---:|---:|---:|---:|
> | `np2-p512-c1-r1` | `0000:04:00.0` | 62 | 64 | 64 | **2** |
> | `np2-p512-c8-r1` | `0000:04:00.0` | 62 | 66 | 68 | **4** |
> | `np4-p512-c4-r1` | `0000:04:00.0` | 56 | 58 | 60 | **2** |
> | `np4-p512-c8-r1` | `0000:04:00.0` | 60 | 64 | 66 | **4** |
>
> **The idle baseline swings 56–62 °C — 6 °C — across four cells run inside ninety minutes, while
> the workload adds only 0–4 °C.** Absolute temperature is dominated by ambient and thermal soak,
> not by what we run. So the comparison "68 °C now versus 96 °C then" is **not like-for-like**: most
> of that gap is the season, exactly as Derek said. A gate calibrated on today's absolute readings
> would be optimistic by summer, and a delta-only gate would miss a genuinely hot room. Both get
> recorded.
>
> **The limit is 95 °C, Derek's call:** *"95 Is a good limit, if we hit that, we back off. i've
> cooked these cards plenty of time, they'll be fine."* Gate 8 aborts at 95 °C on either GPU or VRAM
> and warns at 88 °C, with the attribution carried in the code so it is not quietly "tightened"
> later. A thermal failure **keeps** the row and excludes it from the surface, like `over_admitted`.
>
> **Four corrections to the above, from building the gate (merged 2026-09-09, 338 tests):**
> 1. **The temperature counters are emitted ON CHANGE, not per tick** — 7–31 readings per counter per
>    card across a 175–260 s capture, irregularly spaced. Percentiles are therefore over *readings*,
>    not time-weighted, and a counter can have **zero** in-window readings (one card's VRAM has 7
>    samples, none inside the load window) — those percentiles are `null` with a stated reason while
>    `max_c` still evaluates the abort. `max_c` is deliberately **capture-wide** so a spike just
>    outside the load window cannot slip past the limit.
> 2. **My "0–4 °C rise" was a VRAM figure. GPU deltas reach 7–8 °C.** Still far under the 15 °C delta
>    warn, but the number I quoted was the coolest of the two counters.
> 3. **The "idle" baseline is not a cold idle.** The runner warms to flatness and runs a discarded
>    load *before* the b70 stream starts, so the pre-window readings are a post-warm cooldown —
>    temperature falls monotonically through that segment in all four captures. The reported rise is
>    conservative as a result.
> 4. **A sustained reference already exists on disk, and gate 5 did not know it.** Added 2026-09-09
>    after claim register #30 was corrected. Gate 5 above (and #30) asserted that no sustained-load
>    capture existed on this box, "the longest in the corpus ~260 ticks". That was true of *this
>    campaign's* corpus (`sat-l1` + `ff-probes`; longest 296) and false of the box:
>    `E:\work\battlemage\burnin-2026-08\results\soak1-b70tools\events.jsonl` holds **6.22 h** of B70
>    telemetry, reducible today with `sat_reference_capture.py --reduce ... --counter gpu`. Its
>    sustained per-interval p50 is **160.48 W** (bus 9) / **145.15 W** (bus 4) against gate 5's own
>    frozen burst p50s of 143.93 / 102.64 — the reference is **11.5%** and **41.4%** low. This
>    corroborates the limitation already recorded further down this file ("the threshold is 90% of a
>    *prefill burst* reference, which is the wrong yardstick"), and it generalises it: the yardstick
>    is soft against sustained load, not only against decode-heavy cells.
>    ⚠ **This does NOT re-point the freeze.** soak1 is a triple-load burn-in from 2026-08-20 at a
>    different `-np`, pre-duct — a different instrument and load shape, so re-freezing duty against
>    it would trip R8. The frozen compute control at `ref-20260909T085437Z` stands as acked; soak1 is
>    a comparison point, not a replacement denominator. Note also that this correction's own
>    on-change caveat (1, above) applies to it: those are percentiles over readings.
>
> ⚠ **What this does not license:** extrapolating the partition's thermals from these cells. The
> quarantined experiment was replica-per-card — a whole model on each card, a different and heavier
> load shape than anything measured here. Its Δ is unknown. The gate must be live during that run,
> not inferred beforehand.

> **RESULT — the `-np` axis through 8 slots, 2026-09-09 ~13:25Z. Q2 met; Q1 met at `-np 8`.**
> Each row is the cell at matched clients (N = `np`); all scored, gate 7 zero cached throughout.
>
| `-np` | tok/slot | jobs/hour | vs `-np 2` | decode/req | **aggregate decode** | p95 | board p50 / max W | **duty** |
> |---:|---:|---:|---:|---:|---:|---:|---|---|
> | 2 | 65,536 | 2,128 | — | 66.8 | 133.6 | 3.55 s | 73 / 82 | 0.0 / 0.0 |
> | 4 | 32,768 | **2,769** (3 reps) | ×1.30 | 44.3 | 177.2 | 4.94 s | 77–99 / **140** | 0.0 / **0.062** |
> | 8 | 16,384 | **3,358** | **×1.58** | 25.4 | **203.2** | 9.43 s | 119 / **171** | **0.168 / 0.126** |
>
> - **Q2 — "aggregate jobs/hour at the best `-np` ≥ 1.5× the ~2,130 ceiling": MET at `-np 8`**
>   (×1.58). The knob that was never swept is worth more than half again the throughput that sixteen
>   cells of client-sweeping could not move by 1.7%.
> - **Q1 — "duty rises above 0.0 by `-np 8`": MET.** 0.168 and 0.126, both cards, first time in the
>   campaign. The `≥0.25 at -np 16` half is still open.
> - **Q3 — supported and monotone**: per-request decode 66.8 → 44.3 → 25.4 while aggregate rises
>   133.6 → 177.2 → 203.2. The batching trade, exactly.
> - **Peak power now exceeds the reference.** 171.4 W max on `0000:09:00.0` against the frozen
>   prefill-burst reference of **159.92 W**. The cards are, at peak, working harder than the burst
>   the whole duty metric is calibrated against — while `-np 2` never left ~73 W.
> - **The delayed-round event scales with `np` too**: ~222 ms at `-np 2`, 434–589 at `-np 4`,
>   **674–943 at `-np 8`**, and at `-np 8` all three rounds were delayed. It is not disappearing as
>   the batch widens; it is growing.
>
> **KV still does not scale.** At `-np 8`: `size = 12288.00 MiB (16384 cells, 48 layers, 8/8 seqs)` —
> byte-identical to `-np 2` and `-np 4`. Compute buffers keep shrinking as slots rise, 712 → 456 →
> 328 MiB per card. The stop condition has now been checked at every value and never fired.
>
> **The restart sequence, corrected and used.** `ArcServeRestart` (stop) → confirm nothing listens on
> 8082 → `ArcServeBoot` → the real ready marker → `/health` **and** a real completion → re-baseline.
> Ran clean at the `-np 8` transition; baseline 105.25 tok/s. ⚠ Gate 2 reads `stale` for roughly six
> minutes after any restart until a deep keep-alive sample lands — normal, not a fault.

> **RESULT — the cards are finally loaded. `np8-p512-c16-r1`, 2026-09-09 ~13:35Z.**
>
> | | `-np 8`, N=8 | `-np 8`, **N=16** |
> |---|---:|---:|
> | jobs/hour | 3,358.2 | **3,407.2** (+1.5%) |
> | p95 latency | 9.43 s | 17.14 s |
> | board p50 W, `0000:09:00.0` | 118.7 | **154.68** |
> | board max W | 171.4 | **181.19** |
> | **duty, `0000:09:00.0`** | 0.168 | **0.632** |
> | duty, `0000:04:00.0` | 0.126 | 0.123 |
>
> **Duty 0.63 against a 159.92 W reference, sustained at 154.7 W p50.** That is the closest this
> campaign has come to the thing it set out to find — a card actually kept busy — and it took the
> slot count, not the client count, to get there.
>
> **A correction to my own summary of an hour ago.** I said "throughput is set by slots, latency by
> clients". The first half holds — 3,358 → 3,407 is 1.5%. The second half was incomplete:
> **clients past the slot count barely move throughput but massively move *utilisation*** (duty
> 0.168 → 0.632, p50 power +30%). Eight clients on eight slots leaves gaps between rounds; sixteen
> keeps the slots fed. Both halves matter, and I had only measured one.
>
> ⚠ **Efficiency got worse, not better.** Same throughput for 30% more power. The delayed-round
> count doubled with the client count (3 → 6, skews 449–905 ms). The extra clients are buying
> occupancy, not work.
>
> ⚠ **The split is badly unbalanced, and it is an argument for the partition.** Card `0000:09:00.0`
> sits at 154.7 W p50 / 0.632 duty while `0000:04:00.0` sits at 81.5 W / 0.123 — under `-sm layer
> -ts 1,1`, which is supposed to divide the model evenly. One card is doing roughly twice the work.
> **A partition would give each card its own whole model and its own queue**, which is exactly the
> imbalance this measurement exposes. Recorded as an observation from one cell, not a law.

### The sweep stops at `-np 16` — Derek's prior result, 2026-09-09

> *"yeah i tested those step matrix already 16 is best for this hardware for most our use cases, can
> you move it yes, does that really matter after 16, not unless we're running really small model
> weights"*
>
> **The `-np` axis ends at 16.** Derek has already swept the step matrix on this hardware; 16 is the
> operating point for this model class, and values beyond it only matter for much smaller weights.
> So `-np 24/32` are **not run** — not cancelled for time, but because the answer is already known
> from work that predates this card, and re-deriving it would be exactly the performance-testing the
> pivot was called on.
>
> **This reframes what Lap 1B measured.** It did not discover the operating point; it measured *the
> distance production was sitting from a known one* — `-np 2` against a settled 16 — and priced that
> gap at **×1.58 by `-np 8` alone**, with duty going 0.0 → 0.63. The finding is not "16 is good", it
> is **"production had been running at 2 for months while the answer was 16"**, and nothing in the
> lab's instrumentation surfaced it. `-np 16` is now live.
>
> **It also explains the `GGML_VK_MMV_MAX_COLS=16` in `serve-arc.cmd`.** The MMV window and the slot
> count are the same number, and that is not a coincidence — the widened window is what makes a
> 16-wide decode batch viable at all. **Which makes the one remaining `-np 16` measurement the
> sharpest version of Q4 available**: at 16 slots with the window at its stock 8, the decode batch
> exceeds the window and must fall onto the matmul path. That is the cliff PR 27652 exists to let
> users avoid, measured under real serving load for the first time.

> **RESULT — `-np 16` regresses, and Q4's control lands. 2026-09-09 ~13:55Z.**
>
> | cell | MMV window | jobs/hour | p50 | decode/req | admission skew |
> |---|---:|---:|---:|---:|---|
> | `np8-p512-c8-r1` | 16 | **3,358.2** | 8.42 s | 25.4 | 674–943 ms |
> | `np8-p512-c16-r1` | 16 | **3,407.2** | 16.86 s | 25.2 | 449–905 ms |
> | `np16-p512-c16-r1` | 16 | 2,206.9 | 25.57 s | 8.1 | **2,402–2,932 ms** |
> | `np16-p512-c16-r2` | 16 | 2,439.5 | 23.39 s | 8.8 | **1,865–1,951 ms** |
> | `np16-p512-c16-r3` | **8 (stock)** | **1,953.6** | 29.28 s | 7.1 | anchor ambiguous |
>
> **`-np 16` is ~32% worse than `-np 8` on this workload** (2,323 mean vs 3,383). Per-request decode
> collapses 25 → 8 tok/s and aggregate decode falls with it, 202 → ~135. The admission event that has
> grown all afternoon reaches **2–3 seconds**. Board power goes spiky rather than sustained — peaks of
> 166–183 W over a p50 of only 66–73 W. The engine is misfiring, not making power.
>
> ⚠ **This does not contradict Derek's prior** (*"16 is best for this hardware for most our use
> cases"*) — it bounds it. This cell is a 30B **MoE** at 512-token prompts with 16 concurrent
> clients; his matrix covered his use cases and likely other weights and shapes. What is measured
> here is that **for this model at this prompt size, the peak is `-np 8`.**
>
> - **Q4 — SUPPORTED with two repeats per arm and non-overlapping ranges. The first serving-load
>   evidence for PR 27652.**
>
>   | `GGML_VK_MMV_MAX_COLS` | cells | jobs/hour | mean | decode/req |
>   |---:|---|---|---:|---|
>   | **16** (widened) | r1, r2 | 2,206.9 / 2,439.5 | **2,323.2** | 8.1 / 8.8 |
>   | **8** (stock) | r3, r4 | 1,953.6 / 2,059.5 | **2,006.6** | 7.1 / 7.5 |
>
>   **The widened window is worth 15.8%**, and the two arms' ranges do not overlap — 2,207–2,440
>   against 1,954–2,060. Per-request decode separates the same way. Same binary, same model, same
>   `-np 16`, same prompts; the only change is the environment variable, set in
>   `fleet/arcserve/serve-arc.cmd` and inherited through llama-swap. Restored to 16 afterwards and
>   verified byte-identical to the committed file.
>
>   This is what the knee campaign never had: the knob measured **under llama-server concurrency**
>   rather than `llama-bench` or frame pacing, which is the per-vendor serving evidence PR 27652's
>   maintainer named as the blocker.
> - **The window clamps at 16** (`serve-arc.cmd:32`: *"clamps silently above 16"*), so 16 is the
>   knob's ceiling, not a chosen value. `-np` beyond 16 would push the decode batch past the window
>   with no way to follow it — which is the mechanism behind Derek's "16 is best for this hardware".
> - **Q5/P8 restated:** at `-np 16` a slot holds 8,192 tokens. Confirmed in the load report.

## Pass gate

The surface is the deliverable. **Pass** = every planned `-np 2` cell carries all six gate outcomes,
P1/P3/P5/P7 are each scored *supported* or *refuted* (not "unclear") with the stated repeats, and the
binding channel is named per region. A refuted prediction with a clean measurement is a pass of the
method.

## Kill / pivot gate

- Production reads `degraded` on a fresh sample after any cell → **stop**. Then the remedy depends
  on depth (ADR-0043 as **amended 2026-09-09**, by this campaign's own P7 result):
  **shallow (≥ ~64% of baseline) → warm and re-verify**, unchanged. **Collapsed (~33%, the state a
  verified idle produces) → restart, then let the keep-alive hold it** — warming a collapsed rung
  moved it only 33% → 48% and left it unstable at 81.8% spread, while one restart restored 100% at
  0.24% spread. `ff_ratecheck` names the discriminator: cleared by one restart ⇒ idle collapse;
  survives one ⇒ a different class, and *that* is when restarting again is wrong.
- Repeats drift beyond the noise floor at the N=2 / 512 cell after warm-up → the warm gate is not
  holding; no reading from the block is valid; fix the gate, then resume.
- P5 refuted (duty cycle ≥ 50% at `-np 2`) → the cards are compute-bound at two slots: **pivot** away
  from the feeder lane (Lap 2) toward batching and placement per depth, and say so in the record.
- Both instrument gaps unresolved (no `symmetry_check`, no ETW producer) → the surface is reported on
  jobs/hour, latency, `/slots` and budget only; P5 and channel (b) are marked **untested**, not scored.

## Pre-committed pivot (decided now, not after results)

If `-np` cannot be swept (the production restart is declined), the surface is reported at `-np 2`
only; P6 and P8 are marked untested and nothing about `-np` is inferred from the `-np 2` cells. If
P1 is *refuted* — jobs/hour keeps rising past N=2 at `-np 2` — the model of llama-server admission
in the Mechanism section is wrong, and the next cell is a direct `/slots` occupancy trace at N=4
before any other prediction is scored.
