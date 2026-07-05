# Watchfire Auto-Heal Envelope Design Proposal

## Problem

The current Watchfire implementation only supports `phantom_in_flight` auto-healing, which is a very limited scope for automated remediation. As the fleet scales and complexity increases, there's a need to expand the types of issues that can be automatically resolved while maintaining safety and reversibility guarantees. The system needs a clear framework for determining what qualifies as "obvious" and "undoable" auto-remediations versus ambiguous cases that should trigger alerts instead.

## Proposed approach (concrete, cite real files)

### Safety Envelope Definition

I propose establishing a safety envelope for auto-heal operations by:

1. **Defining clear criteria** for what constitutes an "obvious" and "undoable" remediation based on the two-economy principles (metered vs sunk compute, advisory vs mandatory)
2. **Expanding AUTO_HEAL_KINDS** to include additional categories that meet safety thresholds
3. **Implementing a validation framework** in `tools/workflow/project_coverage.py` or similar workflow tools to ensure auto-heal operations are safe before execution

### Categories for Expansion

Based on the project's architecture and typical fleet issues, I propose adding these categories to AUTO_HEAL_KINDS:

- `resource_starvation`: When compute resources are temporarily insufficient but can be resolved by scaling or re-scheduling
- `network_partition_recovery`: Automatic recovery from temporary network connectivity issues
- `temporary_service_unavailability`: Auto-restart of services that are temporarily unresponsive but recoverable
- `configuration_drift_correction`: Automated correction of non-critical configuration drifts

### Implementation Approach

The safety envelope should be implemented in a way that:

1. Each auto-heal operation is evaluated against a set of safety criteria before execution
2. Operations that don't meet the "obvious and undoable" threshold are escalated to alerting systems
3. The system maintains audit trails for all auto-heal operations for compliance and debugging

This approach aligns with the advisory vs mandatory principles where automatic actions are limited to those that are clearly safe and reversible.

## Key Principles

1. **Metered Compute**: Auto-heals should use metered compute resources, not sunk compute
2. **Advisory Operations**: All auto-heals should be advisory in nature, with human oversight available
3. **Reversibility**: Every auto-heal operation must have a clear rollback path
4. **Obviousness**: The need for the operation must be obvious to prevent false positives

## Risk Mitigation

To ensure safety:

1. Implement a pre-execution validation step that checks all auto-heal operations against the safety envelope
2. Create a tiered system where different types of auto-heals have different risk levels and approval requirements
3. Establish clear logging and monitoring for all auto-heal activities
4. Include human override capabilities for any auto-heal operation

This framework will allow for expansion of auto-healing capabilities while maintaining the safety and reliability that the fleet requires.

## Next Steps

1. Define specific safety criteria for each new auto-heal category
2. Implement validation logic in workflow tools
3. Create audit and logging infrastructure
4. Establish human review processes for higher-risk auto-heal operations
5. Document all categories and their safety thresholds

This approach ensures that as the fleet grows, we can expand our auto-healing capabilities safely and systematically.