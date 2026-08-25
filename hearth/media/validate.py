"""Post-encode validation -- including the size guardrail the encoder cannot enforce.

WHY THE BITRATE CEILING LIVES HERE
----------------------------------
The original contract was "ICQ quality target + encoder-enforced bitrate cap".
Measurement killed that:

* adding ``-b:v 0`` or ``-maxrate`` silently drops h264_qsv out of ICQ into VBR,
  after which ``global_quality`` is ignored entirely -- a sweep of gq 14..22
  produced byte-identical output at 18.69 Mbps;
* and in working ICQ mode ``-maxrate`` does not cap anything: gq=18 with
  ``-maxrate 85M`` still emitted 87.96 Mbps.

So the ceiling cannot be an encoder setting. It is a POLICY checked against the
artifact that was actually produced. An output over the ceiling is reported as a
validation failure with its measured numbers -- never silently accepted, and
never silently re-encoded at a different quality. A retry policy, if one is ever
wanted, is a deliberate design, not something this function should improvise.

Nothing is promoted on the strength of "ffmpeg exited 0". A truncated file, a
dropped audio track and a silently rescaled frame all exit 0.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Frame-accurate trimming should land exactly, but container timebases round.
DURATION_TOLERANCE_S = 0.25


class ProbeError(RuntimeError):
    """ffprobe could not describe the file. Never treat this as valid."""


@dataclass
class ValidationResult:
    ok: bool
    failures: list = field(default_factory=list)
    measured: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "failures": list(self.failures),
                "measured": dict(self.measured)}


def probe(path, ffprobe: str = "ffprobe") -> dict:
    """ffprobe a rendered file into a plain dict."""
    target = Path(path)
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(target)],
        capture_output=True, text=True, errors="replace", timeout=120,
    )
    if proc.returncode != 0:
        raise ProbeError("ffprobe failed on %s: %s"
                         % (target, (proc.stderr or "").strip()[:300]))
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned unparseable JSON for %s" % target) from exc


def _fps(rate: str) -> Optional[float]:
    if not rate or "/" not in rate:
        return None
    num, _, den = rate.partition("/")
    try:
        numerator, denominator = float(num), float(den)
    except ValueError:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def validate_output(
    path,
    *,
    expected_width: Optional[int] = None,
    expected_height: Optional[int] = None,
    expected_duration_s: Optional[float] = None,
    expected_fps: Optional[float] = None,
    require_audio: bool = True,
    max_bitrate_mbps: Optional[float] = None,
    duration_tolerance_s: float = DURATION_TOLERANCE_S,
    ffprobe: str = "ffprobe",
) -> ValidationResult:
    """Check a rendered file against what was actually requested.

    Every failure is collected rather than short-circuited, so one report shows
    everything wrong with the output instead of one problem at a time.
    """
    failures: list = []
    measured: dict = {}
    target = Path(path)

    if not target.exists():
        return ValidationResult(False, ["output does not exist: %s" % target], {})

    size = target.stat().st_size
    measured["size_bytes"] = size
    if size <= 0:
        return ValidationResult(False, ["output is zero bytes"], measured)

    try:
        info = probe(target, ffprobe=ffprobe)
    except ProbeError as exc:
        return ValidationResult(False, [str(exc)], measured)

    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]

    if video is None:
        failures.append("output has no video stream")
    else:
        measured["codec"] = video.get("codec_name")
        measured["width"] = video.get("width")
        measured["height"] = video.get("height")
        measured["fps"] = _fps(video.get("r_frame_rate", ""))
        if expected_width is not None and video.get("width") != expected_width:
            failures.append("width is %s, expected %s"
                            % (video.get("width"), expected_width))
        if expected_height is not None and video.get("height") != expected_height:
            failures.append("height is %s, expected %s"
                            % (video.get("height"), expected_height))
        if expected_fps is not None:
            actual = measured["fps"]
            if actual is None or abs(actual - expected_fps) > 0.01:
                failures.append("frame rate is %s, expected %s" % (actual, expected_fps))

    measured["audio_streams"] = len(audio)
    if audio:
        measured["audio_codec"] = audio[0].get("codec_name")
        measured["audio_sample_rate"] = audio[0].get("sample_rate")
    if require_audio and not audio:
        failures.append("output has no audio stream")

    duration = None
    fmt = info.get("format") or {}
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        duration = None
    measured["duration_s"] = duration
    if expected_duration_s is not None:
        if duration is None:
            failures.append("output duration is unreadable")
        elif abs(duration - expected_duration_s) > duration_tolerance_s:
            failures.append(
                "duration is %.3fs, expected %.3fs (tolerance %.2fs)"
                % (duration, expected_duration_s, duration_tolerance_s)
            )

    if duration and duration > 0:
        mbps = size * 8.0 / duration / 1e6
        measured["bitrate_mbps"] = round(mbps, 2)
        # The guardrail the encoder could not provide. Reported, never absorbed.
        if max_bitrate_mbps is not None and mbps > max_bitrate_mbps:
            failures.append(
                "bitrate %.2f Mbps exceeds the %.2f Mbps guardrail for this "
                "variant; ICQ does not cap rate, so this is enforced here"
                % (mbps, max_bitrate_mbps)
            )

    return ValidationResult(not failures, failures, measured)


def source_dimensions(path, ffprobe: str = "ffprobe") -> tuple:
    """``(width, height, audio_stream_count)`` for a source segment."""
    info = probe(path, ffprobe=ffprobe)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if video is None:
        raise ProbeError("source has no video stream: %s" % path)
    return int(video.get("width")), int(video.get("height")), len(audio)
