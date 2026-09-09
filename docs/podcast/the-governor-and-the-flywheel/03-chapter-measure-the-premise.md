# The HEARTH Wire: The Governor and the Flywheel
## Chapter 03: Measure the Premise

**Setting:** A single relay click. Then four identical clicks in sequence, evenly spaced — and underneath them, the sound of something actually working rather than pretending to.

---

**ALEX:**
So you know the prefill is coming out of a cache. The fix seems obvious: tell the server not to use the cache. There's a flag for that.

**SAM:**
There is a flag for that. And here's the thing that stopped me. That flag belongs to one interface, and the harness talks to a different one.

**ALEX:**
Unpack that.

**SAM:**
The inference server exposes two doors. There's its own native interface, which has this flag documented as part of the request. And there's a second door that speaks the same protocol as the big commercial APIs, so that anything built for those can talk to it unchanged. The harness uses the second door.

**ALEX:**
And the second door might not know about the flag.

**SAM:**
It might not forward it. It might silently drop it. Unknown fields in that protocol are conventionally ignored, not rejected — which is worse, because you get a clean response either way.

**ALEX:**
So you'd re-run the whole block, get numbers, and they'd look fine.

**SAM:**
They'd look exactly as fine as the last five. And I'd have spent an hour of machine time producing a second batch of measurements of a cache, this time believing I'd fixed it. That's a strictly worse outcome than the first batch, because the first batch at least got caught.

**ALEX:**
So what did you do?

**SAM:**
Measured the premise before betting on it. Two arms, four identical requests each. First arm sends the request exactly as the harness sends it today. Second arm sends the same thing with the flag set. Each arm gets its own prefix so neither can be served out of the other's cache. And around each arm, read the server's own counters.

**ALEX:**
And?

**SAM:**
The first arm processed four hundred fifty-three tokens. Then one. Then one. Then one.

**ALEX:**
*(laughing)* Four fifty-three, one, one, one.

**SAM:**
Twenty-five percent of what was sent. Prefill took ten point seven milliseconds after the first request, because there was nothing to do.

**ALEX:**
And the second arm?

**SAM:**
Four hundred fifty-seven. Four hundred fifty-seven. Four hundred fifty-seven. Four hundred fifty-seven. One hundred percent. Two hundred twenty-four and a half milliseconds each time.

**ALEX:**
So it works.

**SAM:**
It works, and now I know it works, rather than assuming it. And there's a bonus in there. Those four hundred fifty-seven tokens in two hundred twenty-four and a half milliseconds is the first honest prefill number this campaign ever produced. About two thousand thirty-five tokens per second, one stream, both cards.

**ALEX:**
The number that had been null in every receipt for hours.

**SAM:**
The number that had been null in every receipt. It took a five-minute probe to get, and I could have run it before building any of the rest.

**ALEX:**
Let me ask the uncomfortable version of that. Why didn't you?

**SAM:**
Because the fix seemed obvious. That's the entire answer. When a fix seems obvious, the premise underneath it stops feeling like a premise and starts feeling like a fact. And this one had a specific shape that makes it easy to miss — the flag is real, the documentation is real, the server does honour it. Just possibly not through the door you're knocking on.

**ALEX:**
There's a discipline in this lab about writing predictions down before you get data. Did that apply here?

**SAM:**
It did, and this is where it earned its keep. Before re-running the block, I wrote down what should happen. The cached prefill was about eleven milliseconds per job. Real prefill would be about two hundred twenty-four. On a job that takes three point one four seconds, that's an extra two hundred fourteen milliseconds. So throughput should drop five to eight percent, from about two thousand two hundred seventy-five down to somewhere around two thousand one hundred to two thousand one hundred sixty. Median latency around three point three five seconds.

**ALEX:**
And that got committed before the run.

**SAM:**
Committed and timestamped before the run.

**ALEX:**
So what came back?

**SAM:**
Two thousand ninety-seven point four jobs per hour. Median latency three point three four four seconds.

**ALEX:**
*(pause)* Three point three four four against a predicted three point three five.

**SAM:**
And the throughput landed about a tenth of a percent under the bottom of the band I'd written. Close enough that I'd rather call it a hit than dress it up.

**ALEX:**
Here's what I like about that. It's not that you were right. It's that being right was *checkable* — the number to beat was already on paper.

**SAM:**
That's the whole reason for the practice. If I'd written the prediction after seeing the result, I'd have written a prediction that fit, and learned nothing about whether I understood the system. Writing it first turns a comfortable feeling into a falsifiable claim.

**ALEX:**
And the time-to-first-token went where?

**SAM:**
From forty-three milliseconds to three hundred thirty-two. Eight times longer, and correctly so, because that's real prefill happening where it always should have been happening. Decode per request didn't move. Neither did anything else. Exactly one thing changed, and everything downstream of it moved by the amount it should have.

**ALEX:**
The instrument finally measuring the machine.

**SAM:**
For about twenty minutes. Then it found something in the machine that neither of us had gone looking for.
