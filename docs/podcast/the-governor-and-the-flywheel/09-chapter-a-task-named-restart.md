# The HEARTH Wire: The Governor and the Flywheel
## Chapter 09: A Task Named Restart

**Setting:** A machine spinning down. Then nothing. A long nothing, where a restart should have been.

---

**ALEX:**
So you're going to change the slot count. That means editing production and bouncing it.

**SAM:**
There's a whole sequence for it, written months ago and tested. Check the gates. Edit the configuration, touching exactly one token, with a backup. Run the restart task. Wait for the real ready marker in the log — not a health check, the actual line the server prints when the model is loaded. Then verify with a live completion. Then re-establish the performance baseline.

**ALEX:**
Sounds thorough.

**SAM:**
It is thorough. It ran. The gates passed. The configuration was edited correctly. The restart task ran and returned success. And then the wait for the ready marker timed out.

**ALEX:**
Because?

**SAM:**
Because production was down. Nothing listening on either port. The server log's last entry was from before the restart. And the configuration was sitting there with the new value in it.

**ALEX:**
The task is called Restart.

**SAM:**
The task is called Restart. It stops.

**ALEX:**
*(pause)* It only stops.

**SAM:**
Only stops. And the reason is documented — in the header of the launcher script itself. There's a sentence describing the recovery procedure: run the restart task, which stops things, then remove a sentinel file, then run the *boot* task. Two different scheduled tasks. One tears down, one brings up.

**ALEX:**
So the answer was in the file.

**SAM:**
In the file, in a comment, at the top, describing this exact situation. I'd read that file earlier in the day for a different reason and hadn't needed that paragraph, so I hadn't retained it.

**ALEX:**
Why is it built that way? That seems like a trap.

**SAM:**
It's deliberate, and once you see the constraint it's reasonable. The server runs elevated. An ordinary process can't kill it. So the elevated restart task exists as a sanctioned way for an unprivileged caller to bring it down — and there's a sentinel file that campaign maintenance can leave in place to *keep* it down until the maintenance is ready to restore it. The task doubles as a stop-only control on purpose.

**ALEX:**
So it's a feature that reads like a bug from the outside.

**SAM:**
Named badly, but not built badly. And here's the detail that made it worse for me: earlier in the day I had run that same task and production came back on its own.

**ALEX:**
So you had evidence it restarts.

**SAM:**
Direct evidence. I'd run it, waited, and the server came back at full rate. So my model of the task was built on one confirming observation.

**ALEX:**
What actually happened that first time?

**SAM:**
The boot task has a retry count. When the process it supervises disappears, it can bring it back on its own. So the first time, something else restarted production and I credited the restart task. Textbook — one confirming observation, wrong causal attribution, and the belief holds until the day it matters.

**ALEX:**
And the recovery worked?

**SAM:**
Exactly as written. Run the boot task. The lifecycle manager came back immediately, production about three minutes later, with its own log line saying the model was loaded and it was listening.

**ALEX:**
And the tooling gets fixed.

**SAM:**
The restart step gets relabelled as what it is — a stop — followed by a bounded wait until nothing's listening, then the boot task, then the ready marker. And if the sentinel appears in between, it aborts rather than booting into somebody's maintenance lock. That lock is shared with another subsystem, so removing it on someone's behalf isn't this code's business.

**ALEX:**
Now — while you were down there, you checked something you'd flagged as a stop condition.

**SAM:**
The cache size. There was a real worry going in. That June project's records note that on the build it used, leaving the slot count on automatic made the engine allocate four times the cache. If that behaviour held here, raising slots from two to sixteen would be a large over-allocation on hardware with a documented history of memory-pressure failures.

**ALEX:**
So what did the new server report?

**SAM:**
Twelve thousand two hundred eighty-eight mebibytes of cache. At two slots, at four slots, and at eight. Identical. What changes is the shape — sixty-five thousand cells across two sequences, thirty-two thousand across four, sixteen thousand across eight.

**ALEX:**
The total is fixed and it's being divided.

**SAM:**
The total is fixed and divided. So the stop condition never fired, and it was checked at every value rather than assumed once. The June behaviour was real for the June build and doesn't apply here — which is exactly why you re-read the log each time instead of carrying a rule forward.

**ALEX:**
Anything else move?

**SAM:**
The compute buffers shrank. Seven hundred twelve mebibytes per card at two slots, four hundred fifty-six at four, three hundred twenty-eight at eight.

**ALEX:**
Shrank? More slots, less scratch memory?

**SAM:**
Smaller per-slot working set. Which nobody predicted and nobody needed, but it's in the record because it was in the log, and a thing you didn't expect is worth writing down even when it costs you nothing.

**ALEX:**
And then the measurements start.

**SAM:**
And then the throttle finally opens.
