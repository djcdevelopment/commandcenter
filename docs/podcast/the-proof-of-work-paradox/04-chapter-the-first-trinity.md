# The HEARTH Wire: The Proof of Work Paradox
## Chapter 04: The First Trinity

**Setting:** The audio steps back in time. The sound of high-end datacenter fans fades, replaced by the distinct single-fan whir of a desktop PC under moderate load. A softer, reflective synth pad accompanies the dialogue.

---

**ALEX:**
Let’s take the time machine back to March 18th, 2026. Almost six months before this 200,000-event snapshot. We’re looking at a folder on Derek’s secondary drive: `E:\work\start\planner\`. And inside that folder sits a design document titled [`FINAL_ARCH.md`](file:///E:/work/start/planner/FINAL_ARCH.md). Sam, read the opening declaration from that document.

**SAM:**
Section 1, *"System Identity"*:
> *"A serial, file-based, Git-backed planning system that runs one role at a time on a single Windows 10 machine with a 4070 Ti (12 GB VRAM). Not an agent platform. Not concurrent. Not a framework. Three Python scripts. Two PowerShell wrappers. Git. Ollama. That's it."*

**ALEX:**
Look at how grounded that is. In a world where the entire tech industry was losing its mind over autonomous multi-agent swarms, venture-backed orchestration platforms, and massive vector databases, Derek sits down at a desk with a single GeForce RTX 4070 Ti and says: *"Three Python scripts. Two PowerShell wrappers. Git. Ollama. That's it."*

**SAM:**
And then he wrote the sentence that anchored the entire philosophy:
> *"First deliverable: `plan.py` — reads a project brief, produces implementation stories with a decision trace. If that isn't useful, nothing else matters."*

**ALEX:**
He called it the Trinity: Planner, Builder, Evaluator. Break down how that triad actually worked.

**SAM:**
Because he only had 12 gigabytes of VRAM on that 4070 Ti, he couldn't run multiple models simultaneously. So he turned hardware scarcity into an architectural virtue. He established three strict cognitive roles:
1. **The Planner**: Running Mistral Small 3.1 24B quantized down to Q4_K_M, taking up about 11 gigabytes of VRAM. Its job was purely cognitive: read the project brief, read the guardrails, inspect recent results, and execute an explicit transition cycle: *select → refine → assess → decide*. And look at the constraint he put in: *The Planner never touches source code. It only writes to `.plan/`.*
2. **The Builder**: When the plan was locked, the Planner was unloaded from VRAM, and in came Qwen2.5-Coder 14B at Q5_K_M (about 9 gigabytes of VRAM). The Builder was a daytime worker. It received one fully specified story from the backlog. It wasn't allowed to interpret the philosophy; its job was to produce file edits and shell commands.
3. **The Evaluator**: When the Builder finished, it was unloaded, and in came Llama 3.1 8B at Q5_K_M (about 5 gigabytes). The Evaluator inspected the story, the build result, and the git diff. It emitted a typed pass/fail verdict with evidence. And its constraint: *The Evaluator never modifies code. It only writes verdicts.*

**ALEX:**
Think about the discipline of that. In March 2026, most people were throwing giant prompts into a single chat window, hoping the model would magically plan, write, debug, and review its own code all in one shot. And Derek recognized immediately: if you give the same agent the authority to write code and evaluate its own work, it will lie to you every single time. You have to separate the cognitive roles.

**SAM:**
*"Separation of Cognitive Roles."* That phrase was already appearing in his personal notes as early as February 15th, 2026. He realized that an AI cannot be its own judge and jury. 

**ALEX:**
And there was another physical constraint that shaped the Trinity: the physical limits of memory. In Chapter Five, we look at how the physical 12 GB VRAM ceiling forced him to invent the model-swapping mechanisms that would eventually grow into the MechNet rotation rungs.
