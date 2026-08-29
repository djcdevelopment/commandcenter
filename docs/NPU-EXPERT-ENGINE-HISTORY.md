# Breaking Through the Software Stack: The NPU Expert-Engine Campaign

**Campaign dates:** August 28–29, 2026  
**Hardware:** Intel AI Boost NPU, architecture 3720, driver 1004778  
**Software:** Windows 11, OpenVINO 2026.3, compiler-in-plugin path  
**Model lane:** Qwen3.8-Flash-Next MoE, 512 experts, top 10, 48 layers  
**Status:** Direct single-stream NPU expert-engine family closed at the current compiler
lowering; diagnostic baseline retained

> “NPU-as-expert-engine is software-blocked today.”
>
> The response was simple: if the blocks are software, go down the stack and remove them.

## The outcome in one minute

The campaign did remove the software blockade.

It took the Qwen3.8-Flash-Next expert path from a graph that did not build, through a driver
compiler incompatibility, a matcher miss, a false HOST_ROUTED attachment, a broken output
boundary, an unusably slow first inference path, and several layers of runtime overhead. By
NPU-19, the alternating routed-layer p95 had fallen from 76.611 milliseconds to 1.906
milliseconds: a 40.19-fold improvement.

That was a real software result. The NPU executed the selected top-10 experts. Routes A and B
produced different outputs, route A replayed exactly, and the NPU agreed closely with the CPU
reference. The compiler accepted 30 dynamic i4 expert matrices and their 30 scale tensors.
The host-routed mechanism was no longer a theory.

It still did not win the machine.

Forty-eight observed NPU-19 expert layers project to 79.1 milliseconds at p50 and 91.5
milliseconds at p95, or about 12.64 and 10.93 tokens per second before attention, routing,
cross-engine hops, and integration overhead. The measured CPU-expert lane was already doing
23–26 tokens per second.

Two final lower-level graphs then tested whether OpenVINO's wrapper shape was hiding another
large win. NPU-20 built a faithful, static, no-repack graph with the exact 71-input ABI needed
by a bespoke runtime. NPU-21 removed the remaining internal f32 precision islands without
changing that ABI or the computation order. Both compiled to exactly the same 235-operation
lowering and exactly the same 2.03080-millisecond estimate.

That identical result was the family stop. On this NPU, driver, and OpenVINO compiler, the
remaining limit is not ordinary Python, logging, profiling, request pooling, graph matching,
or parameter binding. A custom Level Zero runtime would still be an interesting systems
artifact, but the measured compute path no longer funds it for single-stream decode.

## Why the idea was compelling

Qwen3.8-Flash-Next is a bandwidth story disguised as a very large model. Each layer contains
512 experts, but a token selects only 10 of them. The active top-10 expert matrices presented
to the NPU total 24,576,000 packed-i4 bytes per layer, plus 76,800 bytes of scales. Across 48
layers, the routed expert stream is roughly 1.18 gigabytes per token before the shared expert
and other model work.

The NPU shares system memory with the CPU. If it could read only the chosen experts directly
from unified memory, it might turn idle matrix hardware and host DDR bandwidth into a useful
expert engine without consuming B70 VRAM. The host could perform the small routing decision,
rebind the selected weight slices, and submit a static graph. Shapes would not change from
token to token; only the bound addresses would.

The original go/no-go physics were therefore straightforward:

1. Can a selected top-10 expert graph compile and execute?
2. Can dynamic i4 weights stay compressed through lowering?
3. Can route-time rebinding avoid copying or importing the entire expert bank?
4. Can the routed layer get close enough to roughly 25 GB/s of effective source-weight
   throughput to challenge the CPU path after submit and hop costs?

The campaign answered the first three questions far more positively than the initial
“unsupported” label suggested. The fourth answer was no.

## The method: one edge per lap

The work used the laboratory's `/rnd` discipline. Each lap owned one change and stopped at
the first material edge. Compile estimates were never presented as wall time. Modeled source
throughput was never presented as physical DDR traffic. Unexplained behavior was recorded as
unexplained instead of being given a convenient story.

