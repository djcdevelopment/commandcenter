# N4 recovery: delivery is a separate question from seat selection

2026-09-28 UTC. R&D, not promotion. Build receipt `br-20260928-040334-a342809b`.

## Recovered state

Claude session `96c16822-cef5-41eb-9b13-15a9a7308b96` stopped before reviewing N4.
The original plan is `~/.claude/plans/mossy-stargazing-naur.md`; implementation is
flash `a4091a4` + `0f2449b`. ADR-0050 supersedes the plan's midpoint reserves and
inferred-family proposal: use upper bin edges; never invent an absent family.
N0 is built; N1 has exported encoders and CPU/iGPU measurements; N2 rejected its
head; N3 is built but not deployed. N4 completed, but did not qualify long work.

The original 12-brief table selected Ti for six short chores and 5070 for six long
briefs. Six short artifacts passed the runner's shape checks; none of the long
artifacts did. That is not a semantic quality score or a hardware comparison.

| Long brief | Evidence-backed failure |
| --- | --- |
| walkthrough-drain | Last provider response: `finish_reason=length`, 6,144 completion tokens, no parsed tool calls. An unfinished `write_file` envelope was copied from final text into `report.md`. |
| expand-edges | Same truncated tool-envelope failure at 6,144 tokens. |
| full-inventory | Repeated reads; graph recursion ceiling reached without a report. |
| explain-service | Repeated reads; physical-attempt ceiling reached without a report. |
| rewrite-docstrings | Completed 323-word uncited artifact, including constants mislabeled as classes. Existing citation/shape gate rejected it. |
| translate-readme | No model run: source `hearth/localwork/README.md` does not exist. Wrapper traceback proves `FileNotFoundError`. |

Original evidence is preserved at `~/work/npu-sizer-recovery-20260928/evidence/`:
480 files / 7,427,187 bytes, with relative paths and SHA-256 in `manifest.json`.
Original session files and failed outputs remain unchanged. No failed or capped
output becomes an uncensored training label.

## One paired diagnostic

`tools/sizer/probe_delivery.py` makes one accounted request per arm, with the same
frozen `experiment_linux.py`, numbered source preloaded, 650–750-word task (800
hard maximum), model/5070, temperature 0 and 6,144-token ceiling. Only delivery
changes: one `write_file` tool versus plain Markdown. Neither arm uses the agent
read loop. Model tools are not executed; a complete parsed write can be retained
only as a candidate at the fixed report path. Raw responses are always retained.

| Arm | Result | Physical output tokens | Wall |
| --- | --- | --- | --- |
| tool | `length`; no complete write; tail degenerates into numbered references beyond source length | 6,144 | 67.92 s |
| text | `stop`; 591 words, six in-range citations; passes existing shape gate but misses requested length | 785 | 9.25 s |

Run directories: `~/work/deepagents-linux/runs/sizer-preloaded-{tool,text}-20260928T040658Z/`.
Both physical requests are imported into HEARTH. The tool arm initially used an
invalid accounting phase (`delivery-probe`). Its original SQLite outbox is preserved
as `probe-tool-outbox-before-phase-correction.sqlite`; `accounting-correction.json`
records the sole correction to `task`, hashes and attempt ID. Request/response,
usage and outcome were not altered. The tool inference was not repeated.

**Finding:** this sampled tool-write failed even without the read loop; plain text
completed on the same seat. This supports probing delivery format before adding
tokens or blaming the card. It does not isolate parser versus model behavior.
The text result is below even the probe's 600-word long-report screen and is a
medium output by ADR-0050's token bins. It is not a long-output training success.

Manual source review: the six core phase descriptions are recognizable and the
cited ranges contain relevant code. The report overstates process isolation,
universal crash recovery and full reproducibility. Range-valid citations do not
prove those claims. No semantic approval or unattended promotion is recorded.

## Built from the observed failure

The DeepAgents runner now distinguishes `report_delivery_truncated` and
`report_delivery_unparsed_tool_call` when considering final-text fallback. It keeps
the provider text in `final-message.txt`, leaves the report empty, and records
`final_finish_reason` / `delivery_failure` in `result.json`. It never repairs or
executes an incomplete tool envelope. Previously the result had `error: null` and
the envelope masqueraded as report text (although the shape gate rejected these
particular samples).

Replay against actual saved replies identifies both truncated originals and
leaves the seven completed final texts eligible for the existing checks. Evidence:
`~/work/npu-sizer-recovery-20260928/fallback-replay.json`. No unit tests or new model
runs were used for that check. Full agent-loop behavior after the change remains
unsampled; the patch changes only fallback handling and failure observability.

## State and exact rerun

