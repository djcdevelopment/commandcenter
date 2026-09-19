# FX99 Hermes implementation — 2026-09-19

Useful artifacts: a pinned SSH/tmux Hermes controller using shared AM4 inference;
a tested, unmerged fleet-capacity correction produced through governed local work.

Implementation began 16:51:35 UTC. CPU-only preparation checkpoint: 17:21:35 UTC.
Ask if preparation is not ready then; do not turn preparation into an unbounded
experiment. The separately approved live qualification is capped at 90 minutes:
first useful source note by +20, code candidate by +65, final 25 minutes reserved
for source review, accounting, and restoration. Latest useful live start is set
only after CPU integration passes and the fleet is available.

Working branch: agent/hermes-fx99-20260919. Upstream baseline:
f672a13f7fc9259ce540a4613be927970a12a06a. Selected dirty prerequisites are copied
byte-for-byte from the user's checkout and recorded in the baseline commit;
they are not claimed as new Hermes work. Original source remains untouched.
Receipt: br-20260919-165548-b1531f28.

Invariants: no FX99 GPU/model displacement; OMEN's eight production slots stay;
no cloud fallback; AM4 primary/compression/builders share one physical serving
slot, not three; no automatic candidate merge/push/deploy; no credential-bearing
files exposed to model tools; no knowledge projection rebuilds.

Status: implementation in progress, no live qualification yet. Gates and partial
delivery must be reported honestly. Windows Hermes installation is preserved.
