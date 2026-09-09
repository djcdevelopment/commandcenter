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

## Pass gate

The surface is the deliverable. **Pass** = every planned `-np 2` cell carries all six gate outcomes,
P1/P3/P5/P7 are each scored *supported* or *refuted* (not "unclear") with the stated repeats, and the
binding channel is named per region. A refuted prediction with a clean measurement is a pass of the
method.

## Kill / pivot gate

- Production reads `degraded` on a fresh sample after any cell → **stop**; do not restart; warm and
  re-verify before any further cell (ADR-0043: restart is not the remedy).
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
