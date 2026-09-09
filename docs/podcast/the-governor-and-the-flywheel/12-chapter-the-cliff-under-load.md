# The HEARTH Wire: The Governor and the Flywheel
## Chapter 12: The Cliff Under Load

**Setting:** Two takes of the same passage played back to back, identical in every way except one — and the second one is audibly better.

---

**ALEX:**
Set up the experiment. What's the actual question?

**SAM:**
There's a constant inside the graphics backend of the inference engine. It governs how wide one particular decode operation can be before the code takes a different route. In the stock build it's eight. Derek's patch turns it into something you can set when you launch.

**ALEX:**
And past eight, the different route is —

**SAM:**
Much slower. On this hardware, three to six seconds for a pass on the fast route, forty point seven on the slow one. It's not a gradual degradation. It's a switch between two implementations, one of which is well suited to this hardware and one of which is not.

**ALEX:**
So with the patch you can move the boundary.

**SAM:**
You can move it up to sixteen. It clamps there.

**ALEX:**
Which means the test writes itself.

**SAM:**
Run at sixteen slots — where the decode width is right at the boundary — twice with the window widened, twice with it back at the stock eight. Change nothing else. Same binary. Same model. Same slot count. Same prompts. One environment variable.

**ALEX:**
And?

**SAM:**
Widened: two thousand two hundred six point nine, and two thousand four hundred thirty-nine point five. Stock: one thousand nine hundred fifty-three point six, and two thousand fifty-nine point five.

**ALEX:**
So the widened pair are both above the stock pair.

**SAM:**
The ranges don't overlap. Widened runs from two thousand two hundred seven to two thousand four hundred forty. Stock runs from one thousand nine hundred fifty-four to two thousand sixty. There's a gap between them.

**ALEX:**
Which is the thing you want, because it means the effect is bigger than the run-to-run wobble.

**SAM:**
That's why it matters more than the headline percentage. The cells wobble about ten percent from run to run — we established that earlier with the batching modes. If the effect were eight percent, two repeats a side would tell you nothing. Fifteen point eight percent with non-overlapping ranges is a different quality of evidence.

**ALEX:**
And per-request decode agrees?

**SAM:**
Eight point one and eight point eight with the window open. Seven point one and seven point five with it closed. Separates the same way, independently.

**ALEX:**
Now, why does this measurement matter beyond this lab?

**SAM:**
Because of where the patch is. It's an open contribution to the inference engine's upstream repository, and the maintainer's position on it is specific. The default stays at eight. No per-vendor defaults get proposed without measurement data.

**ALEX:**
So he wants evidence, not opinion.

**SAM:**
Per-vendor evidence. And the work behind the patch is substantial — a whole campaign, a published article, a public repository — but it was all measured with a benchmarking tool and frame-pacing tests. Synthetic, single-purpose, isolated.

**ALEX:**
And this is the first time it's been measured on a live server.

**SAM:**
Under concurrent traffic, through the actual serving path, with real requests queueing behind each other, gated the same way every other measurement in this campaign was gated. That's the shape of evidence that was missing.

**ALEX:**
Fifteen point eight percent on real serving traffic.

**SAM:**
On this hardware, on this model, at this slot count, which are exactly the qualifications that make it useful rather than a slogan.

**ALEX:**
There's also something the experiment explains that wasn't the point of it.

**SAM:**
The clamp. Derek had said sixteen was best for this hardware, and I'd taken that as a tuning result — that he'd swept it and sixteen won. It's not. The knob physically stops at sixteen. Push the slots past it and there's nothing left to widen, so the decode width exceeds the window with no way to follow.

**ALEX:**
So his empirical finding has a mechanical explanation sitting inside the code.

**SAM:**
And the two arrive at the same number from opposite directions. He found sixteen by sweeping the machine. The code says sixteen because that's where the clamp is. Neither of us knew the other's version until the two got put side by side.

**ALEX:**
Which is a nice illustration of something.

**SAM:**
That an operator's tuned number and a source-code constant are two views of the same fact, and you learn something by checking one against the other. If they'd disagreed, that would have been more interesting still.

**ALEX:**
And the cleanup afterwards?

**SAM:**
The setting goes back to sixteen, the file gets compared against the committed version byte for byte to make sure nothing else moved, and production gets left at eight slots — which is the best combination measured today. Three thousand three hundred fifty-eight to three thousand four hundred seven jobs an hour, against the two thousand one hundred twenty-eight it had been quietly running at all along.

**ALEX:**
So the day's actual deliverable is a config file with a different number in it.

**SAM:**
And a document explaining why, with every step of the reasoning receipted. But yes. The deliverable is a small number, changed.
