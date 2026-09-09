# The HEARTH Wire: The Honesty Engine
## Chapter 2: The Label That Named a Dead Epoch

**Setting:** A terminal. A health query returns, and the number is fine.

---

**ALEX:**
This one you found by accident.

**SAM:**
I found it by doing the thing I keep telling myself to do, which is checking a fix against the running system instead of against the file I just edited. The fix was already committed. I queried the door's health to confirm production was happy before moving on.

**ALEX:**
And it was happy.

**SAM:**
Perfectly happy. At rate. One hundred and five point three three tokens per second of baseline, observed comfortably above it. Nothing wrong with the number at all.

**ALEX:**
So what caught your eye?

**SAM:**
The label underneath it. The verdict came back carrying a long epoch string, the way this lab insists health verdicts do, and that string named the twenty-ninth of August.

**ALEX:**
And the day was?

**SAM:**
The ninth of September. Eleven days later, three production restarts later, and a completely different server shape. The number was from today. The identity attached to it was from a machine state that no longer existed.

**ALEX:**
Explain why anyone should care. The rate is the rate.

**SAM:**
Because in this lab a rate is not a scalar. That is a decision record with a number on it. Health here is defined as three things together: which epoch the baseline belongs to, what you observed, and the acceptance envelope around it. The epoch is the identity of the contract. Strip it and "at rate" stops being falsifiable, because you can no longer say what it is at the rate *of*.

**ALEX:**
So the cause?

**SAM:**
One missing line. The re-baseline command writes the new decode rate, the new configuration string, and the new note. It never touches the epoch label. And a re-baseline almost always follows a restart, and a restart ends an epoch. So the tool's normal, successful path attaches a fresh number to an epoch that just died.

**ALEX:**
How long had it been like that?

**SAM:**
Since the tool was written, presumably. It only became visible because somebody re-baselined after a restart and then read the label out loud.

**ALEX:**
So you fixed it and moved on.

**SAM:**
I fixed it and then got the replacement label wrong, which is the part worth the airtime.

**ALEX:**
Of course you did.

**SAM:**
I wrote that the epoch had been opened by the slot change. It reads well. It is the obvious story: we moved production from two slots to eight, that opened a new epoch, the baseline was set inside it.

**ALEX:**
And the receipts said otherwise.

**SAM:**
The cell receipts are timestamped. The sweep ran two slots, then four, then eight, then sixteen. The last sixteen-slot cell finished at four minutes past seven. The epoch actually began at five minutes and eighteen seconds past seven.

**ALEX:**
After the sixteen-slot arm.

**SAM:**
Which means the epoch was opened by the *restore* back to eight, not by the change to eight. Different event, seventy-something minutes later, and the difference matters to anyone comparing a reading across it. The baseline was then set at six minutes past, forty-two seconds after the server came up.

**ALEX:**
How did you get the epoch start at all? You said the server's start time isn't readable.

**SAM:**
It isn't. It runs under a scheduled task whose start time is inaccessible, and its process counters read zero while it works. So the epoch start is derived: take the server's own log, read the elapsed stamp on its last line, subtract that from the file's modification time.

**ALEX:**
That's indirect.

**SAM:**
It is, so it got corroborated independently. The running server's own log says its slot context is sixteen thousand three hundred and eighty-four, which is eight slots at that total. The shape in the label matches the shape the server reports about itself.

**ALEX:**
And there's a second half to this.

**SAM:**
The boundary list. This lab keeps an explicit record of where one epoch ended and the next began, precisely so nobody accidentally compares across one. The restore that opened the current epoch had appended nothing to it. The list knew about a cutover from six days earlier and nothing since.

**ALEX:**
So the record of epoch boundaries was missing the boundary that matters most.

**SAM:**
The current one. It has a row now, with the reason and the re-baseline noted.

**ALEX:**
What's the fix in the tool?

**SAM:**
An optional flag to name the epoch, and when it isn't given, a label derived from the baseline's own timestamp and note. The previous label is never carried forward under any path. And the standing warning that a shared epoch is not a guarantee of comparability survives the replacement, because swapping one error for another is not a fix.

**ALEX:**
Seven tests, I assume.

**SAM:**
Seven, and one of them checks the live data file rather than a fixture. That was deliberate. The defect was in the data, not only in the writer, so a test that only ever sees a fixture would have passed happily while the door kept serving a stale label to everyone who asked.
