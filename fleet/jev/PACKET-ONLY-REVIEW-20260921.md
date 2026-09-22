# Packet-only Hermes review — cycle 19

Useful artifact: an opt-in reviewer that consumes a complete evidence packet
without spending agent turns on source lookup, plus one real source review.
This is a review-mode qualification, not a new builder, model, or controller.

## Change and authority

`fleet.jev.review.run_hermes(..., packet_only=True)` adds the installed Hermes
setting `agent.disabled_toolsets: [all]` to the private per-run configuration.
The option is keyword-only, strictly boolean, and defaults to `False`.
`run_review` still calls the existing default; automatic JEV review is unchanged.

The CLI still selects only `hearth`, and the same read-only reviewer credential
and endpoint remain configured. No filesystem/shell toolset is enabled. The
setting is not a security boundary: it filters the offered tools; the existing
restricted credential remains the authority boundary. MCP discovery may still
connect to that same server even with no tools offered to the model.

The wrapper counts assistant messages carrying tool calls and tool-result
messages in the fresh run database. Packet-only mode refuses to return a report
if either count is nonzero. These are message counts, not individual-call counts.
Zero observed calls alone is not proof that no tool schema was offered.

Callers must supply complete, trustworthy source and execution evidence. This
mode cannot repair a missing baseline or investigate facts outside the packet.
Hermes remains an advisory critic, never an automatic acceptance authority.

## Evidence and qualification

Installed source was inspected under
`/home/derek/.local/share/hermes-fleet/hermes-agent` on FX99:

- `cli.py::_init_toolsets` reads `agent.disabled_toolsets`.
- `agent/agent_init.py::_load_tools` forwards the setting to tool selection.
- `model_tools.py::_select_tool_names` subtracts disabled toolsets last.
- `all` resolves registered toolsets, including dynamically registered ones.

A CPU-only call to that installed resolver registered an in-process probe group
with one tool name. Enabled selection returned `probe_read`; adding disabled
`all` returned an empty set. No inference or real tool execution occurred in
that check. Candidate syntax and `git diff --check` also passed. No test files
were added.

The single inference packet contains the full baseline, full proposed wrapper,
the installed-source excerpts, and the CPU check results. It requests a complete
review of at most 250 words and explicitly distinguishes observed checks from
unperformed execution.

The unedited report and assessment are in
[`evidence/20260921-packet-only-review.json`](evidence/20260921-packet-only-review.json).
Installed Hermes commit: `345cd2b057a452236de401d3534b8502a7465e8d`.

| Observed live result | Value |
| --- | --- |
| Final advisory verdict | NEEDS_WORK |
| Hermes elapsed / run ceiling | 32.433 s / 90 s |
| API calls to local model | 1 |
| Input / output tokens | 5,659 / 595 |
| Tool-call messages / tool-result messages | 0 / 0 |
| Actual words / requested maximum | 386 / 250 |
| Private configuration disabled toolsets | `["all"]` |
| Saved session tool inventory | `null`, not an explicit empty list |

Qualification is **limited**, not a promoted default. The report correctly
questions whether a custom in-process resolver probe proves the real MCP tools
were absent. Zero calls, a null session inventory, and an absent console banner
do not directly record the offered schemas. That edge ends this qualification;
there is no second inference or newly built capture harness.

The report also incorrectly says the CLI re-enables Hearth after subtraction;
the supplied code shows enabling first and disabled subtraction last. Its demand
that MCP be disconnected is not a requirement. Its recommendation to remove the
Hearth-only CLI pin is **not adopted**: an omitted selection can enable defaults.
The model's critical-severity claim is not evidence that re-enablement occurred.
The complete pre-run packet could not contain results from the run itself.

The review was delivered, but missed the requested word limit and included
unsupported claims. No quality improvement or latency reduction is established
against earlier reviews of different packets. A future qualification needs
direct evidence of the offered MCP schemas, not another unobserved assertion.

## Time, cost, and attribution

The lap began at 11:55:53 UTC. Source inspection consumed the first ten minutes;
the first-quarter artifact discipline was missed. The implementation was written
at 12:06:51 UTC. One orchestration-tool syntax error occurred before any command
execution and was corrected without dispatching another inference attempt.
Generation ceiling: 12:15 UTC. Delivery/restoration ceiling: 12:20 UTC.

Codex implemented the wrapper and assembled the qualification packet. Hermes
authored only the unedited advisory review. There is no local-builder candidate
and no JEV/API scoring call in this lap. Local GPU use and Codex cost are not
included in the JEV ledger. No speedup is claimed from this unmatched sample.

Receipt: `br-20260921-120651-c51d6724`.
Private local evidence: `C:/Users/derek/.fleet-scheduler/review-cycle19/`.
Private FX99 run: `/home/derek/.local/state/fleet-scheduler/reviews/cycle19-packet-only/`.

## Deployment and restoration

The optional wrapper was hash-guarded onto FX99 for a fresh operator-driven
Python process. Before SHA-256:
`0a50aba9f7e77ca60829886df28cd887a3187c8f2c45a739fb9a2cdf4f6cc0c5`.
Candidate SHA-256:
`38ebcb8de62b2d05555932a0e88a4b9ca0babdb948408ee1f6505db58227cf8d`.
Backup suffix: `.jev-packet-mode-cycle19-20260921.backup`.

The running CPU scheduler was not restarted; its cached module remains the old
version. Even a future new process retains the old mode unless explicitly opted
in. The policy-denied pilot gateway restart was not retried. Production gateway
and OMEN worker configuration were not changed.

AM4 uses the existing fixed Dense-27B, 128k/one-slot, two-NVIDIA-card profile and
owner `jev-fb3a05cd37e34ccb9314e2cb`. No KV state is reused. The established helper
owns startup and release; no bare global unload is used.

The helper returned `owner: null`, `model_resident: false`,
`native_context: null`, and `kv_enabled: false` after the report. This was observed
by 12:10:46 UTC, before both ceilings. AM4's review GPUs are released.

At 12:13:59 UTC the scheduler was ready/idle with no active build, no pending
review, and no GPU reservation; the deployed source hash matched the candidate.
JEV accounting was unchanged: 15 calls, $0.000498582 usage estimate plus
$0.005505024 uncertain reservations ($0.006003606 booked). Reservations are not
confirmed charges, and these figures do not cover Codex or local hardware cost.
