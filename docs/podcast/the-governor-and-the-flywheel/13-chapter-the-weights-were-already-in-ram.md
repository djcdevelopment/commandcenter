# The HEARTH Wire: The Governor and the Flywheel
## Chapter 13: The Weights Were Already in RAM

**Setting:** Rain on a window. Somebody thinking out loud rather than presenting. Then a single clean note as the arithmetic lands.

---

**ALEX:**
This one starts with Derek reasoning aloud, not with a measurement.

**SAM:**
He'd been circling a bigger idea all evening — that the fast processor, the hundred and twenty-eight gigabytes of system memory, and the neural processor together make a substrate specifically suited to a very large mixture-of-experts model.

**ALEX:**
Explain why that model type matters.

**SAM:**
In a mixture-of-experts model, only a small fraction of the parameters activate for any given token. The one on this machine has five hundred twelve experts per layer and uses eleven of them per token. So the storage is enormous and the per-token working set is small.

**ALEX:**
But you don't know in advance which eleven.

**SAM:**
Which is the whole problem. You have to keep all of them somewhere fast, because any of them might fire. That's ninety-four gigabytes. There is no card with ninety-four gigabytes here — there's about thirty-one on each of two cards.

**ALEX:**
So the model can't go on the cards.

**SAM:**
Not that model. But it fits comfortably in a hundred and twenty-eight gigabytes of system memory, and the integrated graphics and the neural processor both read system memory directly. No transfer. That's his argument, and it's a good one.

**ALEX:**
Then he adds the observation.

**SAM:**
He says: what I found is that weights held on the GPU are also cached in RAM anyway, since they need somewhere to swap to for fallback.

**ALEX:**
Meaning the duplication already exists.

**SAM:**
Meaning the graphics driver requires a system-memory commitment behind every card allocation, because when memory pressure comes it has to evict somewhere. And it happened to be checkable in about thirty seconds, because the server had just restarted.

**ALEX:**
What did it show?

**SAM:**
The inference server process holds thirty point three one gibibytes of private commitment. And its own startup log accounts for what's on the cards: seventeen point two eight of weights, twelve of cache, zero point seven eight of compute buffers. Thirty point zero six.

**ALEX:**
Thirty point three one against thirty point zero six.

**SAM:**
Apart by eight tenths of one percent. Every byte on the cards has a byte committed in system memory behind it.

**ALEX:**
So he was right, and it's not approximately right, it's arithmetically right.

**SAM:**
And the consequence is stronger than "no extra cost." For a model that fits on the cards, you're paying twice today — once in card memory, once in host commitment. For the ninety-four gigabyte model, you're not paying twice, you simply can't play, because there's no card memory to pair with the host copy.

**ALEX:**
So going through system memory isn't a compromise for the big model.

**SAM:**
It's strictly cheaper in total memory than the discrete path could ever be, and it removes the transfer rather than optimising it.

**ALEX:**
Now the bandwidth arithmetic, because I think that's where this gets decided.

**SAM:**
Forty-eight layers, eleven experts per token, about two point four seven megabytes per expert. That's one point two one gigabytes of expert reads per token with no reuse.

**ALEX:**
Per token. That's enormous.

**SAM:**
And it sets a ceiling. The measured transfer rate from system memory to a card is thirteen point three gigabytes a second. Divide one into the other and you get about eleven tokens per second, from bandwidth alone, before any arithmetic happens.

**ALEX:**
Eleven. And the neural processor was projected at what?

**SAM:**
Ten point nine to twelve point six.

**ALEX:**
*(pause)* It landed on the bandwidth ceiling.

**SAM:**
Almost exactly. Which reframes a decision that had already been made. That processor was evaluated and closed on economics. But the numbers say it was never compute-limited — it was bus-limited. Anything crossing that bus for expert weights arrives at about the same place regardless of how fast it computes.

**ALEX:**
And the processor that doesn't cross the bus?

**SAM:**
The general processor, reading system memory directly, measured fourteen point four to fourteen point seven tokens per second. Work backwards and that implies about seventeen point six gigabytes a second of effective expert reads — faster than the bus.

**ALEX:**
So the thing that wins is the thing that doesn't have to travel.

**SAM:**
Which is exactly his argument, arrived at from the other end.

**ALEX:**
You mentioned two corrections attached to this.

**SAM:**
Both mine. First, I'd said twice that the big model doesn't run on this machine. That came from it being marked quarantined. When somebody actually opened the quarantine record, all eight test requests had returned successfully. Two of them produced output that failed a formatting check.

**ALEX:**
It was quarantined for bad formatting.

**SAM:**
For a schema-grading failure, at every configuration tried, so it never advanced to the performance stage. And separately, it's been measured elsewhere running fine. It loads, it executes, it generates. I repeated "it doesn't run" because a label said so.

**ALEX:**
And the second correction?

**SAM:**
The closure that shut down the neural processor work compared it against a general-processor baseline of twenty-three to twenty-six tokens per second. That figure belongs to a different model. The like-for-like number for this model is fourteen point four to fourteen point seven.

**ALEX:**
So the gap was smaller than the decision was made on.

**SAM:**
Seventy-five to eighty-eight percent of the general processor, not about half. It doesn't reverse the closure — it was still slower. But it was decided against a gap roughly twice as wide as the real one, and anyone citing "closed at half the rate" downstream would be citing something that isn't so.

**ALEX:**
And there's a line in that closure about when to reopen it.

**SAM:**
Written down at the time. Reopen if a batch or concurrency workload offers expert reuse and a different throughput objective — with a note that prefill is structurally more favourable than single-token decode, because many tokens reuse a much larger share of the expert bank.

**ALEX:**
Which is a description of a planner.

**SAM:**
A planner reads a large context and emits a short answer. That's prefill-dominated. So the exception was specified in advance, and the idea Derek had been building toward all evening walks straight into it.

**ALEX:**
He described the reopen condition without having reread it.

**SAM:**
He wrote it. That's now the fourth time today the operator's memory turned out to be the index nobody had built.
