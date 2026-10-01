"""perception.ocr — CPU OCR with detector profiles and token-level boxes.

Provenance:
Ported from Derek's Clippy AM4 worker (commit 53a2bee8637893a23ab6f6c7405eb18c050e7b51):
  - am4-worker/app/hud.py:449 (_tesseract_text, _crop, _clean_text, NormalizedRegion, HudDetectorProfile)
  - am4-worker/app/accolade.py:623 (_tesseract_tsv, OcrToken, AccoladeOcrProfile)
  - am4-worker/app/pipeline.py:1511 (ocr_text, --psm 6)
"""

from __future__ import annotations

import io
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import cv2
import numpy as np
from PIL import Image


@dataclass
class OcrToken:
    text: str
    confidence: float
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None
    block_num: int | None = None
    paragraph_num: int | None = None
    line_num: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedRegion:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not (0 <= self.x < 1 and 0 <= self.y < 1):
            raise ValueError("Region origin (x, y) must be within [0, 1)")
        if not (0 < self.width <= 1 and 0 < self.height <= 1):
            raise ValueError("Region size (width, height) must be within (0, 1]")
        if self.x + self.width > 1.0001 or self.y + self.height > 1.0001:
            raise ValueError("Normalized region extends beyond frame bounds")


@dataclass
class OcrProfile:
    page_segmentation_mode: int = 6
    ocr_language: str = "eng"
    preprocessing: str = "resize-cubic-rgb-v1"
    upscale: int = 1
    confidence: float = 0.85


def clean_text(text: str) -> str:
    """Strip form-feed characters and collapse whitespace."""
    return " ".join(text.replace("\x0c", " ").split())


def tesseract_version() -> str:
    """Return Tesseract version string or empty string on error."""
    try:
        binary = os.getenv("TESSERACT_PATH", "tesseract")
        result = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.splitlines()[0] if result.returncode == 0 else ""
    except Exception:
        return ""


def crop_region(frame: np.ndarray, region: NormalizedRegion) -> np.ndarray:
    """Crop an image using normalized [0, 1] coordinates."""
    height, width = frame.shape[:2]
    left = round(width * region.x)
    top = round(height * region.y)
    right = round(width * (region.x + region.width))
    bottom = round(height * (region.y + region.height))
    return frame[top:bottom, left:right]


def _tesseract_text_call(
    image_bytes: bytes,
    language: str = "eng",
    psm: int = 6,
    thread_limit: int | None = None,
) -> str:
    binary = os.getenv("TESSERACT_PATH", "tesseract")
    env = os.environ.copy()
    if thread_limit is not None:
        env["OMP_THREAD_LIMIT"] = str(thread_limit)
    completed = subprocess.run(
        [binary, "stdin", "stdout", "-l", language, "--psm", str(psm)],
        input=image_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        env=env,
        timeout=30,
    )
    return completed.stdout.decode("utf-8", errors="replace") if completed.returncode == 0 else ""


def _tesseract_tsv_call(
    image_bytes: bytes,
    language: str = "eng",
    psm: int = 6,
    thread_limit: int | None = None,
) -> tuple[str, list[OcrToken]]:
    binary = os.getenv("TESSERACT_PATH", "tesseract")
    env = os.environ.copy()
    if thread_limit is not None:
        env["OMP_THREAD_LIMIT"] = str(thread_limit)
    completed = subprocess.run(
        [binary, "stdin", "stdout", "-l", language, "--psm", str(psm), "tsv"],
        input=image_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        env=env,
        timeout=30,
    )
    if completed.returncode != 0:
        return "", []
    lines = completed.stdout.decode("utf-8", errors="replace").splitlines()
    tokens: list[OcrToken] = []
    for line in lines[1:]:
        fields = line.split("\t", 11)
        if len(fields) != 12 or not fields[11].strip():
            continue
        try:
            conf_val = float(fields[10])
            confidence = max(0.0, min(100.0, conf_val)) / 100.0
            tokens.append(
                OcrToken(
                    text=fields[11].strip(),
                    confidence=confidence,
                    left=int(fields[6]),
                    top=int(fields[7]),
                    width=int(fields[8]),
                    height=int(fields[9]),
                    block_num=int(fields[2]),
                    paragraph_num=int(fields[3]),
                    line_num=int(fields[4]),
                )
            )
        except ValueError:
            continue
    text = " ".join(t.text for t in tokens)
    return text, tokens


