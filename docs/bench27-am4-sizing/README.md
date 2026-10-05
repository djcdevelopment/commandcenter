# Frozen AM4 sizing comparison — prepared, requires Opus review

The existing `bench27_am4_probe.py` CLI cannot run this comparison unchanged: it adds three needle probes and grades the final using `needle_truth`, which the frozen sizing workload does not contain. `seat_probe.py` supports work-only conversations, estimated admission, and no thermal watchdog. The new standalone `tools/ops/bench27_am4_sizing.py` composes the reviewed AM4 Client streaming/tokenization/guard implementation; it changes no pinned B70 files and performs no deployment.

The frozen file is `/home/derek/work/lab-rnd/research/evidence/qwen38-levers-20261003/sizing-workload.json`, SHA256 `0f65fd1977ded2217bd1f35fd918447c1c983b93f2e82c9b3f9aabbce2fb750d`. Work messages, thinking enabled, temperature0, seed42, top_p0.95 and max_tokens24000 are retained. Work must tokenize to exactly24521 on the observed AM4 model or refuse. Final uses the unchanged4096-token template/schema and original messages + work content + frozen final instruction (no reasoning injection). Every turn independently tokenizes and checks actual observed window. Only the served model alias changes to `qwen3-27b`; SSE/usage streaming is a transport change. No prompt tagging or budget reduction occurs for this single conversation.

Minimum first-turn admission is48521 tokens.32K is insufficient.49152 can admit that first turn, but final admission still depends on actual work content plus4096 and must be measured. Admission is not proof that the KV pool or reasoning budget works. `[DONE]` and stop finish are mandatory; length never passes. Successful transport ends at `awaiting_review`, not substance accepted. The existing delivery renderer/frontier must separately assess final schema, quote support, form and substance from saved artifacts. No capability registry or door budget is changed by this script.

Coordinator prerequisites: reviewed helper/wrapper; healthy intended AM4 recipe with observed window sufficient; continuous AM4 guard; exclusive AM4 campaign access; an already established local-only tunnel to remote127.0.0.1:18094. The driver does not open tunnels, authenticate the facade, restart engines or cancel door jobs. Parent must prevent new competing AM4 dispatch during the lap. Read-only lease/counter checks are evidence, not an atomic exclusion lock: they cannot prove the absence of a failed or cancelled foreign request invisible to success counters. Any known outside use voids attribution. Do not infer dedicated capacity from this alone.

Reviewable invocation (substitute a new evidence output directory and the existing tunnel's local port; do not invoke before review):

```sh
/home/derek/.venvs/hearth-private/bin/python /home/derek/work/worktrees/bench27-am4-sizing/tools/ops/bench27_am4_sizing.py \
  --helper /home/derek/work/commandcenter-linux-flash/tools/ops/bench27_am4_probe.py \
  --workload /home/derek/work/lab-rnd/research/evidence/qwen38-levers-20261003/sizing-workload.json \
  --out /home/derek/work/lab-rnd/research/evidence/bench27-am4-sizing-20261005/RECIPE-UNUSED \
  --port LOCAL_TUNNEL_PORT \
  --guard-log /home/derek/work/lab-rnd/research/evidence/bench27-config-20261005/am4.jsonl \
  --trip /home/derek/work/lab-rnd/research/evidence/bench27-config-20261005/am4.jsonl.tripped \
  --coordination-db /home/derek/hearth-production/var/execution/coordination.sqlite \
  --timeout 1800
```

The1800s bound covers the entire conversation, not each turn (maximum2400s). Review expected time before launch. Missing/stale/unsafe guard or trip/SIGTERM/deadline closes this process's own socket. Active AM4 leases refuse before completion; engine running/waiting must be zero before/between/after, and final success-counter delta must equal owned attempted sends. This permits nonzero historical success totals; no counters are reset. Infra failures retain raw per-turn evidence even when no final counter can be obtained. Output paths cannot be reused. No two-concurrent claim is made; that would need its own admissible independent-prefix workload and review.

Offline validation: six localhost fake-HTTP tests cover32K refusal without sends, exact-token mismatch, preservation of prompt/output allowance at49152, active-lease refusal, missing-guard interruption of blocked streaming, and missing-counter refusal. No inference, services or sealed briefs were accessed.
