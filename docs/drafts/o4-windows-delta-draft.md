# DRAFT — O4 Windows-delta article (first pass, NOT for publication)

> **Provenance + editor's notes (Claude, 2026-07-18):** outline/thesis/arc by **am4-moe
> (gpt-oss-120b, resident)** via `tag:research`; prose expansion by **gcp-gemini flash** in
> two passes (both clipped by thinking-budget burn — pattern noted); final ~200 words +
> stitching by Claude. **Known fixes for Derek's edit:** (1) flash misdescribed
> `--n-cpu-moe 4` as "CPU-bound MoE helper threads" — it is 4 expert *layers* resident in
> host mmap; (2) "Ubuntu 24.04" is invented — verify actual version; (3) title is a
> candidate only; (4) tone runs hotter than your house style in places ("incredibly",
> "massive") — sand it down; (5) the ASCII goodput chart should become a real figure.
> The numbers themselves are verified against the two results docs.

---

# Breaking the NVIDIA Monopoly: Dual Intel Arc Pro B70 Benchmarks and 120B MoE Local Inference on Linux

We have been conditioned to believe that serious local LLM inference requires a premium NVIDIA setup. If you want to run high-parameter models on a budget, the conventional wisdom points toward a compromise: slow CPU execution, or a stack of used consumer cards bottlenecked by platform-level driver quirks.

Recent testing on a budget-conscious AM4 desktop setup running a Ryzen 9 processor, 32GB of DDR4 RAM, and native Ubuntu challenges this status quo. Equipped with two Intel Arc Pro B70 graphics cards (device ID `0xe223`, clocked at 2800 MHz) using the native kernel driver (`xe`) and Level Zero 1.28.2, this machine was put through a rigorous double evaluation. First, we ran a direct hardware-level peer-to-peer (P2P) interconnect benchmark. Second, we subjected the node to a multi-user stress-test suite running a 120-billion parameter mixture-of-experts model (`gpt-oss-120b`).

The raw data produced three primary numbers that shift the narrative on alternative silicon:

1. **14.3 GB/s**: The practical peer-to-peer unidirectional write speed over bifurcated PCIe Gen4 x8 lanes, demonstrating that desktop Intel Arc configurations achieve full wire-speed interconnects without a P2P performance penalty.
2. **26 tokens per second**: The flat, aggregate throughput ceiling reached by a 120B mixture-of-experts model under heavy multi-user oversubscription.
3. **Less than 4%**: The total loss in decode performance when co-tenant processes force 18 GiB of system memory out of physical RAM and into a tiered zram-and-swapfile storage subsystem.

Here is an engineering breakdown of how these cards perform, where the bottlenecks reside, and how native Linux unlocks desktop hardware that was previously written off.

---

## 1. PCIe P2P at Wire Speed

To evaluate the interconnect between our two Arc Pro B70 cards, we built the `level-zero-tests` suite (commit `26e0fab`) from source. This suite includes `ze_peer`, a dedicated tool designed to measure peer-to-peer bandwidth and latency across Level Zero-supported devices.

Our AM4 motherboard bifurcates the primary PCIe 4.0 x16 slot into an x8/x8 configuration. The theoretical limit for PCIe Gen4 x8 is 15.75 GB/s. In our unidirectional tests on the compute engine, we achieved a write plateau of **14.29 GB/s** (device 0 to 1) and **14.30 GB/s** (device 1 to 0). Unidirectional reads reached **13.06 GB/s** and **13.08 GB/s** respectively.

| Direction | Operation | Bandwidth Plateau (256 MB) | Latency Floor (8 B) |
| :--- | :--- | :---: | :---: |
| 0 → 1 | Write | 14.29 GB/s | 6.17 µs |
| 0 ← 1 | Read | 13.06 GB/s | 7.75 µs |
| 1 → 0 | Write | 14.30 GB/s | 6.08 µs |
| 1 ← 0 | Read | 13.08 GB/s | 7.94 µs |

These results are perfectly symmetric, showing zero device-index bias. More importantly, hitting a 14.3 GB/s write plateau proves that P2P transfers traversing the host root complex incur no software overhead penalty. The driver-level implementation is efficient enough to saturate the physical bus.

## 2. Bidirectional Contention at the Host Root Complex

While unidirectional speeds sit at the physical limit of the slots, running bidirectional transfers simultaneously paints a different picture.

