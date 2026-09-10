# The HEARTH Wire: The Proof of Work Paradox
## Chapter 08: The Farmer Doctrine

**Setting:** A solid, driving industrial groove begins to play. The sound of C# compilation—clean, structured, deterministic.

---

**ALEX:**
On April 8th, 2026, Derek began building in `E:\work\planning\`. The project was called **Farmer** ([`E:\work\planning\README.md`](file:///E:/work/planning/README.md)). And if there is any single repository in his entire archive that serves as the Rosetta Stone for how HEARTH works today, it is Farmer. 

Before we look at the code, Sam, why did he name it *Farmer*?

**SAM:**
Because throughout his career, whenever Derek—a working-class builder without an elite pedigree—would step in and cleanly solve an intractable architecture bottleneck that had stymied committees of credentialed engineers and consultants, the question whispered in the halls was always the same: *"Why didn't we think of that? How did that farmer solve this?"* 

He didn't take it as an insult; he took it as a badge of honor. It’s what you might call "farmer strength." A boutique gym athlete trains for vanity under controlled lighting, but a farmer lifting feed sacks, clearing brush, and wrenching seized machinery builds functional, unbreakable tendon density from daily physical contact with reality. When Derek sat down to build the sovereign control plane for autonomous AI agents, he didn't name it `EnterpriseAgentMesh`. He named it **Farmer**.

**ALEX:**
Because a farmer doesn't trust a forecast when he can walk out, feel the soil with his hands, and look at the clouds. Sam, introduce the technical architecture of Farmer in one paragraph.

**SAM:**
Farmer is a .NET 9 control plane running on Windows that orchestrates autonomous Claude CLI workers inside Hyper-V Ubuntu VMs, pairs them with a host-side retrospective agent using the Microsoft Agent Framework and OpenAI, coordinates via NATS JetStream and ObjectStore, and instruments every single stage with OpenTelemetry spans terminating in a local Jaeger instance.

**ALEX:**
Look at the 7-stage deterministic pipeline in `Farmer/README.md`:
1. `CreateRun`
2. `LoadPrompts`
3. `ReserveVm`
4. `Deliver` (SCP prompts and task packet to VM)
5. `Dispatch` (SSH execute `worker.sh`)
6. `Collect` (Read output manifest from mapped drive)
7. `Retrospective` (Run host-side MAF reviewer)

**SAM:**
And then look at [`docs/adr/adr-002-file-first-entry.md`](file:///E:/work/planning/docs/adr/adr-002-file-first-entry.md), written on April 8th. The title tells the entire story: **"File-first primary entry path via InboxWatcher."**

**ALEX:**
Let’s read the debate in that ADR. Because another AI agent working in a parallel branch had proposed an OpenAI-compatible HTTP API (`POST /v1/chat/completions`) with an in-process `ConcurrentDictionary<runId, Task>` holding run state in RAM. Why did Derek reject in-memory state?

**SAM:**
Let’s read his exact words from ADR-002:
> *"Run state was in-memory. A process restart loses every in-flight run. A crash leaves no forensic trail. Two processes can't share the same run queue. Scaling horizontally requires a real state store... The user framing was explicit: **filesystem remains the source of truth even when telemetry is present**."*

**ALEX:**
*"Filesystem remains the source of truth even when telemetry is present."*
Think about why that is such a radical engineering stance. Most engineers build databases, in-memory caches, message queues, and complex web services, and treat the filesystem as an afterthought. Derek did the exact opposite: *The folder on disk IS the state machine.*

**SAM:**
Every single run got its own directory under `planning-runtime/runs/{run_id}/`. And inside that directory sat the complete forensic truth:
- `request.json`
- `state.json`
- `events.jsonl`
- `result.json`
- `cost-report.json`
- `logs/`
- `artifacts/`
If the host machine lost power, if the VM crashed, if the network died mid-flight, you didn't have to guess what happened. You didn't lose your state. You opened the run folder, read `events.jsonl`, and knew the exact nanosecond where execution stopped.

**ALEX:**
And that brings us to the most famous architectural decision in the entire Farmer repository. Because the moment you declare that the filesystem is the source of truth, you run headfirst into the hardest problem in distributed state: what happens when two files disagree? In Chapter Nine, we examine the Anti-Drift Triple Invariant.
