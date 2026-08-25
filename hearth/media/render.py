"""The render execution body: encode, validate, promote, receipt.

ORDER MATTERS AND IS NOT NEGOTIABLE
----------------------------------
    render to a temp file
      -> ffprobe validation (dimensions, duration, fps, audio, size, bitrate)
        -> commit-time revision check under promote:<clip_id>
          -> atomic os.replace into drafts/
            -> receipt

Every step before the replace can fail without touching an existing draft. That
is the point: a bad encode, a superseded clip, or an unreadable authority record
all leave the previous draft exactly where it was.

A job that is superseded still finishes and still validates. It simply discards
its own output and reports ``promoted: false``. Correctness never depends on
cancellation arriving, because a running ffmpeg cannot reliably be stopped.

The PID sidecar exists for restart recovery: a gateway restart re-queues the job
(recover_pending), and without a record of the previous attempt's child process
the old ffmpeg would keep writing while the retry started a second one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from hearth.media import profiles as profiles_mod
from hearth.media import revision as revision_mod
from hearth.media import validate as validate_mod
from hearth.toolsurface._media_scope import media_root, resolve_media

PROGRESS_RE = re.compile(r"^(?P<key>\w+)=(?P<value>.*)$")


def inflight_dir() -> Path:
    configured = os.environ.get("HEARTH_RENDER_INFLIGHT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "var" / "render" / "inflight"


@dataclass
class VariantResult:
    variant: str
    promoted: bool
    reason: str
    output: Optional[str] = None
    validation: dict = field(default_factory=dict)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "promoted": self.promoted,
            "reason": self.reason,
            "output": self.output,
            "validation": dict(self.validation),
            "seconds": round(self.seconds, 2),
        }


@dataclass
class RenderReceipt:
    job_id: str
    clip_id: str
    session_id: str
    clip_revision: int
    profile_version: str
    lane_id: str
    child_device: Optional[int]
    scheduling: dict
    variants: list = field(default_factory=list)
    ok: bool = False
    error: str = ""
    total_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "job_id": self.job_id,
            "clip_id": self.clip_id,
            "session_id": self.session_id,
            "clip_revision": self.clip_revision,
            "profile_version": self.profile_version,
            "lane": {"lane_id": self.lane_id, "child_device": self.child_device},
            "scheduling": dict(self.scheduling),
            "variants": [variant.to_dict() for variant in self.variants],
            "ok": self.ok,
            "error": self.error,
            "total_seconds": round(self.total_seconds, 2),
        }


def write_inflight(job_id: str, payload: dict) -> Path:
    directory = inflight_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("%s.json" % job_id)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def read_inflight(job_id: str) -> Optional[dict]:
    target = inflight_dir() / ("%s.json" % job_id)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_inflight(job_id: str) -> None:
    try:
        (inflight_dir() / ("%s.json" % job_id)).unlink()
    except OSError:
        pass


def reap_orphan(job_id: str, *, killer: Optional[Callable] = None) -> list:
    """Kill any ffmpeg left behind by a previous attempt, and clear its temps.

    PID reuse is real -- this session observed the OS hand the same pid to two
    different processes minutes apart -- so a recorded pid is only acted on when
    the live process still matches the recorded image name AND start time.
    Killing by pid alone would eventually kill something innocent.
    """
    record = read_inflight(job_id)
    if not record:
        return []
    actions = []
    pid = record.get("pid")
    if pid:
        if killer is not None:
            actions.append(killer(pid, record))
        else:
            actions.append(_kill_if_matches(pid, record))
    for temp in record.get("temp_files", ()) or ():
        try:
            Path(temp).unlink()
            actions.append("removed %s" % temp)
        except OSError:
            pass
    clear_inflight(job_id)
    return actions


def _kill_if_matches(pid: int, record: dict) -> str:
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None
    if psutil is None:
        # Without process introspection, refuse to kill rather than risk
        # killing a reused pid.
        return "pid %s left alone (no process introspection available)" % pid
    try:
        process = psutil.Process(pid)
        if process.name().lower() != str(record.get("image", "")).lower():
            return "pid %s is now %s; not ours" % (pid, process.name())
        started = record.get("started_at")
        if started and abs(process.create_time() - float(started)) > 2.0:
            return "pid %s start time differs; not ours" % pid
        process.kill()
        return "killed orphaned pid %s" % pid
    except Exception as exc:  # process gone, or access denied
        return "pid %s not killed: %s" % (pid, exc)


def sha256_of(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_progress(line: str) -> Optional[tuple]:
    match = PROGRESS_RE.match(line.strip())
    if not match:
        return None
    return match.group("key"), match.group("value")


def render_clip(
    *,
    spec,
    lane,
    job_id: str,
    ffmpeg: str,
    ffprobe: str,
    scheduling: Optional[dict] = None,
    leases=None,
    root: Optional[Path] = None,
    on_progress: Optional[Callable] = None,
    cancelled: Optional[Callable] = None,
) -> RenderReceipt:
    """Render every requested variant of one clip and promote what survives."""
    started = time.time()
    base = Path(root) if root is not None else media_root()
    receipt = RenderReceipt(
        job_id=job_id, clip_id=spec.clip_id, session_id=spec.session_id,
        clip_revision=spec.clip_revision, profile_version=spec.profile_version,
        lane_id=lane.lane_id, child_device=lane.child_device,
        scheduling=dict(scheduling or {}),
    )

    # A previous attempt of THIS job may still have a child running.
    reap_orphan(job_id)

    try:
        profile = profiles_mod.get_profile(spec.profile_version)
    except profiles_mod.ProfileError as exc:
        receipt.error = str(exc)
        return receipt

    sources = [str(resolve_media(path, mode="read", root=base))
               for path in spec.source_segments]
    try:
        src_w, src_h, audio_streams = validate_mod.source_dimensions(
            sources[0], ffprobe=ffprobe
        )
    except validate_mod.ProbeError as exc:
        receipt.error = str(exc)
        return receipt

    work_dir = resolve_media(
        "work/%s/render/%s" % (spec.session_id, job_id), mode="write", root=base
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    drafts_dir = resolve_media("drafts/%s" % spec.session_id, mode="write", root=base)
    drafts_dir.mkdir(parents=True, exist_ok=True)

    concat_path = None
    if len(sources) > 1:
        concat_path = work_dir / ("%s.ffconcat" % spec.clip_id)
        lines = ["ffconcat version 1.0"]
        for source in sources:
            lines.append("file '%s'" % Path(source).as_posix().replace("'", "\\'"))
        concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    srt_path = None
    if spec.captions:
        srt_path = work_dir / ("%s.srt" % spec.clip_id)
        srt_path.write_text(spec.captions, encoding="utf-8")
    elif spec.captions_path:
        srt_path = resolve_media(spec.captions_path, mode="read", root=base)

    for variant in spec.variants:
        if cancelled is not None and cancelled():
            receipt.variants.append(
                VariantResult(variant, False, "cancelled_before_start")
            )
            continue
        receipt.variants.append(
            _render_variant(
                spec=spec, lane=lane, job_id=job_id, variant=variant,
                profile=profile, ffmpeg=ffmpeg, ffprobe=ffprobe,
                sources=sources, concat_path=concat_path, srt_path=srt_path,
                work_dir=work_dir, drafts_dir=drafts_dir, base=base,
                src_w=src_w, src_h=src_h, audio_streams=audio_streams,
                leases=leases, on_progress=on_progress,
            )
        )

    clear_inflight(job_id)
    receipt.total_seconds = time.time() - started
    # A job is ok when every requested variant was promoted. A superseded clip
    # is NOT an error -- it is the mechanism working -- but it is not ok either,
    # and the reason on each variant says which happened.
    receipt.ok = bool(receipt.variants) and all(v.promoted for v in receipt.variants)
    if not receipt.ok and not receipt.error:
        reasons = {v.reason for v in receipt.variants if not v.promoted}
        receipt.error = "; ".join(sorted(reasons))
    try:
        if not any(work_dir.iterdir()):
            work_dir.rmdir()
    except OSError:
        pass
    return receipt


def _render_variant(
    *, spec, lane, job_id, variant, profile, ffmpeg, ffprobe, sources,
    concat_path, srt_path, work_dir, drafts_dir, base, src_w, src_h,
    audio_streams, leases, on_progress,
) -> VariantResult:
    started = time.time()
    staged = work_dir / ("%s-%s.mp4.part" % (spec.clip_id, variant))
    destination = drafts_dir / ("%s-%s.mp4" % (spec.clip_id, variant))

    command = profiles_mod.build_command(
        profile=profile, variant=variant, ffmpeg=ffmpeg, inputs=sources,
        start_seconds=spec.start_seconds, duration_seconds=spec.duration_seconds,
        output=str(staged), audio_streams=audio_streams,
        child_device=lane.child_device,
        srt_path=str(srt_path) if srt_path else None,
        concat_path=str(concat_path) if concat_path else None,
        progress=True, src_width=src_w, src_height=src_h,
    )

    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace",
    )
    write_inflight(job_id, {
        "job_id": job_id, "clip_id": spec.clip_id, "variant": variant,
        "pid": process.pid, "image": os.path.basename(ffmpeg),
        "started_at": time.time(), "temp_files": [str(staged)],
    })

    tail: list = []
    try:
        if process.stdout is not None:
            for line in process.stdout:
                parsed = parse_progress(line)
                if parsed and on_progress is not None:
                    on_progress(variant, parsed[0], parsed[1])
        process.wait(timeout=60)
    except Exception:
        process.kill()
        process.wait(timeout=15)
    if process.stderr is not None:
        tail = (process.stderr.read() or "").strip().splitlines()[-6:]

    if process.returncode != 0:
        _discard(staged)
        return VariantResult(
            variant, False, "ffmpeg_failed",
            validation={"stderr": tail}, seconds=time.time() - started,
        )

    spec_variant = profile.variant(variant)
    expected_w = spec_variant.width or src_w
    expected_h = spec_variant.height or src_h
    result = validate_mod.validate_output(
        staged,
        expected_width=expected_w,
        expected_height=expected_h,
        expected_duration_s=spec.duration_seconds,
        expected_fps=float(spec_variant.fps) if spec_variant.fps else None,
        require_audio=audio_streams > 0,
        max_bitrate_mbps=spec_variant.max_bitrate_mbps,
        ffprobe=ffprobe,
    )
    if not result.ok:
        _discard(staged)
        return VariantResult(
            variant, False, "validation_failed",
            validation=result.to_dict(), seconds=time.time() - started,
        )

    measured = dict(result.measured)
    if leases is None:
        _discard(staged)
        return VariantResult(
            variant, False, "no_lease_store_for_promotion",
            validation=result.to_dict(), seconds=time.time() - started,
        )

    outcome = revision_mod.promote_if_current(
        leases=leases,
        session_id=spec.session_id,
        clip_id=spec.clip_id,
        job_revision=spec.clip_revision,
        job_id=job_id,
        invocation_id="render-%s-%s" % (job_id, variant),
        staged=staged,
        destination=destination,
        root=base,
    )
    if outcome.promoted:
        measured["sha256"] = sha256_of(destination)
    validation = result.to_dict()
    validation["measured"] = measured
    validation["promotion"] = outcome.to_dict()
    return VariantResult(
        variant=variant,
        promoted=outcome.promoted,
        reason=outcome.reason,
        output=str(destination) if outcome.promoted else None,
        validation=validation,
        seconds=time.time() - started,
    )


def _discard(staged: Path) -> None:
    try:
        staged.unlink()
    except OSError:
        pass
