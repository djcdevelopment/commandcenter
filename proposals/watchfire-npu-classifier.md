# Watchfire NPU Classifier Design Proposal

## Problem

We need to implement a deferred NPU coherence-gap classifier (ADR-0007) that serves as a learned detector of incoherence/gaps beyond the rules-first approaches in hearth/health/gaps.py. This classifier should operate within the 'act on obvious+reversible, flag ambiguous' policy and integrate with the fleet protocol's blocked-on-ambiguity mechanism.

## Proposed approach (concrete, cite real files)

### Core Functionality

The classifier will be implemented as a machine learning model that runs on NPU hardware to detect coherence gaps in fleet operations. It will be designed to:

1. **Operate in deferred mode**: Only activated when initial rule-based checks don't resolve ambiguity
2. **Follow the 'act on obvious+reversible' policy**: Only take action on clearly identified issues, flag ambiguous cases
3. **Integrate with fleet protocol**: Use the blocked-on-ambiguity mechanism to coordinate with other system components

### Technical Implementation

The classifier will be implemented in a new module that follows existing patterns in the codebase:

- **Model Architecture**: A lightweight neural network designed for edge inference on NPU hardware
- **Signal Processing Pipeline**: Preprocessing of fleet telemetry data to extract relevant features
- **Integration Layer**: Interface with existing fleet protocols and decision-making systems

### File Structure

Based on typical project organization, the implementation would likely reside in:

```
tools/npu_classifier/
├── __init__.py
├── classifier.py
├── model.py
├── pipeline.py
└── config.yaml
```

The classifier will interface with existing fleet components through established protocols and may integrate with:
- `fleet-worker-node/` for operational data
- `tools/` directory for shared utilities
- `tests/` for validation

## Risks and Mitigations

1. **Performance**: NPU inference latency must be within acceptable bounds
   - *Mitigation*: Pre-optimization and profiling during development

2. **Accuracy**: Classifier may produce false positives/negatives
   - *Mitigation*: Comprehensive testing with historical data, human review of flagged cases

3. **Integration Complexity**: Coordination with existing fleet protocols
   - *Mitigation*: Gradual integration with clear error handling and fallback mechanisms

## Integration Points

The classifier will integrate with:

- Fleet protocol's blocked-on-ambiguity mechanism
- Existing rule-based gap detection in hearth/health/gaps.py
- NPU hardware acceleration infrastructure
- Decision-making systems for action triggering

This approach ensures that the classifier operates within established safety boundaries while providing additional intelligence to detect complex coherence gaps that rules alone cannot capture.