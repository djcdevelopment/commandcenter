"""The BF6 sidecar bridge: AM4's render requests, carried to HEARTH's door.

WHERE THIS SITS
---------------
There are now two distinct file boundaries, and confusing them would be a
mistake:

    AM4  <-> OMEN      this module. Sidecars in the MEDIA root (``work/``),
                       crossing the BF6Work$ SMB share.
    gateway <-> agent  hearth.media.handoff. Sidecars in ``hearth/var/render``,
                       entirely local to OMEN.

This bridge is a CALLER of HEARTH, not part of it. It holds the least-privilege
``bf6-dispatcher`` credential, which grants ``media_render`` and nothing else --
it cannot spend a metered token, cancel a job, or read the filesystem through
the door. It never touches the execution ledger.

WHY SIDECARS AND NOT A PORT
---------------------------
The AM4 worker container is deliberately air-gapped (``HF_HUB_OFFLINE=1``, no
outbound HTTP anywhere in its code) and OMEN's inbound surface is deliberately
narrow. Adding a listener would mean a second thing to authenticate, authorise
and keep alive. The ``.mkv.ready`` handover already established the idiom, and
the share is already there.

Note the asymmetry that makes this cheap: **only AM4 crosses SMB.** OMEN owns
``E:`` and reads ``work/`` natively, so a share outage stops AM4 seeing results,
not the bridge doing its job.

CONVERGENCE, NOT MEMORY
-----------------------
A terminal job and a published result are separate events, and a crash can land
between them. So the loop is written to converge from whatever is on disk:

    result present            -> publication complete, nothing to do
    claim present             -> QUERY that job; never blindly resubmit
    no claim                  -> submit (the idempotency key makes even a
                                 duplicate submit return the SAME job)
    terminal but no result    -> reconstruct the result sidecar

Two independent mechanisms therefore prevent a duplicate render: the claim
sidecar, and the deterministic idempotency key derived from
(session, clip, revision, profile, variants).

The bridge NEVER deletes AM4's request sidecar. AM4 owns its own files and
retires them when its reconciler has consumed the result.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from hearth.toolsurface._media_scope import media_root

SCHEMA_VERSION = 1

REQUEST_SUFFIX = ".render-request.json"
CLAIM_SUFFIX = ".render-claim.json"
RESULT_SUFFIX = ".render-result.json"

POLL_SECONDS = 15.0

# A .tmp older than this was orphaned by a writer that died mid-write. AM4
# writes atomically, so a surviving .tmp is never a file we should read.
STALE_TMP_S = 300.0

TERMINAL = {"succeeded", "failed", "cancelled", "rejected", "expired"}

# Exactly the arguments submit_render accepts. AM4 writes what the tool takes,
# so nothing has to be translated in flight.
PASSTHROUGH_KEYS = (
    "session_id", "clip_id", "clip_revision", "source_segments",
    "start_seconds", "end_seconds", "variants", "profile_version",
    "captions", "captions_path",
)


@dataclass
class BridgeTick:
    """What one pass over the share did. Returned so a caller can log it."""

    submitted: list
    reconciled: list
    waiting: list
    skipped: list
    errors: list

    def to_dict(self) -> dict:
        return {
            "submitted": list(self.submitted),
            "reconciled": list(self.reconciled),
            "waiting": list(self.waiting),
            "skipped": list(self.skipped),
            "errors": list(self.errors),
        }

    @property
    def did_work(self) -> bool:
        return bool(self.submitted or self.reconciled)


# The bridge authenticates as `bf6-dispatcher`, whose profile grants
# `media_render` and nothing else -- it cannot spend a metered token, cancel a
# job, or reach the filesystem through the door.
KEY_ENV = "HEARTH_BF6_KEY"
KEY_FILE_ENV = "HEARTH_BF6_KEY_FILE"
DEFAULT_KEY_FILE = Path(__file__).resolve().parents[1] / "var" / "bf6-dispatcher.key"


def bridge_key() -> str:
    """The caller secret, from the environment or a gitignored file.

    Never inlined, never committed: hearth/var is gitignored and callerctl was
    given --secret-file precisely so the value is not printed at mint time.
    """
    configured = os.environ.get(KEY_ENV)
    if configured:
        return configured.strip()
    path = Path(os.environ.get(KEY_FILE_ENV) or DEFAULT_KEY_FILE)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            "no bf6-dispatcher credential: set %s, or %s to a key file, or mint "
            "one with `callerctl mint --id bf6-dispatcher --profile bf6-render "
            "--secret-file %s`" % (KEY_ENV, KEY_FILE_ENV, DEFAULT_KEY_FILE)
        ) from exc


def work_root(root: Optional[Path] = None) -> Path:
    return (Path(root) if root is not None else media_root()) / "work"


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_atomic(target: Path, payload: dict) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def list_requests(root: Optional[Path] = None) -> list:
    """Every render request on the share, oldest first.

    Only final names are returned. A ``.tmp`` is a write in progress -- reading
    one would mean acting on a half-written request.
    """
    base = work_root(root)
    try:
        found = [p for p in base.glob("*/*" + REQUEST_SUFFIX) if p.suffix == ".json"]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.name)


def clip_id_for(request_path: Path) -> str:
    return request_path.name[: -len(REQUEST_SUFFIX)]


def sidecar(request_path: Path, suffix: str) -> Path:
    return request_path.with_name(clip_id_for(request_path) + suffix)


def collect_stale_tmp(root: Optional[Path] = None, older_than: float = STALE_TMP_S) -> list:
    """Remove orphaned ``.tmp`` files. Never touches a final sidecar."""
    removed = []
    now = time.time()
    try:
        candidates = list(work_root(root).glob("*/*.json.tmp"))
    except OSError:
        return removed
    for path in candidates:
        try:
            if now - path.stat().st_mtime < older_than:
                continue
            path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    return removed


def arguments_from(request: dict) -> dict:
    """The submit_render call this request describes."""
    return {key: request[key] for key in PASSTHROUGH_KEYS if key in request}


def build_result(clip_id: str, job_id: str, status: dict, receipt: Optional[dict]) -> dict:
    """The terminal record AM4's reconciler consumes."""
    variants = []
    for entry in (receipt or {}).get("variants", []) or []:
        measured = (entry.get("validation") or {}).get("measured") or {}
        variants.append({
            "variant": entry.get("variant"),
            "promoted": entry.get("promoted"),
            "reason": entry.get("reason"),
            "output": entry.get("output"),
            "bitrate_mbps": measured.get("bitrate_mbps"),
            "size_bytes": measured.get("size_bytes"),
            "sha256": measured.get("sha256"),
        })
    state = status.get("status")
    return {
        "schema_version": SCHEMA_VERSION,
        "clip_id": clip_id,
        "job_id": job_id,
        "status": state,
        "ok": state == "succeeded",
        "reason": status.get("reason") or (receipt or {}).get("error") or "",
        "lane": status.get("lane"),
        "profile_version": status.get("profile_version"),
        "variants": variants,
        "finished_at": time.time(),
    }


