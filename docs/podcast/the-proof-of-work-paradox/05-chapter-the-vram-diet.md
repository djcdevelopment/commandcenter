# The HEARTH Wire: The Proof of Work Paradox
## Chapter 05: The VRAM Diet

**Setting:** The mechanical sound of hard drives accessing data sequentially, accompanied by an analog synth pulse that cycles up, holds, and sweeps down—mimicking the loading and unloading of model weights into GPU memory.

---

**ALEX:**
Sam, let’s talk about the physics of GPU memory in March 2026. Because people today take for granted that you can just fire off an API call to a 2-trillion-parameter frontier model in the cloud and get an answer in three seconds. But Derek was committed to running locally on consumer hardware. Why was he so obsessed with staying on local iron?

**SAM:**
Because of the economics and sovereignty. If every single thought, every plan, every intermediate validation step in an autonomous loop has to round-trip to an external API at twenty dollars per million tokens, an iterative agent that loops fifty times on a single refactor will drain your wallet before you even get a working test suite. 

**ALEX:**
It’s the metered-fire problem. If every spark costs money, you become afraid to let the fire burn.

**SAM:**
Exactly. Sunk-cost hardware changes developer psychology. Once you purchase a GPU, the marginal cost of a token is just electricity—a fraction of a cent per hour. You can let a model think for forty-five minutes on an architectural brief overnight without waking up to a surprise four-hundred-dollar cloud invoice. But the trade-off is the VRAM wall. On a 12 GB RTX 4070 Ti, you cannot fit a 70-billion-parameter model. And you certainly cannot fit two 14-billion-parameter models side-by-side.

**ALEX:**
So you have to live on what he called the "VRAM Diet." How did he manage the transitions between Planner, Builder, and Evaluator without crashing Windows or running out of memory?

**SAM:**
He relied on Ollama's model swapping, but wrapped it in strict sequential orchestration. In `FINAL_ARCH.md`, he laid out the rule:
> *"Only one model runs at a time. Ollama swaps models automatically."*
When `plan.py` ran, it loaded Mistral 24B. The moment the plan was saved to disk as `runs/{date}-plan.md` and `runs/{date}-decisions.json`, the Python process terminated. The GPU flushed. Then the Builder script was invoked, triggering Ollama to load Qwen 14B into VRAM.

**ALEX:**
Think about the seed of that idea. That cold-start model swap—loading weights from NVMe into VRAM, running an inference job, flushing, and loading the next model—is the exact physical mechanism that eventually evolved into HEARTH’s rotation rungs!

**SAM:**
Direct lineage. In commandcenter today, we have:
- `omen-arc`: The resident, sunk-cost door default on port 8082 that stays resident on the Intel Arc cards.
- `omen-arc-oss`: Banked fire—pin-only because loading the 120B model costs a heavy model swap.
- `omen-swap`: The rotation rung on port 8081 with an explicit rotation window.
The vocabulary changed from a raw Python script calling Ollama in March, to an enterprise FastMCP execution control plane in August. But the underlying physics never changed: *VRAM is finite. Model weight residency has a real time-tax. You have to treat model swapping as a first-class scheduling decision rather than pretending memory is infinite.*

**ALEX:**
And while he was solving this memory diet on the local machine, he was already realizing something even bigger: text in a terminal wasn't enough. He needed a way to model the relationships between ideas, code, and goals. In Chapter Six, we look at the moment Derek started building his own graph database without even calling it a graph: ContextForge.
