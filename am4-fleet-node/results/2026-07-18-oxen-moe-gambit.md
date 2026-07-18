# oxen-moe gambit — stress characterization of resident gpt-oss-120b

2026-07-18 · receipt `br-20260718-074554-fe90b0be` · raw data + instruments in
`raw-2026-07-18-gambit/` (453 requests, 10s-cadence samples incl. GPU die temps)

**Derek's ask:** "run the gambit on the new hardware, let's find out if it breaks before we
try to build things with it."

**Verdict: it doesn't break — anywhere we could reach.** 453/453 requests succeeded, 0
llama-server restarts, no OOM, no thermal drift, and host-RAM pressure to 18 GiB couldn't
dent decode. The system's real limits are **latency curves and one silent-degradation
trap**, both policy matters for the router, not stability risks.

Setup: `b70-moe` as shipped (dual B70 layer-split, `--n-cpu-moe 4`, `-c 65536 -np 4` =
4×16k slots, q8_0 KV). Driver on-box (`moe_gambit.py`, stdlib, streaming TTFT,
nonce-prefixed prompts to defeat the prompt cache except where testing it).

## A — concurrency ladder (prompt ~800 tok, 128 out, ×2 rounds)

| offered | TTFT mean | TTFT max | decode/stream | aggregate goodput |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1.8 s | 1.8 s | 29.0 | 20.7 tok/s |
| 2 | 3.6 s | 3.7 s | 14.2 | 20.2 |
| 3 | 4.8 s | 4.9 s | 11.4 | 24.0 |
| 4 | 5.9 s | 5.9 s | 9.5 | 26.4 |
| 6 | 11.6 s | 23.0 s | 11.3 | 24.4 |
| 8 | 15.5 s | 25.2 s | 9.3 | 25.9 |
| 12 | 24.8 s | 43.9 s | 9.7 | 26.5 |
| 16 | 35.0 s | 63.9 s | 9.4 | **26.1** |

**Goodput saturates at ~26 tok/s at c=4 and holds perfectly flat to c=16** — oversubscription
costs queue latency (roughly +4.4 s TTFT per queued request at this shape), never
throughput. The queue is stable and fair.

## B — context depth (c=4, 128 out)

| prompt tokens | TTFT mean | decode/stream | effective prefill |
| ---: | ---: | ---: | ---: |
| 512 | 4.2 s | 9.0 | — |
| 2,048 | 12.1 s | 9.2 | ~190 tok/s |
| 4,096 | 25.9 s | 8.3 | ~180 |
| 8,192 | 46.5 s | 7.6 | ~185 |
| 12,288 | 64.6 s | 6.0 | ~195 |
| 15,000 | 76.5 s | 5.4 | ~196 |

Prefill rate holds ~190 tok/s to depth (4-way concurrent); decode at 15k depth costs ~40%
(9 → 5.4 tok/s/stream) — attention over deep KV, as expected.

**⚠ The one trap — over-limit prompts truncate SILENTLY.** The deliberate 17.5k-token
request (vs the 16k slot ceiling) did **not** error: it returned `ok`, TTFT 25 s, normal
decode — llama-server truncated the prompt to fit. **Context loss with no signal to the
caller.** The rung's door-side `context_bytes = 57344` (~14k) budget is therefore
load-bearing, not advisory: the door must keep enforcing payload fit because the server
won't refuse. (A1 payload-aware routing already does this — validated design.)

**Prompt-cache economics:** identical 8k prompt back-to-back: TTFT **11.8 s cold →
0.17 s warm (68×)**. Session parking / stable prefixes are enormously worth engineering
for (the KV-rotation idea from the design discussion is confirmed viable).

## C — decode-heavy

1024-token generations at c=4: steady 9.3 tok/s/stream, no slot starvation. c=8 with
512-out: TTFT tail 49.7 s (queueing, per A), all complete.

## F — overload burst (32 concurrent, 8× slots)

**All 32 completed, zero errors, zero timeouts.** TTFT quartiles 3.9 / 16.9 / 43.5 /
69.1 / 95.9 s — a textbook linear queue drain (8 waves of 4), max wall 105 s. Graceful at
8× oversubscription; goodput held (~28 tok/s aggregate).

## E — sustained soak (10 min continuous 4-way)

32 waves: decode 9.26 → 9.06 tok/s (−2%, noise), TTFT 5.9 → 6.0 s. **Max GPU die 78 °C**
(idle ~50), CPU 71 °C — far from the box's proven 92 °C imagegen envelope. No drift.

## D — host-RAM pressure (the denning-style test)

Steady 2-way decode while a hog (`OOMScoreAdjust=1000`) stepped up; the question: does
page-cache eviction of the ~7 GB mmap'd CPU experts collapse decode, and does the D2
zram/swap architecture catch it?

| phase | decode/stream | TTFT p95 | swap in use |
| :--- | ---: | ---: | :--- |
| baseline | 14.6 | 3.2 s | ~0 |
| hog 6 GiB | 14.1 | 13.4 s¹ | zram 8G FULL + file 1.3G |
| hog 10 GiB | 14.3 | 4.7 s | zram 7.9G + file 5.2G |
| hog 14 GiB | 14.3 | 4.3 s | zram 7.8G + file 8.5G |
| hog 18 GiB | **14.0** | 3.8 s | zram 7.9G + file 11.4G (19.3 GiB peak) |

¹ one-time allocation shock while the kernel first pushed 8G into zram; later, larger hogs
didn't reproduce it.

**Decode lost ≤4% at 18 GiB of co-tenant pressure** (≈48G demand vs 30G physical). Three
protective mechanisms, all confirmed: hot expert pages win LRU (buff/cache pinned ~6.5Gi
throughout — the per-token working set never evicted); zram absorbs first at pri 100 with
the 16G file as overflow exactly as D2 designed; swap I/O rides the main NVMe while model
mmap rides the 4TB — different disks, no contention. *Caveat:* the hog's pages are
near-perfectly compressible, so zram capacity here is best-case; a real co-tenant
compresses worse and would spill to the file sooner — but the file leg demonstrably kept
up at 11.4G used.

## Capacity facts (→ O5 / router cost model)

* Aggregate decode ceiling: **~26 tok/s** (batched, any load ≥4); solo stream 29.
* Prefill: **~190–220 tok/s**, roughly flat to 15k depth.
* TTFT model: ≈1.4 s + prompt_tokens/200 + 4.4 s × queue_position (at the 800/128 shape).
* Deep-context decode penalty: −40% at 15k.
* Cache-hit TTFT ~0.2 s (68× cold) — route repeat-prefix work to the same rung.
* Payloads > ~14k tokens: **enforce door-side** (server silently truncates).
* Co-tenant host-RAM tolerance: ≥18 GiB with ≤4% decode cost (zram best-case caveat).
* Thermal envelope: 78 °C GPU at full sustained load — ample headroom.

## Instruments

`raw-2026-07-18-gambit/` carries `moe_gambit.py` (driver: sweeps A–F, streaming TTFT,
slot/mem/temp sampler), `hog.py` (pressure), `analyze_gambit.py` (aggregation), plus all
raw JSONL — the campaign is rerunnable after any config change (e.g. slot-count or
`--n-cpu-moe` retuning) for A/B comparison.
