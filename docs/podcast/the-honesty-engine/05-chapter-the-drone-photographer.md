# The HEARTH Wire: The Honesty Engine
## Chapter 5: The Drone Photographer

**Setting:** The operator needs his own machine for a couple of hours. The experiment that wanted a quiet machine has to wait.

---

**ALEX:**
He takes the machine back.

**SAM:**
He tells me Valheim is running in the background to capture images for another project, that it will take some VRAM, and that it shouldn't be too much on a machine this strong. And then a moment later, that he is about to start it.

**ALEX:**
Which gave you a before.

**SAM:**
Which gave me a clean baseline, and that is the only reason any of this is attributable. Card at bus four: fourteen point four eight nine gigabytes committed, memory at fifty-six degrees. Card at bus nine: fifteen point five eight seven, fifty-four degrees. That is production's two halves and nothing else.

**ALEX:**
And after?

**SAM:**
It lands entirely on one card. Bus nine gains one point seven gigabytes. Bus four does not move by a single byte for the rest of the afternoon. The integrated graphics reports nothing at all.

**ALEX:**
So the game is a single-card tenant.

**SAM:**
A single-card tenant that takes no tenancy fence, which means production keeps serving right through it. That combination is rare and it is exactly what two open items had been waiting for.

**ALEX:**
Waiting for a game?

**SAM:**
Waiting for hours of real sustained load on this box, which had never been captured. The longest capture in the entire corpus was about two hundred and sixty ticks. Every duty-cycle figure in this campaign divides by a ninety-two-second burst, and the stated way to retire that caveat is a sustained capture. He was about to hand me one.

**ALEX:**
So you attached to it.

**SAM:**
Passively. The collector reads kernel and driver counters and allocates about four kilobytes of its own. And it is not a judgement call that it is safe — the image-generation agent already spawns that same collector about five times per job while holding the card fence. Four thousand two hundred and fifty times in one session with no failures.

**ALEX:**
What did ninety-eight minutes get you?

**SAM:**
The first thermal plateau ever measured on this machine. His card climbed and then stopped. Memory sensor plateaued at seventy-two degrees within the first minute, the tile at seventy-two within nine. Peaks of seventy-eight and seventy-seven. Against a ninety-five degree limit.

**ALEX:**
He said it would be light. Was he right?

**SAM:**
Thermally, completely right, and I had been sceptical in the previous hour. He also gave me the reason when I stopped assuming: it isn't gameplay. It's a camera rig. He teleports around the world and photographs buildings from multiple angles. It's a series of loads and stills, not a sustained combat scene.

**ALEX:**
And why does a plateau matter beyond today?

**SAM:**
Because the replica configuration's whole reputation rests on a climb from seventy-eight to ninety-six with no plateau in it. That reads as "it never stops rising." But that run was cut at forty-eight seconds — before a plateau could possibly have appeared. Now we know these cards do settle, they settle within about ten minutes, and they settle low.

**ALEX:**
That isn't proof the heavier configuration settles safely.

**SAM:**
It isn't, and the register row says so. But it removes "they never stop climbing" as the reason not to measure it, and that was the strongest argument against ever running the experiment.

**ALEX:**
You said two open items. What's the second?

**SAM:**
The one I did not go looking for. The untouched card sat at twenty-six point six watts for the entire ninety-eight minutes. Flat. Every single one-minute bucket.

**ALEX:**
That's idle.

**SAM:**
That is precisely idle. And here's the independent check on the instrument: twenty-six point six matches the previously known idle floor for these cards with a model resident, which was measured on a different day by a different method. So the reduction is calibrated against a prior it didn't know about.

**ALEX:**
Fine. So one card idle. What's the finding?

**SAM:**
The inference rung was running at roughly half its rate the whole time.

**ALEX:**
With half the machine doing nothing.

**SAM:**
Half the machine at the idle floor and the rung at fifty to fifty-six percent. And the mechanism is not mysterious. Production layer-splits one engine across both cards, so every single token has to touch both. One of them is busy with photographs. So the whole engine waits.

**ALEX:**
Whereas if each card had its own engine.

**SAM:**
The card he isn't using would serve at full rate and wouldn't care what happens on the other one. That is the partition argument, and it just got made by his photography rather than by my plan. I registered it as a prediction rather than a claim, because I have two clean samples and the tool refuses a steady-state call under three.

**ALEX:**
The tool refuses.

**SAM:**
The tool refuses. Which brings us to the last chapter, because that is the fourth time today something I built told me I was not allowed to conclude what I wanted to conclude.
