# Taking the B70 pool back from the imagegen lane

Written after 2026-09-04, when "the B70s are available again" and the machine's own
readings disagreed for about ten minutes. Both were right about different things. This
is what happened, why the disagreement was structural rather than anyone's mistake, and
the procedure that avoids it next time.

## The procedure (if you only read one section)

```
python -m hearth.rotation.preflight        # G0 now says WHY the fence is held
```

- **G0 GO** — pool is free, carry on.
- **G0 "held … queued 0, running 0"** — the holder is **idle but has not released**.
  Call `stop_image_session(force=False)`. It drains, then asynchronously restores a warm
  ArcServe — which is also the restart that activates the `-vk0` sibling entries.
- **G0 "held … running N"** — the holder is **busy**. Wait, or drain deliberately with the
  same non-forced call.

> ⚠ **`force=False` drains RUNNING work, not the QUEUE.** Measured 2026-09-04: the session went
> `draining_imagegen` → `restoring_llm` at 09:44:39 with **7–8 jobs still queued**, and at
> `session.restored` (09:46:22) the queue read 0 with `hearth/var/imagegen/queue/` and `results/`
> both empty. Those jobs did not run and were not preserved. **Check `queued` before you drain**
> — if it is non-zero, that work is lost unless someone resubmits it. Non-forced is gentler than
> `force=True`, not lossless.

**Never kill the imagegen processes to reclaim the pool.** There are ~16 of them, killing by
command-line match is how three production services died once before, and `stop_image_session`
exists precisely so you don't have to. `hearth/var/arc-maintenance.stop` is a **shared** lock —
not yours to remove either.

## Why the readings disagreed

"Available" turned out to name four different facts, and no single reading carried all of them:

| reading | what it actually says | what it does **not** say |
|---|---|---|
| Derek's "available again" | the art work he was running is finished | that the lane released the pool |
| tenancy lease (`active_image_session`) | someone **claims** the pool | that anyone is **using** it — it is a heartbeat, not a measurement |
| queue depth (`queued`/`running`) | jobs are in flight | that they are on the GPU right now |
| `b70_snapshot()` VRAM + temps | what the silicon is doing **at one instant** | the trend — whether it is ramping up or cooling down |

A lease renewing every 30 seconds and a pair of cards that have gone cold are perfectly
consistent: the session holds until something tells it to stop.

## Timeline (2026-09-04, local)

| time | event |
|---|---|
| ~01:18 | imagegen acquires `omen-b70-pool`, epoch 31 (`imgsess_c1972c5d…`) |
| 01:52 | first preflight: all four gates NO-GO. G0 held, production down under the fence. Correct. |
| ~02:30 | **"b70's are available again"** |
| 02:32 | preflight unchanged — still NO-GO on all four |
| 02:33 | evidence: 16 imagegen processes; nothing listening on 8081/8082; `04:00.0` **21.37 GB** committed, GPU 72 °C / VRAM 82 °C; `09:00.0` 21.37 GB, 71 / 74 °C. **I reported "actively holding and working" and stopped.** |
| 02:34 | lease: renewed 31 s ago, TTL 180 s, held 81.6 min → live, not stale |
| ~02:38 | Derek: the work has stopped, take it back |
| 02:40 | re-check: lease still renewing (6 s ago), **but `04:00.0` had cooled 72 → 56 °C GPU, 82 → 64 °C VRAM, 21.37 → 20.20 GB**; `09:00.0` flat |
| 02:40 | `get_image_session`: `queued 1, running 1` |
| 02:41 | `stop_image_session(force=False)` → accepted; queued job promoted, 2 running, drain begins |

## What I got wrong, precisely

At 02:33 I had **one** telemetry sample and reported it as a steady state — "actively holding
and working them". By 02:40 the second sample showed a 16 °C drop on one card: the work had
been winding down the whole time. Derek's statement was right and my inference was wrong, and
it was wrong in a specific, already-named way.

This is **L-2026-09-03-11 committed again, one day later, in the opposite direction**. That
lesson says a claim about an interval needs the samples inside the interval, not its endpoints;
yesterday it produced a falsely *optimistic* "at_rate throughout", today a falsely *pessimistic*
"actively working". One reading is not a regime, whichever way it points.

The second-order failure: the fence gate reported **held** and stopped there. "Held" was true
and useless — it could not distinguish a lane mid-render from a lane that finished twenty
minutes ago and never let go. Reporting a true fact that does not discriminate between the two
actions available to you is not much better than reporting nothing.

## What changed as a result

1. **G0 now reads activity, not just the lease.** `pool_activity()` (in `preflight.py`) adds
   queue depth and lease age to the gate, and the remedy differs: *idle-but-held* names
   `stop_image_session`, *busy* says wait or drain. Advisory only — if the imagegen surface
   cannot be read, the gate falls back to the plain message and never blocks on it.
2. **When a person's report contradicts telemetry, take a second sample before writing a
   verdict.** Temperature and committed VRAM are trends. A single `b70_snapshot()` cannot tell
   "working" from "just finished", and the difference is exactly what is being asked.
3. **The handover has a named path** — the procedure at the top of this file — so the next
   person does not have to reason from a process list toward a kill.

## A placement oracle worth keeping

The imagegen agent heartbeat (`get_image_session().agent.record.lanes`) publishes the
`pci_bdf` → `vulkan_ordinal` mapping directly from the driver:

- `b70@bus9` = `0000:09:00.0` → `vulkan_ordinal 0`
- `b70@bus4` = `0000:04:00.0` → `vulkan_ordinal 1`

That independently corroborates the `-vk0`/`-vk1` seat naming from a **different source** than
llama.cpp's `-lv 5` load report, and it confirms the two-card runbook's expectations:
`phi4-vk0` (env 0) should land on `0000:09:00.0`, `qwen14b-vk1` (env 1) on `0000:04:00.0`.
It does not replace the ADR-0042 assertion — enumeration can still shift, and placement is
asserted from the load report, never trusted from an index — but two independent sources
agreeing beforehand is worth reading before a window, not after a failure.