Every inference lap used deterministic, disjoint top-10 routes. Route A selected experts 0
through 9; route B selected experts 256 through 265. Correctness required CPU agreement,
exact A replay, and A/B separation before timing could be admitted. Compile-only structural
experiments had a declared latency gate before they ran.

Production remained live throughout. Each lap closed with a real one-token serviceability
check, a HEARTH door check, and confirmation that the BF6 render queue and claims were empty.
The final receipt ledger contains 163 valid JSONL records.

## Act I: reaching the compiler

### NPU-0 and NPU-1 — build the real-weight substrate

The first correction was conceptual: OpenVINO 2026.3 did ship a Qwen-style HOST_ROUTED MoE
path. “MoE unsupported on NPU” was too broad. The right question was whether the released
matcher and executor could be driven on architecture 3720.

NPU-0 installed OpenVINO 2026.3 in an isolated environment, enumerated the Intel AI Boost
device, and built a probe around the real Qwen geometry: hidden size 2560, expert intermediate
size 640, 512 experts, and top 10. The first stop was almost comically high in the stack:
`gguf-py` needed PyYAML.

NPU-1 installed the pinned dependency, hash-verified all three 93.7-gigabyte GGUF shards,
streamed layer zero once, and produced a 1,262,223,360-byte symmetric-i4 expert cache with
per-artifact hashes. The next stop was ownership: a read-only scale memmap could not be used
as a shared-memory OpenVINO constant.

These were mundane problems, but eliminating them mattered. From this point onward, the
campaign used real model weights rather than toy matrices.

### NPU-2 through NPU-4 — configuration and compiler ABI

NPU-2 made the scale mapping writable and reached `core.compile_model`. It stopped because
the accepted debug enum was `LOG_DEBUG`, not `DEBUG`.

NPU-3 fixed that one value and reached the Level Zero graph extension. The runtime selected
the no-weight-copy serializer, produced a small IR shell, and called `pfnCreate2`. The installed
driver compiler then rejected the graph because it exposed API 8.1 while the OpenVINO model
expected 8.2.

That was a useful distinction: the graph had reached a concrete software ABI seam. It had not
hit a silicon limit.

NPU-4 selected OpenVINO's compiler-in-plugin path instead of changing the installed driver.
The wheel's VCL compiler accepted the model and initialized Level Zero graphs. The compiled
plan still lacked expert tags, so the host-routed executor could not attach. Source inspection
eventually found the cause: Python's convenience Swish builder emitted a two-input node, while
the released Qwen expert matcher expected the one-input opset4 form.

## Act II: making host routing real

### NPU-5 and NPU-6 — a matcher success that was not yet routing

NPU-5 built the exact one-input Swish with `NodeFactory("opset4")`. Expert tags appeared,
Level Zero graphs compiled, deterministic A and B routes executed, and A replayed exactly.
The numerical gate still failed at 1.86% and 2.41% NRMSE, so the lap correctly withheld a
performance claim.

NPU-6 did not rerun the unexplained input. It audited the plan and runtime logs instead. That
audit overturned the apparent breakthrough: HOST_ROUTED had never attached. The four expert
fragments were separate, but `MoEExperts::from()` required one cohesive model containing both
the expert Tile and the final route-score Multiply. Gate, up, and down had each expanded to
512 Convolutions and 512 Slices. The device was executing a dense expert bank, not rebinding
the selected ten experts.

This was one of the campaign's most important corrections. Successful compilation and
different route outputs were not enough; the partition plan had to prove that the intended
mechanism was active.

### NPU-7 and NPU-8 — the real top-10 graph, then a shipped boundary bug

NPU-7 supplied an exhaustive offline partition plan, explicit `npuw_moe_k=10` metadata, and
the fold setting that actually invoked the MoE partition and compilation stages. The resulting
expert compiler contract had 71 inputs:

- one activation;
- 30 i4 matrices for gate, up, and down across ten selected experts;
- 30 f16 scale tensors;
- ten route-score closures.

The lowering contained 30 Convolutions, ten Slices, and ten Tiles. There was no 512-way expert
expansion. The first genuine top-10 HOST_ROUTED graph had compiled on architecture 3720.

