# OPERATIONS-CHECKLIST.md

## Normal Work Cycle

1. **Start**: Run `hermes-fleet` to enter the FX99 session. Confirm readiness via `hermes-fleet status` (or `--json`). Only proceed if the fleet reports native identity and readiness.

2. **Build Setup**: Use only `cc-builder-2` or `cc-builder-3`. These VMs are CPU-only and run through the MechNet conductor. Do not use AM4 or cloud fallbacks.

3. **Task Execution**: For each build:
   - Write the required deliverable (e.g., `OPERATIONS-CHECKLIST.md`).
   - Do not perform redundant reads of source code.
   - Use `omen-resident-hearth` preset to route generation to OMEN.
   - Ensure prompt size fits within 16384-token slots (max 8 active on B70 pair).
   - Never submit prompts exceeding 131072 tokens (shared across AM4’s Dense 27B and compression).

4. **Slot Admission**: If a job exceeds slot capacity, reject the prompt immediately. No inference runs on oversized inputs.

5. **Worker Status**: If OMEN reports busy or in maintenance, wait within budget. Do not silently switch hardware. Stop if timeout is exceeded.

6. **Build Completion**: Save deliverables. Commit only required files (e.g., `OPERATIONS-CHECKLIST.md`). Do not modify global presets.

7. **Review**: Candidate builds are not merged automatically. Submit for manual review. Acceptance requires:
   - File exists in branch.
   - Content matches requirements.
   - Behavior verified independently (do not rely on inherited tests or self-written retro).

8. **Evidence & Routing**: Accepted outcomes are recorded as receipts. Physical inference logs support later routing. Do not confuse this with model-weight training.

## When to Stop

Stop after committing the required deliverable, **only if**:
- The file is correct per requirements.
- No additional files are needed.
- The system state is unchanged (no changes to source, presets, or fleet).

Do not proceed with further actions unless re-queued. No assumptions on spills, placement, or future features (e.g., MemSplice, SYCL, 256k workers). This rollout does not certify VRAM spill-free operation or deep-context performance.

**Note**: If a task is ambiguous, write `QUESTION.md` and stop. Otherwise, build with stated assumptions.