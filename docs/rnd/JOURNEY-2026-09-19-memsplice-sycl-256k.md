# Fourteen hours, twenty-two laps: the day a five-GPU home fleet learned to work at 256k

*A narrative record of R&D session `cc-544e4480` (2026-09-19, 00:33Z → 14:30Z) on Derek's hybrid
fleet — written so it can be read, retold, or turned into a podcast. Every number here is on the
register at `docs/rnd-log.md` (rows for laps 7–12c and L0–L4h) with result JSON under
`C:\work\memsplice\results\`. Where a number is one sample, it says so.*

---

## 0. The cast and the question

**The machines.** Two boxes on a desk, one cable between them.

- **OMEN** — Windows 11, Core Ultra 285K, 128 GB RAM, and **two Intel Arc Pro B70s** (32 GB each).
  This is production: an always-on gateway called HEARTH routes every agent's "grunt work" to a
  30-billion-parameter mixture-of-experts model that lives on those two cards. The engine is
  llama.cpp on the **Vulkan** backend, Derek's own fork (he has an open upstream PR that fixed a
  performance cliff on these cards).
- **AM4** — Ubuntu, an **RTX 4070 Ti** and an **RTX 5070** (12 GB each). NVIDIA, CUDA, the cards
  everybody writes software for first.
- **fx99** — a monitoring node with an RTX 2070 SUPER that keeps production warm and reads its pulse.
- **The link** — a direct **1 GbE** cable between OMEN and AM4 (about 112 MB/s in practice). A
  faster card is "not in the cards"; the fleet has to make do.

**The models.** Qwen 3.8, three flavours: a **27B dense** model (every weight touches every token),
a **30B-A3B mixture-of-experts** (production's workhorse), and **Flash-Next** — a 93.7 GB, 512-expert
hybrid that mixes state-space layers with attention and advertises a 262,144-token context.

**The question Derek set, in his words:** *"it's good to know the limits because these qwen3.8
models specifically are known for getting better over longer tasks... as an expression of total
throughput and accuracy of the work completed on local hardware, that's the real goal, so even
theoretical slowness might be desirable if it means we can keep the b70's saturated and actively
decoding."* The scoreboard is not latency. It is **work completed × accuracy, on hardware that is
already paid for, with the B70s kept decoding.** The concrete target: **run the 27B at a 256k context**
— and if 256k could prefill in three minutes on Intel, *"that'll be a miracle and we should stop
there, document and celebrate."*

**The method.** R&D mode: one edge per lap, vertical slices, no test files, every lap written to the
register whether it wins or loses. A clean negative is a result.

---

## Act I — MemSplice: prefill over the wire (laps 7–12c, 00:33–04:45Z)

### The idea

A long prompt costs twice: once to **prefill** it (read every token, build the attention "KV cache"),
then again every generated token (**decode**) as the model re-reads that cache. On the B70s, prefill of
a huge prompt is slow. On the NVIDIA cards it is fast — but they only have 12 GB each. MemSplice is the
trick: **prefill on the NVIDIA pair, save the KV cache to a file, ship it over the cable, restore it
into the B70s, and decode there.** The B70s never pay the prefill.

### Lap 7 — does it work for a dense model?

Yesterday it had worked for the mixture-of-experts. Today: the 27B dense. Prefill over the wire
beat a single B70 by **3.14× at 16k and 4.90× at 30k**, and beat both B70s together by **1.92× / 2.74×**.
The hybrid attention of this model makes its cache small (40–45 KB per token) — the wire is cheap.

One trap found and kept: a restored cache for a hybrid/recurrent model is **silently discarded**
unless the next request extends it by at least one token. The benchmark grew a `--hot-suffix`.

### Lap 8 — how deep can AM4 go?

The NVIDIA pair's ceiling for the 27B: **128k tokens with a 4-bit KV cache** (119k tokens at
1,462 tok/s), 96k at 8-bit, 160k refused at the door. 19.8 KB per token.

### Lap 9 — the number that reframed the day

Ship the 119,203-token cache to the B70s. It crossed. The answer was **exact** — token-for-token the
same as decoding on the CUDA card that made it. And the comparison: the dual B70s prefilling that
prompt themselves take **16.9 minutes**; disaggregated it takes **~103 seconds. 9.8×.** Decode at that
depth on the B70s: 4.9 tokens/second.

### Laps 10–11 — capacity, not speed

Derek: *"in R&D looking for capacity and capability limits."* Eight 119k contexts fit on the B70s at
once — but with a unified cache every sequence pays attention over every cell, so aggregate decode
stays flat at ~5 tok/s however many are resident. *Memory yes, compute no.* And a 133k prompt — past
AM4's ceiling — was served by **splitting** it: prefill what fits, ship it, let the B70s finish the
tail at 44 tok/s.

### Lap 12 — the patch

Restoring a one-stream cache into a multi-slot server needed a flag that hurt everything else.
Derek: *"i've already submit one PR for llama.cpp and it was a huge performance boost, so i'm
completely open to trying it again."* So: a patch to `llama-kv-cache.cpp` that lets a saved state
restore across cache layouts. Measured: idle residency becomes free (4.89 vs 0.75 tok/s), and the
~6 tok/s aggregate ceiling at 119k stands — attention-compute-bound, not a memory problem.

Then his call: *"let's bake these changes into AM4 first. we're not going to publish a PR for
llama.cpp today"* — and twenty minutes later, *"let's patch OMEN's prod too, otherwise i might forget
later."* By 04:45Z every build on the fleet carried the patch, production included (fork `60cdd25`,
build 52, verified at 110.6 tok/s single-stream). The upstream PR sits parked in a worktree.

---

## Act II — the engine question: SYCL vs Vulkan (L0–L4, 04:50–07:05Z)

### The desk lap

Derek: *"look online at vllm and their most recent product offerings... i believe the SYCL backend is
supposed to be quite a bit faster than llama's."* Three read-only sweeps settled it: vLLM runs on B70s
now — **but Linux only**, and the WSL2 dual-Arc bridge on this host is kernel-dead. Parked with a
written reopen condition. llama.cpp's SYCL backend, though, had merged something big in July: **an
XMX/oneDNN flash-attention kernel claiming 4.26× prefill at 80k on a B70.** Nobody had measured both
backends' newest kernels on the same card at the same depth. That was the gap, and Derek approved a
ladder to walk it: L1 environment, L2 correctness, L3 production's shapes, L4 depth, L5–L8 beyond.

### L1 — can it even build?

Intel's oneAPI toolkit went in (with Derek's UAC click), except winget refused it (a ghost registry
entry), the installer wanted `--action modify`, and Intel's own `setvars.bat` failed on this box. A
hand-written `sycl-env.cmd` calls each component's environment script by full path. The knee fork
built SYCL on Windows at the exact production commit; both B70s enumerated.

### L2 — is it correct?

On one B70, same fork, same card, same prompts: the **27B dense is byte-identical at T=0** between
SYCL and Vulkan, with SYCL prefilling **2.16× faster at 16k**. The **mixture-of-experts is not
deterministic** on SYCL at T=0 — the same prompt gives different (both coherent) answers. That is a
policy question for Derek, not a bug to fix tonight.

### L3 — does production's shape exist?

Production's exact shape — the MoE split across both cards, eight 16k slots — **loads on SYCL**, honours
the tensor split, and matches single-stream throughput (104.9 tok/s). `-sm tensor`, which segfaulted
on AM4 in June, works here.

### L4 — the knee that wasn't

The Vulkan prefill curve for the dual-B70 27B: 609 tok/s at 16k, 443 at 30k, **117 at 119k** — a knee.
The SYCL curve: **813 / 817 / 736 / 614.** No knee. **5.2× at 119k.** The July claim held on these
cards. Tensor-split decode at 119k: 6.74 tok/s, +38% over Vulkan's 4.9.

That single measurement moved the MemSplice policy: at 119k, disaggregation is now a 1.9× win over
SYCL-local rather than 9.8× over Vulkan-local. The wire became the larger term.

---

## Act III — the levers (L4b–L4g, 06:45–11:55Z)

### L4b — the 256k question, and the bar

Derek: *"does that mean i could run a qwen3.8-27b on a (or both) b70's and have a 256k context?"*
Yes: the 27B **loads at 262,144 context on both B70s** (2.3 GB KV per card at 4-bit). A 248,508-token
prompt prefilled in **9.2 minutes on SYCL alone (449 tok/s)**; hybrid — AM4 prefills its 128k share,
ships it, the B70s finish — **7.2 minutes.** The bar was three. *"well if hybrid AM4 helps go for it
(and a lil over 3 min still excellent)."* And L7 closed for free: a CUDA-made cache restores into a
SYCL seat. The cache file is backend-agnostic.

### L4c — the f16 unlock

Derek: *"does our new tooling unlock anything we haven't tried yet... maybe more specific memory
placement and allocation or routing or tolerance for chunking?"* Reading the SYCL attention source
answered it: the fast XMX path is prefill-only; **decode with a quantized cache de-quantizes every
step, but an f16 cache hits native SDPA.** Measured: decode at 119k **4.24 → 9.41 tok/s; at 248k
2.24 → 5.89.** 2.2–2.6×, no prefill cost. A larger micro-batch (`-ub 4096`) added 19% to prefill;
256k now prefills in **8.5 minutes**. Derek: *"let's go!!! good find! good find!"*

### The inventory

Derek: *"let's do another pass of our tools and what levers we can pull on what we haven't tried
yet... instead of saying no, we're inventorying what we have, what exists."* The result is
`LEVERS-256K.md`: OMEN turns out to already have a **10 GbE port** (AM4 is the 1 GbE end); the fork
ships `ggml-rpc` (remote GPUs as devices) and an OpenVINO backend (off); an **MTP speculative-decoding
head** for the 27B is already on disk; n-gram drafting needs no model at all; fx99's 2070 SUPER is a
possible fifth device. Nine levers, each with a cost and an untested status.

### L4d — the screenshot

The first MTP attempt at 256k. Derek, from Task Manager: *"curious why there's 91 gig in sys ram"*
— then *"i think we overfilled and it dumped to system memory."* He was right: a B70 over its ~31 GB
budget had silently paged 24.8 GB to shared system RAM. Windows never says no; it just gets slow. This
is exactly the cliff documented in `denning`, Derek's own memory-management repo from June — *"it
would be worth an /rnd detour... but that was before i knew about the splicing."* The detour produced
a spill gate (`gpu-mem-gate.ps1`) that runs before every benchmark, and the acknowledgement that
`b70tools`, which he built for exactly these questions, is the proper instrument.

### The incident

Mid-L4d, production went down. Two model loads died at their five-minute timeout. I blamed the GPUs —
WDDM, a wedge — and Derek approved a driver-level card reset. It was unnecessary. The cause was a
**runaway `grep -r` from a read-only search agent** sweeping `E:\work\battlemage` — the models
directory, hundreds of gigabytes of `.gguf` — saturating the disk at 350 MB/s. Kill the grep;
production came back. Lesson recorded: **read the disk counters and per-process I/O before blaming
the GPU; never let a recursive search touch a models directory; kill an agent's leftover background
work.** (Keep that lesson in mind. It comes back.)

### L4d resumed — MTP doubles decode, exactly

Derek: *"back in the saddle, let's go."* The multi-token-prediction head drafts several tokens; the big
model verifies them in one pass. At T=0 the output is **identical** — same tokens, faster. Decode at
119k: **9.41 → 19.98 tok/s. At 248k: 5.89 → 14.37.** Three drafted tokens per step is the sweet spot;
six over-drafts. N-gram drafting on prose: worse than none. Clean negative.

### L4e/L4f — Derek's research list

*"while we were limit capped i was doing some research. take a look at these levers and see if we
have tried them yet (i want to try everything, don't just decide it's not worth it)."*

- **Asymmetric KV** (8-bit keys, 4-bit values): 5.84 tok/s — a *memory* lever (3.4 GB/card at 256k),
  not a speed one. f16 keys with 4-bit values lose 44% prefill because they fail the XMX type gate.
- **Ahead-of-time compiled kernels** (`bmg-g31`): identical to JIT within noise. Only removes the
  first-run compile.
- **The four-GPU RPC pipeline.** Build `ggml-rpc-server` on AM4, expose both NVIDIA cards over the
  cable, and OMEN's server sees **four devices** — `RPC0, RPC1, SYCL0, SYCL1` — and layer-splits one
  model across two hosts. It loaded. It ran. Output identical run-to-run. On 1 GbE it is a **decode
  lever (+24%) and a prefill loss (−28%)**: activations cross as f32, ~42 MB per micro-batch, and RPC
  devices have no async overlap. One rpc-server for both remote cards beat two (the hop between them
  was client-mediated otherwise). The traps: the CMake target is `ggml-rpc-server` not `rpc-server`;
  AM4's firewall needed a rule; `.cmd` files need CRLF for `goto`; Git Bash strips the inner quotes on
  comma lists, so seats launch from PowerShell.

### L4g — "run them all down to the ground"

- Vulkan with an f16 cache: **no decode gain.** The f16 unlock is SYCL-specific — Vulkan's wall is its
  attention kernel, not dequant.
- SYCL 8-bit/8-bit cache: **4.15 tok/s, the slowest** of all — a known Xe2 Q8 kernel problem reaching
  the cache path. The SYCL cache ladder is complete: q8 < q4 < q8/q4_1 ≈ f16/q4 < **f16**.
- **An f16 119k state saved on the B70s (16 s, 7.97 GB) restores INTO the four-device cache** — the
  cache placed per layer across two hosts, two 2 GB slices crossing the cable — **and decodes with MTP at
  21.6 tok/s.** Weight the split toward the NVIDIA cards (`-ts 2,2,1,1`, they carry two-thirds of the
  layers) and it reaches **24.9 tok/s at 119k — the fleet's best depth decode.** That morning, the same
  prompt decoded at 4.9 on Vulkan.
- fx99 as a fifth device: blocked on CUDA architecture (AM4's build has no sm_75); a build started.

---

## Act IV — the wild find (L4h, 12:22–14:30Z)

Derek: *"if we think we can actually fit the flash-next, that would be a wild find... or the 2,2,1,1
let's do them both either order."*

### 256k on four devices: parity

The 27B's 248k f16 state (16.45 GB) restored into the four-device cache in 34–62 seconds and decoded
with MTP at **13.1–14.0 tok/s** — against 14.37 on the dual B70s alone. At 256k the NVIDIA cards
cannot hold the layer share that won at 119k plus a 256k cache; the split falls back onto the B70s
and the cable becomes pure cost. **The 27B at 256k is a dual-B70 job: 8.5 minutes of prefill, 14.4
tok/s with MTP.** A clean, useful negative.

### Flash-Next

Derek's second message: *"if we can get a 256k context flash-next to work, that would be beyond
amazing, because we could use it for planning on how to have the 256 context 27b models run."*

Flash-Next's architecture (`qwen4exp`) is not in the knee fork; upstream master has it. So: a
**master build** with SYCL and RPC on OMEN, a matching master `ggml-rpc-server` on AM4. The first
launch died instantly — master had replaced `--no-mmap` with `--load-mode`. Drop the flag. The
second loaded in **11.7 minutes** (16.8 GB of experts streaming over the cable; the rpc-server's local
cache makes the reload 80 seconds): 49 of 49 layers on GPU across four cards, the 27.5 GB per-layer
token embedding kept on the CPU. Gate clean. It **talked** — coherent, 19 tok/s. A 16k prompt:
first pass 98 tok/s prefill, second pass 287; the difference was master's default *lazy* mode
memory-mapping that 27 GB embedding and page-faulting it off disk per token. `--load-mode none
--lazy-mode off` pins it in host memory. The 16k answer was correct — and, being a MoE on SYCL, not
deterministic.

Then 262,144. **The first launch refused loudly**: the cache fit everywhere, but the 4070 Ti needed
a 6.2 GB compute buffer on top of 9.5 GB of weights. Read the refusal, fix that one thing: lighter
split (`4,5,20,19`), half the micro-batch (`-ub 512`). Loaded in 80 seconds. **B70s at 29.8 / 29.1 GB
dedicated, zero shared** — under the cliff. NVIDIA cards at 10.2 / 10.9 of 12.

The 248,515-token body went in. Prefill: **51.3 minutes** — 186 tok/s at 32k, falling to about 60 by
the end, 81 average. Decode: **3.5 tok/s** (two samples). The answer: coherent and grounded — it named
the real modules in the packed code. The hybrid state **saved in 28 seconds: 6.52 GB for 248k tokens,
28 KB per token** (the 27B's f16 cache is 66). That context never has to be paid for again.

**And the disk incident came back.** Halfway through the prefill, E: was at 317% disk time. Not the
model: **five recursive `grep -r … /e/work` sweeps from other agent sessions**, reading every `.gguf`
under the models directory. Per-process I/O counters found them in one command; killed, the disk went
to 0% — and the prefill rate *kept falling anyway*, which means the depth limiter is in the engine
path, not I/O. Nothing was saturated: B70s 11–25% busy, NVIDIA idle, one CPU core half used. That is a
**can't-answer-why row**, written as one.

Derek, at the end: *"yeah, let's not do any more of those long runs, i'm gonna get bored and forget
to come back here."* Now a standing rule: laps stay minutes-scale; anything over ten gets an
estimate and his cue first; depth samples come from saved states, not re-prefills.

---

## 5. The scoreboard, before and after

Qwen3.8-27B on the fleet. "Before" is the Vulkan production fork at the start of the day.

| Measurement | Start of day | End of day | How |
| --- | --- | --- | --- |
| Dual-B70 prefill at 119k | 117 tok/s (a knee) | **614 tok/s** | SYCL backend, XMX flash attention |
| Dual-B70 prefill of 248k | not attempted | **8.5 min** | SYCL, `-ub 4096` (bar was 3 min) |
| Decode at 119k, dual B70 | 4.9 tok/s | **19.98 tok/s** | f16 KV (9.41) + MTP |
| Decode at 248k, dual B70 | — | **14.37 tok/s** | f16 KV (5.89) + MTP |
| Decode at 119k, best on fleet | 4.9 | **24.9 tok/s** | f16 state → four-GPU RPC cache (`-ts 2,2,1,1`) + MTP |
| 119k prompt, prefill elsewhere | 16.9 min local | **~103 s** ship + restore | MemSplice (AM4 prefill at 1,462 tok/s) |
| Max context on the B70s | 128k (Vulkan seats) | **262,144** | loads; measured at 248k |
| Restore a saved state across layouts | needs `-kvu` | free | the across-layouts patch, in production |
| Flash-Next at 262,144 context | "parked" | **runs** on four GPUs | 51 min prefill, 3.5 tok/s, 6.5 GB state |

Every "after" is a measurement on file, most with two or more samples; the Flash-Next decode is two
samples and its prefill is one run.

---

## 6. What we still don't know

- **Why 90 → 60 tok/s at depth on Flash-Next with nothing busy.** Suspects: SYCL's IQ4_XS
  mixture-of-experts kernel, the XMX attention path wanting `-ub ≥ 1024` (which the 256k compute
  buffer forbids on a 12 GB device), synchronous RPC per graph split.
- **Whether the Flash-Next state restores into a fresh seat** (hybrid: KV + recurrent state, across
  four devices). The file exists; untested.
- **fx99 as a fifth device** — an sm_75 build was started, never shipped.
- **The MoE non-determinism policy** on SYCL — Derek's call.
- **MTP for Flash-Next** — the sidecar lacks the hybrid-attention tensors; off.
- **L5 and L6** of the ladder — jobs-per-hour at production's shape, and deep concurrency on SYCL.
  Those decide whether SYCL replaces Vulkan under production, and they were not run.
- **The 10 GbE port on OMEN** — every RPC number above is a 1 GbE number.

---

## 7. How the day actually went (process, not numbers)

- **Twenty-two laps, one edge each.** The register has a row for every one, including the negatives:
  n-gram drafting, AOT, asymmetric KV, Vulkan-f16, q8 cache, 256k-on-four. Negatives saved future laps
  as surely as wins.
- **"Instead of saying no, inventory what we have."** The levers document turned "we can't" into
  nine tries with costs attached. Four of the nine paid off (f16, MTP, RPC, `-ub`); the rest closed
  cleanly.
- **The machine was consulted, not reasoned about.** The f16 finding came from reading kernel source
  and then *running* it. The spill came from Derek reading Task Manager. The disk incident came from a
  counter, twice — and the second time the counter was read first.
- **Two misdiagnoses, both mine, both disk.** The first cost a needless GPU reset. The second cost
  twenty minutes of a wrong story about a lazy-loaded embedding before the per-process I/O counter told
  the truth. The lesson is now in memory with the exact one-liner that finds it.
- **The loud refusal is the friend.** The 256k launch that *refused* (a 6.2 GB compute buffer on a
  12 GB card) was fixed in one relaunch. The run that *silently* paged 24.8 GB to system RAM was found
  by a human looking at a screenshot.
- **Decisions ended deliberation.** "Bake it into AM4, not upstream." "Patch prod too." "A little over
  3 min still excellent." "No more long runs." Each one was a sentence, and each moved the work.

---

## 8. Glossary for the hosts

- **Prefill** — reading the prompt: every token, all at once, building the model's working memory.
  Fast per token, but proportional to prompt length and slower as the prompt deepens.
- **Decode** — generating one token at a time, each re-reading that working memory.
- **KV cache** — the working memory: per-layer keys and values for every token seen. Its size per
  token is a property of the model (27B dense: 66 KB in f16; Flash-Next: 28 KB).
- **MemSplice** — prefill on one machine, save the KV cache, ship it, restore it on another, decode
  there.
- **f16 vs q4_0 / q8_0 KV** — how many bits each cache entry keeps. Smaller saves memory; on SYCL,
  smaller also costs a de-quantization every decode step.
- **MTP (multi-token prediction)** — a small "draft" head proposes several next tokens; the big model
  verifies them in one pass. At temperature zero the output is identical, only faster.
- **SYCL / Vulkan** — two GPU programming backends llama.cpp can use on Intel Arc. Vulkan is the
  graphics-API route; SYCL is Intel's compute route via oneAPI, with hand-tuned kernels (XMX = the
  matrix units).
- **ggml-rpc** — llama.cpp's way to treat a GPU on another machine as a local device over TCP.
- **WDDM spill** — on Windows, a GPU over its memory budget silently pages to system RAM instead of
  failing. Everything keeps running, slowly. `denning` documents it; a gate now checks for it.
- **Mixture-of-experts (MoE)** — a model whose feed-forward layers are split into many "experts," a
  few active per token. Big on disk, cheap per token.
- **Hybrid (SSM + attention)** — Flash-Next's design: most layers are state-space (constant memory
  per token), every fourth is attention. That is why its 256k cache is 6.5 GB, not 16.

---

*Sources: `docs/rnd-log.md` rows dated 2026-09-19 (session `cc-544e4480`); `docs/rnd/sycl-vs-vulkan/
WORKFLOW.md` and `LEVERS-256K.md`; `C:\work\memsplice\results\*` (drivers under
`tests/benchmarks/`); seat logs in `hearth/var/swap-logs/`; commits `2f6da9f..73d925e` (commandcenter)
and `0bd15f2..5b17e02` (memsplice).*
