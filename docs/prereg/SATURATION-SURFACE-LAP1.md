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
     never inferred from `/slots`. Capturing it is a named ops item, Derek's call.
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
