# The HEARTH Wire: The Governor and the Flywheel
## Chapter 11: Valve Float

**Setting:** An engine revved past its useful range. Individual combustion events getting louder and further apart, the note ragged, power going nowhere.

---

**ALEX:**
Eight slots gave fifty-eight percent. So sixteen gives more.

**SAM:**
Sixteen gives two thousand two hundred six point nine. And on the repeat, two thousand four hundred thirty-nine point five.

**ALEX:**
Against three thousand three hundred fifty-eight at eight.

**SAM:**
About thirty-two percent worse.

**ALEX:**
It went *backwards*.

**SAM:**
Substantially backwards, and reproducibly. And the internals are worse than the headline. Per-request decode collapsed from twenty-five point four to about eight. Aggregate decode fell too — from two hundred three down to about a hundred thirty-five. So it isn't the batching trade any more. It's just less work getting done.

**ALEX:**
And the admission event you've been tracking?

**SAM:**
That's the tell. It's grown all afternoon. About two hundred twenty-two milliseconds at two slots. Four hundred thirty-four to five hundred eighty-nine at four. Six hundred seventy-four to nine hundred forty-three at eight. And at sixteen slots — one thousand eight hundred sixty-five to two thousand nine hundred thirty-two.

**ALEX:**
Nearly three seconds.

**SAM:**
Nearly three seconds to hand a waiting request to a free slot.

**ALEX:**
And the power trace?

**SAM:**
This is the part that made the metaphor click. Peaks of a hundred sixty-six to a hundred eighty-three watts — as high as anything measured all day. But the median sat at sixty-six to seventy-three.

**ALEX:**
Big bangs, low average.

**SAM:**
Big bangs, low average. The card is capable of the peak and spends most of its time nowhere near it.

**ALEX:**
That's valve float. That's exactly what it sounds like on a bench. You keep opening the cam up, and past a point the valve can't close in time, the cylinder doesn't fill, and you get noise instead of power. More lift, less torque.

**SAM:**
And the diagnosis follows the same shape. It isn't the fuel and it isn't the cylinders. The requests that do get admitted run at a sensible rate. It's the timing gear — the mechanism that decides when work enters the engine — and it degrades faster than linearly as you open it up.

**ALEX:**
Now. Derek had told you sixteen was best for this hardware.

**SAM:**
He had, and I want to be careful here rather than either dismissing it or overriding it. He'd already swept that step matrix. His finding is that sixteen is best on this hardware for most of his use cases.

**ALEX:**
And your measurement says eight.

**SAM:**
My measurement says eight, for one model, at one prompt size, with a particular concurrency. That's one point on a surface. His matrix covered his actual workloads, which include different weights and different shapes — and he specifically noted that going past sixteen only matters for much smaller models.

**ALEX:**
So it bounds his finding rather than contradicting it.

**SAM:**
That's the honest framing and it's the one in the record. This is a mixture-of-experts model at five hundred twelve tokens. It's entirely possible — likely, even — that a dense model, or longer prompts, or a different quantisation moves the peak. What we can say is: for this model at this size, the peak is eight, and sixteen costs you a third of your throughput.

**ALEX:**
There's also the structural thing from earlier.

**SAM:**
Right — the setting that widens the decode path clamps at sixteen. So sixteen isn't a tuned value, it's the ceiling of what the knob can express. Which means at sixteen slots you're running with the batch width exactly at the boundary, and anything that nudges past it falls off the fast path entirely.

**ALEX:**
Sitting right on the edge.

**SAM:**
Sitting right on the edge, which is where you'd expect behaviour to get strange. And that made the next measurement obvious, because if the boundary is doing this much damage at sixteen, then moving the boundary should be measurable.

**ALEX:**
Which is a test of his patch.

**SAM:**
Under real serving load, which nobody had ever done. The original work on that cliff used a benchmarking tool and frame-pacing measurements. Never a live server under concurrent traffic.

**ALEX:**
And that's the next chapter.

**SAM:**
That's the next chapter. But I want to close this one on the thing I keep coming back to. Every ceiling in this campaign has been a ceiling somebody installed. The prompt cache. The measurement window. The slot count sitting at two. And now a hard clamp at sixteen inside a graphics backend.

**ALEX:**
None of it silicon.

**SAM:**
Not once. Nine hours in, and the hardware has not yet been the limit on anything.
