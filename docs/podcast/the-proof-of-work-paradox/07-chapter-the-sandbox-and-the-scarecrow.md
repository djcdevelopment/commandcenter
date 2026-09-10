# The HEARTH Wire: The Proof of Work Paradox
## Chapter 07: The Sandbox & The Scarecrow

**Setting:** A low, metallic clink—like a deadbolt sliding into place on an industrial steel door—followed by the dry hum of virtualized hardware booting up.

---

**ALEX:**
By the first week of April 2026, Derek made a crucial pivot in his architecture. He moved from running local models directly on his primary Windows desktop to running autonomous Claude Code agents in dedicated virtual machines. In `E:\work\start\scarecrow\`, we find an Electron application called **Scarecrow** ([`E:\work\start\scarecrow\README.md`](file:///E:/work/start/scarecrow/README.md)). Sam, what was the problem Scarecrow was designed to solve?

**SAM:**
The problem was blast radius. When you unleash a tool-calling frontier agent with permission to run bash commands, edit files, and install dependencies, you cannot run that on your primary workstation where your personal files, SSH keys, and system configuration live. You want the agent to operate in "full dangerous mode"—no human-in-the-loop approval prompts for every single `mkdir` or `npm install`—so it can actually make autonomous progress while you sleep. But you need to contain the explosion.

**ALEX:**
So he spun up Hyper-V virtual machines running Ubuntu: `claudefarm1`, `claudefarm2`, `vm-golden`.

**SAM:**
Right. The agents lived completely inside those Hyper-V VMs. But then you hit a second problem: *How does the human on the Windows host observe what the agent inside the VM is doing in real time without constantly remoting in over SSH?*

**ALEX:**
And how did he solve that?

**SAM:**
Through filesystem projection and an Electron dashboard. He mapped the VM’s `/home/claude/projects/` directory back to the Windows host as a virtual drive letter using SSHFS-Win: Drive `N:`, Drive `O:`, Drive `K:`.
Then he built Scarecrow. Scarecrow sat on the Windows desktop, polling those mapped drives. It read `.comms/progress.md` and `.comms/services.json`. It gave him a real-time HUD of every running VM:
- Was the agent online, offline, working, or blocked?
- What Git branch was it on?
- What was its last commit message?
- What local web services had it started?

**ALEX:**
And look at the port management. If you have three VMs running parallel web apps on port 3000 or 5173, how do you preview them on the host without network port collisions?

**SAM:**
Look at the table in `scarecrow/README.md`:
> *"Port Offset Scheme: Each VM gets a port offset to avoid collisions when tunneling.*
> - *VM 1 (+0): remote :3000 → localhost:3000*
> - *VM 2 (+100): remote :3000 → localhost:3100*
> - *VM 3 (+200): remote :3000 → localhost:3200"*

**ALEX:**
That’s so pragmatic. No complex software-defined overlay network, no Kubernetes ingress controller. Just an arithmetic port offset scheme and mapped drives over SSHFS.

**SAM:**
And look at how the agents communicated:
> *"Builder Integration: Deploy `skills/register-service.md` to each VM's `.claude/skills/` directory. Builders will then write to `.comms/services.json` when starting or stopping services, and Scarecrow will auto-detect them."*
He was already using agent skills to standardize how the autonomous workers reported their state to the host control plane.

**ALEX:**
Scarecrow proved that you could run autonomous builders in isolated VMs and monitor them from a central cockpit. But Scarecrow was just a viewer; it didn’t control the workflow. It couldn't orchestrate multi-stage pipelines or enforce data contracts. And that brings us to the pivotal repository of mid-April 2026: **Farmer**. In Chapter Eight, we examine the birth of the Farmer Doctrine.
