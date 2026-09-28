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
latency promotion evidence. NPU hardware measurement is independent and awaits
verified user-space installation; OpenVINO still lacked NPU at 04:11Z despite the
reported install, and the package log/libraries showed no installation yet.
