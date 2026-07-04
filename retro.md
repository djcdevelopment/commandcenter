# HEARTH Banked Fire Retrospective (am4-worker-1)

## 1. Mechanical vs Narrative Reality (10-15 lines)

The narrative describes a complex, multi-layered system: P1 (router), P2 (occupancy probe), P3 (task lane), P4 (self-healing watchdog), and P5 (idle-drain). The mechanical reality, inferred from the narrative, suggests a system with strong fault tolerance and opportunistic scheduling. However, the narrative omits critical details: no mention of the FanOutEdgeGroup bug fix, which caused the initial conductor crash—this is a major omission. The "clean supervised busy-no-op cycle" is mentioned as a precondition for arming the idle-drain, but no code-level evidence of such a cycle is provided. The "three-plane doctrine" is described as adopted, but no code changes to enforce boundary ledgering or cross-plane coordination are detailed. The incident with 7 leaked test items due to a test-mock default-arg bug is noted, but no code changes to fix this are described. The fix for the single-builder FanOutEdgeGroup bug is mentioned as applied in the task lane, but no diff or commit shows how this was implemented. The self-healing MechnetWatchdog is said to be "registered, kill/heal proven ~15s"—but no code for the 15s timing or registration mechanism is visible. The "ratified operating budget" for 46 priced candidates is mentioned, but no budget enforcement logic is described. The narrative assumes the system is stable post-fix, but the absence of any code changes in the diff suggests the fix may be undocumented or uncommitted. Overall, the narrative is rich in intent but thin on mechanical verification.

## 2. Three Risks in the Banked Fire Design

1. **Undocumented Critical Fixes**: The FanOutEdgeGroup bug fix was applied but not committed or documented, creating a high risk of regression or loss of context.
2. **Test Pollution Risk**: The test-mock default-arg bug that leaked 7 items into the conductor indicates weak test isolation and a lack of defensive programming in test setup.
3. **Over-Reliance on Manual Supervision**: The requirement for a "clean supervised busy-no-op cycle" before arming the idle-drain introduces a manual dependency that could be forgotten, leading to unintended offloads.

## 3. One Improvement Requested

As a fleet worker, I would request a **centralized, versioned configuration registry** for all operational parameters (e.g., budget, idle-drain thresholds, supervision cycle triggers) to eliminate manual configuration drift and enable auditability and rollback.
<!-- agent_openai completion: reason=finished steps=3 elapsed_s=33.3 model=vllama-planner -->
