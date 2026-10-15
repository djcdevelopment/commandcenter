# ADR-0066: A pooled lane declares its code backend; an absent default falls back by configuration

- Status: **Accepted** (Derek, 2026-10-15; deployed on the door at c0c7dc3)
- Date: 2026-10-15
- Goal: G-bench27 (G-lab); INTEGRATION G7

## Context

Two refusals reached frontier sessions through amux on 2026-10-15:

1. `submit_local_work(task_family="code_fix")` without a delivery brief refused with
   "pooled code work is unsupported: no code-qualification source; a delivery brief is required".
   ADR-0062 says report verdicts are not code qualification and that singleton code behaviour is
   unchanged, but ADR-0065 made the deep lane a three-backend pool on 2026-10-13, so no singleton
   code route remained.
2. `local_generate` with a default-lane family (summarization, drafting) failed with
   "routing failed: backend 'omen-vllm' is marked absent under active configuration 'three-dense'".
   `select_backend` skipped absent tag candidates but then returned the absent pool default, and
   `plan_execution` reported `lane_not_live` without refusing, so plan and dispatch disagreed.

## Decision

1. **Lane code backend.** A lane table in the local-work route profile may declare
   `code_backend = "<name>"`; it must name one of the lane's backends (else
   `code_backend must name one of the lane's backends`). Work without a delivery brief on a pooled
   lane goes to that one backend, unpooled, and the manifest records
   `route.backend_choice.by = "lane_code_backend"`. Without a `code_backend` the ADR-0062 refusal
   stands unchanged. The tracked Linux profile declares `[lane.deep] code_backend = "omen-dense-27b"`.
2. **Default fallback by configuration.** When the pool default is absent under the active lab
   configuration and the call asked for the default lane (no tags, or tags including `default`),
   the door walks the configuration's `default_fallback` list (three-dense:
   `["omen-dense-27b", "omen-dense-27b-b"]`), else `dense`-tagged backends in pool order, and takes
   the first live one that fits the payload and output reserve. The reason is
   `absent_fallback:<default>-><name>`, logged as a warning, carried in `routed_by`, and the
   occupancy, plan and dispatch record carry `fallback_from`. Plan and dispatch share the selection,
   so they name the same provider and reason. No live fallback that fits is a loud refusal.
3. **Capability lanes refuse loudly.** When the default is absent and the call named a capability
   other than default (tool-use, utility, vision) whose candidates are all absent, the door raises
   `lane [...] absent under configuration <cfg>: <names>` instead of returning the absent default.
   With a live default the documented `<tag>_lane_not_live:default` behaviour is unchanged.

## Consequences

- Code work is singleton on the deep lane (omen-dense-27b) until a code-qualification source exists;
  ADR-0062 is unchanged in intent. Delivery briefs still pool by recorded verdicts.
- Default-lane offloads keep working under three-dense on a 27B replica, visibly labelled; they are
  slower per token than the absent 30B, and the label lets the ledger count them.
- Tool-use calls under three-dense refuse with the lane named, instead of failing at dispatch on a
  seat name.
- Deploy: the integrator syncs `local-work-routes-linux.toml` and the managed
  `lab-configurations.toml` copy to their live partners (`host_config.py --check` shows the drift
  until then) and restarts the door after the suite passes.

## Amended 2026-10-15

Operator direction: all GPUs are available; code work runs in parallel on both B70s and AM4.

- **A list.** A lane may declare `code_backends = [...]` (nonempty, distinct, each one of the lane's
  backends). `code_backend = "<name>"` stays as its one-element alias; declaring both refuses
  (`must declare at most one of code_backend or code_backends`). The tracked Linux profile declares
  `code_backends = ["omen-dense-27b", "omen-dense-27b-b", "am4-vllm"]`.
- **The pick.** Work without a delivery brief on a pooled lane still lands on ONE seat, unpooled (no
  pooled claim, no qualification check; ADR-0062 unchanged in intent). The list is ordered by the
  pool's own load key in `submit` (outstanding assignments, then active leases, then configured
  order) and the first seat that is not `absent` under the active lab configuration
  (`hearth.toolsurface.backends._absent`) and is in the pool is taken. The manifest records
  `route.backend_choice = {by: "lane_code_backend", backend, considered, assignments, skipped,
  output_need}` and one `log.info` line names the same. No eligible seat refuses with the skip map. A
  retry keeps the recorded provider.
- **Output guard.** A seat whose ordinary `max_tokens` is below the submission's output need is
  skipped with the reason (`am4-vllm` declares 4,096 ordinary; `deliberate_max_tokens` does not
  count). The need is the caller's `max_tokens` (`output_need_rule = "caller"`); without one it is
  estimated from the packed source (`source_tokens` = source pack bytes // 4, since no exact
  tokenizer has run before the pick): `whole_file` min(16384, max(1024, round(1.5 x source) + 512))
  (`whole_file_estimate`), other kinds min(8192, max(1024, source + 512)) (`diff_estimate`). A small
  file therefore fits AM4; a large whole-file rewrite does not. (First cut, superseded the same day:
  the largest listed allowance, 16,384, which shut AM4 out of every default-budget submission.)
