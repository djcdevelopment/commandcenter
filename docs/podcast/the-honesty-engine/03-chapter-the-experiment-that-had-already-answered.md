# The HEARTH Wire: The Honesty Engine
## Chapter 3: The Experiment That Had Already Answered

**Setting:** Two research agents have come back with contradictory briefs. Neither is obviously wrong.

---

**ALEX:**
This is the one where the register catches you.

**SAM:**
The register catches me, and it catches something I had already published in the previous episode. So it is in the claim register, in the plan, and in a podcast script. Three places, all wrong, all mine, all written that morning.

**ALEX:**
Say the claim.

**SAM:**
That running one whole model per card — a replica on each — had never been measured. That the experiment was thermally quarantined at ninety-six degrees before producing any data. I used it to argue that the configuration the operator kept asking for was unmeasured rather than good or bad.

**ALEX:**
And it was measured.

**SAM:**
It was measured, and it won. I only found out because I sent two researchers at the same question and they came back disagreeing. One said three cells of data exist with the numbers attached. The other said the quarantine wrote skip rows and no performance data survived.

**ALEX:**
What do you do with two credible contradictory reports?

**SAM:**
You stop relaying and you go read the file. This lab has a standing rule about that, from an earlier occasion when I took an agent's word for something and it was wrong. So I opened the backfill myself.

**ALEX:**
And?

**SAM:**
Forty-eight replica rows. Real measurements, with sample sizes attached.

**ALEX:**
Give me them.

**SAM:**
At one client, the replica configuration ran four hundred and ten point zero nine jobs per hour. The split configuration ran three hundred and ninety-six point seven five. The replica is ahead by three point four percent.

**ALEX:**
And at two.

**SAM:**
Eight hundred and nineteen point four eight against six hundred and thirty-six point zero six. Twenty-eight point eight percent ahead. Better ninety-fifth-percentile latency in both.

**ALEX:**
So the thing you said was unmeasured had already beaten the incumbent twice.

**SAM:**
And there's a detail underneath that I find more persuasive than the headline. The replica's decode rate held flat — twenty-three point zero five, then twenty-three point one zero — while the split configuration's fell from twenty-two point nine seven to eighteen point seven zero.

**ALEX:**
So the split degrades under concurrency and the replica doesn't.

**SAM:**
Over the range we can see. Which is a short range, and this is where I have to take most of it back again.

**ALEX:**
Go on.

**SAM:**
Three requests. Then six. Cells of about twenty-six seconds. That is far below this campaign's own standard, which is at least three repeats of multi-request cells with a bootstrap confidence interval. So it is a strong prior on a thin foundation, and the register row says exactly that rather than quoting the percentages on their own.

**ALEX:**
What about the third cell? You said three.

**SAM:**
The third one is the aborted one, and it is not citable. Twelve requests attempted, ten valid. It is the cell that was killed in flight at ninety-six degrees, and its jobs-per-hour figure is computed over a run that was cut. I found it quoted as a clean result in one of the briefs and it cannot be.

**ALEX:**
And the other side of the comparison?

**SAM:**
Ran its whole matrix. The split configuration has numbers out to twenty-four clients. The replica stops at two. So the honest summary is: the partition started ahead and we do not know where its curve goes, because the quarantine stopped the rest of the matrix.

**ALEX:**
Which is different from stopping the premise.

**SAM:**
Completely different, and that is the correction. What the quarantine stopped was the remaining cells. Somewhere between that and this morning it turned into "never measured," and I wrote it down that way in three places.

**ALEX:**
Do you know how it happened?

**SAM:**
I can reconstruct it. The quarantine file lists sixty-six skipped cells. If you read the quarantine record and not the backfill, "no data" is the obvious impression, and it is nearly true. Nearly true is the dangerous kind.

**ALEX:**
So what does the register say now?

**SAM:**
That two clean cells exist, that the partition won both, the sample sizes, that the third is not citable, that the model was the dense twenty-seven billion at one slot per server rather than the mixture model at eight, and that the curve past two clients is unknown. Everything a person needs to not repeat my mistake in either direction.

**ALEX:**
Both directions?

**SAM:**
That's the part I want to land. It would be just as wrong to now go around saying the partition wins by twenty-eight percent. The row has to stop the overcorrection as hard as it stops the original error, because the overcorrection is the one that feels like a discovery.
