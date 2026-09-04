"""Pre-flight gates for a rotation window (ADR-0045).

Four conditions must hold before a side model may be loaded beside production.
Three of them were already readable one tool call at a time; the fourth was only
ever a paragraph in a README, and on 2026-09-03 it cost two proof attempts:

  G0  the imagegen tenancy fence is clear      (rotation refuses under the fence)
  G1  the sibling entries the window needs are declared by the RUNNING llama-swap
  G2  production is ``at_rate`` on its baseline epoch (ADR-0044)
  G3  the door is not older than the code it mounts

G3 is L-2026-09-03-1 turned into a gate. A running llama-swap keeps the entries
it booted with and the gateway runs the code it was started with, so both G1 and
G3 are really the same question asked of two processes: *did the thing restart
after the change landed?* Neither is answerable from the change itself.

Every gate fails CLOSED and carries its own remedy. Unreadable inputs are a
NO-GO, never an assumed pass: the whole point of the 2026-09-03 receipts is that
a port probe and a health check both passed against a rung that could not serve.

Run it:

    python -m hearth.rotation.preflight
    python -m hearth.rotation.preflight --models phi4-vk0 qwen14b-vk1

Exit code 0 = GO, 1 = NO-GO, so it composes into a ceremony script.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

REPO = Path(__file__).resolve().parents[2]
GATEWAY_LOG = REPO / "hearth" / "var" / "gateway-task.log"

#: Packages the gateway mounts. A commit touching any of these is not live until
#: the door restarts -- see ``docs/adr#0045`` and the 2026-09-03 handoff.
DOOR_PATHS: tuple[str, ...] = ("hearth/rotation", "hearth/health", "hearth/toolsurface")

#: The two-card proof's seats: phi-4 on env 0, qwen14b on env 1 (ADR-0042 -- the
#: ids encode the env index, never a trusted Vulkan index).
DEFAULT_MODELS: tuple[str, ...] = ("phi4-vk0", "qwen14b-vk1")

RESTART_DOOR = r"schtasks /Run /TN HearthGatewayRestart   (PowerShell, not Git Bash)"

# "2026-09-03 17:50:12,345 mcp.server... StreamableHTTP session manager started"
_GATEWAY_START_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3}.*StreamableHTTP session manager started"
)


def _gate(gate_id: str, name: str, ok: bool, detail: str, remedy: Optional[str] = None) -> dict:
    return {"id": gate_id, "name": name, "ok": bool(ok), "detail": detail,
            "remedy": None if ok else remedy}


# --------------------------------------------------------------------------- gates (pure)
def gate_fence(fence, activity: Optional[dict] = None) -> dict:
    """G0. ``fence`` is default_fence()'s answer: None (free), a session id, or "unreadable".

    ``activity`` (optional, from :func:`pool_activity`) answers the question the
    bare fence cannot: the holder is holding, but is it *working*? "Held" and
    "busy" are different facts, and on 2026-09-04 conflating them cost a round
    trip -- the lease was renewing every 30 s while the cards had already gone
    idle and cooled 16 C. Held-and-idle has a remedy; held-and-busy has a wait.
    """
    if fence == "unreadable":
        return _gate("G0", "tenancy fence", False,
                     "cannot read the omen-b70-pool tenancy store",
                     "fix the store before acting; an unreadable fence is not a free one")
    if fence:
        detail = f"held by image session {fence}"
        remedy = "wait for the imagegen lane to release; production is stopped under the fence"
        if isinstance(activity, dict) and activity.get("available"):
            queued, running = activity.get("queued"), activity.get("running")
            age = activity.get("lease_age_s")
            detail += f" -- queued {queued}, running {running}"
            if age is not None:
                detail += f", lease renewed {age:.0f}s ago"
            if queued == 0 and running == 0:
                remedy = ("holder is IDLE but has not released: stop_image_session(force=False) "
                          "drains and restores a warm ArcServe -- do NOT kill its processes")
            else:
                remedy = (f"holder is BUSY ({running} running, {queued} queued): let it drain, or "
                          "stop_image_session(force=False) to drain in-flight work then restore ArcServe")
        return _gate("G0", "tenancy fence", False, detail, remedy)
    return _gate("G0", "tenancy fence", True, "omen-b70-pool free")


def pool_activity() -> dict:
    """Is the pool's holder actually working? Read-only; never raises.

    A tenancy lease is a heartbeat, not a measurement -- it says someone claims
    the pool, not that anyone is using it. Queue depth plus lease age is what
    separates "wait" from "take it back".
    """
    out: dict = {"available": False}
    try:
        from hearth.toolsurface.image_generate import get_image_session
        status = get_image_session()
    except Exception as exc:  # noqa: BLE001 - advisory only, never blocks a gate
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    if not isinstance(status, dict):
        return out
    session = status.get("session") or {}
    out.update({
        "available": True,
        "queued": status.get("queued"),
        "running": status.get("running"),
        "session_id": session.get("session_id"),
        "epoch": session.get("epoch"),
    })
    updated, expires = session.get("updated_at"), session.get("expires_at")
    now = datetime.now(timezone.utc).timestamp()
    if isinstance(updated, (int, float)):
        out["lease_age_s"] = now - updated
    if isinstance(expires, (int, float)):
        out["lease_ttl_s"] = expires - now
    return out


def gate_siblings(model_ids: Optional[Iterable[str]], required: Sequence[str] = DEFAULT_MODELS) -> dict:
    """G1. The RUNNING llama-swap must declare every entry the window will load.

    ``model_ids`` None means llama-swap was unreachable. A yaml edit activates only
    at the next ArcServe restart, so this asks the process, never the file.
    """
    if model_ids is None:
        return _gate("G1", "sibling entries", False, "llama-swap unreachable on :8081",
                     "start/restart ArcServe; until then no entry list can be trusted")
    declared = set(model_ids)
    missing = [m for m in required if m not in declared]
    if missing:
        return _gate("G1", "sibling entries", False,
                     f"declared {len(declared)} entries; missing {', '.join(missing)}",
                     "the running llama-swap predates the yaml rename -- restart ArcServe "
                     "to activate the -vk0/-vk1 entries (a pin gets a fast 404 until then)")
    return _gate("G1", "sibling entries", True, f"{', '.join(required)} declared")


def gate_production(state: Optional[dict]) -> dict:
    """G2. Production must be at rate on its own baseline epoch before we crowd it."""
    if not isinstance(state, dict):
        return _gate("G2", "production health", False, "no rung state available",
                     "read hearth/var/arc-keepalive.jsonl directly")
    verdict = str(state.get("verdict", "unknown"))
    observed, baseline = state.get("observed_tok_s"), state.get("baseline_tok_s")
    frac = state.get("frac_of_baseline")
    rate = (f"{observed}/{baseline} tok/s"
            + (f" ({frac:.0%} of epoch)" if isinstance(frac, (int, float)) else "")
            ) if observed is not None and baseline is not None else "no sample"
    if verdict != "at_rate":
        return _gate("G2", "production health", False, f"verdict {verdict}: {rate}",
                     "do not load beside a rung that is not at rate; a dip during a window "
                     "cannot be attributed afterwards (L-2026-09-03-11)")
    if not state.get("last_ping_ok", False):
        return _gate("G2", "production health", False, f"at_rate but last ping failed: {rate}",
                     "the verdict is from the sample tail; the rung is not answering now")
    return _gate("G2", "production health", True, f"at_rate {rate}")


def gate_door_fresh(gateway_started_at: Optional[datetime],
                    newest_commit: Optional[tuple[str, datetime]]) -> dict:
    """G3. The gateway must have started AFTER the newest commit to code it mounts.

    Unknowable either way -> NO-GO. "I could not tell" and "it is fine" are the
    same string only to an optimist; the 2026-09-03 attempts were lost to exactly
    that reading.
    """
    if gateway_started_at is None:
        return _gate("G3", "door freshness", False, "cannot read the gateway's start time",
                     f"restart it anyway, then re-run: {RESTART_DOOR}")
    if newest_commit is None:
        return _gate("G3", "door freshness", False, "cannot read the newest provider commit",
                     "check git; without it the door's age is unknowable")
    sha, committed_at = newest_commit
    started = gateway_started_at.astimezone(timezone.utc)
    landed = committed_at.astimezone(timezone.utc)
    if started < landed:
        return _gate("G3", "door freshness", False,
                     f"gateway started {started:%Y-%m-%d %H:%M}Z, but {sha} landed "
                     f"{landed:%Y-%m-%d %H:%M}Z in {', '.join(DOOR_PATHS)}",
                     f"the door runs the code it was started with: {RESTART_DOOR}, then doorcheck")
    return _gate("G3", "door freshness", True,
                 f"gateway started {started:%Y-%m-%d %H:%M}Z, newest provider commit {sha} "
                 f"{landed:%Y-%m-%d %H:%M}Z")


def preflight(gates: Sequence[dict]) -> dict:
    """Aggregate. GO only when every gate passed."""
    gates = list(gates)
    return {"go": all(g.get("ok") for g in gates) and bool(gates), "gates": gates,
            "ts": datetime.now(timezone.utc).isoformat()}


# --------------------------------------------------------------------------- live readers
def read_gateway_start(path: Path = GATEWAY_LOG, tail_bytes: int = 4 * 1024 * 1024) -> Optional[datetime]:
    """The last recorded gateway start, from its own log tail. Local time, tz-aware.

    The log carries occasional binary noise, so it is decoded with replacement
    rather than trusted to be clean utf-8.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            raw = fh.read()
    except OSError:
        return None
    stamp: Optional[datetime] = None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        match = _GATEWAY_START_RE.match(line)
        if match:
            try:
                stamp = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
    if stamp is None:
        return None
    return stamp.astimezone()


