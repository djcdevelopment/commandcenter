# HEARTH Banked Fire Retrospective (am4-worker-1)

## 1. Mechanical vs Narrative Reality

The narrative describes a complex, orchestrated deployment of Banked Fire on AM4 with multiple planes: P1 (router), P2 (occupancy probe), P3 (task lane), P4 (self-healing watchdog), and P5 (idle-drain). However, without access to the actual codebase via git, I cannot verify:
- Whether the FanOutEdgeGroup bug was truly fixed in the task lane (P3) or if the fix was superficial.
- If the MechnetWatchdog's kill/heal cycle is truly registered and proven within ~15s (no logs or test evidence available).
- Whether the idle-drain of 46 candidates was actually budget-constrained and ratified (no budgeting logic visible).
- The exact nature of the "doorcheck --revive DETACHED" mechanism (no code to confirm implementation).

The narrative claims a "clean supervised busy-no-op cycle" before arming P5, but no evidence of such a cycle exists in the codebase.

## 2. Three Risks in the Banked Fire Design

1. **Single Point of Failure in Task Lane**: The earlier crash of the conductor fan-out due to a FanOutEdgeGroup bug indicates a fragile core component. Even after the fix, the design remains vulnerable to similar edge-case failures in the task dispatch layer.

2. **Unverified Self-Healing**: The MechnetWatchdog is claimed to be "registered, kill/heal proven ~15s", but without observability or logs, this cannot be independently validated. A false sense of reliability may lead to undetected failures.

3. **Test-Driven Leak Risk**: The incident where 7 junk test inbox items leaked to the conductor due to a test-mock default-arg bug suggests weak test isolation. This could recur if test and production code paths are not strictly separated.

## 3. One Improvement Requested

As a fleet worker, I would request **a dedicated, isolated test environment with real-time observability** for all new builds and deployments. This would allow us to validate the full lifecycle (including idle-drain, self-healing, and task routing) before any offload crosses the ledger, reducing the risk of production incidents and enabling faster, safer iteration.
<!-- agent_openai completion: reason=finished steps=3 elapsed_s=36.1 model=vllama-planner -->
