# The HEARTH Wire: The Governor and the Flywheel
## Chapter 08: The Wrong Variable

**Setting:** A throttle body opening — a long, rising intake note that has been waiting all episode to be heard.

---

**ALEX:**
This is the turn. What did Derek actually say?

**SAM:**
That it felt like we'd stopped doing research and started doing performance testing. That the margins being chased were thin and close to things already measured.

**ALEX:**
Was he right?

**SAM:**
Look at what the sweep had produced. One client: one thousand five hundred fourteen jobs per hour. Two: two thousand one hundred twenty-eight. Four: two thousand one hundred twenty-seven. Eight: two thousand one hundred sixty-four.

**ALEX:**
Flat from two onward.

**SAM:**
Within one point seven percent across a fourfold increase in clients. Meanwhile latency doubles every time — two point four seconds, three point five, seven, thirteen point seven. Pure queueing. And through all of it, both cards sitting at about seventy-three watts against a reference of a hundred sixty and a hundred fourteen. Duty cycle zero at every single point.

**ALEX:**
So the honest read of that data is "we're saturated."

**SAM:**
That's the read I'd written. Server fully occupied, cards not, therefore the configuration can't load the hardware. Which is true as far as it goes. What I did next was keep adding clients to a server that had already told me it wouldn't take them.

**ALEX:**
And then he points at something.

**SAM:**
His own patch. Open upstream in the inference engine's repository. It takes a hardcoded constant in the graphics backend — a limit of eight on how wide a particular decode operation can be — and turns it into something you can set at runtime.

**ALEX:**
Why does eight matter?

**SAM:**
Because past that width the code falls off one implementation onto a much slower one. On this hardware the measurements are stark: the fast path takes three to six seconds for a pass, the slow path takes forty point seven.

**ALEX:**
That's not a slope, that's a cliff.

**SAM:**
It's a cliff, and he found it, patched it, wrote it up, published it, and sent it upstream weeks ago.

**ALEX:**
So what did you find when you went and looked at the running system?

**SAM:**
Production runs the patched binary. The launcher script already exports the widened setting. The window is at sixteen.

**ALEX:**
So the patch is live.

**SAM:**
The patch is live, the window is open to sixteen, and the server was configured to use *two* slots.

**ALEX:**
*(long pause)* Two.

**SAM:**
Two of a possible sixteen. Eight times of headroom, bought, patched, enabled, and never used.

**ALEX:**
How long had it been like that?

**SAM:**
Since the current serving arrangement was set up. Months.

**ALEX:**
And nothing surfaced it.

**SAM:**
Nothing. Not a dashboard, not an audit, not a health check. Everything reported healthy, because it *was* healthy. It was healthy at a third of its capability.

**ALEX:**
Now be precise about your own error here, because I think it's a specific one.

**SAM:**
I varied the number of clients. Clients don't do anything except queue. The server admits as many requests as it has slots and the rest wait. So sixteen cells of measurement, each one carefully gated and receipted, all varying a quantity that the server structurally ignores.

**ALEX:**
While the quantity that mattered sat untouched.

**SAM:**
And here's the worst part. That quantity is named in this campaign's own measurement protocol. There's a line in the pre-registration card describing it as the one production knob that caps concurrent admission, never swept on the server side. I wrote that line. And then I built a sweep that didn't touch it, and later cancelled the phase that would have.

**ALEX:**
You wrote down where the treasure was and then dug somewhere else.

**SAM:**
That's fair and I'd rather it be said than softened.

**ALEX:**
What made you miss it? Genuinely.

**SAM:**
The client axis is easy. It needs no restart, no configuration change, no permission. The slot axis requires editing production's configuration and restarting a live service, which is somebody else's call. So the cheap axis got explored exhaustively and the expensive one got deferred — and deferred is how "we'll get to it" becomes "we didn't."

**ALEX:**
There's a nice mechanical detail about why sixteen specifically.

**SAM:**
There's a comment in the launcher saying the setting clamps silently above sixteen. So sixteen isn't a number anyone chose as optimal — it's the ceiling of what the knob can express. Push the slots past it and the decode width exceeds the window with nothing able to follow.

**ALEX:**
Which explains his own finding.

**SAM:**
He'd told me sixteen was best for this hardware. Turns out that's not a tuning result, it's a structural boundary. The knob stops there, so the useful range stops there.

**ALEX:**
So how do the two ideas fit together — his partition idea and his patch?

**SAM:**
They're halves of one design, and the arithmetic shows it. The total context gets divided among the slots. Two slots means sixty-four thousand tokens each. Sixteen slots means eight thousand each. So one engine cannot be both wide-batch and long-context. It has to pick.

**ALEX:**
And two engines don't have to pick.

**SAM:**
One card takes the wide batch for short work. The other takes the whole context for long work. His patch makes the first half possible; his partition makes having both possible. He'd been describing one design and I'd been hearing two proposals.

**ALEX:**
So the plan gets rewritten.

**SAM:**
The slot sweep goes first, the client sweep is closed out with what it honestly scored, and the phase I'd cancelled comes back — because it was never confirmatory. It was the experiment.