def _load_image_as_bgr(image_input: str | Path | bytes | Image.Image | np.ndarray) -> np.ndarray:
    if isinstance(image_input, np.ndarray):
        return image_input
    if isinstance(image_input, (str, Path)):
        frame = cv2.imread(str(image_input))
        if frame is None:
            raise ValueError(f"Could not read image from path: {image_input}")
        return frame
    if isinstance(image_input, Image.Image):
        rgb = np.array(image_input.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if isinstance(image_input, (bytes, bytearray)):
        arr = np.frombuffer(image_input, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode image from bytes")
        return frame
    raise TypeError(f"Unsupported image input type: {type(image_input)}")


def ocr_image(
    image_input: str | Path | bytes | Image.Image | np.ndarray,
    *,
    region: NormalizedRegion | Sequence[float] | None = None,
    profile: OcrProfile | None = None,
    psm: int | None = None,
    language: str | None = None,
    upscale: int | None = None,
    tsv: bool = False,
    thread_limit: int | None = None,
    reader: Callable[..., Any] | None = None,
) -> str | tuple[str, list[OcrToken]]:
    """Perform CPU OCR on an image or cropped region.

    Args:
        image_input: File path, bytes, PIL Image, or numpy array.
        region: Optional normalized region (x, y, w, h) in [0, 1].
        profile: Optional OcrProfile setting defaults.
        psm: Tesseract page segmentation mode (overrides profile).
        language: Tesseract language code (overrides profile).
        upscale: Scaling multiplier with bicubic interpolation.
        tsv: If True, returns (clean_text, list[OcrToken]) with bounding boxes.
        thread_limit: Optional limit for OMP_THREAD_LIMIT.
        reader: Injectable seam for testing without invoking the Tesseract binary.

    Returns:
        Cleaned text string, or tuple of (clean_text, tokens) if tsv=True.
    """
    prof = profile or OcrProfile()
    effective_psm = psm if psm is not None else prof.page_segmentation_mode
    effective_lang = language if language is not None else prof.ocr_language
    effective_upscale = upscale if upscale is not None else prof.upscale

    frame = _load_image_as_bgr(image_input)

    if region is not None:
        if isinstance(region, (list, tuple)):
            if len(region) != 4:
                raise ValueError("Region tuple must be (x, y, width, height)")
            norm_region = NormalizedRegion(float(region[0]), float(region[1]), float(region[2]), float(region[3]))
        elif isinstance(region, NormalizedRegion):
            norm_region = region
        else:
            raise TypeError(f"Invalid region type: {type(region)}")
        frame = crop_region(frame, norm_region)

    if effective_upscale > 1:
        frame = cv2.resize(
            frame,
            None,
            fx=effective_upscale,
            fy=effective_upscale,
            interpolation=cv2.INTER_CUBIC,
        )

    # Injectable reader seam
    if reader is not None:
        res = reader(frame, prof)
        if tsv:
            if isinstance(res, tuple):
                return clean_text(res[0]), res[1]
            return clean_text(str(res)), []
        if isinstance(res, tuple):
            return clean_text(res[0])
        return clean_text(str(res))

    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        return ("", []) if tsv else ""

    img_bytes = encoded.tobytes()
    if tsv:
        text, tokens = _tesseract_tsv_call(
            img_bytes,
            language=effective_lang,
            psm=effective_psm,
            thread_limit=thread_limit,
        )
        return clean_text(text), tokens

    raw_text = _tesseract_text_call(
        img_bytes,
        language=effective_lang,
        psm=effective_psm,
        thread_limit=thread_limit,
    )
    return clean_text(raw_text)
