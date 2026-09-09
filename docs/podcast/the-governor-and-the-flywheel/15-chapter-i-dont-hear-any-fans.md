# The HEARTH Wire: The Governor and the Flywheel
## Chapter 15: I Don't Hear Any Fans

**Setting:** A room with a machine in it. Fans at idle — the sound the whole episode has been arguing about. They never rise.

---

**ALEX:**
Last chapter. And it starts with Derek not believing the instruments.

**SAM:**
He put it plainly. He's sitting next to the machine. He knows that image generation turns it into a small star after three or four hours, and that it bounces off ninety degrees within the first thirty minutes even in cool weather. So when an experiment tells him we're maxed out, and he hears no fans, feels no heat, and sees no system impact — he doesn't believe it.

**ALEX:**
And that's not stubbornness. That's a calibrated instrument.

**SAM:**
It's a better-calibrated instrument than mine was, and the record proves it three separate times. The duty metric read zero while the cards drew seventy-three watts. The client sweep said flat and saturated at two slots, and then eight slots gave fifty-eight percent more. Sixteen clients took one card from seventeen percent duty to sixty-three at unchanged throughput. Every single "we are at the limit" reading was an artifact or a configuration. Not once was it silicon.

**ALEX:**
So you went to check the number he already knew.

**SAM:**
And I got it wrong, which is the last correction of the day and the one I most want on the record.

**ALEX:**
Go on.

**SAM:**
I said no sustained-load capture existed on this machine. That every thermal record was a single snapshot or a run under five minutes, and that nothing had ever recorded the ninety degrees he was describing.

**ALEX:**
And that was wrong.

**SAM:**
The image generation lane writes three telemetry snapshots into every job's receipt — at start, at a ten-second poll, and at the end. There are two thousand five hundred eighty-eight of those receipts. And across them, the memory temperature on one card has a median of eighty degrees and a maximum of ninety-two.

**ALEX:**
Ninety-two. He said bouncing off ninety.

**SAM:**
He was accurate to within two degrees, from feel, and the data proving it had been sitting in the receipts the entire time. Never assembled. Never plotted. Nobody had ever joined those snapshots into a curve and looked at it.

**ALEX:**
So the number existed and the knowledge didn't.

**SAM:**
Which is a different failure from the one I'd claimed. I said it wasn't measured. It was measured and never read.

**ALEX:**
What genuinely isn't measured?

**SAM:**
Power. Not once. There's a field for it in the telemetry contract and it is null in twelve thousand nine hundred forty samples out of twelve thousand nine hundred forty.

**ALEX:**
All of them.

**SAM:**
Every one. And the reason is almost too tidy: the probe takes a single tick. Watts have to be derived by differencing two consecutive energy readings. One snapshot yields nothing to difference. The probe is correct, the counter is correct, and the arithmetic can never happen.

**ALEX:**
What does a real run look like, now that somebody's added them up?

**SAM:**
The longest session was three point five nine hours. Eight hundred fifty images. Six point eight three GPU-hours of work across two cards — which is a duty of one point nine out of a possible two.

**ALEX:**
Both cards ninety-five percent busy for three and a half hours.

**SAM:**
That is what this machine does when it is actually working. And the reference number that every duty measurement in this campaign was compared against is the median of a twenty-second burst.

**ALEX:**
*(pause)* Twenty seconds standing in for three and a half hours.

**SAM:**
So when I reported "duty zero point six three, the cards are finally loaded" — that was against a spike. He heard the fans not spinning and knew the comparison was wrong before I did.

**ALEX:**
Let me put the whole day together, because I think there's one thing under all of it.

**SAM:**
Go ahead.

**ALEX:**
Every ceiling you hit was one somebody had installed. A prompt cache serving the same prefix. A measurement window drawn twice as wide as the thing inside it. A slot count sitting at two of a possible sixteen. A clamp at sixteen inside a graphics backend. A task named Restart that only stops. A metric calibrated against a twenty-second burst. And the operator, who couldn't see any of those, was right about all of them because the machine wasn't making the noise it makes when it's working.

**SAM:**
That's the day. And I'd add one thing to it. He wasn't guessing. He grew up around hit-and-miss engines, shear pins, power take-offs — machines at fairs and shows and on farms, doing real work in front of him. Those machines tell you what they're doing. They change pitch under load. They get hot. They shake.

**ALEX:**
And a rack in a room does too, if you know what to listen for.

**SAM:**
It does. And the entire apparatus this campaign built — the gates, the receipts, the pre-registered predictions, the refuted hypotheses — all of that exists to give a machine a voice that a person can check. On the days it works, the instrument and the ear agree. Today they disagreed, repeatedly, and the ear was right every time.

**ALEX:**
So what's the next measurement?

**SAM:**
The one that should have been first. Put a passive collector alongside a real image-generation run — three and a half hours, both cards, the load that actually heats this machine — and record what it does. Then every number from today gets restated against what the machine does when it works, instead of against a spike.

**ALEX:**
And you'll find out whether ninety-two was the ceiling or just the warm-up.

**SAM:**
We'll find out whether we've ever seen this machine at its limit at all. Nine hours of measurement today, and my honest answer is: not yet.
