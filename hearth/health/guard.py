"""Production-rung guard: a port that answers is not a rung that serves.

Ported verbatim in behaviour from ``E:\\omen\\tensor\\experiments\\model_surface\\guard.py``
(2026-09-09) so campaign harnesses in this repo stop cross-importing another tree. The
rules, the verdict sets and the staleness discipline are unchanged; the only differences
are mechanical:

- ``live_rung_state`` is imported from :mod:`hearth.health.rungstate` in this package
  instead of through a ``sys.path`` insert at ``C:\\work\\commandcenter``;
- ``health()`` (the plain unauthenticated ``/health`` probe the original borrowed from
  ``experiments.model_surface.reuse``) lives here;
- every reader is injectable, so the tests exercise the rules without a live rung.

Why it exists, kept from the original because the measurement is the argument:

    Every probe in that campaign gated on HTTP 200 from the production llama-server's
    ``/health``. That gate is blind to starvation. Measured on this machine on
    2026-09-08: a co-resident 30B canary held ``:8082`` returning 200 while production
    decode collapsed from 108.09 to 10.91 tok/s -- 10.3% of its 106.0 tok/s baseline.
    Health stayed green for the entire eleven minutes.

    The verdict comes from commandcenter's passive reader (``hearth.health.rungstate``),
    which reads the keep-alive tail and the recorded rate baselines. It never probes,
    needs no auth, and costs the rung nothing.

    Staleness is load-bearing. That reader is only as fresh as the keep-alive cadence
    (~5 minutes), so a short worker can begin and end inside one sampling interval. A
    sample taken before the worker started cannot say anything about the worker, and
    this module refuses to let one count as a pass -- on 2026-09-08 a stale ``degraded``
    row persisted for four minutes after its cause was already dead. The reader lags
    reality in both directions.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

#: Verdicts that stop a run. ``warn`` is recorded but tolerated; the unknowable
#: verdicts (``stale``, ``no_baseline``, ``unknown``) are reported, never passed.
STOP_VERDICTS = frozenset({"degraded", "stalled", "unreachable"})
SERVING_VERDICTS = frozenset({"at_rate", "warn"})

DEFAULT_HEALTH_URL = "http://127.0.0.1:8082/health"


class ProductionDegraded(RuntimeError):
    """The production rung stopped serving at its baseline rate."""


def health(url: str = DEFAULT_HEALTH_URL, timeout_s: float = 5.0) -> dict:
    """Unauthenticated liveness probe. ``/health`` is the only route that needs no bearer.

    Liveness ONLY. It answered 200 throughout the 2026-09-08 collapse, which is exactly
    why :func:`enforce` also requires a non-stop rung verdict.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return {"http_status": response.status, "body": response.read().decode("utf-8", "replace")}
    except (OSError, urllib.error.URLError) as exc:
        return {"error": str(exc)}


def _default_state_reader() -> dict:
    from hearth.health.rungstate import live_rung_state

    return live_rung_state()


def sample(since: float | None = None, *, state_reader=None, health_reader=None,
           clock=time.time) -> dict:
    """One guard reading. Never raises: an unreadable rung is a recorded fact.

    ``since`` is a ``time.time()`` epoch. When given, the sample is only ``fresh`` if the
    rung reading was taken after it. Freshness is derived from the reader's own
    ``observed_age_s`` rather than by parsing ``observed_at``, so this does not depend on
    timestamp formatting.
    """
    state_reader = state_reader or _default_state_reader
    health_reader = health_reader or health
    record: dict = {"sampled_at": clock(), "health": health_reader()}
    try:
        state = state_reader()
    except Exception as exc:  # noqa: BLE001 - an unreadable guard is not a silent pass
        record.update(verdict="unreadable", error=str(exc), fresh=False)
        return record
    record["rung"] = state
    record["verdict"] = state.get("verdict", "unknown")
    age = state.get("observed_age_s")
    if since is None:
        record["fresh"] = True
    elif isinstance(age, (int, float)) and not isinstance(age, bool):
        record["observed_epoch"] = record["sampled_at"] - float(age)
        record["fresh"] = record["observed_epoch"] >= since
    else:
        record["fresh"] = False
    return record


def serving(record: dict) -> bool:
    """True only for a fresh reading that says the rung is actually serving."""
    return bool(record.get("fresh")) and record.get("verdict") in SERVING_VERDICTS


def enforce(record: dict, phase: str) -> dict:
    """Raise on a stop condition. Staleness is reported by the caller, not raised.

    A stale or unreadable sample is not evidence of damage, so it does not abort a run
    that is otherwise complete -- but :func:`serving` will refuse to call it a pass, and
    the summary carries the reason.
    """
    verdict = record.get("verdict")
    if verdict in STOP_VERDICTS:
        rung = record.get("rung") or {}
        raise ProductionDegraded(
            f"production rung {verdict} at {phase}: "
            f"{rung.get('observed_tok_s')} tok/s = "
            f"{rung.get('frac_of_baseline')} of the {rung.get('baseline_tok_s')} baseline"
        )
    if (record.get("health") or {}).get("http_status") != 200:
        raise ProductionDegraded(f"production health unavailable at {phase}: {record.get('health')}")
    return record


def gate(phase: str, since: float | None = None, **readers) -> dict:
    """Sample and enforce in one call. Returns the record for the receipt."""
    return enforce(sample(since=since, **readers), phase)


def wait_for_fresh(since: float, phase: str, timeout_s: float = 420.0, poll_s: float = 15.0,
                   *, sleep=time.sleep, clock=time.time, **readers) -> dict:
    """Block until the reader produces a sample taken after ``since``, or give up.

    A GPU probe here runs for well under a second, which is far inside the keep-alive
    cadence -- so an immediate post-run reading is always the pre-run one wearing a new
    timestamp, and says nothing about what the probe did. This waits for a genuinely
    independent sample so "production was unaffected" is an observation rather than an
    inference. A timeout is recorded as ``fresh: False``, never silently upgraded.
    """
    deadline = clock() + timeout_s
    record = sample(since=since, clock=clock, **readers)
    while not record.get("fresh") and clock() < deadline:
        sleep(poll_s)
        record = sample(since=since, clock=clock, **readers)
    record["waited_s"] = round(timeout_s - max(deadline - clock(), 0.0), 1)
    return enforce(record, phase)