NPU-8 allowed execution. The runtime created 66 Level Zero tensors, issued 62 graph-argument
updates, ran the expert graph, and released 60 pool-backed custom slice bindings. It then
asserted that `expert_output_accumulator` was null. Batch mode intentionally did not create
that accumulator; the normal tensor link already carried the graph output. The shipped
downstream boundary contradicted its own batch-mode ownership model.

### NPU-9 — correctness, at last, and a brutal first number

NPU-9 kept the top-10 reduction inside the expert function and bypassed the broken downstream
path. All 138 outer requests completed. Both routes passed at cosine above 0.9999995 and NRMSE
at or below 0.106%. A replay was exact and the routes separated.

The first valid wall-time result was bad: alternating routed-layer p50/p95 measured
72.824/74.701 milliseconds. The compiler's own optimistic complete-layer estimate was 18.326
milliseconds. Even that estimate implied only about 1.14 tokens per second across 48 expert
layers, before attention or hops.

The mechanism worked, but it was nowhere near useful yet.

## Act III: finding the large software wins

### NPU-10 through NPU-12 — reject the obvious explanations

NPU-10 disabled performance counters. Profiling instrumentation was not the wall: alternating
p50/p95 slightly regressed to 74.421/76.611 milliseconds. A source audit also corrected the
language around route-time activity. The 60 operations were same-context weight-bank pointer
rebindings and slice-wrapper releases, not 60 fresh physical imports and not a route-time
memcpy of the expert matrices.

NPU-11 tried the tempting structural shortcut: algebraically collapse the ten expert branches
into three wide MatMuls. The graph compiled, but architecture 3720 lowered it into an unhappy
tiling regime. Four nonfatal tile warnings accompanied a 19.527-millisecond estimate, 23.4%
worse than the then-current expert estimate. A real runtime would also have needed to read and
write a 24.65-megabyte packed bundle every layer. The compile gate killed the packer before
that tax was built.

NPU-12 enabled NPUW dynamic quantization. It fixed the router but not the experts. The router
estimate fell from 434.61 to 89.29 microseconds, while the expert graph stayed at 15.82403
milliseconds. The reason was a proven matcher boundary: the unrolled expert scales remained
rank-three tensors, and the available CWi and grouped-quantization matchers did not accept that
layout.

### NPU-13 — the decisive compiler flag

NPU-13 enabled the separate compiler property
`NPU_COMPILER_DYNAMIC_QUANTIZATION=YES`.

This was the largest single technical win in the campaign. All 30 explicit i4-to-f16 expert
Converts and 30 PermuteQuantize operations disappeared from the lowered graph; 30
QuantizeCasts appeared instead. The expert estimate fell from 15.82403 milliseconds to
2.10406 milliseconds, a 7.52-fold improvement. Router, expert, and final graphs together were
estimated at 2.19564 milliseconds per routed layer.

The original idea had moved from “probably unsupported” to a graph whose modeled cost was at
least in the right order of magnitude.

## Act IV: turning compiler potential into wall time

NPU-14 through NPU-19 held the model, routes, and compiler-DQ mechanism fixed while removing
one runtime cost at a time.

| Lap | Sole useful change | Alternating p50 / p95 | What it established |
|---|---|---:|---|
| NPU-10 | Profiling counters off | 74.421 / 76.611 ms | Counters were not the wall |
| NPU-14 | Execute compiler-DQ graph | 20.318 / 23.807 ms | Correctness passed; cache misses and debug overhead dominated |
| NPU-15 | `NPUW_MOE_POOL_SIZE=0` | 3.811 / 4.701 ms | Request-cache lifecycle was a major tax |
| NPU-16 | `LOG_DEBUG` to `LOG_NONE` | 2.074 / 2.748 ms | Captured debug logging materially distorted the runtime |
| NPU-17 | `OPENVINO_NPUW_PROF=NO` | 1.772 / 2.216 ms | Host profiling still had measurable cost |
| NPU-19 | `NPU_TURBO=YES` | 1.648 / 1.906 ms | Final admitted diagnostic baseline |

