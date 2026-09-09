# The HEARTH Wire: The Honesty Engine
## Chapter 4: A Gate That Scores the Corpse

**Setting:** A thermal gate that has been shipping for a day, being read properly for the first time.

---

**ALEX:**
You were about to re-run the configuration that hit ninety-six degrees.

**SAM:**
With the operator's blessing and his limit, which is ninety-five. His words were that he has cooked these cards plenty of times and they will be fine. That is a tenancy decision about his own hardware and it is not mine to shave.

**ALEX:**
So there's a gate.

**SAM:**
There is a gate. It watches both temperature counters on both cards, it has the right threshold, it records the absolute reading and the rise above idle, and it keeps a hot cell in the receipt rather than dropping it. All of that is good.

**ALEX:**
But.

**SAM:**
But it runs after the telemetry stream has already finished. It parses a capture that self-terminated. There is no live temperature poll anywhere in the runner.

**ALEX:**
So it can't stop anything.

**SAM:**
It cannot stop anything. And it gets worse when you look at what happens on a failure. A cell that reaches ninety-five gets marked, gets excluded from the surface, and then the next cell launches. The failure isn't in the list of conditions that halt a sweep.

**ALEX:**
Onto a card that just hit ninety-five.

**SAM:**
Onto a card that just hit ninety-five. The gate protects the dataset. It does not protect the hardware, and I had been treating it as though it did.

**ALEX:**
What caught the original event, then?

**SAM:**
A live watchdog. The older harness polled every ten seconds, killed the run in flight, and held a resume line at eighty degrees before anything restarted. That watchdog is gone. So re-running that shape today, with the current gate, would be running it with less protection than it had the first time.

**ALEX:**
That's a regression nobody logged.

**SAM:**
Nobody logged it because the number is the same. Ninety-five is ninety-five in both. It's the timing that changed, and a threshold with no actuator behind it reads identical in a config file.

**ALEX:**
So you built one.

**SAM:**
Three properties matter. It kills only processes it was handed by identity, never by name — the restart script on this machine kills every server by image name, which is exactly why a hand-launched experiment dies to somebody else's maintenance, and why a stuck one keeps production down. Second, a stream it cannot read is a stop, not an all-clear.

**ALEX:**
Say more about that one.

**SAM:**
No readings, a missing counter, a stale stream — all return blind, and blind stops the run exactly like a breach does. Because "no reading" and "cool" are the same value to a naive check and opposite facts to a card. That is the same lesson as a port being open not meaning a model can serve, restated for temperature.

**ALEX:**
And the third?

**SAM:**
A slope rule, because the limit alone fires far too late on this hardware. Look at the real series. The card went seventy-eight, eighty-eight, ninety-two, ninety-four, ninety-six across five readings about twelve seconds apart. Forty-eight seconds, start to abort, still climbing about two degrees a tick when it was cut.

**ALEX:**
There's almost no warning in that.

**SAM:**
None. So the rule is: back off on a six-degree rise between consecutive readings while already above eighty-five. Below eighty-five it stays quiet, because a cold card gaining ten degrees is a card waking up and that would fire on every load start.

**ALEX:**
How much earlier does it fire?

**SAM:**
I wrote twenty-four seconds in the docstring. I got that by eyeballing the table and assuming it would trigger on the step from eighty-eight to ninety-four.

**ALEX:**
And?

**SAM:**
And the test replayed the actual series and said thirty-six. Because the step from seventy-eight to eighty-eight already qualifies — it's a ten-degree rise arriving above the floor. It fires on the second reading, not the fourth.

**ALEX:**
Your own test corrected your documentation.

**SAM:**
By twelve seconds, in the safe direction, on the specific series the tool exists for. The code now says what the test measured, and the docstring says I got it wrong and how.

**ALEX:**
Is there a test for the case the slope rule is designed to miss?

**SAM:**
There is, and it matters. A climb of two degrees per reading never trips the slope term. So there's a test asserting the absolute limit still catches that one. A back-off rule that quietly replaced the limit would be a downgrade wearing a feature's clothes.

**ALEX:**
And which card are you actually watching?

**SAM:**
Both, but if you are reading one number, read the VRAM sensor on the card at bus four. It runs four to eight degrees above its sibling under identical load across every dataset we hold. At the moment of that abort, its own GPU tile read seventy-five while its memory read ninety-six.

**ALEX:**
Twenty-one degrees apart on the same card.

**SAM:**
Which is a cooling defect on one card, not a property of the workload. And it means any statement of the form "it hit ninety" is meaningless until you say which sensor. The operator's own ninety is the memory sensor. The tile has never exceeded eighty-one on this machine.