The bounded driver temporarily stops the drain timer, refuses active drain slots
or occupied AM4 dense metrics, restores the prior profile, then restores the
timer. On restore failure it leaves the timer stopped and reports the failure.
Both diagnostic windows restored `dense-tp2`; normal drain scheduling resumes.
Global `HEARTH_SIZER` stays off; no head or sizer service is promoted.

To rerun this exact pair deliberately (not an instruction to repeat for a green
result), on this host:

```bash
~/work/npu-sizer-recovery-20260928/run-probe.sh
```

The driver generates fresh run names; its task is
`~/work/npu-sizer-recovery-20260928/probe-task.md`. A single arm can be selected with
`run-probe.sh text` or `run-probe.sh tool`. The driver requires AM4 initially on
`dense-tp2` because its occupancy check reads that seat's metrics.

**Next distinct lap:** retain the real read loop and ask for plain-text final
delivery, rather than write_file, on one of the original report tasks. Keep the
existing length/citation checks; record factual support separately. That isolates
whether plain-text delivery helps inside the agent harness. Do not broaden to a
suite or tune repeatedly on this source.

**Uncertainty:** one source/prompt per arm; no Ti comparison; no end-to-end long
report qualified; original read-loop failures remain; no global routing/pin/door
latency promotion evidence. NPU sequence length 512, competing CPU load, power use, service warm-up, and trained-head accuracy remain unsampled. Hardware verification completed in the separate lap below.


## N1 completed after Derek installed the drivers

The initial reply referred to downloaded packages. Derek then ran all three
install/reload commands; dpkg now confirms the matched Intel 1.38.0 packages and
OpenVINO 2026.4.0 sees `NPU`, Intel AI Boost, architecture 3720. No reboot required.
The busy counter started at zero and advanced during inference.

Static `[1,256]`, 50 measured forwards after five warm-ups, separate fp16/int8 IRs:

| Variant | NPU p50 / p90 | CPU p50 / p90 | NPU cold / cached compile | Busy delta |
| --- | --- | --- | --- | --- |
| fp16 | 7.94 / 8.43 ms | 6.70 / 7.22 ms | 0.574 / 0.044 s | +496,639 us |
| int8 | 8.17 / 9.41 ms | 3.54 / 3.66 ms | 0.611 / 0.042 s | +469,200 us |

NPU memory increases by 62,853,120 bytes (59.94 MiB), above the 68,722,688-byte
idle driver baseline, and returns to baseline after the process exits. Benchmark
RSS deltas are process high-water increments, not a standalone service footprint.
Ten existing export probes compared with CPU-fp16 embeddings: minimum cosine
0.9999973 (NPU fp16) and 0.9995586 (NPU int8).

The temporary HTTP service was forced to `NPU` with **no CPU fallback**, then driven
through the existing 30 ms sizer client. First two calls timed out into the local
heuristic (31.03 / 30.33 ms); next three completed on NPU (18.79 / 17.96 / 18.41 ms).
The service recorded all five encoder calls and +79,480 us busy time. `/health`
was already `ok` before the first requests: compilation readiness does not mean
warm inference readiness. No timeout was increased and no requests were retried.
After stopping the owned service, heuristic fallback took 0.22 ms.

**The whole service is not a 60 MiB process:** sampled VmRSS after five requests
was 1,094,648 KiB (~1.04 GiB), including Python/tokenizer/OpenVINO and their loaded
libraries. RSS and NPU memory may overlap; do not add them as independent totals.
No head was loaded, so `source` correctly remained `heuristic` while
`encoder_device=NPU` identified the redundant encoder pass.

Decision: the NPU is a measured, usable encoder device; the CPU INT8 path is faster
on this unloaded sample. Leave the sizer unit uninstalled/inactive and the global
routing gate off. CPU-contention/power advantages are hypotheses, not measurements.
A learned head needs representative successful long-output labels before another
accuracy comparison. Service warm-up and memory footprint matter only if a useful
encoder workload earns deployment.

Raw records: `~/work/npu-sizer-recovery-20260928/npu-{fp16,int8,agreement,http}.json`.
Exact benchmark rerun from the flash repo (new cache directory avoids disturbing
other users' caches; this is not a request to repeat the sample):

```bash
~/.venvs/npu/bin/python tools/sizer/bench.py --device NPU --device CPU --ir ~/models/minilm-ov/fp16 --seq 256 --n 50 --cache ~/work/npu-sizer-recovery-20260928/ov-cache/fp16 --json ~/work/npu-sizer-recovery-20260928/npu-fp16.json
```

Use `int8` in place of `fp16` for that variant. HTTP slice:
`~/.venvs/hearth-private/bin/python ~/work/npu-sizer-recovery-20260928/npu-http-probe.py`.
The service process is stopped in `finally`; no systemd unit was installed.
