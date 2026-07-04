1. A process may hold the render node open without actively using the GPU, leading to false occupancy readings.
2. Multiple processes may share a render node, making ownership attribution ambiguous.
3. A process may hold the render node open after GPU work is complete, causing prolonged occupancy tracking.

Mitigation for the biggest risk: Combine render node ownership with active GPU compute activity monitoring via GPU driver APIs.