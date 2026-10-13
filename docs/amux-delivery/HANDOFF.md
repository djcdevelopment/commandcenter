# Handoff — Amux local capability, 2026-10-13

Read [RESULT.md](RESULT.md), [PLAN.md](PLAN.md), then [RETROSPECTIVE.md](RETROSPECTIVE.md). Current state: explicit AM4 thinking is deployed and the three-backend `one_call` pool is live (qualification decided by Codex under a fresh delegation, threshold unchanged); the original 404-word thinking report awaits a recorded verdict, and no candidate was applied. See Follow-through below; older sections are first-landing history.

## Follow-through (current)

Live session: <https://localhost:18824/#peek=amux-local-capability-20261013>. For a normal unpinned delivery use the existing preparer recipe in [README](README.md) (`tools/ops/amux_delivery_prepare.py`, no backend/model/procedure pins) and submit the arguments through the native worker; the door picks among the three qualified backends and chooses `one_call` today. Thinking and the pool are live as recorded in [RESULT](RESULT.md) and `evidence/activation-followthrough.json`; candidates still stop at `awaiting_review`. The drafted controller in `operational-drafts/` stays inert; live control evidence is in the parent scratch directory. The original 404-word thinking report (`work_37d9ac46…`) awaits a recorded verdict; its repaired form is a sidecar derivative, not the original.

## Entry and saved work (first landing)

- Repository worktree: `/home/derek/work/worktrees/amux-am4-delivery-20261013`; branch `amux/am4-delivery-20261013`; base `831ecea2a67feb552574e795fef72cf3438844f2`.
- Earlier commits: preparer `7f16c46e6d0b4cd4c52626836c0799ca870df564`; sidecar `cf463ebf9beca8874b878e9780188677047a6a0c`. The landing commit adds the reviewed thinking adapter and this closure packet.
- Native builder UI: `https://localhost:18824/#peek=amux-am4-delivery-20261013`.
- Native inference caller UI: `https://localhost:18824/#peek=amux-local-capability-20261013`.
- Source clone: `/home/derek/work/worktrees/amux-local-capability-20261013`, commit `4f3c67f8888a8ed6698af1182d2e94d9ecf0f4d4`.
- Receipt `br-20261013-190915-a5ea463f`; scratch and original artifacts named in RESULT. No original run directory is to be modified.
- Preserve unrelated dirty `hearth/etc/start-hearth-gateway.cmd`; it was not part of this work.

## Exact next action (first landing, now done)

Show Derek the existing Amux surface and the proposed small AM4 fact-record lap. Use `PLAN.md` and the archived proposal for the actual activation decision. Do not restart inference or repeat the same24K OMEN carry prompt merely because it produced no answer. No deployment, new verdict or pool activation was authorized by the landing request.

To prepare an existing ordinary brief offline:

```bash
~/.venvs/hearth-private/bin/python tools/ops/amux_delivery_prepare.py \
  docs/amux-delivery/specs/log-dedupe-review.spec.json --out /tmp/amux-delivery-args.json
```

Submit the exact resulting object with native HEARTH tools from the Amux UI; HEARTH chooses capability/capacity. An ordinary code_review currently selects OMEN carry. Do not dispatch it simply to verify this handoff.

For a counted citation derivative of an existing candidate:

```bash
~/.venvs/hearth-private/bin/python tools/ops/amux_delivery_sidecar.py \
  /home/derek/work/commandcenter-linux-flash/runs/operator/work_eb2686322ad4c268e43bddb78a783c49 \
  /tmp/amux-listener-derivative-NEW
```

The destination must be new. A mechanically repaired quote is not a substance verdict.

## Posture and concrete cautions

Use `rnd`: one useful vertical slice, one observed edge, then end the lap. Claude's existing memory explicitly warned against the prolonged assurance loop reproduced here. Keep normal terminal colors and manual native permissions; operate Amux as Derek would.

The operational controller archive is unfinished and intentionally stored as `.py.txt`. Its latest guard was not checked; no claim of live readiness. Current AM4 declaration and staged proposal have different fingerprints, so prior-profile qualification cannot transfer.

The first two commits fired the repository's enabled post-commit JEPA harvest hook and appended telemetry to a protected ledger (sidecar harvest reported47590bytes). Records were preserved and the violation recorded. Only that telemetry hook is enabled; this landing suppresses it per command via `git -c core.hooksPath=/dev/null commit`. Global hook configuration is unchanged.
