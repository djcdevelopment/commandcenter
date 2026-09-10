# The HEARTH Wire: The Proof of Work Paradox
## Chapter 11: The Inflection Point

**Setting:** The audio environment expands dramatically. The sound of multiple network switches clicking, packets routing across a LAN, and a low, resonant synthesizer drone signaling an expansion of scale.

---

**ALEX:**
We’ve arrived at late April 2026. If you look at the directory tree under `E:\work\`, you notice an explosion of new workspaces:
- `claude-fleet-control` (April 30)
- `repos/multi-agent-build-blueprint` (May 6)
- `steppe-strategy` (May 9)
- `Guild` and `contracts` (May 15)
- `campfire` and `lantern` (May 21)
- `manifest` (May 22)
Derek described this moment to us earlier tonight with remarkable clarity:
> *"that's the phase where I was just starting to think my experiments and training had become strong enough to say, 'I'm going to try and solve this problem' — which was distributed agentic building at that time."*

**SAM:**
Notice the phrasing: *"my experiments and training had become strong enough."* 
He spent March and early April building muscles. He figured out model swapping on the 4070 Ti in `planner`. He figured out protobuf data contracts in `contextforge`. He figured out VM sandboxing in `scarecrow`. He figured out anti-drift state machines and retrospective QA in `Farmer`. 
None of those were isolated toy projects; they were rigorous, iterative training sessions.

**ALEX:**
And by the end of April, he realized: the single-machine paradigm had hit an insurmountable hardware wall. Even with Hyper-V VMs on a single desktop, you run out of CPU threads, you run out of memory channels, and you run out of PCIe bandwidth. If you want parallel agents building complex software, you have to distribute the load across multiple physical machines.

**SAM:**
Let’s open [`E:\work\claude-fleet-control\README.md`](file:///E:/work/claude-fleet-control/README.md), dated April 30th, 2026. The mission statement is right there at the top:
> *"Scale from one manual agent cycle to a repeatable fleet loop:*
> 1. *A hopper receives rough ideas.*
> 2. *The conductor turns each idea into deterministic planning packets.*
> 3. *Multiple CLI Claude workers plan in parallel on isolated machines or VMs.*
> 4. *Workers report only through durable output artifacts.*
> 5. *The operator reviews, approves, rejects, or rehydrates any plan later.*
> 6. *Builds remain gated. Planning may be full auto; canonical build/promotion may not."*

**ALEX:**
Look at step 6: *"Planning may be full auto; canonical build and promotion may not."*
That is the core philosophy of human agency in distributed systems. You let the agents explore, plan, draft, and speculate in parallel at low cost. But when it comes to merging into the canonical repository—when it comes to deploying to production or promoting a build—the human operator holds the gate.

**SAM:**
And then look at his rules:
- *"Plan parallel and cheap. Build selective and expensive."*
- *"No unapproved build reaches canonical state."*
- *"A plan is not valid unless it can be rehydrated from artifacts."*
- *"The control center is thin: visibility, dispatch, approval gate."*

**ALEX:**
He was defining the rules of distributed autonomous labor before the enterprise world had even agreed on what an agent was. In Chapter Twelve, we look at the physical blueprint he drew to make this fleet real: The 3-PC Topology.
