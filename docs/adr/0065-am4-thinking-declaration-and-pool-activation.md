# ADR-0065: The AM4 thinking declaration and the intent to activate a qualified pool

- Status: **Accepted by Derek** for the scoped AM4 thinking declaration and protocol deployment. **Follow-through, 2026-10-13: deployed, and the qualified three-backend pool is live** (see below).
- Date: 2026-10-13
- Goal: G-ecosystem (G-bench27 context)
- Receipt: `br-20261013-190915-a5ea463f`

## Authority

Derek's latest instruction, verbatim: "if we just wasted an hour... shouldn't be actually patch in the thinking and pool activatio so we got something out of this other than burning $100". The parent session treats it as the fresh H-3 approval for the already-reviewed AM4 declaration and protocol change. It is fresh user authorization, not a decision under the earlier Codex delegation, which expired (ADR-0064's delegation ended with its campaign window). No qualification gate waiver and no delegation of production candidate verdicts is inferred.

## Decision

1. Apply the two reviewed patches (preserved in `docs/amux-delivery/operational-drafts/`) to the tracked host files:
   - `host/omen-linux/hearth-production/backends-linux.toml`: `am4-vllm` declares `context_tokens = 49152`, `context_bytes = 172032`, `deliberate_max_tokens = 24576` and `reasoning_effort = "xhigh"`. Ordinary `max_tokens` stays 4096, `parallel_slots` stays 1, and default thinking stays off.
   - `host/lab-configurations.toml`: only the three-dense `am4-vllm` row's `context_tokens` becomes 49152.
2. The protocol code that sends `reasoning_effort = "xhigh"` for explicit thinking on exactly `am4-vllm` / `am4-dense-27b` is already committed (`hearth/toolsurface/inference.py`). It refuses before the network if the declaration lacks `xhigh`.
3. No facade or GPU change. The observed engine already reports 49,152 context and the facade already admits 24,576 output tokens.

## What does not change

- Candidate acceptance stays with Derek in production; no local candidate is applied before a recorded verdict.
- Profile qualification thresholds are unchanged: pool membership requires two distinct accepted briefs per current backend, serving profile and procedure, recorded as verdicts and exported (ADR-0062).
- The currently exported table has zero current-v2 qualified entries for all three backends. No counts are fabricated, and a pool with no eligible provider is not activated: it would refuse every delivery. The deep route stays the singleton `omen-dense-27b` until qualification evidence exists.
- Automatic procedure choice must stay truthful to that evidence; AM4's new profile qualifies on its own record, not by borrowing another backend's.

## Deployment (parent-owned)

The parent performs a controlled reload of only the HEARTH door after a sole-writer and quiescence preflight, deploys the matching reviewed bytes with backups, and runs a small useful AM4 fact-record lap through the native Amux worker. No GPU or AM4 facade restart. The expected new AM4 serving-profile fingerprint is `e955dcd89f9e53632a46d32dfa83100368522d4fbfa75e2285534c5248004df1`.

## Consequences

AM4 explicit thinking turns become possible and attributable in the wire artifact. Whether they complete, and with how many reasoning tokens, is a live observation still to be made. Pool activation follows qualification, not this ADR.

## Follow-through

After deployment, Derek delegated the qualification verdicts for this slice to Codex (`docs/amux-delivery/evidence/qualification-delegation-20261013.json`; threshold, review boundary and cloud ban unchanged). Each of `omen-dense-27b`, `omen-dense-27b-b` and `am4-vllm` (profile `e955dcd8…`) now has two distinct accepted `one_call` briefs at its own current v2 profile, decided by Codex; no counts were borrowed. The matching route and procedure table were deployed with a door reload only (no GPU or facade restart), and the pool is live with `one_call` as its automatic procedure. `carry` is unqualified; capacity and held-out ability are unknown. Evidence: `docs/amux-delivery/evidence/`, receipt `br-20261013-205415-0fa54b22`. The sections above record the state and intent at the time of the decision.
