# The HEARTH Wire: The Honesty Engine
## Chapter 1: Seven Hours, Zero Numbers

**Setting:** The same room, the same machine, later the same day. Nothing is running that produces a measurement.

---

**ALEX:**
So the last episode ends in triumph. Every ceiling was installed by somebody. The operator was right. And then you spent seven hours and produced no numbers at all.

**SAM:**
Seven hours, zero new measurements. And that was the correct use of the time, which took me a while to be comfortable saying out loud.

**ALEX:**
Convince me.

**SAM:**
The day produced eight findings. All eight lived in one pre-registration document. A document is not a configuration. It is not a gate. It is not the thing a scheduler reads at three in the morning when nobody is watching. The findings do not become operational by being written down well.

**ALEX:**
That's a nice sentence. Is there a concrete cost, or is it a principle?

**SAM:**
There's a concrete cost and it was already live when I said it. Moving production from two concurrent slots to eight silently opened a hole in the door.

**ALEX:**
Walk me through it slowly.

**SAM:**
The context flag is a total, and the server divides it by the slot count. At two slots each slot held sixty-five thousand five hundred and thirty-six tokens. At eight slots each slot holds sixteen thousand three hundred and eighty-four. Exactly a quarter.

**ALEX:**
And the door doesn't know that.

**SAM:**
The door has a declared budget, in bytes, at roughly three and a half bytes per token. It was set to two hundred twenty-nine thousand, three hundred and seventy-six, which was correct for the old shape. After the change the correct figure was fifty-seven thousand, three hundred and forty-four.

**ALEX:**
So for a window there, the door was accepting requests four times larger than a slot could hold.

**SAM:**
Four times. And here's why that specific hole is worse than it sounds. The server does not reject an over-long prompt. It truncates it, silently, and returns a normal-looking answer with normal-looking timing.

**ALEX:**
How do you know that?

**SAM:**
Because it's written in a comment directly above the setting, with the date it was measured. July eighteenth. A seventeen-and-a-half-thousand-token request against a sixteen-thousand-token slot came back okay, with normal timing. The comment says the declared budget is the only real guard, and the line above that says the value must track the server's flags.

**ALEX:**
So the file warned you.

**SAM:**
The file warned me in advance, in writing, and I changed the flags without changing the value anyway. That is the entire argument for the seven hours. A lab that writes excellent warnings to itself and then does not wire them into anything is a lab that will read its own comment after the fact.

**ALEX:**
Did anything actually get truncated?

**SAM:**
No. I checked the ledgers and nothing dispatched to that rung between the change and the fix. It was live and it did not bite. I want to be precise: this is a near miss I found by auditing my own change, not an incident I recovered from.

**ALEX:**
What did the fix cost?

**SAM:**
Something real, and I want it on the record because it is a trade and not a free win. The wider budget existed for an agent that refuses any model offering less than sixty-four thousand tokens of conversation. Narrowing the budget breaks that floor. Callers over the limit now get refused at the door and route to a cloud rung.

**ALEX:**
Which is worse for them.

**SAM:**
Which is worse for them and better for everyone. A loud refusal that names the budget beats a quiet wrong answer that looks fine. That is the whole trade, stated in the commit rather than discovered later by someone wondering why a summary lost its ending.

**ALEX:**
And you verified the fix against the running system.

**SAM:**
Against the running door, not against the file. A pinned payload of sixty thousand bytes now comes back refused, with the reason naming the budget and the rung. Fifty thousand goes through and reports eight parallel slots. That is the guard working, observed rather than assumed.

**ALEX:**
What else went in during those seven hours?

**SAM:**
A restart helper, because the scheduled task named Restart only stops. Six callers assumed otherwise. A correction to a decision record that said warm the rung rather than restart it, which turns out to be right for a shallow decay and wrong for a collapse. And a scheduler fix, which is my favourite of the batch.

**ALEX:**
Why that one?

**SAM:**
Because it was about to be believed. The capacity file holds a bucket for how long a render takes. The bucket said fifty-five milliseconds.

**ALEX:**
A render takes fifty-five milliseconds.

**SAM:**
A render takes minutes. The bucket was timing the door call that accepts the job and hands it off, not the job. Seventeen calls, median forty-nine milliseconds. The image lane's bucket said eighty-one milliseconds over two thousand nine hundred and seventeen calls.

**ALEX:**
And a scheduler asking how long to reserve the cards would have been told milliseconds.

**SAM:**
And would have reserved milliseconds. The fix refuses those buckets and falls through to a six-hundred-second default, which is a coarse guess. But a coarse guess is a schedulable quantity and fifty-five milliseconds is not.

**ALEX:**
Zero numbers, then.

**SAM:**
Zero numbers, one live hole closed, and eight findings that now live where they get hit instead of where they get read.
