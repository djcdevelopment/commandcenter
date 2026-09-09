# The HEARTH Wire: The Proof of Work Paradox
## Chapter 12: The 3-PC Topology Blueprint

**Setting:** The rhythmic hum of three distinct computing chassis—each with its own pitch—blending into a three-part harmony. The audio conveys physical hardware distributed across a workshop.

---

**ALEX:**
On May 6th, 2026, Derek committed [`E:\work\repos\multi-agent-build-blueprint\README.md`](file:///E:/work/repos/multi-agent-build-blueprint/README.md). This wasn’t just a private repository; it was designed as a public companion guide to two articles on `steppeintegrations.com`:
- *"Dispatch + MAF + OTel: A Complete Multi-Agent Stack"*
- *"Five Layers for a Replicable AI-Assisted Build Session"*
Sam, break down the three pillars he declared in that blueprint.

**SAM:**
The three pillars were:
1. **Cowork with Dispatch (Build-Time)**: Running parallel AI-assisted engineering sessions with automatic worktrees and phone-pairing.
2. **Microsoft Agent Framework (Runtime)**: Three-tier middleware orchestrating agent-to-agent interactions via `AIAgent.RunAsync`.
3. **OpenTelemetry (Cross-Cutting)**: GenAI semantic conventions tying every build session, agent handoff, and runtime inference call under **one unified trace ID**.

**ALEX:**
And then he took that software stack and mapped it across physical hardware: the **3-PC Topology Pattern**. How did he divide the labor across three computers?

**SAM:**
He split the roles across three physical nodes on a local network:
- **PC 1: The Orchestrator**: The primary boundary machine. It runs the control plane, hosts the OpenTelemetry collector (Jaeger v2), handles ingress requests, and monitors overall fleet state.
- **PC 2: The Overflow / Worker**: The computational muscle. Dedicated to running heavy local builds, Docker containers, and autonomous agent worktrees without degrading the operator's primary interface.
- **PC 3: The Persistent / Service Hub**: The storage and local inference engine. Running dedicated GPU models, hosting durable message queues, and acting as the local model lab.

**ALEX:**
Look at how that 3-PC blueprint from May 2026 maps directly to the physical machines sitting in his lab today:
- PC 1 is **OMEN**, running the HEARTH gateway on port 8710, orchestrating execution.
- PC 2 is the **AM4 fleet node**, running the MoE cluster on port 8090 and Jaeger OTLP on port 4318.
- PC 3 is the local storage and media engine.
And how do they communicate?
Over Tailscale, with authenticated TLS headers, W3C TraceContext propagation, and port 8710 as the single unified MCP door!

**SAM:**
The continuity is stunning. People who see HEARTH in September think it was born as a standalone gateway. It wasn't. It was the natural crystallization of that 3-PC topology from May. 

**ALEX:**
And why three PCs on a LAN instead of thirty servers in AWS or Azure? In Chapter Thirteen, we explore the deep moral and philosophical vision behind Steppe Integrations: the democratization of engineering for the working class.
