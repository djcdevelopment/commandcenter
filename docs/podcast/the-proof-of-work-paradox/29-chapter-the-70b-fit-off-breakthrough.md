# The HEARTH Wire: The Proof of Work Paradox
## Chapter 29: The 70B Fit-Off Breakthrough

**Setting:** The roar of dual GPU axial fans at maximum RPM. The high-pitched electronic whine of power delivery chokes under heavy load. A tense, aggressive techno-industrial rhythm underscoring the physical battle against memory architecture and bus bandwidth.

---

**ALEX:**
Sam, in May 2026, Derek made a radical hardware bet. 

The rest of the AI industry was renting NVIDIA H100s in the cloud at five to ten dollars an hour, running up enormous monthly cloud bills just to experiment with large models. 

Derek refused to pay cloud rent. He bought two Intel Arc Pro B70 32GB GPUs—the Battlemage architecture—and decided he was going to run a full 70-billion parameter model locally on Windows. 

What happened when he first tried to boot it?

**SAM:**
It collapsed. 

The document is in `E:\work\battlemage\arc-b70-dual-70b-windows-vulkan.md`, dated May 24th, 2026. 

On paper, the math worked: two 32GB cards give you 64 gigabytes of VRAM. A 4-bit quantized 70B model requires roughly 40 to 45 gigabytes with KV cache. It should have fit easily across the two PCIe slots.

**ALEX:**
So why did it crash?

**SAM:**
Because of the Windows Vulkan memory allocator and PCIe topology. 

When `llama.cpp` initialized the weights across the two physical adapters, the Vulkan driver attempted to dynamic-allocate memory buffers based on default heuristic sizing. 

The moment the prompt evaluation exceeded a certain threshold, the memory allocator panicked. It spilled weights out of the GPU's dedicated high-speed GDDR6 memory across the PCIe bus and directly into host system DDR5 RAM.

**ALEX:**
And PCIe bandwidth is orders of magnitude slower than GDDR6 memory bus bandwidth.

**SAM:**
It was a catastrophe. 

The token generation speed plummeted from a usable 14 tokens per second down to 0.4 tokens per second. The entire machine froze. Windows desktop manager stuttered. The PCIe bus was completely saturated with spilled memory pages.

**ALEX:**
Most engineers at that point give up and say: *"Intel Arc drivers on Windows are broken. Vulkan is buggy. I'll just go buy an expensive NVIDIA card or rent cloud compute."* 

What did Derek do?

**SAM:**
He went into the source code and the command-line flags. 

He discovered the critical parameter: `-fit off`.

By default, the runtime tried to dynamically "fit" allocations into perceived available space. But Derek explicitly disabled auto-fit, forced strict boundary reservation on each physical device, and pinned the layer splits manually. 

Card zero got layers 0 through 42; card one got layers 43 through 80. Zero spill. Zero PCIe memory thrashing.

**ALEX:**
And then he took it even further, didn't he? He didn't just fix the flag—he wrote custom diagnostic tools.

**SAM:**
He built `b70tools` in modern C++20. 

Instead of relying on bloated third-party monitoring software that polls every five seconds and introduces CPU scheduler jitter, `b70tools` was a lightweight, passive telemetry observer that hooked into the GPU telemetry pipes and ASPEED AST2600 BMC controllers. 

And look at the benchmarks he unlocked:
- Under Vulkan: 4.17 tokens per second at 25k context.
- But when he moved to IPEX-LLM under Intel SYCL: **14.47 tokens per second**. 
- A **3.5x speedup** on identical consumer-accessible silicon!

**ALEX:**
Think about the mechanical sympathy in that achievement, Sam. 

Understanding how silicon moves bytes across copper traces on a motherboard. Refusing to accept that a 70B model couldn't run on local dual cards. Writing the C++ observer tools to prove the link bandwidth. 

That wasn't cloud magic. That was wrenching on an engine until every cylinder fired in time.

---
*End of Chapter 29. Next: Chapter 30 – The Working-Class Apprentice Engine.*
