# AM4 Collaboration Retrospective

Date: 2026-06-29
Scope: retrospective on the AM4 build/benchmark collaboration

## Framing

This was a good example of what happens when the work starts from the real constraint instead of the surface request.

The visible request was to get AM4 acting like a fleet node and to push Hermes toward usable inference service. The actual job was narrower and more important:

- establish whether Linux AM4 could carry the serving path at all
- find the machine's real operating envelope
- separate platform friction from architectural limits
- write down enough evidence that future work does not restart from folklore

From my side, the collaboration worked because you were already reasoning from system behavior rather than from slogans about what "should" work.

## What made this collaboration effective

1. You supplied prior context that mattered.

The Denning work, the Windows failure modes, the SYCL multicard history, the RAM ceiling, the suspicion about shared-memory cliffs, and the note that small-context wins can invert at large context all materially changed the work. None of that was fluff.

2. You gave permission to operate.

You explicitly said AM4 could be cleared, that autorestart/watchdog remnants might still be wired in, and that we could shut down whatever was occupying the box. That avoided the usual dead time where everyone is afraid to touch the system.

3. The work stayed empirical.

We did not stop at "layer works" or "single card seems fine." We turned the machine into a benchmarked node:

- placement ladder
- service-path proof
- service-path depth ladder
- true concurrent ladder

That sequence is what made the conclusions trustworthy.

4. You were already pointed at the right abstraction boundary.

The question about MCP versus NATS was the right kind of question. It pushed the work toward control-plane clarity instead of premature bus complexity.

5. The session did not confuse motion with progress.

We fixed launcher bugs, environment precedence bugs, and harness issues, but those only mattered because they were in service of finding the real limit. The work never drifted into tool polishing for its own sake.

## What I think we did unusually well

- We used the old `vllama` proof shape as a constraint instead of reinventing the benchmark.
- We treated failed runs as information, not embarrassment.
- We kept the write-downs close to the work as it happened.
- We let the machine disprove attractive ideas quickly.

That last part matters. A lot of teams spend too long protecting a hoped-for architecture from contact with reality. Here, multicard `layer` was allowed to be real, useful, and still not the default.

## What slowed us down

Most of the friction was mechanical:

- oneAPI `setvars.sh` breaking under `set -u`
- env-file precedence overriding benchmark intent
- invalid device identifiers for `llama-server`
- port collisions when trying to launch multiple backend-owned runs at once

None of those were conceptually hard, but they are the kind of problems that waste hours if nobody is paying close attention to the exact failure surface.

## From my perspective

The strongest part of the collaboration was that you were not asking for reassurance. You were asking for an honest boundary.

That changes the quality of engineering work. It means the fastest path is not "produce a success story." It is:

- tighten the hypothesis
- instrument the machine
- run the ladder
- keep what survives
- write down what failed

That is why the session moved quickly. The decision standard was clear.

## If this had been a typical dev team

I think a typical team would have taken longer and learned less.

Likely failure modes:

1. They would have declared victory too early.

Someone would have gotten a single shallow-context multicard run to succeed and translated that into "dual GPU inference is working."

2. They would have benchmarked the wrong surface.

A lot of teams would stop at raw backend throughput and never measure the served path through the actual facade contract.

3. They would have under-measured concurrency.

It is common to treat slot count as concurrency and never test what overlapping requests feel like from the client side.

4. They would have buried the failures.

The launcher issues, port collisions, and unstable modes would be omitted from the written record, which guarantees somebody else rediscovers them later.

5. They would have escalated architecture too early.

Instead of proving MCP-first control and optional NATS, a typical team might jump to queue infrastructure before even understanding the node's envelope.

6. They would have argued from preference.

There would be a lot more "I think layer should win at scale" or "Linux should fix this" and a lot less measured evidence.

## Where I think this leaves the project

In a good place.

Not because the machine is suddenly bigger than it is, but because the ambiguity is lower:

- AM4 can serve
- AM4 has a measured service-path ladder
- AM4 has a measured concurrent ladder
- `single0` is the operational default
- `layer` is a bounded stretch path
- the next real problem is scheduler/control-plane work

That is enough clarity to hand to other builders without wasting their time.

## Final read

If this were a normal team retrospective, the headline would be:

We avoided a false positive.

We did not mistake "inference can run" for "the node is operationally understood." We pushed until the machine showed its real shape, then captured that shape in artifacts and turned it into a tooling roadmap.
