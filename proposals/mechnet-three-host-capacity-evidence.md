# Mechnet Three-Host Capacity Evidence

## Design Document

Date: 2026-07-03

This document outlines the smallest durable observation set needed to plan throughput across three physically distinct GPU hosts: OMEN with two Intel Arc Pro B70 cards, FX99 with RTX 2070 SUPER 8 GiB, and AM4 with RTX 5070 about 12 GiB. The current host placement is caller-supplied and not derived from the source evidence.

## Sensor Facts

The following sensor facts must be observed:

- Peak power consumption (`power_w_peak`)
- Peak VRAM usage (`vram_gb_peak`)
- Peak throughput (`tokens_per_s`)

## Residency and Quality Conclusions

The following residency and quality conclusions must be derived from the sensor facts:

- Thermal envelope and sustained-decay coefficients
- Warm/cold bias effects on model residency
- Power-throughput ceiling

## Hardware Profile ID

The `hardware_profile_id` field prevents an obsolete AM4-B70 observation from being treated as 5070 capacity. This ensures that observations are specific to the hardware configuration they were taken under.

## Concrete Measurements

The following three concrete measurements should be collected next:

1. **Sustained-load decay**: Measure the drop in `tokens_per_s` after a sustained period of high load on each GPU host.
2. **Cold-load latency tax**: Measure the elevated `ttft_s` and lower early-window `tokens_per_s` on cold-load versus warm-resident models on the same builder+model.
3. **Power-throughput ceiling**: Measure the peak power consumption and corresponding peak throughput on each GPU host to identify the power-throughput ceiling.

## References

- Section 1: Proposed observation model
- Section 2: Metrics that must never become authored state
- Section 5: Model residency update on 2026-07-03