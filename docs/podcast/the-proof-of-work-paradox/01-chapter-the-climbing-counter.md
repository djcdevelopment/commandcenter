# The HEARTH Wire: The Proof of Work Paradox
## Chapter 01: The Climbing Counter

**Setting:** The audio opens with the faint, rhythmic mechanical hum of server fans spinning up, mixed with the click-clack of heavy mechanical key switches. A clean, subtle electronic bass tone enters and establishes an analytical, documentary atmosphere.

---

**ALEX:**
If you pull open a browser right now and navigate to `steppeintegrations.com`, you are greeted by something that immediately stops you in your tracks if you understand modern distributed systems. Right there, below the architectural fold, sits a live, auto-updating telemetry console titled: *"HEARTH pulse · published aggregate."*

**SAM:**
And if you look at the raw numbers on that console as of this week in September 2026, the dials are spinning fast. Over two hundred and five thousand observed boundary events. Over three million input tokens tracked and audited. Eight hundred and twenty-seven token-bearing receipts. Hundreds of concrete inference invocations routed through local Vulkan kernels on dual Intel Arc Pro B70 cards. 

**ALEX:**
To anyone browsing the web from Silicon Valley, or Seattle, or London, it looks like an enterprise-scale computational machine room running at full throttle. It looks like an established engineering organization executing continuous, high-concurrency automated builds.

**SAM:**
It *is* a high-concurrency automated build system. But there is a massive paradox sitting right in the center of that dashboard. A tension between what the numbers appear to say to the outside world, and what is actually happening inside the hardware on the floor.

**ALEX:**
The user whose name is on the domain—Derek Ciula—dropped a prompt into the console late tonight that cut right to the bone of this entire project. He looked at that public dashboard and said: *"These numbers are going up on my page under my name. I need to show and prove to people I'm using them, not just use them. I need to ensure everything I do locally gets credited through HEARTH's manifest."*

**SAM:**
That sentence—*"I need to show and prove to people I'm using them, not just use them"*—is the catalyst for this entire investigation. Because when you put on the headphones of a cold-context auditor and inspect the actual append-only ledgers on disk, you realize that the counter is climbing, but the human being who poured the concrete and wired the motherboards is almost completely absent from the official record.

**ALEX:**
How does that happen? How do you build a private AI lab with three physical machines, custom GPU kernels, and append-only cryptographic verification, and end up in a situation where the website makes it look like the machines are running themselves while the architect is invisible?

**SAM:**
To answer that, you have to break down the composition of those two hundred thousand events. You have to open up `hearth/var/ledger/events.ndjson`—a hundred and seventy-four megabytes of raw NDJSON records—and run a cold-context spectrum analysis across every single packet that crossed port 8710. And when you do that, the illusion of the climbing counter begins to fracture into two completely different realities.

**ALEX:**
And that’s where our story starts. In Chapter Two, we take the lid off that 200,000-event ledger and listen to what the machine is actually whispering to itself in the dark.
