"""Write one capacity-observation.v1 per offload dispatch — ADR-0027 option B.

The belief projectors do not read events; they read `capacity_observation` artifacts
referenced from events via `artifact_refs` (`tools/workflow/project_capacity.py`
`extract_observations`). So "close the learning loop" means: measure the dispatch where it
happens, write the artifact, and hang a ref on an event in the same run directory.

WHAT COUNTS AS EVIDENCE is the authored decision here, and ADR-0002 governs it: a failure
caused by the plumbing says nothing about the model and must not enter the belief layer,
but it must be COUNTED rather than dropped silently. `_classify_outcome` below is that
table, and every exclusion is reported back on the result and appended to
`exclusions.ndjson` beside the artifacts.

Two mappings are easy to get wrong, and both would quietly corrupt the belief layer:

- `workflow_id` comes from the CALLER (the occasion), `builder_id` from the EXECUTOR (the
  machine). They must not both be caller identity: the association engine's gate 1 counts
  distinct `workflow_id` ("do not generalize from one occasion") and gate 2 requires
  variation in `builder_id` or `model_id` ("something must have varied"). Feed them the
  same field and two independent checks collapse into one, and a "capability" forms out of
  gate arithmetic rather than evidence. In the existing corpus `builder_id` is always a
  logical fleet worker (`omen-worker-1`, `cc-builder-2`, `omen-5070`) — never the
  requester — so the executor reading is also the consistent one.
- `task_kind` defaults to `offload-generate`, NOT `inference`. `inference` is already the
  kernel ledger's `task_class` for this tool; two fields in two bounded contexts sharing a
  string value invites a join that ADR-0010 says must not exist.

Never raises. A dispatch must not fail because evidence collection did — the house pattern
documented at `hearth/kernel/ledger.py` for `_record*`.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from hearth.errortax import CAPABILITY_ERROR_CODES, INFRA_ERROR_CODES, classify_error
from hearth.health.rungstate import summarize_for_notes
from hearth.observation.identity import DispatchIdentity, current_identity

_REPO_ROOT = Path(__file__).resolve().parents[2]

CONTRACT_VERSION = "capacity-observation.v1"
EVENT_TYPE = "work.accepted"
ARTIFACT_TYPE = "capacity_observation"
DEFAULT_TASK_KIND = "offload-generate"

# Bytes-per-token rule of thumb this repo already uses in backends.toml when sizing
# payloads against a rung's context budget. Arbitrary-at-birth, recorded per D18.
BYTES_PER_TOKEN = 4

# Last-resort executor identity when a rung declares no `settings.node` and the endpoint
# host is not one we know. Authored naming facts, not beliefs, so declaring them does not
# collide with "no organizational truth may be authored if it can be derived".
BUILDER_BY_HOST = {
    "127.0.0.1": "omen",
    "localhost": "omen",
    "::1": "omen",
}

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def runs_root() -> Path:
    """Where the domain corpus lives: $HEARTH_RUNS_ROOT if set, else <repo>/runs.

    Deliberately NOT `resolve_in_scope`. That reads the *caller's* narrowed file_scope, so
    a scoped caller's own observation write would be refused — and there is no
    caller-supplied path here to guard in the first place. This is lab infrastructure, the
    same reason the kernel ledger append sits outside the caller_scope block in
    gateway.py. Mirrors `hearth.kernel.ledger.hearth_root`'s env-override shape.
    """
    env = os.environ.get("HEARTH_RUNS_ROOT")
    return Path(env).resolve() if env else _REPO_ROOT / "runs"


def slug(raw: str) -> str:
    """Filesystem- and path-safe form of an identity.

    Load-bearing: the run id becomes a directory name, and `_resolve_artifact_path` locates
    an artifact by finding the FIRST "artifacts/" substring in the stored ref and
    re-anchoring the remainder — so an identity containing a slash could silently redirect
    resolution.
    """
    cleaned = _SLUG_RE.sub("-", (raw or "").strip().lower()).strip("-._")
    return cleaned or "unknown"


def builder_id_for(endpoint: str, backend: Optional[str], settings: Optional[dict]) -> str:
    """The machine that executed the dispatch. Never empty (schema minLength: 1)."""
    declared = (settings or {}).get("node")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    try:
        host = (urllib.parse.urlsplit(endpoint or "").hostname or "").lower()
    except ValueError:
        host = ""
    if host in BUILDER_BY_HOST:
        return BUILDER_BY_HOST[host]
    if host:
        return f"host:{host}"
    return f"backend:{backend}" if backend else "unknown-node"


def _classify_outcome(result: dict, resolved_max_tokens: Optional[int]) -> tuple[
        Optional[str], Optional[str], Optional[str], Optional[str]]:
    """(outcome, failure_class, note, excluded_reason).

    `outcome is None` means "do not emit"; `excluded_reason` says why, so the caller can
    count it. The table is the authored evidence semantics — see ADR-0027's amendment.
    """
    # Nothing was dispatched: quality="best" returns an ask, and a routing refusal never
    # reached a backend. Neither measured anything.
    if result.get("ask"):
        return None, None, None, "not-a-dispatch:ask"
    if result.get("error_code") == "routing_refusal" or result.get("routing_refusal"):
        return None, None, None, "not-a-dispatch:routing_refusal"

    if result.get("ok"):
        text = result.get("text") or ""
        if not text.strip():
            # A real, documented pathology: thinking models spending the whole output
            # budget on hidden reasoning and returning nothing. That IS about the rung.
            return "error", "empty_output", "ok=true but no visible text returned", None
        tokens_out = result.get("tokens_out")
        if (resolved_max_tokens and isinstance(tokens_out, int)
                and tokens_out >= resolved_max_tokens):
            # Truncation is the CALLER's budget choice, not a rung failure. Calling it one
            # would poison buckets with caller noise under the unanimous-outcome gate.
            return "success", None, (f"output hit the requested max_tokens "
                                     f"({resolved_max_tokens}); truncation is a budget "
                                     f"choice, not a rung limit"), None
        return "success", None, None, None

    code = result.get("error_code") or classify_error(result.get("error") or "")
    if code in CAPABILITY_ERROR_CODES:
        # The rung could not do this workload inside its own declared budget.
        return "timeout", "timeout", f"error_code={code}", None
    if code in INFRA_ERROR_CODES:
        return None, None, None, f"infra:{code}"
    # Unclassified: cannot be asserted to be capability signal. Counted, so it stays
    # visible and reclassifiable rather than silently becoming evidence.
    return None, None, None, f"unclassified:{code}"


def build_observation(*, result: dict, endpoint: str, backend: Optional[str],
                      model: Optional[str], settings: Optional[dict],
                      task: Optional[str], payload_bytes: Optional[int],
                      resolved_max_tokens: Optional[int],
                      identity: DispatchIdentity,
                      timestamp: Optional[str] = None,
                      observation_id: Optional[str] = None) -> Optional[dict]:
    """The capacity-observation.v1 document, or None when this is not evidence."""
    outcome, failure_class, note, _excluded = _classify_outcome(result, resolved_max_tokens)
    if outcome is None:
        return None

    duration_ms = result.get("duration_ms")
    tokens_out = result.get("tokens_out")
    tokens_in = result.get("tokens_in")
    runtime_s = round(duration_ms / 1000.0, 4) if isinstance(duration_ms, (int, float)) else None
    tokens_per_s = None
    if isinstance(tokens_out, (int, float)) and isinstance(duration_ms, (int, float)) \
            and duration_ms > 0:
        tokens_per_s = round(tokens_out / (duration_ms / 1000.0), 2)

    # runtime_s is not comparable across rungs without saying which clock produced it:
    # the ollama adapter prefers the server's own total_duration, the openai/gemini
    # adapters measure wall time around the POST.
    clock = "server-reported" if (result.get("api") == "ollama"
                                  or "/api/generate" in (endpoint or "")) else "wall-clock"
    notes = [f"routed_by={result.get('routed_by')}", f"occupancy={result.get('occupancy')}",
             f"runtime_s is {clock}"]
    if result.get("escalation"):
        notes.append(f"escalated from {result['escalation'].get('from')}: "
                     f"{result['escalation'].get('error')}")
    if result.get("files_packed"):
        notes.append(f"{len(result['files_packed'])} file(s) packed")
    # P8: the observation schema is closed, so the rung's health at dispatch time and
    # the pool declaration that routed the call ride `notes`. `summarize_for_notes`
    # emits no ';' (the joiner below) and is capped at 96 chars; a stamp that is not
    # a dict is not evidence of anything and is skipped rather than guessed at.
    rung_state = result.get("rung_state")
    if isinstance(rung_state, dict):
        try:
            notes.append(f"rung_state: {summarize_for_notes(rung_state)}")
        except Exception:  # noqa: BLE001 — a malformed stamp must not block evidence
            notes.append("rung_state: unsummarizable")
    pool_hash = result.get("pool_config_hash")
    if isinstance(pool_hash, str) and pool_hash:
        notes.append(f"pool={pool_hash}")
    if note:
        notes.append(note)

    stamp = timestamp or datetime.now(timezone.utc).isoformat()
    return {
        "contract_version": CONTRACT_VERSION,
        "observation_id": observation_id or _observation_id(identity, stamp),
        "decision_id": None,
        "workflow_id": f"wf-hearth-offload-{identity.caller_id}",
        "run_id": f"hearth-offload-{slug(identity.caller_id)}",
        "timestamp": stamp,
        "builder_id": builder_id_for(endpoint, backend, settings),
        "model_id": model,
        "backend": backend,
        "hardware_profile_id": (settings or {}).get("hardware_profile_id"),
        "workload_shape": {
            "task_kind": task or DEFAULT_TASK_KIND,
            "estimated_context_tokens": (payload_bytes // BYTES_PER_TOKEN
                                         if isinstance(payload_bytes, int) else None),
            "requires_gpu": None,
            "notes": "; ".join(notes),
        },
        "observed": {
            "runtime_s": runtime_s,
            "ttft_s": None,          # stream=False: never measured
            "tokens_per_s": tokens_per_s,
            "ram_gb_peak": None,     # no host telemetry at the door
            "vram_gb_peak": None,
            "context_tokens": tokens_in if isinstance(tokens_in, int) else None,
            "physical": None,        # model_residency is DERIVED; producers never set it
        },
        "outcome": outcome,
        "failure_class": failure_class,
        "promotion_status": None,    # nothing promotes a dispatch
    }


def _observation_id(identity: DispatchIdentity, stamp: str) -> str:
    compact = _SLUG_RE.sub("", stamp.replace("-", "").replace(":", "").replace(".", ""))
    return f"obs-offload-{slug(identity.caller_id)}-{compact}-{uuid.uuid4().hex[:8]}"


def _append_exclusion(run_dir: Path, record: dict) -> None:
    """Append-only trail of what was declined and why, so exclusions are counted rather
    than silent (ADR-0002's no-silent-caps clause). Not a workflow event, because it is
    not one — same shape as corpus_guard's policy_audit.ndjson."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "exclusions.ndjson").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def emit_dispatch_observation(*, result: dict, endpoint: str, backend: Optional[str],
                              model: Optional[str], settings: Optional[dict] = None,
                              task: Optional[str] = None,
                              payload_bytes: Optional[int] = None,
                              resolved_max_tokens: Optional[int] = None,
                              identity: Optional[DispatchIdentity] = None,
                              root: Optional[Path] = None) -> Optional[dict]:
    """Record one dispatch as capability evidence.

    Returns `{"emitted": True, "observation_id", "path", "event_id"}` on success,
    `{"emitted": False, "excluded": reason}` when the dispatch is deliberately not
    evidence, and None when no identity is in force (nothing is recorded and nothing is
    counted — an unidentified dispatch is not a measurement of anything).
    """
    from tools.workflow.append_event import append_event
    from tools.workflow.fsio import atomic_write_json

    identity = identity or current_identity()
    if identity is None:
        return None

    base = Path(root) if root is not None else runs_root()
    run_dir = base / f"hearth-offload-{slug(identity.caller_id)}"

    outcome, _fc, _note, excluded = _classify_outcome(result, resolved_max_tokens)
    if outcome is None:
        _append_exclusion(run_dir, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "caller_id": identity.caller_id, "backend": backend, "model": model,
            "error_code": result.get("error_code"), "excluded": excluded,
        })
        return {"emitted": False, "excluded": excluded}

    observation = build_observation(
        result=result, endpoint=endpoint, backend=backend, model=model, settings=settings,
        task=task, payload_bytes=payload_bytes, resolved_max_tokens=resolved_max_tokens,
        identity=identity)

    day = observation["timestamp"][:10]
    # Date-sharded: one flat directory would accumulate a file per dispatch forever.
    # `_resolve_artifact_path` keeps the whole tail after "artifacts/", so sharding needs
    # no change on the read side.
    relative = f"artifacts/{day}/{observation['observation_id']}.json"
    atomic_write_json(run_dir / relative, observation)

    event = {
        "event_id": f"evt_{observation['observation_id']}",
        "event_type": EVENT_TYPE,
        "timestamp": observation["timestamp"],
        "workflow_id": observation["workflow_id"],
        "run_id": observation["run_id"],
        "actor": {"type": _actor_type(identity.runner_class), "id": identity.caller_id},
        "status": "completed" if observation["outcome"] == "success" else "failed",
        "outcome": "success" if observation["outcome"] == "success" else "failure",
        "segment_id": identity.task_id,
        "payload": {
            "source": "hearth-offload-dispatch",
            "tool": "local_generate",
            "backend": backend,
            "model": model,
            "runner_class": identity.runner_class,
            "node": identity.node,
            "profile": identity.profile,
        },
        # artifact_id is REQUIRED per item by contracts/workflow-event.schema.json. The
        # hand-rolled validator ignores artifact_refs entirely and every pre-existing
        # corpus event omits it; do not inherit that defect.
        "artifact_refs": [{
            "artifact_id": observation["observation_id"],
            "artifact_type": ARTIFACT_TYPE,
            "path": f"{observation['run_id']}/{relative}",
        }],
    }
    append_event(run_dir / "events.jsonl", event)

    return {"emitted": True, "observation_id": observation["observation_id"],
            "path": str(run_dir / relative), "event_id": event["event_id"]}


_ACTOR_TYPE_BY_RUNNER_CLASS = {"frontier": "builder", "local": "builder", "human": "operator"}


def _actor_type(runner_class: Optional[str]) -> str:
    return _ACTOR_TYPE_BY_RUNNER_CLASS.get(runner_class or "", "system")


def record_dispatch(**kwargs) -> Optional[dict]:
    """`emit_dispatch_observation`, but swallowing every failure.

    The call site is the offload hot path. Evidence collection must never be able to break
    a dispatch, so the caller gets a diagnostic instead of an exception.
    """
    try:
        return emit_dispatch_observation(**kwargs)
    except Exception as exc:  # noqa: BLE001 — deliberate: never break the dispatch
        return {"emitted": False, "error": f"{type(exc).__name__}: {exc}"}
