"""Render profiles: the versioned encode contract, and the ffmpeg graphs.

The audio graph and the caption style here are copied VERBATIM from the AM4
renderer (``am4-worker/app/pipeline.py``). They are a compatibility contract,
not a design choice, and they must not be "improved" while migrating encoders --
a highlight whose voice balance shifts is a broken highlight even if it encodes
faster.

Two details in the audio chain are load-bearing and easy to lose:

* ``amix=...:normalize=0`` -- without it, amix applies automatic 1/N attenuation
  and the explicit per-track ``volume=`` gains stop meaning what they say.
* ``loudnorm=I=-14:TP=-1:LRA=11`` single-pass, then ``aresample=48000``.

The video side differs per profile. QSV has no gaussian blur, so the vertical
bed is approximated with a vpp_qsv downscale/upscale pair -- a visible departure
from ``gblur=sigma=28``, taken deliberately to keep the composite on the media
engine rather than round-tripping 4K through system memory.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

# --- the compatibility contract (do not tune) --------------------------------

# Track gains: 0 = game bus, 1 = Derek's mic, 2 = Discord. Voice is lifted above
# the game bus so callouts survive an explosion.
TRACK_GAINS = (1.0, 1.35, 1.25)
MAX_AUDIO_TRACKS = 3
LOUDNORM = "loudnorm=I=-14:TP=-1:LRA=11"
AUDIO_SAMPLE_RATE = 48000
AUDIO_CODEC = ("-c:a", "aac", "-b:a", "320k")
CONTAINER_FLAGS = ("-movflags", "+faststart")
CONTAINER_FORMAT = "mp4"

SUBTITLE_STYLE = (
    "FontName=DejaVu Sans,FontSize=18,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,BorderStyle=3,Outline=2,MarginV=110"
)

VARIANTS = ("horizontal", "vertical")

# Seconds of decode to run before the clip's in-point when using QSV.
#
# WHY THIS EXISTS -- measured 2026-08-25, and it is a correctness fix, not a
# performance tweak. Plain `-ss <t> -i <src>` input seeking returns DIFFERENT
# FRAMES under `-hwaccel qsv` than it does with the software decoder. Isolated
# with no encoder in the path at all, software-decode vs QSV-decode of the same
# file at the same seek scored SSIM Y:0.906 / U:0.990 / V:0.991 -- luma badly
# mismatched while chroma tracked, the signature of a temporal offset rather
# than lossy decode.
#
# Left unfixed, every clip would start on a slightly different frame than the
# AM4 renderer produced, and burned-in captions -- which are timed relative to
# the clip's in-point -- would drift against the picture.
#
# The fix: seek to a keyframe safely BEFORE the in-point, keep absolute
# timestamps with -copyts, and trim exactly in the filter graph. That is
# pixel-identical to software input seeking (SSIM 1.000000) and still fast:
# 4.9 s versus 34.5 s for decoding from zero, on a clip 200 s into a segment.
#
# OBS records with 2-second keyframes, so 10 s is five keyframes of margin.
QSV_PREROLL_SECONDS = 10.0


class ProfileError(ValueError):
    """An unknown or malformed render profile."""


@dataclass(frozen=True)
class VariantSpec:
    encoder: str
    args: tuple
    maxrate: Optional[str]
    bufsize: Optional[str]
    global_quality: Optional[int]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[int]
    burn_captions: bool
    calibrated: bool
    blur_width: Optional[int]
    blur_height: Optional[int]
    rate_control: str = "cq"
    max_bitrate_mbps: Optional[float] = None
    bitrate: Optional[str] = None


@dataclass(frozen=True)
class RenderProfile:
    version: str
    description: str
    graph: str
    node: str
    reference_only: bool
    variants: dict

    def variant(self, name: str) -> VariantSpec:
        try:
            return self.variants[name]
        except KeyError:
            raise ProfileError(
                "profile %r has no %r variant" % (self.version, name)
            ) from None


def default_profiles_path() -> Path:
    configured = os.environ.get("HEARTH_RENDER_PROFILES")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "etc" / "render-profiles.toml"


def _variant_from(raw: dict) -> VariantSpec:
    return VariantSpec(
        encoder=str(raw["encoder"]),
        args=tuple(str(item) for item in raw.get("args", ())),
        maxrate=raw.get("maxrate"),
        bufsize=raw.get("bufsize"),
        global_quality=raw.get("global_quality"),
        width=raw.get("width"),
        height=raw.get("height"),
        fps=raw.get("fps"),
        burn_captions=bool(raw.get("burn_captions", False)),
        calibrated=bool(raw.get("calibrated", True)),
        blur_width=raw.get("blur_width"),
        blur_height=raw.get("blur_height"),
        rate_control=str(raw.get("rate_control", "cq")),
        max_bitrate_mbps=raw.get("max_bitrate_mbps"),
        bitrate=raw.get("bitrate"),
    )


def load_profiles(path: Optional[Path] = None) -> dict:
    """Load the profile registry.

    Raises rather than defaulting: an unknown profile version must fail the job,
    because silently substituting a different encode contract would produce a
    draft that does not match what its manifest claims.
    """
    target = Path(path) if path is not None else default_profiles_path()
    try:
        with open(target, "rb") as handle:
            document = tomllib.load(handle)
    except OSError as exc:
        raise ProfileError("cannot read render profiles at %s: %s" % (target, exc)) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError("malformed render profiles at %s: %s" % (target, exc)) from exc

    profiles = {}
    for version, raw in (document.get("profile") or {}).items():
        variants = {}
        for name in VARIANTS:
            if name in raw:
                variants[name] = _variant_from(raw[name])
        profiles[version] = RenderProfile(
            version=version,
            description=str(raw.get("description", "")),
            graph=str(raw.get("graph", "")),
            node=str(raw.get("node", "")),
            reference_only=bool(raw.get("reference_only", False)),
            variants=variants,
        )
    if not profiles:
        raise ProfileError("no profiles defined in %s" % (target,))
    return profiles


def get_profile(version: str, path: Optional[Path] = None) -> RenderProfile:
    profiles = load_profiles(path)
    try:
        profile = profiles[version]
    except KeyError:
        raise ProfileError(
            "unknown profile_version %r; known: %s"
            % (version, ", ".join(sorted(profiles)))
        ) from None
    if profile.reference_only:
        raise ProfileError(
            "profile %r is reference-only and cannot be dispatched" % (version,)
        )
    return profile


# --- audio -------------------------------------------------------------------

def mix_filter(audio_streams: int, trim: Optional[tuple] = None) -> tuple:
    """Build the three-track mix. Ported verbatim from pipeline.mix_filter.

    Returns ``(filter_string, output_label)``, or ``("", "")`` when the source
    carries no audio.

    ``trim`` is ``(start_seconds, duration_seconds)`` in ABSOLUTE source time,
    used only on the QSV path where ``-copyts`` keeps timestamps absolute. The
    audio must be trimmed on exactly the same boundary as the video or the mix
    drifts against the picture -- so the trim is applied here rather than being
    left to an outer ``-ss``/``-t``.

    The gains, ``normalize=0``, the loudnorm settings and the 48 kHz resample
    are the compatibility contract and are identical either way.
    """
    if audio_streams <= 0:
        return "", ""
    labels = []
    chains = []
    for index in range(min(audio_streams, MAX_AUDIO_TRACKS)):
        label = "a%d" % index
        labels.append("[%s]" % label)
        if trim is not None:
            chains.append(
                "[0:a:%d]atrim=start=%.3f:duration=%.3f,asetpts=PTS-STARTPTS,"
                "volume=%s[%s]" % (index, trim[0], trim[1], TRACK_GAINS[index], label)
            )
        else:
            chains.append("[0:a:%d]volume=%s[%s]" % (index, TRACK_GAINS[index], label))
    chains.append(
        "%samix=inputs=%d:normalize=0:dropout_transition=2,%s,aresample=%d[mix]"
        % ("".join(labels), len(labels), LOUDNORM, AUDIO_SAMPLE_RATE)
    )
    return ";".join(chains), "[mix]"


def escape_filter_path(path: str) -> str:
    """Escape a path for use inside a filter argument (the subtitles= source)."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


