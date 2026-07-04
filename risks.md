1. Processes may hold render nodes without actively using GPU resources, leading to overestimation of occupancy.
2. Multiple processes may share a render node, making ownership attribution ambiguous.
3. Render node ownership can be transient or spoofed, leading to inaccurate tracking.

Mitigation for the biggest risk: Combine ownership checks with actual GPU utilization metrics from GPU monitoring tools to avoid overestimation.