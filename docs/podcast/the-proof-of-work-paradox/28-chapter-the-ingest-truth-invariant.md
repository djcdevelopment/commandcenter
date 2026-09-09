# The HEARTH Wire: The Proof of Work Paradox
## Chapter 28: The Ingest-Truth Invariant

**Setting:** A sharp, architectural audio landscape. The sound of clean glass surfaces and responsive UI sliders, layered over the heavy, solid clunk of an immutable physical ledger closing. The mood is one of rigorous boundary enforcement.

---

**ALEX:**
Sam, if Chapter 27 was about capturing the mind as a graph, Chapter 28 is about the crucible where Derek tested how humans and machines observe that data in real time: liveView. 

When people hear  observability dashboard or UI, they usually think of pretty charts, Grafana panels, or React dashboards. But liveView wasn’t a dashboard. What was the core architectural battle being fought in E:\work\liveView in mid-March?

**SAM:**
The battle was over *who owns truth*.

In E:\work\liveView\PROJECT_CONTEXT.md, Derek wrote an iron rule that is repeated across three separate design specifications:

* Ingest owns truth. UI owns interpretation. The UI must not parse raw source artifacts as a primary mechanism.*

**ALEX:**
Break that down, Sam. Why is that such a profound statement?

**SAM:**
Because 95% of software frontends violate it. 

Most dashboards read raw, volatile logs, parse timestamps on the fly, calculate ad-hoc aggregates in JavaScript, and mutate local state as the user clicks filters. 

And what happens? Two engineers look at two different browser tabs and see two different versions of reality. The UI lies because the UI is trying to be the source of truth.

**ALEX:**
And in liveView, Derek enforced a strict CQRS architecture:
- ingest/: Pure, immutable command ingestion. It captures the raw event, stamps the cryptographic hash, records the physical offset, and writes the snapshot.
- ui/: Read-only projection models. It has three distinct views: conversation/, observatory/, and workbench/.

**SAM:**
And look at SNAPSHOT_CONTRACT.md, which we hashed at 846de1b6.... 

It specifies deterministic replay: you don't mutate history when you debug. If a developer or an agent wants to re-examine what happened at timestamp T-zero, the system performs a *strict deterministic replay*, calculating a structural diff against the canonical snapshot. 

Volatile fields—like local machine clock jitter or transient thread IDs—are explicitly stripped from the diff so you only measure structural domain changes.

**ALEX:**
Think about how that connects to the limits of human focus. 

In the commit notes and chat transcripts from that period, Derek was pushing his personal cognitive capacity to the edge. He was building for hours in intense, multi-system flow states. 

If you are operating at that speed, your tools cannot deceive you. You cannot afford to spend fifteen minutes wondering whether an error is real or just a front-end rendering artifact.

**SAM:**
*Ingest owns truth. UI owns interpretation.* 

That single invariant solved the problem. It meant that no matter how complex the agent workflows became, the underlying event log was un-corruptible. 

And that exact pattern is why, months later, Derek could build DMos—a 173,000-line spatial operating system with a raw WebGL2 cockpit—in under 24 hours without a single state-corruption bug. 

The cockpit rendered 22,000 instances at 60 frames a second because the cockpit was never allowed to touch canonical truth. It was just a lens.

**ALEX:**
The lens interprets; the engine records. 

And that principle didn't just govern UI. It became the bedrock of HEARTH itself, where 229,000 events are locked in an append-only ledger that no UI, no agent, and no human can retroactively alter.

---
*End of Chapter 28. Next: Chapter 29 – The 70B Fit-Off Breakthrough.*
