# The HEARTH Wire: The Proof of Work Paradox
## Chapter 19: The Unclassified Overflow

**Setting:** A quick, inquisitive percussive beat—like sorting through a pile of unlabelled cardboard boxes in an attic.

---

**ALEX:**
Sam, if you look at the chart on `steppeintegrations.com` in Derek’s screenshot, right under the big bars for Door Status and Health, there’s a label:
*"UNCLASSIFIED: 2,504 preserved instead of guessed."*
And on the logarithmic chart, there’s an entry for **Other**: 2,584 calls.
In the staged snapshot we examined tonight, that number had climbed to **2,724 events**.
Why does a precision engineering system have an "Other" bucket with nearly three thousand events in it?

**SAM:**
Because of contract drift between the tool dispatcher and the presentation classifier.
When HEARTH started, every tool was categorized into a fixed public family in [`hearth/projection/call_mix_dashboard.py`](file:///c:/work/commandcenter/hearth/projection/call_mix_dashboard.py):
- `local_generate` → Inference
- `git_*` → Git / VCS
- `read_file`, `write_file` → Filesystem
- `run_tests` → Test / assay
- `submit_task` → Fleet / builds
- `patrol`, `watchfire` → Health / automation
- `kernel_status` → Door status
And any tool that wasn't in one of those explicit lists fell into line 186:
`return "Other"`

**ALEX:**
And what new tools were added to the gateway as the summer progressed that never got added to those lists?

**SAM:**
We ran a Python frequency query across the raw ledger to identify every single event classified as "Other." Here are the top offenders:
- `get_execution`: 140 calls
- `watch_execution`: 113 calls
- `plan_execution`: 36 calls
- `submit_delegated_execution`: 35 calls
- `get_execution_artifact`: 34 calls
- `rotation_status`, `rotation_load`, `rotation_unload`: 23 calls
- `rotation_window`: 4 calls
- `masters_pet`: 2 calls

**ALEX:**
Look at those tool names!
`get_execution`, `watch_execution`, `plan_execution`, `submit_delegated_execution`!
Those aren't miscellaneous garbage. That is the **ADR-0030 Execution Control Plane**!
Those are the high-value tools that orchestrate durable asynchronous jobs, manage worker capacity, and record immutable result artifacts!

**SAM:**
And look at `rotation_load` and `rotation_unload`: that’s the GPU model lifecycle manager!
Because nobody had updated the classification sets in `call_mix_dashboard.py` to recognize the execution control plane, every time an agent submitted a delegated execution job or checked an execution artifact, the gateway dutifully recorded it... and the classifier dumped it into "Other"!

**ALEX:**
It’s like an accounting department that invents a new division for enterprise cloud computing, forgets to give it a budget code, and files all the million-dollar contracts under "Miscellaneous Office Supplies"!

**SAM:**
And look at what happened earlier in the year: before image generation and video highlight rendering were given dedicated public lanes, thousands of ComfyUI and video highlight jobs were also getting lumped into "Other." That’s how the number hit 2,504 in the screenshot.

**ALEX:**
And the fix is clean:
We add `EXECUTION_TOOLS` and `ROTATION_TOOLS` to `call_mix_dashboard.py`. We map them to their rightful families—`Execution Control` and `Fleet / builds`.
And instantly, the "Other" bucket drains from 2,724 down to under 50 genuine anomalies.
The work was always there. It was just misfiled.

**SAM:**
And speaking of filing work: there is another massive body of work in Derek's lab that never got counted on the dashboard. Not code, but words. In Chapter Twenty, we examine the monumental effort he spent explaining this system to others: The Craft of Translation.
