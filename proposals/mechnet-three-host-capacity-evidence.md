# Mechnet Three-Host Capacity Evidence Proposal

## Introduction
This document outlines the smallest durable observation set needed to plan throughput across three physically distinct GPU hosts: OMEN two Intel Arc Pro B70 cards, FX99 RTX 2070 SUPER 8 GiB, and AM4 RTX 5070 about 12 GiB. This is a 2026-07 design document, and current host placement in this brief is caller-supplied, not source evidence.

## Smallest Durable Observation Set
To plan throughput across three physically distinct GPU hosts, the following sensor facts must be observed:

1. **Model Residency**: Identify whether the model is in `warm_resident` or `cold_load` mode.
2. **Power Consumption**: Monitor peak power consumption (`power_w_peak`) to understand the hardware's thermal limits.
3. **Throughput Performance**: Measure sustained throughput (`tokens_per_s`) under different load conditions to determine capacity.

## Residency/Quality Conclusions
The residency and quality conclusions must be derived from the observed data:

1. **Sustained-Load Decay**: Analyze how `tokens_per_s` drops after a certain period at high temperatures.
2. **Cold-Load Latency Tax**: Evaluate the impact of cold load on latency (`ttft_s`) and throughput (`tokens_per_s`).
3. **Power-Throughput Ceiling**: Identify when power consumption peaks while throughput does not scale further.

## Hardware Profile ID
The `hardware_profile_id` prevents an obsolete AM4-B70 observation from being treated as 5070 capacity by ensuring that each observation is tied to a specific GPU/driver/thermal-solution lineage.

## Next Concrete Measurements
To collect the required measurements, the following actions should be taken:

1. **Run Workflows**: Execute multiple workflows on each host to gather data on model residency, power consumption, and throughput performance.
2. **Analyze Data**: Analyze the collected data to derive findings on sustained-load decay, cold-load latency tax, and power-throughput ceiling.
3. **Document Observations**: Document the observations and conclusions in this document for future reference.

## Conclusion
This proposal outlines the smallest durable observation set needed to plan throughput across three physically distinct GPU hosts. By collecting and analyzing the required data, we can make informed decisions about hardware placement and capacity planning.