NPU-14 first proved that compiler DQ was numerically sound on the NPU. It also exposed a
cache-state asymmetry: the fixed route was hit-heavy, while 63 of 64 alternating samples were
exact-route misses. The resulting fixed-versus-alternating difference was a cache/configuration
premium, not a clean measurement of pointer rebinding alone.

NPU-15 disabled the MoE request cache. The source path copies only small inputs into
request-owned tensors and directly binds larger inputs. For this graph, 30 scale tensors
totaling 76.8 kilobytes took the small-copy path, while 30 i4 matrices totaling 24,576,000
bytes stayed direct-bound. Custom detach/reacquire lifecycles halved from 60 to 30 per request,
and alternating p95 fell to 4.701 milliseconds. This proved the lifecycle reduction; it did
not prove 30 physical driver imports or physical DDR traffic.

NPU-16 removed runtime debug logging. Captured output fell from 34,569 lines and 6.70
megabytes to seven lines and 11.6 kilobytes. Alternating p95 fell to 2.748 milliseconds.

NPU-17 removed NPUW host profiling and reached 2.216 milliseconds p95. NPU-19 enabled the
locally advertised turbo property and froze the runtime baseline at 1.648/1.906 milliseconds
p50/p95. Correctness, replay, and route separation remained green.

From NPU-10 to NPU-19, alternating p95 improved 40.19-fold. The stack had been unblocked.

But the modeled source-weight throughput at NPU-19 was only 14.14/12.22 GB/s p50/p95, and
that was arithmetic over source bytes and wall time—not a physical DDR counter. The original
rough target was 25 GB/s. The expert-only projection remained about half the measured CPU
token rate before adding the rest of the model.

## Act V: go beneath NPUW

The remaining challenge was the original one: perhaps a direct graph and bespoke Level Zero
binding runtime could remove the last wrapper costs.

Chronology note: NPU-18 ran between NPU-17 and the final turbo-only NPU-19 lap. It is grouped
here because it begins the structural, below-the-wrapper branch of the investigation.

### NPU-18 — revisit packing under the good compiler

Before building a new runtime, NPU-18 gave the previously rejected three-wide packed graph
the compiler-DQ pass discovered in NPU-13. Its estimate improved 9.73-fold, from 19.527 to
2.00592 milliseconds. That looked promising until the full cost was counted.

The graph still missed its strict sub-millisecond gate. It was only 4.66% faster than the
normal expert estimate, while route-time packing would require 49.3 megabytes of host read plus
write traffic per layer. Fitting that work into the available p95 headroom would require more
than 400 GB/s of host packing throughput. The graph was rejected without inference.

### NPU-20 — the faithful direct graph

NPU-20 removed packing entirely. It authored the expert computation directly as one static
graph while retaining the exact runtime shape a low-level binder would need:

- 30 independent dynamic i4 weight parameters;
- 30 independent f16 output-scale parameters;
- ten f16 route scores;
- one f16 activation;
- no weight concatenation, pre-scaling, or route-time repack.

The graph compiled with all 71 names, types, shapes, and input identities intact. Compiler DQ
covered all 30 matrices. Post-lowering contained 30 QuantizeCasts and no remaining explicit
i4-to-f16 Convert. No tile error appeared.

The estimate was 2.03080 milliseconds. That was only 3.48% better than NPU-13 and 35.39%
above the predeclared 1.50-millisecond gate for funding a lower-level runtime. Forty-eight
expert graphs alone modeled to 97.48 milliseconds, or 10.26 tokens per second, before routing,
attention, binding, submission, and cross-engine hops.

The direct ABI worked. Its performance did not earn the runtime.

### NPU-21 — the final precision falsification

One final ambiguity remained. NPU-20 preserved f32 islands around Swish, the gate/up merge,
score multiplication, and reduction. Perhaps those islands were forcing the 20
GroupConvolutions or blocking fusion.

NPU-21 changed only that internal precision schedule. It retained NPU-20's exact ordered
71-input ABI, graph topology, layouts, score placement, and reduction order. Source operations
fell from 383 to 311, and source Converts fell from 102 to 30.

