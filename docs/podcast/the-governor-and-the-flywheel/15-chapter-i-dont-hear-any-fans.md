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
And then you went looking a second time.

**SAM:**
Because "no sustained capture on this machine" was doing a lot of work in that sentence, and I had only ever searched two folders. The campaign's own. There is a third — a burn-in archive from August. Six sustained runs sitting in it.

**ALEX:**
How sustained?

**SAM:**
Six hours and thirteen minutes of continuous card telemetry in the longest one. Fifteen thousand three hundred fifty-six temperature readings. And the memory on the same card I had been talking about peaks at ninety-four degrees, two hours in, against an abort line of ninety-five.

**ALEX:**
Ninety-four. You had said nothing ever recorded ninety.

**SAM:**
Twice now. And here is the part I would rather not say out loud: somebody had already reduced that file. It is written up in a document in the same repository, from August. Finding seven. Ninety-four on one card, eighty-six on the other, an eight-degree spread between two identical cards under one load.

**ALEX:**
So it wasn't just unread. It was read, written down, and then contradicted.

**SAM:**
By me. In a claim register whose entire purpose is to stop exactly that.

**ALEX:**
How did a six-hour file hide?

**SAM:**
It didn't. It was named backwards. The one-shot snapshots are called b-seventy-tools-dash-something. The long runs are called something-dash-b-seventy-tools. Same directory. Reversed. I matched the prefix, got fourteen snapshots, every one of them a single tick per card, and concluded that was the whole world.

**ALEX:**
The search was right and the conclusion was too big for it.

**SAM:**
An empty result is only ever empty within its scope. If you don't say the scope out loud, "I found nothing" becomes "there is nothing" somewhere between the terminal and the sentence.

**ALEX:**
So what genuinely isn't measured?

**SAM:**
Board power. Narrower than I said the first two times. In the imagegen receipts there is a watts field and it is null in twelve thousand nine hundred forty samples out of twelve thousand nine hundred forty.

**ALEX:**
All of them.

**SAM:**
Every one. And the reason is almost too tidy: the probe takes a single tick. Watts have to be derived by differencing two consecutive energy readings. One snapshot yields nothing to difference. The probe is correct, the counter is correct, and the arithmetic can never happen.

**ALEX:**
But the burn-in files stream.

**SAM:**
They do. Around ten thousand nine hundred energy readings per card. GPU-tile watts come straight out of them — a hundred and sixty on one card, a hundred and forty-five on the other, sustained, across six hours. Which is the uncomfortable part.

**ALEX:**
Why uncomfortable?

**SAM:**
Because the yardstick I had been dividing every duty number by is a ninety-two-second burst that reads a hundred and forty-four and a hundred and three. The six-hour workload sits above the burst on both cards. Forty-one percent above, on one of them.

**ALEX:**
Your reference for "working hard" was softer than the actual work.

**SAM:**
The caveat I wrote said the denominator was too short. It is also too low. The complaint survives — it just got sharper, and it got sharper from the file I said didn't exist.

**ALEX:**
And board power?

**SAM:**
Still nothing, and now it is the only thing. The whole-card energy counter gets emitted once per capture. One number, nothing to difference it against. That gap is real, and no amount of running longer will close it. The tool has to change.

**ALEX:**
What does a real run look like, now that somebody's added them up?

**SAM:**
The longest session was three point five nine hours. Eight hundred fifty images. Six point eight three GPU-hours of work across two cards — which is a duty of one point nine out of a possible two.

**ALEX:**
Both cards ninety-five percent busy for three and a half hours.

**SAM:**
That is what this machine does when it is actually working. And the reference number that every duty measurement in this campaign was compared against is the median of a ninety-second burst.

**ALEX:**
*(pause)* Ninety seconds standing in for three and a half hours.

**SAM:**
So when I reported "duty zero point six three, the cards are finally loaded" — that was against a spike. He heard the fans not spinning and knew the comparison was wrong before I did.

**ALEX:**
Let me put the whole day together, because I think there's one thing under all of it.

**SAM:**
Go ahead.

**ALEX:**
Every ceiling you hit was one somebody had installed. A prompt cache serving the same prefix. A measurement window drawn twice as wide as the thing inside it. A slot count sitting at two of a possible sixteen. A clamp at sixteen inside a graphics backend. A task named Restart that only stops. A metric calibrated against a ninety-second burst. And the operator, who couldn't see any of those, was right about all of them because the machine wasn't making the noise it makes when it's working.

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
