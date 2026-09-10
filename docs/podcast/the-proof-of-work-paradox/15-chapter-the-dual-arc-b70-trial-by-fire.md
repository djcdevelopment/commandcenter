# The HEARTH Wire: The Proof of Work Paradox
## Chapter 15: The Dual Arc B70 Trial by Fire

**Setting:** The aggressive, high-airflow whine of dual blower-style workstation GPUs spinning under heavy load, punctuated by the sharp click of hardware relays. A heavy, industrial electronic drive propels the discussion.

---

**ALEX:**
In June and July 2026, the lab underwent a massive hardware transplant. The single NVIDIA GeForce RTX 4070 Ti in the primary chassis was replaced by a workstation setup: **two Intel Arc Pro B70 graphics cards**, each with 16 gigabytes of VRAM. Sam, why Intel Arc Pro? In an AI industry that treats NVIDIA CUDA as the only viable language in the world, why take the road less traveled?

**SAM:**
Because of cost per gigabyte of VRAM and architectural curiosity. NVIDIA datacenter GPUs and even high-VRAM consumer cards carry massive price premiums. Intel’s Arc Pro B70s offered sixteen gigabytes of fast memory per card at a fraction of the cost. But the price you pay isn't in dollars; it’s in software sweat. With NVIDIA, you get CUDA and cuBLAS off the shelf; everything works on day one. With Intel Arc on Windows, you are on the bleeding edge of **Vulkan compute kernels** and `llama.cpp` driver support.

**ALEX:**
Look at the files in `E:\work\`:
`llama.cpp-src`, `llamacpp-b10549-vulkan`, `llamacpp-b9279-vulkan`, `llamacpp-knee`, `llamacpp-qwen38`.
Look at how many custom builds of `llama.cpp` he compiled just to find the exact Vulkan dispatch constants that wouldn't hang the Intel driver!

**SAM:**
And look at the results displayed on `steppeintegrations.com` right now:
> *"CAUSAL RESULT: 19.7 → 131.5 tok/s. One Vulkan dispatch constant moved the ninth-request result by 6.7x."*
> *"OPERATING POINT: 0.69x → 5.49x. The 'slower' model reversed the verdict as real prompt depth increased."*
> *"WHOLE SYSTEM: 847 waves · 0 resets. Serving held through concurrent inference and disk traffic: the box survived the claim."*

**ALEX:**
Those aren't synthetic benchmark marketing slides from Intel! Those are battlefield measurements from an engineer who sat in front of the console, tuned Vulkan workgroup sizes, tested kernel dispatch depth, and proved that dual consumer-priced Intel cards could hold up under continuous concurrent agent traffic without crashing Windows!

**SAM:**
And that is what gave birth to **HEARTH**. In July 2026, `c:\work\commandcenter` was established. The gateway on port 8710 was locked down. The dual Arc cards were mapped:
- `omen-arc` on port 8082 as the resident workhorse.
- `omen-swap` on port 8081 as the rotation slot.
- `am4-oxen` on the network as the MoE backend.
- And `hearth.projection.public_portfolio` publishing the proof directly to `steppeintegrations.com`.

**ALEX:**
The machine was built. The fleet was real. The numbers were climbing into the hundreds of thousands.
And then, late tonight, as we were running our cold-context analysis on the live gateway... the front door broke.
In Chapter Sixteen, we conduct the forensic autopsy of the zero-day crash that blinded the gateway: The Sixty-Byte Murder Weapon.