Before compilation, a paired CPU emulation used the validated real layer-zero cache and the
two disjoint routes. All-f16 versus the faithful schedule measured:

| Route | Cosine | NRMSE | Maximum absolute difference |
|---|---:|---:|---:|
| A, experts 0–9 | 0.999999722 | 0.0007469 | 0.000007629 |
| B, experts 256–265 | 0.999999689 | 0.0007895 | 0.000007629 |

Route A replayed exactly and A/B remained separated. This admitted the compile, while still
being labeled correctly as CPU boundary emulation rather than NPU numerical proof.

The compiler input graph shrank from 210 to 157 IE operations. The final lowering did not
change at all. It was the same 235 operations as NPU-20: 30 QuantizeCast, 30 Convolution, 20
GroupConvolution, 57 PermuteCast, 20 ShapeCast, and ten Tile operations. The compiler estimate
was again exactly 2.03080 milliseconds.

The compiler had already absorbed the precision islands. Removing them at the source level
could not move the machine.

## What “unblocked” means now

The campaign does **not** conclude that an NPU can never be an MoE expert engine. It concludes
something narrower and more useful:

- OpenVINO 2026.3's HOST_ROUTED Qwen expert path can be made to match, compile, bind selected
  dynamic weights, execute, and pass numerical checks on Intel NPU architecture 3720.
- Compiler dynamic quantization is essential. Without it, the expert shape is hopeless.
- Request pooling, debug logging, profiling, and turbo configuration collectively hide orders
  of magnitude of wall-time performance.
- A faithful direct 71-input graph is possible, so ordinary NPUW graph construction is not the
  remaining blocker.
- At the current compiler lowering, that direct graph converges on approximately 2.03
  milliseconds of modeled expert work per layer. The measured NPUW runtime is already near
  that regime.
- The single-stream full-model economics therefore lose to the existing CPU-expert lane on
  this box.

The unmeasured Level Zero questions—arbitrary-host-USM rebinding across the full expert pool,
physical DDR traffic, submit latency in a bespoke runtime, and staging behavior—remain genuine
questions. They were not measured because the compute admission gate failed first. Writing
that runtime would answer interesting software questions, but it would not be a disciplined
goodput investment under the present evidence.

## What would reopen the lane

The single-stream expert-engine branch should reopen only when at least one premise changes:

1. A new OpenVINO compiler or NPU driver materially changes the 30-Convolution plus
   20-GroupConvolution lowering or reports an expert estimate below 1.50 milliseconds.
2. New NPU silicon changes the memory or compute balance enough to beat the CPU expert path.
3. A batch or concurrency workload offers expert reuse and a different goodput objective.
   Prefill is structurally more favorable than single-token decode because many tokens reuse
   a much larger fraction of the expert bank.
4. Full-model scheduling can keep work on one engine and remove the cross-engine hop tax.

NPU-19 remains the diagnostic baseline for those future comparisons:

`compiler DQ + pool size 0 + performance counters off + runtime logging off + NPUW profiling
off + NPU turbo on`.

## Lap-by-lap chronology

