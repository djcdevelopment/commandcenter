# 0053 — The environment (dev | prod) is the lab's policy axis: an authored, expiring, ledgered setting that relaxes schedule and presence gates only

**Status:** Accepted and proven (2026-10-02, omen-linux; plan `~/work/devmode-plan/`, all six tasks done). The
proof lap ran today's three night briefs in the daytime through the real units under dev, then restored prod;
evidence `~/work/devmode-plan/evidence/`, findings in `docs/rnd-log.md` (2026-10-02 row).

**Companion to:** ADR-0006 (arming is an authored object), lab-rnd ADR 0005 (night queue governance) and 0007
(acceptance is recomputed from artifacts), D-112 (`approve` only for `human-operator`), `host/lab-configurations.toml`
(serving shape), `hearth/etc/profiles.toml` (authority).

## Context

On 2026-10-02 Derek ran three approved night briefs in the daytime. One lap tripped the 22:00–23:30 night window
(`lab-rnd/daily/night_work.py`), the presence gate (`fleet/presence_linux.py`, idle ≥ 20 min), head-of-line
blocking in the drain, and a citation-spelling bug in the local-work verifier. The first two were bypassed by hand
(a hand-written receipt; `PRESENCE_IDLE_MIN=0` on manual ticks). Five scans then counted ~95 hard-wired values across
lab-rnd, the fleet drain, the door and the host. Most protect something real; a few are schedule/presence policy
that R&D needs to relax; several are plain bugs.

## Decision

1. **A third axis.** Lab configuration says *what serves where*; caller profiles say *who may do what*; the
   environment says *which policy applies*. It is kept beside the other two, never merged into them.
2. **One flat file is the contract.** `~/.config/hearth/environment` (non-secret, 0644, `KEY=VALUE`) is rendered by
   `hearth-env set dev|prod --by WHO --reason WHY [--until WHEN]` from tracked `host/environments/<name>.env`, with an
   authored header (`HEARTH_ENVIRONMENT`, `_SET_BY`, `_SET_AT`, `_REASON`, `_UNTIL`) and one `hearth_environment.set`
   ledger row per switch. Readers: `fleet/environment.py` (flash) and a copy in lab-rnd `daily/environment.py`.
3. **Fail-closed to prod.** A missing or unreadable file, an unknown name, or a past `_UNTIL` read as prod; every
   consumer's code default is its prod value.
4. **Read the file, don't inherit it.** Python re-reads the file on every call; units do not load it with
   `EnvironmentFile=`, because a copied value would outrank the file and outlive `_UNTIL`. The process environment is
   for explicit one-off overrides only.
5. **Only policy gates are knobs** (v1): `NIGHT_WINDOW_ENFORCE`, `NIGHT_ALLOW_RERUN`, `PRESENCE_GATE`,
   `PRESENCE_IDLE_MIN`, `BANKEDFIRE_SLOTS`. Adding a knob = one line in both `.env` files plus its reader.
6. **Never knobs:** interactive-TTY `night approve`/`night effort`; the `approve` capability (D-112); arm state and
   scope (ADR-0006); operating-budget `suspended`; `pause.dispatch` and ai-mode; GPU tenancy and experiment fencing;
   admission/KV checks; `HEARTH_SCOPE` narrowing and the knowledge guard; `review_required`; `ct`'s `CLAUDECODE` refusal.
7. **Every record carries the environment** (window receipts, approvals, effort, campaign, tick ledger rows, runner
   state, morning report), and lab-rnd's `night audit` excludes dev-queued dispatches (`dev_excluded`), so dev evidence
   never satisfies a production criterion (lab-rnd ADR 0007).
8. **Bugs are fixed, not toggled.** Head-of-line blocking, string citations, the Windows-era ledger default and the
   fail-open deepagents fence are fixed in both environments (devmode tasks 4–5).

## Consequences

- R&D laps run in the daytime through the real units (`systemctl --user start bankedfire-drain.service`) with no
  hand-written receipts or env overrides, and the switch shows up in the ledger and in every receipt.
- `--until` makes "dev for this afternoon" self-ending.
- `tools/ops/host_config.py --check` runs `hearth-env check` (knobs vs source; the rendered header is not byte-compared).
- `PRESENCE_IDLE_MIN` and `BANKEDFIRE_SLOTS` move out of `hearth-ops.env` (task 3), or they would override the file.
