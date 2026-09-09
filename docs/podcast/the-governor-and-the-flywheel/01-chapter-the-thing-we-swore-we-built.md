# The HEARTH Wire: The Governor and the Flywheel
## Chapter 01: The Thing We Swore We Built

**Setting:** A quiet room. Fan noise at the very bottom of the mix, steady, unremarkable — the sound of a machine that is on but not working. A single soft synth note holds underneath.

---

**ALEX:**
Sam, the whole day starts with a sentence I love, because every engineer has said it at some point. Derek says: *I swear we wrote something that helped move around KV and manage that storage. We might have made a VM to help with this process. Can you find what I'm thinking about?*

**SAM:**
And the honest answer is: he was right, and he was also half-remembering two different things that had gotten tangled together in his head.

**ALEX:**
Okay, so what's KV, for anyone who hasn't spent their weekend inside an inference server?

**SAM:**
When a language model reads your prompt, it does a lot of expensive arithmetic and caches the intermediate result. That cache is the key-value cache. It is the model's working memory for that specific conversation. If you throw it away, the next time you want to continue that conversation the model has to read the whole thing again from scratch.

**ALEX:**
And "from scratch" costs what, exactly?

**SAM:**
On this machine, for one particular long prompt, a hundred and two point eight seconds. Nearly two minutes of the cards doing arithmetic they had already done once.

**ALEX:**
And if you saved the cache instead?

**SAM:**
Two point six eight gigabytes written in one point seven four seconds. Read back in one point one nine. So roughly sixty to one in favour of not throwing it away.

**ALEX:**
Sixty to one. That's not an optimisation, that's a different category of decision.

**SAM:**
It is. And it was already built. There's a module in the rotation lane that does exactly this — saves a slot, restores a slot. It even solves a subtle problem: the saved file carries no model identity at all. Nothing inside the file says which model produced it.

**ALEX:**
Wait. So you could restore a cache into the wrong model?

**SAM:**
You could, and the result would be confident garbage. So the identity was pushed out into the filename and a manifest sitting beside it, and there's a hard refusal in the code before any network call happens if the model doesn't match. That's the thing Derek remembered building. He was right.

**ALEX:**
And the VM? The other half of the memory?

**SAM:**
That's the part that hadn't happened. There was a plan — a card in an experiment brief — to mount a RAM disk. Eight gigabytes of system memory pretending to be a drive, so that these cache files could be written and read at memory speed instead of disk speed.

**ALEX:**
Specced but never built.

**SAM:**
Specced, costed, and never built. So it got built that night. Eight gigabytes, mounted as a drive letter.

**ALEX:**
And I have to ask, because this is the pattern that shows up all day — did it help?

**SAM:**
Barely. And that's the first honest finding of the session. The RAM disk shaves maybe half a second off an operation that already takes about one and a half seconds, and which is already sixty times cheaper than the alternative. It's real, it's just not a lever.

**ALEX:**
So the answer to "should we build the RAM disk" was "yes, and then discover it doesn't matter much."

**SAM:**
And there's a worse constraint underneath. The original idea was to stage model weights on it — keep a model's file in memory so loading it is instant. The brief's verdict on that was a flat *ignore*, and the arithmetic is brutal. The smallest model in the set is eight point two eight gigabytes. The RAM disk has seven point nine seven gigabytes free.

**ALEX:**
*(laughing)* It doesn't fit. By three hundred megabytes.

**SAM:**
Nothing in the set fits. Not one. So the RAM disk's honest job is holding cache files, which are small, and being a place to write trace data so the instrument's own writes don't disturb the thing it's measuring. Both real. Neither dramatic.

**ALEX:**
And that's chapter one of a day that ends with a fifty-eight percent throughput win. Which tells you something about where the wins actually were.

**SAM:**
It tells you they weren't where anyone was looking.
