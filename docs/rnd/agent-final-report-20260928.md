# Final-text reports through the real read loop

2026-09-28 UTC. One R&D run; no unit tests or baseline reruns.
Build receipt: `br-20260928-052214-b633b7c8`. DeepAgents commit: `e84270f`.

## Question and implementation

Does plain-text final delivery work with the real agent read loop, rather than
the preloaded-source shortcut from the prior lap?

`run_linux_delivery.py` now accepts `--report-delivery tool|final`, only with
`--report`. Omitting it preserves tool delivery. Final mode exposes the existing
four read tools and asks for completed Markdown in the final answer. The runner
materializes that answer through the existing truncation and citation checks;
raw replies are retained. The delivery mode is recorded in manifest and result.
No scheduler interface or production default changes.

The archived expand-edges task and frozen `experiment_linux.py` are byte-preserved.
Source SHA-256: `2e5b0a384428e7d7fb694200543454e2b46b7441f6863cf4cc0231336684d4ce`.
Same 5070/8B, temperature 0, thinking disabled, 16,384 context, 6,144 output ceiling,
35 physical attempts, recursion 96, 1,800-second deadline and 2,048-token tool-result
eviction. The hard report cap remains 800 words; the existing system target is 640.

## Observed edge

Run: `~/work/deepagents-linux/runs/sizer-agent-final-20260928T052256Z/`.

| Observation | Result |
| --- | --- |
| Read loop | One grep, one full-source read, three reads of the evicted result |
| Comparison | Same tool names and arguments as historical failed expand-edges, ignoring generated eviction IDs |
| Tool exposure | All six physical request schemas contain only ls/read_file/glob/grep; no write or edit tools |
| Delivery | Complete final text; `finish_reason=stop`; materialized to report.md |
| Agent trace elapsed | 14.399 seconds, excluding profile switches and process startup |
| Physical calls | 6, all accounted in HEARTH |
| Output | 597 words, 822 final-answer tokens; 1,084 tokens across all calls including tool-control output |
| Shape | Seven citations, valid syntax/ranges; zero citation rewrites; accepted_shape=true |
| Semantic review | Not accepted as a faithful account of recorded measured history |

Historical expand-edges exhausted 6,144 tokens inside an unfinished write_file
envelope after the same reads. This sample supports the delivery intervention; it
does not establish a success rate or isolate every model/parser effect. The 822
final-answer tokens are **medium**, below the long bin's 1,024-token boundary.
Aggregate tool-control tokens must not be used to call it a long-output success.

## Meaning is still a separate gate

The source contains operational comments but no dated measurement history. The
report labels seven code phases “Measured Edge,” which changes code description
into an unsupported claim about the evidence. Basic phase descriptions are mostly
recognizable, but lease TTL is conflated with execution timeout, and restoration
and release guarantees are overstated. Snapshot details require following the
referenced method beyond the cited range. Hypothetical rule changes are invited
by the task, but are not recorded historical decisions.

Full manual review: `~/work/npu-sizer-agent-final-20260928/semantic-review.md`.
Shape success does not constitute semantic approval. No repair or second answer
was requested, and no failed/capped response becomes a training success.

## Evidence, rerun and next uncertainty

`~/work/npu-sizer-agent-final-20260928/` holds registration, task, sampled runner,
raw-run pointer, request/budget comparison, source review, hashes and lap log.
Continuity execution receipt: `2e9e4829b0c2a2c19e4c06f2`.

Exact deliberate rerun (fresh run directory; no automatic repeat):

```bash
~/work/npu-sizer-agent-final-20260928/run-lap.sh
```

The driver verifies source/task hashes and AM4 idle state, temporarily suspends the
drain timer, runs the patched runner with `--report --report-delivery final
--max-report-words 800`, accounts the calls, and restores dense-tp2 and the prior
timer state. A restore failure leaves the timer stopped. Both were restored here.

**Next question:** can a source-grounded task distinguish recorded observations
from implementation behavior, including saying that requested history is absent?
Use a fresh source and explicitly allow that answer; do not tune this exposed
expand-edges prompt into a green result.

Unsampled: repeatability, other files/models/cards, longer final answers, global
route/pin/latency promotion. NPU state is unchanged: verified hardware, rejected
head, no deployed sizer service, global sizing off.