| Pair | Operation | Aggregate Bandwidth (256 MB) | Latency Floor (8 B) |
| :--- | :--- | :---: | :---: |
| 0 ↔ 1 | Write | 16.61 GB/s | 5.07 µs |
| 0 ↔ 1 | Read | 20.92 GB/s | 6.67 µs |
| 1 ↔ 0 | Write | 16.80 GB/s | 5.07 µs |
| 1 ↔ 0 | Read | 20.90 GB/s | 6.55 µs |

If the interconnect was fully non-blocking, we would observe aggregate speeds approaching twice the unidirectional rate, roughly 26 to 28 GB/s. Instead, aggregate bidirectional writes reach 16.6 to 16.8 GB/s, and reads reach 20.9 GB/s.

This behavior highlights a physical hardware bottleneck: host root-complex contention. Concurrent bilateral traffic must negotiate the desktop processor's internal crossbar, where writes face heavier performance degradation than reads. When designing model architectures that span multiple GPUs, remember that bi-directional data exchange is physically constrained by the host motherboard's routing design.

## 3. The Dedicated Copy Engine as a Free Lane

The Intel Arc Pro B70 architecture exposes two distinct command queue groups: Group 0, which handles both compute and copy instructions, and Group 1, which represents the dedicated hardware copy engine (the blitter).

Our benchmarks compared P2P transfers on the compute engine against those on the copy engine. By passing the `-u 1` flag to `ze_peer`, we targeted the dedicated copy engine directly:

* **Unidirectional Write (0 → 1):** Compute Engine = 14.29 GB/s vs. Copy Engine = 14.29 GB/s
* **Unidirectional Read (0 ← 1):** Compute Engine = 13.06 GB/s vs. Copy Engine = 13.05 GB/s

The performance of the blitter matches the compute engine to the second decimal place. This is a vital architectural insight for local multi-GPU serving. Because the copy engine is an independent silicon block, we can offload tensor-parallel weight and KV-cache distributions to the blitter without consuming execution units on the compute engine. Multi-GPU routing layers should be configured to target copy engine queues exclusively for model synchronization.

## 4. Latency Curves and the Micro-Batch All-Reduce Limit

When coordinating multiple cards, small transfers are bounded by latency rather than raw throughput. Our latency microbenchmarks revealed a floor of **6.1 µs** for writes and **7.8 µs** for reads at small transfer sizes (8 B).

The saturation ramp for a unidirectional write from device 0 to device 1 clarifies this boundary:

* **64 KB:** 4.41 GB/s
* **256 KB:** 9.12 GB/s
* **1 MB:** 13.22 GB/s
* **8 MB:** 14.16 GB/s
* **256 MB:** 14.29 GB/s

The interconnect reaches 92% of its maximum bandwidth plateau only when transfer sizes meet or exceed 1 MB. This curve dictates how we should orchestrate distributed inference. Small, frequent synchronizations (such as a 16 KB tensor-parallel all-reduce payload, which takes ~6.8 µs and runs entirely at the latency floor) will underutilize the bus. To extract the full value of the physical link during multi-GPU setups, inference engines must batch or combine communications to exceed the 1 MB threshold.

## 5. Scaling the Concurrency Ladder

To test these synthetic limits under a realistic production workload, we configured an active server running `gpt-oss-120b`, a 120-billion parameter mixture-of-experts model. The model's layers were split across both B70 cards with four expert layers held in host memory via mmap (`--n-cpu-moe 4`). We allocated four 16k context slots (`-c 65536 -np 4`) using a `q8_0` KV cache quantization scheme.

To push the limits of this configuration, we executed a test suite called the "oxen-moe gambit," running up to 16 concurrent streams using an 800-token prompt and a 128-token output window.

At a single concurrent stream, we achieved a decode speed of 29.0 tokens per second. As more clients connected, the system reached its physical processing limits. Aggregate goodput plateaued at **26.4 tokens per second** at 4 concurrent clients and held flat at **26.1 tokens per second** under a 16-way overload. This oversubscription costs queue latency—roughly 4.4 seconds of TTFT per queued request at this shape—but never throughput. The queue remains stable and fair. Even when subjected to an extreme overload burst of 32 concurrent requests (an 8x oversubscription of our 4 slots), the system executed a clean linear queue drain across 8 waves of 4 requests with zero errors or timeouts. The entire burst cleared within a maximum wall clock time of 105 seconds, maintaining an aggregate goodput of approximately 28 tokens per second. Time-to-first-token (TTFT) quartiles recorded at 3.9, 16.9, 43.5, 69.1, and 95.9 seconds, demonstrating graceful queue management under extreme pressure.

## 6. Context Depth and Prefill Dynamics

