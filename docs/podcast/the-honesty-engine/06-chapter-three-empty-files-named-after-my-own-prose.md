# The HEARTH Wire: The Honesty Engine
## Chapter 6: Three Empty Files Named After My Own Prose

**Setting:** The end of the session. A reboot is pending. A tidy-up that finds one more thing.

---

**ALEX:**
Last chapter. Count them for me.

**SAM:**
Six times in one afternoon that a tool I built, or a rule this lab wrote down, stopped me from publishing something wrong. I want to go through them because individually they're small and together they're the entire argument for working this way.

**ALEX:**
Start.

**SAM:**
One. The rate checker printed, in capital letters, that fifty-five percent of baseline is degradation and not noise. And it was right about the sample and wrong about the machine. That reading was taken while the game was streaming assets in. A minute later the same rung read ninety-nine percent.

**ALEX:**
How were you supposed to tell those apart?

**SAM:**
By the spread, and I nearly didn't. The degraded reading had a twenty-one point eight three percent repeat spread. The healthy one had zero point three four. A genuine regime change moves the mean and keeps the spread tight. Contention moves both. So the sampler I then wrote classifies every row by its own spread and will not summarise a steady state from fewer than three clean samples.

**ALEX:**
Two.

**SAM:**
I nearly blamed the operator's workload for a number my own test suite caused. One of the low samples landed at almost exactly the timestamp of a full suite run of mine — thousands of tests, very CPU heavy, and host-side submission gaps are a known channel on this box. That went into the receipt as a caveat rather than being quietly dropped.

**ALEX:**
Three.

**SAM:**
The spacing guard. I wrote a loop to take a sample every ten minutes for two and a half hours. It fired all fifteen iterations inside one second.

**ALEX:**
Why?

**SAM:**
Because the sleep command I used fails when standard input is redirected. It printed an error about input redirection and returned immediately, fifteen times. In a background task nobody was watching.

**ALEX:**
So you hammered his machine.

**SAM:**
No — and that's the point. The sampler has a rule that it refuses to run twice inside sixty seconds, with the reasoning written next to it: the instrument must not become the load. It refused fourteen of the fifteen. One extra sample was taken. The operator's machine never noticed.

**ALEX:**
You wrote a guard against a mistake you then made.

**SAM:**
Within the hour. I wrote it thinking about somebody else being careless with his hardware.

**ALEX:**
Four.

**SAM:**
The steady-state refusal we ended the last chapter on. I wanted to write down that a single-card tenant costs the split engine half its rate. The evidence points that way. The tool said two clean samples, needs three, one sample is not a regime.

**ALEX:**
Five.

**SAM:**
Hunting for my own leftover processes before the reboot. I searched for processes whose command line contained the name of my script, found one, killed it, searched again, found another with a different identifier, killed that.

**ALEX:**
It was multiplying.

**SAM:**
It was my own query. The search string was in the command line of the process running the search. I was matching myself, every time, and killing my own tool's shells to chase a ghost. Which is a gentler version of something that has genuinely happened on this machine — a process kill by command-line match once took down three production services.

**ALEX:**
How did you settle it?

**SAM:**
Excluded the current process and its parent. Nothing was running. It had been nothing for three rounds.

**ALEX:**
And six.

**SAM:**
Six is the one I like. I was writing the session's notes into the project memory — the corrections, the plateau, the traps — and I built the text inside a shell command.

**ALEX:**
With backticks in it.

**SAM:**
With backticks around every file path, because that's how you format a path. The shell treats backticks as an instruction to run what's inside them. So it ran fragments of my own prose as commands, and where my sentences had arrows in them — decode fell twenty-two point nine seven to eighteen point seven, the card climbed seventy-eight to ninety-six — it read those arrows as instructions to create files.

**ALEX:**
So what did you end up with?

**SAM:**
A memory note with holes where every file path should be. "Plan:" followed by nothing. "I read, myself." And three empty files in the repository root named after words from the middle of my own sentences. One of them was called "This."

**ALEX:**
That's the whole episode in one artifact.

**SAM:**
It really is. A note about being careful, silently damaged by not being careful, leaving debris named after the sentence describing the damage. I rewrote it with the proper tool, deleted the three files by exact name rather than by pattern — because deleting by pattern is how you turn a small mess into an incident — and added both traps to the notes.

**ALEX:**
Give me the through-line.

**SAM:**
Every one of those six was a machine telling its author no. Not a colleague, not a review, not hindsight a week later. A refusal, in the moment, from something written down earlier by someone who suspected this exact failure was coming.

**ALEX:**
And the tools were right every time.

**SAM:**
Every time. The one that told me fifty-five percent was degradation was right about what it saw. The one that refused a steady state was right that I had two samples. The spacing guard was right that I was about to hammer a machine somebody else was using. That is what an honesty engine is. You do not build it to catch other people.
