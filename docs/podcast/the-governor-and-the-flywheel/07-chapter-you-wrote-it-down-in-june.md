# The HEARTH Wire: The Governor and the Flywheel
## Chapter 07: You Wrote It Down in June

**Setting:** Pages turning. An old hard drive spinning up — the sound of something being read that hasn't been opened in a while.

---

**ALEX:**
Partway through the day Derek drops a path into the conversation. Just: this project, have a look.

**SAM:**
A .NET project from June. Six commits over about two weeks, roughly fifteen hundred lines of C sharp. It wraps the inference server — owns starting it, stopping it, checking on it, and puts a friendly interface in front so an agent framework can address models by nickname.

**ALEX:**
And you'd been re-deriving something it already knew.

**SAM:**
Its seventh decision record is titled, essentially: readiness means *can serve*, not *process is up*. And the enforcement is a real generation. Not a health check. It resolves the nickname exactly the way a real request would, sends a one-token request through that path, and only calls the thing ready if a token comes back.

**ALEX:**
Why does that need writing down? Surely a health endpoint returning two hundred means it's healthy.

**SAM:**
That's precisely the assumption it exists to kill. The incident behind it: two models loaded, both passing their health checks, both reporting fine. One of them returned a service-unavailable error to every request while looking completely healthy from the outside. The cause was a naming mismatch between how the model registered itself and how the router looked it up. Healthy model, valid request, no path between them.

**ALEX:**
And this campaign hit the same wall independently.

**SAM:**
A co-resident model was quietly crushing production. Production's throughput fell to about a tenth. Its health endpoint returned two hundred the entire time. I wrote "HTTP two hundred is not serving" into the record as though it were a finding.

**ALEX:**
And it was a finding. Just not a new one.

**SAM:**
Three months late, from a different failure, in a project sitting on the same machine. The law is identical. And the health gate this campaign uses — which checks the rung's observed rate rather than its status code — is a re-implementation of that decision record without knowing it existed.

**ALEX:**
Is that a failure of housekeeping?

**SAM:**
Partly. But I'd frame it differently. Two independent investigations, months apart, from unrelated failures, converged on the same rule. That's not embarrassing — that's the rule being real. The failure is only that nobody could point at the first one when the second one started, so the second one paid full price for it.

**ALEX:**
You found something else in there too. Something still unused.

**SAM:**
A lever. That June project launches the inference server with its cache quantized to eight bits. Production runs it at sixteen.

**ALEX:**
Meaning what, practically?

**SAM:**
Production's cache is twelve gigabytes — six for one half, six for the other. Halving the precision roughly halves that. Call it three gigabytes freed per card.

**ALEX:**
Three gigabytes per card. That's not nothing.

**SAM:**
It's the difference between a model fitting and not fitting. It's a longer context, or another model beside the first, or headroom you don't currently have. And it was proven working on this exact hardware in June.

**ALEX:**
So why isn't it on?

**SAM:**
Because it's a real trade and it isn't mine to make. Lower-precision cache can cost output quality, and the amount depends on the model and the task. That's a judgement about what this lab serves, and it belongs to the person who owns the lab. So it went into the record with its numbers attached and a note saying explicitly: not taken, here's what it would buy, here's what it might cost.

**ALEX:**
Recorded rather than done.

**SAM:**
Recorded rather than done. There's also a discipline reason. This campaign was measuring a surface, and a surface has to be measured at one setting. Changing the cache precision halfway through would have contaminated everything measured on either side of it.

**ALEX:**
There's a third thing in that project I want you to say out loud, because I think it's the funniest sad detail of the day.

**SAM:**
It doesn't run any more.

**ALEX:**
Why not?

**SAM:**
Its configuration points at a drive letter that no longer exists on this machine. Every path — the models, the binaries, the output directories — refers to a drive that was reassigned at some point after June.

**ALEX:**
So a working, tested, documented piece of software is inert because of a letter.

**SAM:**
One character. The code is fine. The build artifacts are there. Its evidence directory has real runs in it. And it would fail on the first file it tried to open.

**ALEX:**
That's a very particular kind of decay.

**SAM:**
It's the kind that doesn't announce itself. Nothing rotted. Nothing broke. The world moved half an inch sideways and the software stayed exactly where it was. And there's no test that catches it, because the test would need to run, and running is the thing that fails.

**ALEX:**
What's the carry-forward?

**SAM:**
Three things, and they're unrelated to each other, which is itself the lesson about going back to look. A law this campaign re-derived at cost, and the citation now points at where it was first written. A three-gigabyte lever, costed and deliberately not pulled. And a reminder that a project can be complete, correct, and dead, all at once, over a drive letter.

**ALEX:**
And you only found any of it because he said go look.

**SAM:**
Which is the second time in two chapters. He's not remembering facts I could have derived. He's remembering *where things are*, and that turns out to be the scarcer thing.