| Lap | Edge moved or closed |
|---|---|
| NPU-0 | Corrected “MoE unsupported”; stopped at missing PyYAML before compilation |
| NPU-1 | Built and hashed the real 1.262 GB layer-zero i4 cache; stopped at read-only scale ownership |
| NPU-2 | Reached compile; corrected unsupported `DEBUG` log enum |
| NPU-3 | Reached Level Zero `pfnCreate2`; found driver compiler API 8.1 versus required 8.2 |
| NPU-4 | Used compiler-in-plugin successfully; found missing expert tags |
| NPU-5 | Fixed the one-input Swish matcher; real inference ran, but numerical gate failed |
| NPU-6 | Proved HOST_ROUTED had not attached; dense 512-expert expansion explained the false path |
| NPU-7 | Compiled the first genuine K=10 HOST_ROUTED graph with a 71-input expert ABI |
| NPU-8 | Executed the selected graph; found the EXPERT_BATCH output-ownership contradiction |
| NPU-9 | Moved reduction inside the graph; correctness passed, first valid path measured roughly 75 ms p95 |
| NPU-10 | Disabled performance counters; no gain, and route-time operations were identified as pool-backed rebindings |
| NPU-11 | Collapsed to three wide MatMuls; architecture-3720 tiling made it slower |
| NPU-12 | NPUW DQ fixed the router but missed rank-three expert scales |
| NPU-13 | Compiler DQ cut the expert estimate 15.82403→2.10406 ms |
| NPU-14 | Proved compiler-DQ correctness on hardware; measured the cache-miss/debug-heavy wall path |
| NPU-15 | Disabled MoE request cache; halved custom tensor lifecycles and reached 4.701 ms p95 |
| NPU-16 | Disabled debug logging; reached 2.748 ms p95 |
| NPU-17 | Disabled NPUW host profiling; reached 2.216 ms p95 |
| NPU-18 | Retried packed collapse with compiler DQ; 2.00592 ms estimate could not pay its packing tax |
| NPU-19 | Enabled turbo; froze the diagnostic baseline at 1.648/1.906 ms p50/p95 |
| NPU-20 | Compiled the faithful no-repack direct 71-input graph; 2.03080 ms failed the runtime-build gate |
| NPU-21 | Removed f32 islands; final lowering and 2.03080 ms estimate were identical, closing the family |

## Lessons worth carrying forward

The phrase “software-blocked” should begin an investigation, not end one. But it needs to be
decomposed. Packaging, ownership, configuration, compiler ABI, graph matching, partitioning,
runtime ownership, numerical correctness, compiler lowering, lifecycle policy, observability,
and hardware scheduling were all separate blockers in this campaign. Several were worth
orders of magnitude; others were clean negatives.

The most important result was not any single latency number. It was knowing where the number
came from.

By the end, the team could say exactly which parts were solved, which parts were measured,
which parts were merely modeled, and why the next several weeks of low-level runtime work did
not have a performance case. That is a stronger outcome than either “the NPU is unsupported”
or “a custom runtime will surely make it fast.”

Software opened the door. Measurement decided not to walk farther through it—yet.

## Evidence and implementation map

- Campaign ledger and board: [`ROTATION-PROGRAM.html`](../ROTATION-PROGRAM.html)
- Main HOST_ROUTED probe: [`npu0_host_routed_moe.py`](../campaign/lz-probes/npu0_host_routed_moe.py)
- Three-wide collapsed probe: [`npu11_collapsed_expert.py`](../campaign/lz-probes/npu11_collapsed_expert.py)
- Faithful direct graph: [`npu20_direct_dq_expert.py`](../campaign/lz-probes/npu20_direct_dq_expert.py)
- Final all-f16 graph: [`npu21_f16_dq_expert.py`](../campaign/lz-probes/npu21_f16_dq_expert.py)
- Runtime logs, graph contracts, precision artifact, summaries, and 163-line receipt ledger:
  `E:\work\battlemage\lz-probes\`

Selected upstream implementation references:

- [OpenVINO 2026.3 release](https://github.com/openvinotoolkit/openvino/releases/tag/2026.3.0)
- [Qwen-style MoE matcher](https://github.com/openvinotoolkit/openvino/blob/2026.3.0/src/plugins/intel_npu/src/plugin/npuw/partitioning/patterns/moe.cpp)
- [HOST_ROUTED MoE executor](https://github.com/openvinotoolkit/openvino/blob/2026.3.0/src/plugins/intel_npu/src/plugin/npuw/moe/moe_executor.cpp)
- [Dynamic-quantization full rewrite](https://github.com/openvinotoolkit/openvino/blob/2026.3.0/src/plugins/intel_npu/src/plugin/npuw/partitioning/patterns/opt.cpp#L175-L250)
- [Small-tensor versus direct-binding path](https://github.com/openvinotoolkit/openvino/blob/2026.3.0/src/plugins/intel_npu/src/plugin/npuw/moe/moe_infer_utils.cpp#L122-L140)
- [Custom-tensor Level Zero lifecycle](https://github.com/openvinotoolkit/openvino/blob/2026.3.0/src/plugins/intel_npu/src/backend/src/zero_infer_request.cpp#L1029-L1124)
