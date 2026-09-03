"""FFmpeg normalization, looping, muxing, and acceptance probes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

CLIP_SECONDS = 81 / 24
VIDEO_FILTER = (
    "scale=1280:720:force_original_aspect_ratio=decrease,"
    "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p"
)


def _run(argv: list[str]) -> None:
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=7200)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-4000:]
        raise RuntimeError("FFmpeg failed: " + detail)


def probe(path: Path) -> dict:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError("FFprobe failed: " + (completed.stderr or "").strip()[-2000:])
    return json.loads(completed.stdout)


def _duration(info: dict) -> float:
    value = (info.get("format") or {}).get("duration")
    if value is None:
        raise ValueError("media has no reported duration")
    return float(value)


def verify_wan_clip(path: Path) -> dict:
    info = probe(path)
    video = next((row for row in info.get("streams", []) if row.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("Wan output has no video stream")
    if video.get("codec_name") != "h264" or video.get("width") != 1280 or video.get("height") != 720:
        raise ValueError("Wan output must be H.264 at 1280x720")
    rate = video.get("r_frame_rate")
    if rate not in {"24/1", "48/2"}:
        raise ValueError("Wan output must be 24 fps")
    frames = video.get("nb_frames")
    if frames is not None and int(frames) != 81:
        raise ValueError("Wan output must contain 81 frames")
    duration = _duration(info)
    if abs(duration - CLIP_SECONDS) > (1 / 24 + 0.01):
        raise ValueError("Wan output duration is outside the one-frame tolerance")
    return {"codec": "h264", "video_width": 1280, "video_height": 720,
            "video_fps": 24.0, "duration_seconds": duration}


def normalize_scene(source: Path, destination: Path, *, still: bool) -> None:
    argv = ["ffmpeg", "-y"]
    if still:
        argv.extend(["-loop", "1"])
    argv.extend(["-i", str(source), "-t", f"{CLIP_SECONDS:.3f}", "-an",
                 "-vf", VIDEO_FILTER, "-c:v", "libx264", "-preset", "medium",
                 "-crf", "20", "-pix_fmt", "yuv420p", str(destination)])
    _run(argv)


def compose_full_audio(segments: list[Path], audio: Path, output: Path, work: Path) -> dict:
    if not segments:
        raise ValueError("at least one visual segment is required")
    concat = work / "segments.txt"
    concat.write_text("".join("file '%s'\n" % str(path).replace("'", "'\\''") for path in segments),
                      encoding="utf-8", newline="\n")
    cycle = work / "visual-cycle.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
          "-c", "copy", str(cycle)])
    audio_duration = _duration(probe(audio))
    _run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(cycle), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-t", f"{audio_duration:.6f}",
        "-vf", "fps=24,format=yuv420p", "-c:v", "libx264", "-preset", "medium",
        "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(output),
    ])
    info = probe(output)
    video = next((row for row in info.get("streams", []) if row.get("codec_type") == "video"), None)
    audio_stream = next((row for row in info.get("streams", []) if row.get("codec_type") == "audio"), None)
    if not video or not audio_stream:
        raise ValueError("final output must contain audio and video")
    if (video.get("codec_name"), video.get("width"), video.get("height"), video.get("r_frame_rate")) != (
        "h264", 1280, 720, "24/1"
    ):
        raise ValueError("final video failed H.264/1280x720/24fps acceptance")
    if audio_stream.get("codec_name") != "aac":
        raise ValueError("final video audio must be AAC")
    final_duration = _duration(info)
    if abs(final_duration - audio_duration) > 0.5:
        raise ValueError("final audio/video duration differs by more than 0.5 seconds")
    return {"codec": "h264", "video_width": 1280, "video_height": 720,
            "video_fps": 24.0, "duration_seconds": final_duration}
