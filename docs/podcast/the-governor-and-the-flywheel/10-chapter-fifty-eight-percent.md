# The HEARTH Wire: The Governor and the Flywheel
## Chapter 10: Fifty-Eight Percent

**Setting:** An engine taking load for the first time — the note dropping as it bites, then holding steady. Somewhere underneath, a cooling fan finally audible.

---

**ALEX:**
Four slots instead of two. What happened?

**SAM:**
Two thousand nine hundred eighteen jobs per hour, against a ceiling of about two thousand one hundred thirty that sixteen cells of client-sweeping couldn't move by more than one point seven percent.

**ALEX:**
That's a thirty-seven percent jump from one number in a config file.

**SAM:**
And the ninety-fifth percentile latency *fell*. Four point nine four seconds, where eight clients on two slots had been thirteen point six eight.

**ALEX:**
More work and lower latency at the same time. That's the shape you almost never see.

**SAM:**
Because it isn't an optimisation. It's removing a restriction. The work was always arriving; it was queueing at the door.

**ALEX:**
Then eight slots.

**SAM:**
Three thousand three hundred fifty-eight. One point five eight times the two-slot ceiling.

**ALEX:**
Fifty-eight percent. And the trade?

**SAM:**
Per-request decode falls — sixty-six point eight at two slots, forty-four point three at four, twenty-five point four at eight. Each individual request gets slower.

**ALEX:**
But there are more of them.

**SAM:**
Aggregate decode goes the other way: a hundred thirty-three point six, a hundred seventy-seven point two, two hundred three point two. That's the batching trade, and it's the expected one. It's the first prediction of the day that landed the way it was supposed to.

**ALEX:**
Now the number this whole campaign has been chasing. Duty cycle.

**SAM:**
Zero. All day. Every cell, both cards, at every client count. Zero point zero.

**ALEX:**
And?

**SAM:**
At four slots, one card reads zero point zero six one five.

**ALEX:**
Not zero.

**SAM:**
First non-zero duty reading of the entire campaign. About a second of a sixteen-second cell above threshold. Tiny. But it's the first time the metric moved at all.

**ALEX:**
And power?

**SAM:**
That's the better signal, and it exposes a flaw in my own metric. At two slots the cards sat at seventy-three watts, flat. At four, peaks of a hundred forty. At eight, a hundred seventy-one.

**ALEX:**
A hundred seventy-one against a reference of a hundred sixty.

**SAM:**
Above it. And still reading low duty, because duty is defined as time spent above ninety percent of that reference — and the reference was taken from a prefill burst. This is a decode-heavy workload. Wrong yardstick. The cards were doing far more work than the metric was willing to say.

**ALEX:**
So the metric under-reports the thing it exists to report.

**SAM:**
In this regime, badly. Watts are the honest signal and watts were climbing hard. That correction went into the record next to the number it corrects.

**ALEX:**
Then sixteen clients on eight slots.

**SAM:**
Throughput barely moves — three thousand four hundred seven against three thousand three hundred fifty-eight. One and a half percent.

**ALEX:**
So more clients still don't buy throughput.

**SAM:**
But duty on one card goes from zero point one six eight to zero point six three two. Sustained a hundred fifty-four point six eight watts, peaking a hundred eighty-one.

**ALEX:**
*(pause)* So the same work, but the card is finally busy doing it.

**SAM:**
Which corrects something I'd said an hour earlier. I'd summarised it as: throughput is set by slots, latency by clients. First half holds. Second half was incomplete. Clients past the slot count barely move throughput but massively move *occupancy*. Eight clients on eight slots leaves gaps between rounds. Sixteen keeps them fed.

**ALEX:**
Both knobs matter and you'd measured one.

**SAM:**
Both knobs matter and I'd measured one, then stated a rule as though I'd measured both.

**ALEX:**
Is there a cost to that occupancy?

**SAM:**
Efficiency gets worse. Same throughput for about thirty percent more power. And the delayed-admission event doubles alongside the client count. The extra clients buy occupancy, not work.

**ALEX:**
Which is worth knowing if you're paying for electricity.

**SAM:**
Or if you're deciding what "saturated" should mean. Occupied and productive are different states, and this is the first measurement in the campaign that separates them.

**ALEX:**
And then there's the thing about the split.

**SAM:**
This is the finding I didn't expect. One card sits at a hundred fifty-four point seven watts and sixty-three percent duty. The other sits at eighty-one point five watts and twelve percent.

**ALEX:**
Under a setting that's supposed to divide the model evenly.

**SAM:**
Evenly, by ratio, one to one. And one card is doing roughly twice the work of the other.

**ALEX:**
Is that a bug or a property?

**SAM:**
Unknown from one cell, and it's recorded as one observation, not a law. But it's a strong argument for the thing Derek had been describing all along. If layer-splitting one model across two cards loads them this unevenly, and buys only about six percent on decode while costing a synchronisation on every single token — then two independent engines, each with a whole model and its own queue, stops looking like a bigger change and starts looking like the simpler arrangement.

**ALEX:**
The imbalance is the argument.

**SAM:**
The imbalance is the argument, and I'd have kept measuring the split forever if the power numbers hadn't finally moved enough to show it.