# --- video graphs ------------------------------------------------------------

def qsv_vertical_filter(spec: VariantSpec, srt_path: Optional[str],
                        src_label: str = "[0:v]",
                        src_width: int = 3840, src_height: int = 2160) -> str:
    """The 1080x1920 composite, kept on the media engine as far as possible.

    GEOMETRY. The bed must COVER the vertical frame, matching the AM4 graph's
    ``scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920``.
    Scaling 16:9 straight into 9:16 instead SQUASHES the picture -- which is
    what a naive ``vpp_qsv=w=68:h=120`` does, and it is obvious on screen once
    you look. So the source is cropped to the target aspect first, using
    vpp_qsv's own cw/ch crop, then scaled.

    BLUR. QSV has no gaussian blur, so the bed is approximated by a
    downscale/upscale pair. Two details, both chosen by looking at frames:

    * a single 68x120 -> 1080x1920 jump is a hard mosaic, not a blur;
    * going only as low as 136x240 leaves the bed READABLE -- menu text and
      faces are still legible, which defeats the point of a background.

    A staged 68x120 -> 270x480 -> 1080x1920 with denoise reads as a blurred
    bed while staying entirely on the media engine. It is still visibly
    coarser than gblur=sigma=28; that is the accepted, deliberate deviation.
    """
    width = spec.width or 1080
    height = spec.height or 1920

    # Crop the source to the output aspect so the bed fills without distortion.
    crop_w = min(src_width, int(round(src_height * width / float(height))))
    crop_h = min(src_height, int(round(src_width * height / float(width))))
    if crop_w < src_width:
        crop_h = src_height
    else:
        crop_w = src_width

    blur_w = spec.blur_width or 68
    blur_h = spec.blur_height or 120
    stage_w, stage_h = width // 4, height // 4
    denoise = ":denoise=100"

    chain = (
        "%ssplit=2[bg][fg];"
        "[bg]vpp_qsv=cw=%d:ch=%d:w=%d:h=%d,vpp_qsv=w=%d:h=%d,"
        "vpp_qsv=w=%d:h=%d%s[blur];"
        "[fg]vpp_qsv=w=%d:h=-1[front];"
        "[blur][front]overlay_qsv=x=(W-w)/2:y=(H-h)/2[composite]"
        % (src_label, crop_w, crop_h, blur_w, blur_h,
           stage_w, stage_h, width, height, denoise, width)
    )
    if srt_path and spec.burn_captions:
        # Down to system memory for the subtitle burn, and NOT back up.
        #
        # The natural-looking `...,hwupload=extra_hw_frames=64` fails on this
        # driver with "Could not create the texture (80070057)" -- E_INVALIDARG
        # while building the child frames context. Re-uploading is unnecessary
        # anyway: h264_qsv accepts system-memory NV12 and uploads internally, so
        # the same copy happens once, managed by the QSV runtime instead of the
        # filter graph. Verified working against real 4K HEVC source.
        chain += (
            ";[composite]hwdownload,format=nv12,"
            "subtitles='%s':force_style='%s',setsar=1[v]"
            % (escape_filter_path(srt_path), SUBTITLE_STYLE)
        )
    else:
        # No transcript: stay in QSV memory all the way to the encoder, which is
        # the cheaper path. The chain still terminates in [v] so -map [v] always
        # resolves -- the same reason the AM4 renderer ends with [base]null[v].
        chain += ";[composite]null[v]"
    return chain