We mapped performance across expanding context depths using four concurrent clients (c=4) generating 128 tokens. The prefill rate held remarkably flat at approximately 190 to 220 tokens per second up to the 15,000-token depth. Specifically, we observed prefill rates of ~190 tokens per second at 2,048 tokens, ~180 tokens per second at 4,096 tokens, ~185 tokens per second at 8,192 tokens, ~195 tokens per second at 12,288 tokens, and ~196 tokens per second at 15,000 tokens.

While prefill remains highly stable, the attention overhead over deep KV caches imposes a noticeable cost on the decode phase. At a shallow 512-token depth, decode achieved 9.0 tokens per second per stream. At 15,000 tokens, decode fell to 5.4 tokens per second per stream—a 40% penalty.

However, prompt-cache economics present an exceptionally high-value optimization. Running an identical 8,192-token prompt back-to-back reduced the TTFT from a cold 11.8 seconds down to a warm 0.17 seconds—a massive 68x acceleration. This confirms that session parking and stable prefix routing are highly viable architectural strategies for this hardware.

## 7. The Silent-Truncation Trap

The most critical policy-level discovery was a silent-degradation trap. A deliberate 17.5-kilotoken request sent against the 16-kilotoken slot ceiling did not trigger an error or a timeout. Instead, the server returned an `ok` status, a 25-second TTFT, and completed a normal decode sequence.

Behind the scenes, the server silently truncated the input prompt to fit the slot. The caller received no signal of this severe context loss. Consequently, our door-side budget of 57,344 bytes (~14,000 tokens) must be treated as a strict load-bearing limit enforced at the routing layer, as the server itself will not refuse over-limit payloads.

## 8. Memory-Pressure Resilience

To test host-RAM resilience under co-tenant load, we executed a denning-style test. We ran a steady 2-way decode while a memory hog (`OOMScoreAdjust=1000`) stepped up allocations to 18 GiB. Under baseline conditions with no swap, decode was 14.6 tokens per second per stream with a p95 TTFT of 3.2 seconds. At 6 GiB of hog pressure, a one-time allocation shock occurred as the kernel pushed 8 GiB into zram, spiking p95 TTFT to 13.4 seconds, though decode held at 14.1 tokens per second.

As pressure climbed to 10 GiB, 14 GiB, and finally 18 GiB, decode throughput proved incredibly resilient, yielding 14.3, 14.3, and 14.0 tokens per second respectively. At the 18 GiB peak, zram (priority 100, 8 GB limit) was full at 7.9 GiB, and the 16 GiB swapfile backstopped it with 11.4 GiB in use (representing 19.3 GiB of peak swap). Despite an effective demand of ~48 GiB against 30 GiB of physical host RAM, decode throughput lost 4% or less.

Three safeguards prevented performance collapse:
1. **LRU Win:** Hot expert pages won the kernel's LRU competition, keeping the ~6.5 GiB active working set pinned in buffer cache.
2. **Tiered Swap:** The zram priority-100 layer absorbed the initial wave, while the swapfile handled overflow up to 11.4 GiB.
3. **Disk Separation:** Swap I/O was isolated to the main NVMe system drive, while the model's memory-mapped weights ride a separate 4TB NVMe — paging and weight reads never contend for the same device.

One honesty note on this result: the hog's pages were near-perfectly compressible, which is the best case for zram. A real co-tenant with less compressible memory would spill to the swapfile sooner. The swapfile leg demonstrably kept up at 11.4 GiB in use, but the zram capacity figure should be read as an upper bound.

## What This Data Does Not Show

Rigor demands the boundary lines. This is a single node; multi-host topologies remain untested. No mixed image-generation workloads ran alongside inference. Power draw and acoustics were not measured — the 78°C sustained GPU die temperature (over a 10-minute soak with a drift of only -2% in decode rate) leaves thermal headroom but says nothing about efficiency. Only the B70 on a Gen4 x8 link was evaluated. And the big one: the Windows comparison is **inferred, not measured side-by-side**. The earlier Windows-era findings — pre-registered experiments showing the Windows video memory manager evicting a fully-fitting model and causing a 5x decode collapse — are from a different software generation. The honest claim is that this Linux stack exhibits *none of those failure modes*, not that we re-ran the same matrix on both operating systems.

## The Question

If a consumer-grade AMD desktop can move data between two Intel GPUs at full PCIe wire speed and hold a 120-billion parameter model at production-grade throughput while shrugging off 18 GiB of memory pressure — should the community start treating Intel Arc as the new default for local inference experiments?
