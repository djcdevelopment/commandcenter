# Offline arm preliminary package

Input: `/home/derek/work/lab-rnd/research/evidence/bench27-more-arms-20261005/bench27-more-mtp-off-pass-a`. Control: seat-0; treatment: seat-1.

Run records found: 8. Harness phase: done. No completion or restoration claim is inferred.

Cold exclusions remain visible. At least three admissible warm rows per seat are required to compare against frozen spread. No substance grades or adoption decision. Raw metrics may cover only part of the workload.

| Metric | Control median | Treatment median | n control / treatment | Treatment change | Frozen spread | Ready |
|---|---:|---:|---:|---:|---:|---|
| seconds_total | 225.4 | 379.2 | 3 / 3 | 0.6823425022182785 | 0.8095529699938764 | True |
| work_seconds | 204.0 | 350.7 | 3 / 3 | 0.7191176470588234 | 0.7634961439588688 | True |
| final_seconds | 21.0 | 28.2 | 3 / 3 | 0.34285714285714275 | 1.9565217391304346 | True |
| decode_tokens_per_s | 56.52955369527114 | 29.60703675903118 | 3 / 3 | -0.47625560748929296 | 0.03302938974509243 | True |

Assignment verified: True (harness spec.dropin). Card map: {'seat-0': 'card2', 'seat-1': 'card3'}. Divergence reference: rep-1/seat-0/run.json (may be the treatment seat in a swapped pass).
Card and treatment are confounded within one pass; an arm verdict needs both swapped passes. Lower seconds is better; higher decode tokens/s is better. Null spread comparisons remain unavailable; see per-metric reasons in metrics.json.

All rates above are fractions. Exact source fields, run hashes, thermal windows, first-token/prefill metadata, counters and exclusions are in metrics.json. Decode uses generation_tokens_total/decode_seconds_sum; durations use run.seconds_total and conversation work/final.seconds. Output length may differ. No A/B-derived recalibration.

Only blind/ goes to the grader. Raw copies, deterministic renderer repair manifests and mapping remain outside it. Needles are unmeasured unless a grade file was explicitly supplied. Re-run into a new output directory after remaining work completes; preserve this snapshot.
