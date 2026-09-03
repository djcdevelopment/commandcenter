"""HEARTH tool: query_rung_state — the omen-arc rung's health as ADR-0044 defines it.

A passive read of two files (the FF rate baselines and the keep-alive tail) —
it never touches the rung, never sends a probe, and never raises. A 1-token
keep-alive ping proves liveness only; ``at_rate`` needs a DEEP row (the
32-token probe) inside the staleness window, so a rung nobody has measured
reads ``stale``, never healthy. The envelope is a fraction of THIS baseline
epoch, not of capacity, and no regime is named (ADR-0044).

Verdicts: ``at_rate | warn | degraded | stalled | stale | unreachable |
no_baseline | unknown``. The same verdict rides the mechnet watchdog's
``rung_state`` block and the patrol's ``rung_*`` gaps, so an operator sees one
answer whichever door they knock on.

Kernel-free by the provider contract: this module reads the health module
only and holds no ledger, no gateway, no scheduler.
"""
from __future__ import annotations

from typing import Callable

from hearth.health.rungstate import live_rung_state, summarize_for_notes

VERDICTS = ("at_rate", "warn", "degraded", "stalled", "stale", "unreachable",
            "no_baseline", "unknown")


def query_rung_state(rung: str = "omen-arc") -> dict:
    """Read the rung's rate-health verdict from the keep-alive tail and the FF baselines.

    Passive: reads ``campaign/ff-probes/rate-baselines.json`` and the tail of
    ``hearth/var/arc-keepalive.jsonl``; sends nothing to the rung. Returns
    ``{ok, rung, port, verdict, baseline_tok_s, baseline_epoch, envelope,
    observed_tok_s, observed_at, observed_age_s, frac_of_baseline,
    prefill_stall_recent, last_ping_ok, deep_samples, excluded_windows, note,
    summary}``. ``ok`` is whether the READ succeeded (the reader returned a
    verdict), not whether the rung is healthy — ``verdict`` says that:
    ``at_rate``/``warn`` inside the epoch's envelope, ``degraded`` below its
    fail line, ``stalled`` on a prefill stall, ``stale`` when no deep sample is
    recent (pings alone never earn ``at_rate``), ``unreachable`` when the last
    ping failed, ``no_baseline`` when the rung has no epoch on record.
    ``summary`` is a <=96-char one-liner safe for a notes column. The envelope
    is of this baseline epoch, not of capacity, and the restart discriminator
    is not applied (ADR-0044).
    """
    try:
        state = live_rung_state(rung)
    except Exception as exc:  # noqa: BLE001 - the reader never raises; belt and braces
        state = {"rung": rung, "port": None, "verdict": "unknown",
                 "error": f"{type(exc).__name__}: {exc}"}
    out = dict(state)
    out["ok"] = "error" not in state and out.get("verdict") in VERDICTS
    out["summary"] = summarize_for_notes(out)
    return out


def get_tools() -> "list[Callable]":
    return [query_rung_state]
