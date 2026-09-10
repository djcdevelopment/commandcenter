# The HEARTH Wire: The Proof of Work Paradox
## Chapter 10: QA as the First Postmortem

**Setting:** A calm, clinical atmosphere. The ambient hum of a clean room, accented by the soft rustle of documents being flipped over and scrutinized.

---

**ALEX:**
On April 10th, 2026, Derek merged [`E:\work\planning\docs\adr\adr-007-qa-as-postmortem.md`](file:///E:/work/planning/docs/adr/adr-007-qa-as-postmortem.md). In standard software engineering, QA—Quality Assurance—is usually a gate you run before deployment: run the unit tests, verify the API responses, check for lint errors. How did Derek redefine QA in Farmer?

**SAM:**
He redefined QA as a forensic postmortem.
In Farmer, the worker running inside the Hyper-V VM—Claude Code—was allowed to finish its entire build cycle. It wrote the code, modified files, ran whatever local scripts it wanted, and deposited its output manifest.
Only *then* did the 7th stage of the pipeline fire on the host: `RetrospectiveStage`.
And who ran that stage? An independent retrospective agent built with the Microsoft Agent Framework and OpenAI (`gpt-4o-mini`).

**ALEX:**
Notice the hybrid architecture: Claude CLI inside the Linux VM doing the building; OpenAI on the Windows host doing the postmortem. Why use two different frontier models from two different AI labs?

**SAM:**
Because of cognitive independence. In [`adr-006`](file:///E:/work/planning/docs/adr/adr-006-openai-over-anthropic-maf.md) and [`adr-009`](file:///E:/work/planning/docs/adr/adr-009-hybrid-maf-host-cli-vm.md), he explained why: if you use Claude to review Claude's code, it shares the same latent blind spots, the same sycophancy, the same stylistic biases. By bringing in a completely different model family running on a separate host process, you get an adversarial, objective review.

**ALEX:**
And what artifacts did that retrospective agent emit into the run directory?

**SAM:**
Three permanent markdown and JSON documents:
1. `qa-retro.md`: A human-readable postmortem detailing what worked, what drifted, and what criteria were missed.
2. `review.json`: A structured machine-readable verdict: `Accept`, `Retry`, or `Reject`, with typed finding codes.
3. `directive-suggestions.md`: Concrete recommendations for how to improve the initial planning prompts for the next cycle.

**ALEX:**
And then, five days later, in [`adr-011-retry-driver.md`](file:///E:/work/planning/docs/adr/adr-011-retry-driver.md), he closed the self-learning loop. Explain how the retry driver worked.

**SAM:**
If the retrospective agent issued a `Retry` verdict, Farmer didn't just throw away the work and start from scratch with the original prompt. It initiated an automated retry attempt. And before launching the second attempt, it injected a synthetic prompt file: `0-feedback.md`.
That feedback file contained the exact `ReviewVerdict.Findings` and `Suggestions` from attempt 1.
So attempt 2 woke up with the accumulated wisdom of attempt 1's postmortem!

**ALEX:**
That is the definition of a self-learning agentic loop!
The worker tries, the independent reviewer critiques, the critique is serialized as an immutable file, and the second attempt consumes the critique as its primary constraint.

**SAM:**
And look at how this connects to the live telemetry on `steppeintegrations.com` right now. Remember that category: `Learning / retro`? Over **5,300 events** in the public portfolio!
That category represents the direct evolutionary descendant of Farmer's `RetrospectiveStage` and `0-feedback.md` feedback loops. In HEARTH, the watchdog runs `hindsight` and `dream` calls, updating knowledge associations from historical runs.

**ALEX:**
Farmer proved that deterministic multi-stage pipelines with postmortem reviews worked on a single host. But by late April 2026, Derek ran into the physical ceiling of a single box. In Chapter Eleven, we arrive at the great inflection point: scaling to distributed agentic building.