def newest_provider_commit(repo: Path = REPO,
                           paths: Sequence[str] = DOOR_PATHS) -> Optional[tuple[str, datetime]]:
    """(short sha, commit datetime) of the newest commit touching door-mounted code."""
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%h %cI", "--"] + list(paths),
            cwd=str(repo), capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        return None
    sha, _, iso = out.partition(" ")
    try:
        return sha, datetime.fromisoformat(iso.strip())
    except ValueError:
        return None


def live_preflight(models: Sequence[str] = DEFAULT_MODELS, endpoint: Optional[str] = None) -> dict:
    """Read all four gates off the live machine. Never raises."""
    from hearth.rotation.lifecycle import default_fence
    from hearth.rotation.swapclient import DEFAULT_ENDPOINT, LlamaSwapClient

    try:
        fence = default_fence()
    except Exception:  # noqa: BLE001 - an unreadable fence is a NO-GO, not a crash
        fence = "unreadable"

    model_ids: Optional[list[str]] = None
    try:
        client = LlamaSwapClient(endpoint=endpoint or DEFAULT_ENDPOINT)
        if client.health():
            model_ids = sorted(client.models())
    except Exception:  # noqa: BLE001
        model_ids = None

    try:
        from hearth.health.rungstate import live_rung_state
        state = live_rung_state()
    except Exception:  # noqa: BLE001
        state = None

    return preflight([
        gate_fence(fence, pool_activity() if fence not in (None, "unreadable") else None),
        gate_siblings(model_ids, models),
        gate_production(state),
        gate_door_fresh(read_gateway_start(), newest_provider_commit()),
    ])


# --------------------------------------------------------------------------- cli
def format_report(result: dict) -> str:
    lines = []
    for gate in result.get("gates", []):
        mark = "GO   " if gate["ok"] else "NO-GO"
        lines.append(f"  {mark} {gate['id']} {gate['name']}: {gate['detail']}")
        if gate.get("remedy"):
            lines.append(f"        -> {gate['remedy']}")
    verdict = "GO" if result.get("go") else "NO-GO"
    lines.append(f"  == {verdict}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Rotation window pre-flight gates (ADR-0045).")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS),
                        help="entry ids the window will load (default: the two-card proof's seats)")
    parser.add_argument("--endpoint", default=None, help="llama-swap endpoint (default: loopback :8081)")
    parser.add_argument("--json", action="store_true", help="emit the raw result")
    args = parser.parse_args(argv)

    result = live_preflight(args.models, args.endpoint)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("rotation pre-flight")
        print(format_report(result))
    return 0 if result["go"] else 1


if __name__ == "__main__":
    sys.exit(main())
