# QUESTION — hearth-2da0a51f / cc-builder-1 / lap1

**Single blocking question:**

The plan file `hearth-2da0a51f-cc-builder-1.md` contains only the single character `q` as its task body
(followed by the standard fleet-protocol footer). There is no actionable specification — no stream ID,
no deliverable list, no TASKS block, no DEFINITION OF DONE.

**What should I build?**

Observed options from context:
- Wave 2 pour stream **D1** (`pour-d1-cc-builder-1.md`): Δ1 economics correction — scheduler-decision schema + project_scheduler.py
- Wave 2 pour stream **E1** (`pour-e1-cc-builder-1.md`): Δ4 idle-drain prerequisites — idle-state schema + experiment flags (nothing dispatches on idle)
- Wave 2 pour stream **F1** (`pour-f1-cc-builder-1.md`)
- Wave 2 pour stream **Ga** (`pour-ga-cc-builder-1.md`)
- Wave 2 pour stream **Gc** (`pour-gc-cc-builder-1.md`): confidence-curve calibration projection + experiment candidate
- The regression probe task (`regression-probe-ccb1-cc-builder-1.md`): `tools/text/wordcount.py` + tests
- Something else entirely

**What I found:** baseline suite passes at `Ran 162 tests ... OK` from the repo root
(`python3 -m unittest discover -s tests/workflow`). Working tree is clean on branch
`ccfarm/hearth-2da0a51f/cc-builder-1/lap1`.

**Recommendation (if no answer arrives):** interpret `q` as a reference to the **Gc** stream
(calibration / quality — the only stream with a thematic "q" connection), and proceed with
`pour-gc-cc-builder-1.md`. State this assumption in BUILD-NOTES-Gc.md.

Awaiting operator answer before proceeding.