def nvenc_vertical_filter(spec: VariantSpec, srt_path: Optional[str],
                          src_label: str = "[0:v]") -> str:
    """The original CPU graph, kept for reference and A/B comparison."""
    width = spec.width or 1080
    height = spec.height or 1920
    chain = (
        "%ssplit=2[bg][fg];"
        "[bg]scale=%d:%d:force_original_aspect_ratio=increase,"
        "crop=%d:%d,gblur=sigma=28[blur];"
        "[fg]scale=%d:-2[front];"
        "[blur][front]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
        % (src_label, width, height, width, height, width)
    )
    if srt_path and spec.burn_captions:
        chain += ";[base]subtitles='%s':force_style='%s'[v]" % (
            escape_filter_path(srt_path), SUBTITLE_STYLE
        )
    else:
        chain += ";[base]null[v]"
    return chain


def _encoder_args(spec: VariantSpec) -> list:
    """Encoder flags for one variant.

    ICQ is fragile on QSV: adding `-b:v 0` or `-maxrate` drops the encoder into
    VBR, after which `-global_quality` is silently ignored (measured -- a gq
    14..22 sweep produced byte-identical output). So in ICQ mode we emit the
    quality target and nothing that would override it, and the size ceiling is
    enforced downstream at validation instead.
    """
    args = ["-c:v", spec.encoder]
    if spec.rate_control == "vbr":
        # Bounded mode. QSV VBR DOES honour -maxrate (it is ICQ that ignores
        # it), so this is the only QSV configuration that actually caps output.
        # global_quality is NOT emitted here: mixing it back in is what silently
        # drops the encoder into a mode where neither control works as written.
        if not spec.bitrate or not spec.maxrate:
            raise ProfileError(
                "rate_control=vbr requires both bitrate and maxrate on %r"
                % (spec.encoder,)
            )
        args += ["-b:v", spec.bitrate, "-maxrate", spec.maxrate]
        if spec.bufsize:
            args += ["-bufsize", spec.bufsize]
        args += list(spec.args)
        return args

    if spec.rate_control == "icq":
        if spec.global_quality is None:
            raise ProfileError(
                "rate_control=icq requires global_quality on %r" % (spec.encoder,)
            )
        args += ["-global_quality", str(spec.global_quality)]
        args += list(spec.args)
        return args

    if spec.global_quality is not None:
        args += ["-global_quality", str(spec.global_quality)]
    args += list(spec.args)
    if spec.maxrate:
        args += ["-maxrate", spec.maxrate]
    if spec.bufsize:
        args += ["-bufsize", spec.bufsize]
    return args


