# The HEARTH Wire: The Governor and the Flywheel
## Chapter 04: Two Hundred and Twenty-Two Milliseconds

**Setting:** Two mechanical clicks landing together, over and over, perfectly in step. Then one pair falls out of sync — a soft stumble — and never quite recovers its rhythm.

---

**ALEX:**
Fixed instrument, five fresh repeats. What came back?

**SAM:**
Two thousand ninety-seven. Two thousand one hundred. Two thousand two hundred four. Two thousand one hundred twenty-five. Two thousand one hundred fifteen. Spread of five percent.

**ALEX:**
Five percent. Is that noise?

**SAM:**
That's what I wanted to call it. But look at where the five percent lives. Median latency spread across those repeats was two point two six percent. Decode rate, two point one four. And the ninety-fifth percentile latency spread ten point four five percent.

**ALEX:**
So the middle of the distribution is rock steady and the tail is flapping.

**SAM:**
All of the variance is tail. Which is not what noise looks like. Noise moves everything a little. This moved one thing a lot.

**ALEX:**
So you went looking for the one thing.

**SAM:**
Four of the five repeats have exactly one round — one pair of concurrent requests — where the two requests did not run together. And when they don't run together, you can see it in the server's own numbers. Prefill for that round runs at about one thousand nine hundred ninety tokens per second instead of one thousand four hundred seventy.

**ALEX:**
Hang on. It ran *faster*?

**SAM:**
Faster per request, worse in aggregate. Two prefills batched into the same pass each report about one thousand four hundred seventy, which is two thousand nine hundred forty of combined work. One prefill running by itself reports one thousand nine hundred ninety and that's all you get.

**ALEX:**
So the fast number is the symptom.

**SAM:**
The fast number is the symptom. And decode falls at the same time, from about sixty-six point seven down to fifty-eight point five, because now one slot is prefilling while the other is decoding and they're stepping on each other.

**ALEX:**
Okay, so why did they fall out of step?

**SAM:**
The server's own log records when each request is handed to a slot. On a good round, the two hand-offs are two tenths of a millisecond apart. On the bad round, they're two hundred twenty-two milliseconds apart. Another cell, two hundred twenty-two point four. A third, two hundred fifty point seven.

**ALEX:**
That's a big, consistent gap. Is that the client being slow to send?

**SAM:**
That's exactly what I assumed, and it's wrong. The harness records when it sends each request, and across every cell the two sends are between zero and twelve and a half milliseconds apart. Usually under one. The client sends them together. The server picks them up a fifth of a second apart.

**ALEX:**
So it's inside the server.

**SAM:**
Inside the server, on the admission path. Not compute. The requests that finally do run take exactly as long as they should.

**ALEX:**
Now — you had a theory about this that didn't survive. Tell that part.

**SAM:**
I did. My theory was that the instrument was tripping over itself. The harness fires warm-up probes before the measured load. I supposed one of those probes was still holding a slot when the real load arrived, so the first pair couldn't both be admitted.

**ALEX:**
Plausible. Self-inflicted, which is usually the right guess.

**SAM:**
Very plausible, and it had the appeal of being my fault, which makes a theory feel responsible. Then I read the log. In both cells I checked, the warm-up request released its slot between twenty-four and thirty-two milliseconds *before* the measured pair was admitted. And then the pair was admitted two tenths of a millisecond apart.

**ALEX:**
So the probe was already gone.

**SAM:**
Already gone, and the pair that followed it was perfectly in step. My theory wasn't just unproven, it was refuted by the specific evidence I'd gone looking for to confirm it. That went in the record as a refuted hypothesis with my name on it, because a campaign that only records the guesses that worked is a campaign you can't trust.

**ALEX:**
So what *is* the mechanism? Do you know?

**SAM:**
Not the cause. But the consequence became very clear, and it's the interesting part. Every request's own prefill rate tells you which of three situations it was in. About one thousand nine hundred ninety means it had the machine to itself. About one thousand eight hundred fifty means it was prefilling next to the other slot's decode. About one thousand four hundred seventy means it was batched with the other slot's prefill.

**ALEX:**
Three modes.

**SAM:**
Three tight bands with empty space between them. No values in between. Across every cell measured, every single request falls into one of the three.

**ALEX:**
And the throughput follows the mode.

**SAM:**
Cleanly. The one cell where every request batched came in at two thousand two hundred four — the fastest of the block. The cell where nothing batched came in at one thousand nine hundred ninety-nine. Group them across all the cells and fully batched averages two thousand one hundred ninety-nine point five, fully unbatched one thousand nine hundred ninety-eight point five.

**ALEX:**
So batching is worth about ten percent.

**SAM:**
Ten point one. And here's what makes it more than a curiosity: the same grouping holds whether you're running two clients or four. The client count doesn't separate those groups. The batching mode does.

**ALEX:**
So the thing everybody would call noise —

**SAM:**
— is a discrete state variable. The cell is in one of two conditions and you're averaging across them. Which means reporting a spread number for that cell actively misrepresents it. The honest statistic is how many rounds fell out of step, not how far the means wandered.

**ALEX:**
And the mechanism you'd propose?

**SAM:**
Two requests admitted together prefill together, decode together, finish together — so the next pair arrives together. It's self-sustaining. One delayed admission knocks them out of phase, and after that every prefill lands beside a decode instead of beside a prefill, and nothing pushes them back into step. So one two-hundred-millisecond event doesn't cost you two hundred milliseconds. It costs you the rest of the cell.

**ALEX:**
The delay isn't the expense. The phase change is.

**SAM:**
That's the claim. Measured association across ten cells, mechanism inferred. Which is a hypothesis with good support, not a proven fact, and it's written down that way.
