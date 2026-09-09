# The HEARTH Wire: The Governor and the Flywheel
## Chapter 05: The Rung Is Never Idle

**Setting:** Silence. Genuine silence, held long enough to be uncomfortable. And then — faintly, regularly, every half-minute or so — a single small tick.

---

**ALEX:**
There's a prediction in this campaign about what happens when the system sits idle. Walk me through it.

**SAM:**
There's a documented behaviour that if the inference rung goes quiet for more than about a minute, it stops performing at its known-good rate. The decision record has readings from when it was discovered — sixty-eight, sixty-nine, seventy-four, ninety-two tokens per second, where the healthy number is a hundred and six.

**ALEX:**
So it degrades when you leave it alone.

**SAM:**
And the prediction written down for this campaign was specific: the first request after an idle should read somewhere between sixty-five and ninety percent of what a warm one reads.

**ALEX:**
Easy enough to test. Stop sending it work.

**SAM:**
That's what I did. Two hundred seconds of no traffic from anything I was running. Then measure.

**ALEX:**
And?

**SAM:**
One hundred six point seven one. Against a warm reference of one hundred six point two six.

**ALEX:**
That's not degraded. That's the highest reading of the three.

**SAM:**
It was the highest of the three. A hundred percent and change. So either the documented behaviour is wrong, or the rung wasn't actually idle.

**ALEX:**
And you checked which.

**SAM:**
Went to the server's log. Through the entire two hundred seconds, a one-token completion request arrives every thirty-one seconds. Without a single gap.

**ALEX:**
Every thirty-one seconds. Something is knocking on the door twice a minute.

**SAM:**
There's a log file for it too. Four twenty-nine twenty-eight. Four twenty-nine fifty-nine. Four thirty thirty. Four thirty-one oh-one. Four thirty-one thirty-two. Thirty-one seconds apart, to the second, indefinitely.

**ALEX:**
So the machine is never idle.

**SAM:**
The machine is never idle. Which means every "unwarmed" measurement this campaign had taken was measuring a rung that had been kept warm the whole time. The prediction couldn't be tested, because the condition it describes doesn't occur.

**ALEX:**
And here's where I want to slow down, because this is the part of the day I think is most instructive. What did you write down at that point?

**SAM:**
I wrote that the prediction was untestable as configured. That the instrument which lets us gate on the rung's health is the same one that prevents us from ever seeing it go cold, and that testing it would mean blinding the gate.

**ALEX:**
Which sounds thorough.

**SAM:**
It sounds thorough and it was wrong.

**ALEX:**
What happened?

**SAM:**
Derek read it and said, roughly: I set that up. On a separate machine, because it was needed to keep performance up.

**ALEX:**
He built the thing you'd just discovered.

**SAM:**
Months ago, for exactly the reason the decision record gives. And once you know that, the obvious next question is the one I never asked: if he built it, can it be turned off?

**ALEX:**
And it can.

**SAM:**
It's two timers on a small Linux box on the network. There's passwordless access from this machine. Stopping them is one command.

**ALEX:**
Okay but here's the part that would sting.

**SAM:**
Say it.

**ALEX:**
Four other probes in this same campaign family already do exactly that.

**SAM:**
Four of them. Same codebase. Each one has a small helper function whose docstring says, in plain English, that it stops and starts the keep-alive timers over the network, best effort, never fatal. They stop the timers, run their measurement, start them again. It's a routine move in this lab and has been for weeks.

**ALEX:**
So the mechanism you declared didn't exist was sitting in four sibling files.

**SAM:**
And I want to be precise about the error, because "I missed it" undersells it. I identified the interfering process correctly. I diagnosed why the measurement was impossible correctly. And then I stopped, because I'd reached a conclusion that felt complete. I never asked whether the thing could be turned off. I described the obstacle beautifully and didn't try the handle.

**ALEX:**
The operator knew because he'd built it.

**SAM:**
He knew because he'd built it, and that's a kind of knowledge that doesn't live in the code. The timers are on a different machine. The helper functions are in probe files I had no reason to open. The connection between "there's a heartbeat" and "here's how you pause it" existed in one place — his memory.

**ALEX:**
There's a broader point in here about working with someone who built the thing you're measuring.

**SAM:**
There is, and it recurs three more times before the day ends. My instruments can see the system's current state. They cannot see its history, or its intent, or the reason a particular thing was put there. When I hit something that looks like a wall, the operator's first question is usually not "is that really a wall" — it's "did I build that wall, and if so, where did I put the gate?"

**ALEX:**
So the prediction becomes testable.

**SAM:**
The prediction becomes testable. The cost is stated honestly: while the timers are stopped, the gate that protects every measurement in this campaign goes blind, because it reads that same heartbeat. But the staleness budget is twelve minutes, and the pause needed is four. So there's room.

**ALEX:**
And you correct the record.

**SAM:**
Same document, dated, with the wrong version left visible above it. "Untestable" becomes "testable, here's how, here's what it costs." That's the whole discipline. You don't get to quietly replace the sentence you'd rather not have written.

**ALEX:**
And then you go and do it.

**SAM:**
And then I go and do it, and I break production.