def build_command(
    *,
    profile: RenderProfile,
    variant: str,
    ffmpeg: str,
    inputs: Sequence,
    start_seconds: float,
    duration_seconds: float,
    output: str,
    audio_streams: int,
    child_device: Optional[int] = None,
    srt_path: Optional[str] = None,
    concat_path: Optional[str] = None,
    progress: bool = True,
    src_width: int = 3840,
    src_height: int = 2160,
) -> list:
    """Assemble the full ffmpeg invocation for one variant.

    Seeking differs by graph, and the difference is deliberate:

    * **nvenc** (reference) uses ``-ss``/``-t`` before ``-i``, exactly as the AM4
      renderer does today.
    * **qsv** seeks to ``start - QSV_PREROLL_SECONDS``, keeps absolute
      timestamps with ``-copyts``, and trims precisely in the filter graph.
      Plain input seeking under ``-hwaccel qsv`` lands on a different frame than
      the software decoder does; see QSV_PREROLL_SECONDS for the measurement.

    Multi-segment clips arrive via a generated .ffconcat, so the seek addresses
    the concatenated timeline exactly as it does today.
    """
    if variant not in VARIANTS:
        raise ProfileError("unknown variant %r" % (variant,))
    spec = profile.variant(variant)
    is_qsv = profile.graph == "qsv"

    command = [ffmpeg, "-hide_banner", "-nostdin", "-y"]

    if is_qsv:
        if child_device is None:
            # Refusing here is the whole point of lane calibration: without an
            # explicit child_device, ffmpeg silently falls back to DXGI adapter
            # 0, which on this box is the integrated GPU.
            raise ProfileError(
                "qsv profile requires an explicit child_device; refusing to let "
                "ffmpeg default to adapter 0 (the iGPU)"
            )
        command += [
            "-init_hw_device",
            "qsv=hw:hw_any,child_device=%d,child_device_type=d3d11va" % child_device,
            "-filter_hw_device", "hw",
            "-hwaccel", "qsv",
            "-hwaccel_output_format", "qsv",
        ]

    if is_qsv:
        seek_start = max(0.0, start_seconds - QSV_PREROLL_SECONDS)
        command += ["-copyts", "-ss", "%.3f" % seek_start]
        trim = (start_seconds, duration_seconds)
    else:
        command += ["-ss", "%.3f" % start_seconds, "-t", "%.3f" % duration_seconds]
        trim = None

    if concat_path:
        command += ["-f", "concat", "-safe", "0", "-i", concat_path]
    else:
        if len(inputs) != 1:
            raise ProfileError(
                "%d inputs given without a concat list; pass concat_path"
                % (len(inputs),)
            )
        command += ["-i", str(inputs[0])]

    audio_filter, audio_map = mix_filter(audio_streams, trim=trim)

    if variant == "horizontal":
        # No scaling -- the 4K master passes through, exactly as the AM4
        # renderer does with -map 0:v:0. The output is 3840x2160 because the
        # source is, not because anything resizes it. On QSV the frames must
        # still cross a trim filter to land on the right in-point.
        if is_qsv:
            video_filter = (
                "[0:v]trim=start=%.3f:duration=%.3f,setpts=PTS-STARTPTS[v]"
                % (start_seconds, duration_seconds)
            )
            combined = video_filter + (";" + audio_filter if audio_filter else "")
            command += ["-filter_complex", combined, "-map", "[v]"]
            if audio_filter:
                command += ["-map", audio_map]
        else:
            command += ["-map", "0:v:0"]
            if audio_filter:
                command += ["-filter_complex", audio_filter, "-map", audio_map]
    else:
        if is_qsv:
            source = "[vsrc]"
            prefix = (
                "[0:v]trim=start=%.3f:duration=%.3f,setpts=PTS-STARTPTS[vsrc];"
                % (start_seconds, duration_seconds)
            )
            video_filter = prefix + qsv_vertical_filter(
                spec, srt_path, source, src_width, src_height)
        else:
            video_filter = nvenc_vertical_filter(spec, srt_path)
        combined = video_filter + (";" + audio_filter if audio_filter else "")
        command += ["-filter_complex", combined, "-map", "[v]"]
        if audio_filter:
            command += ["-map", audio_map]
        if spec.fps:
            command += ["-r", str(spec.fps)]

    if is_qsv:
        # -copyts keeps absolute PTS so the trim can address real source time.
        # -start_at_zero then rebases the OUTPUT to begin at 0.
        #
        # MEASURED 2026-08-25 -- this pairing is not interchangeable with the
        # obvious alternatives:
        #   -start_at_zero                 -> first PTS 0.000000, duration
        #                                     20.000000, SSIM 0.980477  <-- use
        #   -avoid_negative_ts make_zero   -> first PTS 0.033008 (two frames
        #                                     late), duration 20.021333,
        #                                     SSIM 0.962129
        # The two-frame offset is invisible in a file listing and shows up only
        # as a quality score that will not improve no matter how many bits the
        # encoder spends -- and as captions sitting slightly off the picture.
        command += ["-start_at_zero"]
        # Bounds the output in case a trim is ever mis-specified.
        command += ["-t", "%.3f" % duration_seconds]

    command += _encoder_args(spec)
    if audio_streams > 0:
        command += list(AUDIO_CODEC)
    else:
        command += ["-an"]
    command += list(CONTAINER_FLAGS)
    # Renders are staged as `<name>.mp4.part` and only renamed once validated
    # and cleared for promotion, so the filename ffmpeg sees has no usable
    # extension. Without an explicit muxer it refuses with "Unable to choose an
    # output format" -- state the container rather than relying on the name.
    command += ["-f", CONTAINER_FORMAT]
    if progress:
        command += ["-progress", "pipe:1", "-nostats"]
    command.append(str(output))
    return command
