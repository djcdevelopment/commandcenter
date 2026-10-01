"""perception — Local CPU perception lane (OCR + image scoring).

Serves text-from-image (Tesseract) and image scoring (CLIP ViT-L/14 + aesthetic head)
on CPU beside the GPU seats.
"""

from .ocr import OcrProfile, OcrToken, clean_text, crop_region, ocr_image, tesseract_version
from .rubric import (
    RUBRIC_VERSION,
    build_judge_prompt,
    coerce_rubric,
    extract_rubric_json,
    judge_image,
    parse_judge_response,
)
from .score import Aesthetic, PerceptionScorer, load_scores, write_scores_atomic

__all__ = [
    "Aesthetic",
    "OcrProfile",
    "OcrToken",
    "PerceptionScorer",
    "RUBRIC_VERSION",
    "build_judge_prompt",
    "clean_text",
    "coerce_rubric",
    "crop_region",
    "extract_rubric_json",
    "judge_image",
    "load_scores",
    "ocr_image",
    "parse_judge_response",
    "tesseract_version",
    "write_scores_atomic",
]