class Bf6Bridge:
    """Carries AM4's render requests to HEARTH and its outcomes back."""

    def __init__(
        self,
        *,
        submit: Optional[Callable] = None,
        status: Optional[Callable] = None,
        receipt: Optional[Callable] = None,
        root: Optional[Path] = None,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._submit = submit
        self._status = status
        self._receipt = receipt
        self._root = root
        self._poll = poll_seconds
        self._stop = False

    # ------------------------------------------------------------ door calls

    def _client(self):
        # Lazy: the mcp SDK is only needed when actually talking to the door, so
        # tests and mcp-free code paths can import this module freely.
        from hearth.callers.client import HearthClient

        return HearthClient(key=bridge_key())

    @staticmethod
    def _payload(result: dict, tool: str) -> dict:
        # call_sync flattens the CallToolResult; an error comes back as text
        # with ok=False rather than an exception.
        if not result.get("ok", True):
            raise RuntimeError("%s refused: %s" % (tool, (result.get("text") or "")[:300]))
        return json.loads(result["text"])

    def submit_render(self, arguments: dict) -> dict:
        if self._submit is not None:
            return self._submit(arguments)
        # HearthClient is an ASYNC context manager but exposes call_sync; using
        # `with` on it fails at runtime.
        client = self._client()
        return self._payload(client.call_sync("submit_render", **arguments),
                             "submit_render")

    def render_status(self, job_id: str) -> dict:
        if self._status is not None:
            return self._status(job_id)
        client = self._client()
        return self._payload(client.call_sync("get_render_status", job_id=job_id),
                             "get_render_status")

    def render_receipt(self, status: dict) -> Optional[dict]:
        if self._receipt is not None:
            return self._receipt(status)
        results = [a for a in (status.get("artifacts") or [])
                   if a.get("role") == "result"]
        if not results:
            return None
        # Read the receipt locally: OMEN owns E: and the artifact store, and
        # get_execution_artifact is not in this caller's capability.
        from hearth.execution.artifacts import ArtifactStore

        try:
            return json.loads(ArtifactStore().read(results[-1]).decode("utf-8"))
        except Exception:
            return None

    # ------------------------------------------------------------------ tick

    def tick(self) -> BridgeTick:
        """One convergent pass over the share."""
        result = BridgeTick([], [], [], [], [])
        collect_stale_tmp(self._root)

        for request_path in list_requests(self._root):
            clip_id = clip_id_for(request_path)
            try:
                self._advance(request_path, clip_id, result)
            except Exception as exc:  # one bad clip must not stop the pass
                result.errors.append("%s: %s" % (clip_id, exc))
        return result

    def _advance(self, request_path: Path, clip_id: str, tick: BridgeTick) -> None:
        result_path = sidecar(request_path, RESULT_SUFFIX)
        if result_path.exists():
            tick.skipped.append(clip_id)      # publication already complete
            return

        request = _read(request_path)
        if request is None:
            tick.errors.append("%s: unreadable request" % clip_id)
            return

        claim_path = sidecar(request_path, CLAIM_SUFFIX)
        claim = _read(claim_path)
        job_id = (claim or {}).get("job_id")

        if job_id:
            # A known job is QUERIED, never resubmitted.
            status = self.render_status(job_id)
        else:
            submitted = self.submit_render(arguments_from(request))
            job_id = submitted.get("job_id")
            if not job_id:
                tick.errors.append("%s: submit returned no job_id" % clip_id)
                return
            _write_atomic(claim_path, {
                "schema_version": SCHEMA_VERSION,
                "clip_id": clip_id,
                "job_id": job_id,
                "clip_revision": request.get("clip_revision"),
                "idempotency_key": submitted.get("idempotency_key"),
                "submitted_at": time.time(),
            })
            tick.submitted.append(clip_id)
            status = self.render_status(job_id)

        if status.get("status") not in TERMINAL:
            tick.waiting.append(clip_id)
            return

        _write_atomic(result_path,
                      build_result(clip_id, job_id, status, self.render_receipt(status)))
        tick.reconciled.append(clip_id)

    # ------------------------------------------------------------------ loop

    def stop(self, *_args) -> None:
        self._stop = True

    def run(self) -> None:
        print("bf6 bridge: watching %s" % work_root(self._root), flush=True)
        while not self._stop:
            try:
                tick = self.tick()
                if tick.did_work or tick.errors:
                    print("bf6 bridge: %s" % json.dumps(tick.to_dict()), flush=True)
            except Exception as exc:
                print("bf6 bridge error: %s" % exc, flush=True)
            time.sleep(self._poll)


def main(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="BF6 render sidecar bridge")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=POLL_SECONDS)
    args = parser.parse_args(argv)

    bridge = Bf6Bridge(poll_seconds=args.poll)
    if args.once:
        print(json.dumps(bridge.tick().to_dict(), indent=2), flush=True)
        return 0
    bridge.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
