# Image-gen scheduling: 250-request comparison

Input: 250 synthetic image-gen requests (bursts of 10-20 per model).

## Duration heuristic

`seconds = steps * (width*height / 1024^2) * batch_size * k_model`

- k_model: {'sd3.5_large': 0.55, 'sdxl-base': 0.3, 'flux-schnell': 0.18}
- model load (swap) seconds: {'sd3.5_large': 25.0, 'sdxl-base': 10.0, 'flux-schnell': 15.0}
- At 1024^2: sd3.5_large ~0.55 s/step (~16.5s @30 steps), sdxl ~0.30, flux-schnell ~0.18.

## Machine

Single stateful `am4-imagegen`: host am4, 2x Arc Pro B70 32GB, staging_slots=1 (single DDR4), cold start (resident_models=[]).

## Results

| Arm | Mode | Solver | Makespan (s) | Loads | Setup (s) | Deadline misses | Makespan vs FIFO |
|---|---|---|---|---|---|---|---|
| fifo-baseline | serial single-queue, 1 resident model | n/a (simulation) | 3378.0 | 24 | 410.0 | 19 | 0.0% |
| smart-baseline | serial single-queue, 1 resident model | n/a (simulation) | 3018.0 | 3 | 50.0 | 16 | 10.7% |
| scheduler | single-shot-250 | OPTIMAL | 3124.0 | 3 | 50.0 | 0 | 7.5% |

## Load counts per model per arm

- **fifo-baseline**: {'sd3.5_large': 9, 'sdxl-base': 8, 'flux-schnell': 7} (total 24), setup 410.0s
- **smart-baseline**: {'flux-schnell': 1, 'sd3.5_large': 1, 'sdxl-base': 1} (total 3), setup 50.0s
- **scheduler**: {'flux-schnell': 1, 'sd3.5_large': 1, 'sdxl-base': 1} (total 3), setup 50.0s

## Deadline misses (rush jobs)

- **fifo-baseline**: 19
- **smart-baseline**: 16
- **scheduler**: 0
