"""perception.rubric — Defect-grounded VLM judge rubric and defensive parser.

Provenance:
Ported from Derek's OMEN perception node v2 (work/omen-perception: vlm_judge.py):
  - Defect-grounded rubric (observations, defects, craft, slop, subject_clarity,
    originality, merch_appeal, predicted_verdict, verdict_confidence, refusal_risk)
  - Defensive 3-tier JSON extractor (direct -> regex outermost braces -> regex field salvage)
  - Strict type/range coercion with 'bazaar' neutral fallback
  - Interface stub raising NotImplementedError until task 12 (VLM lane) is deployed
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

RUBRIC_VERSION = "v1"

JUDGE_SYSTEM = (
    "You are a strict, DISCRIMINATING pre-screen judge for a print-on-demand merch shop. Each "
    "image is an AI-generated candidate design. A stricter remote 'gate' later marks it premium / "
    "bazaar / quarantined, flags low-effort 'AI slop', and REFUSES ambiguous/abstract images "
    "(-> bazaar). Most candidates are mediocre; reserve high scores for genuinely clean, "
    "distinctive, sellable work. Differentiate — do NOT give different images the same numbers."
)

JUDGE_INSTRUCTIONS = (
    "Look carefully, then return ONLY a JSON object with EXACTLY these keys:\n"
    '  "observations": string  // one line: medium, subject, any rendered text (quote it + say if '
    "garbled/misspelled), resolution/cleanliness, obvious artifacts\n"
    '  "defects": object of booleans: garbled_text, low_res_or_muddy, compression_or_artifacts, '
    "anatomy_or_geometry_errors, generic_ai_stock_feel\n"
    '  "craft": int 0-100        // start from observations, subtract hard per true defect; garbled '
    "text or low-res alone => craft < 40; only a clean, crisp, professional image earns 80+\n"
    '  "slop": float 0.0-1.0     // generic_ai_stock_feel true => >= 0.6; distinctive/intentional => <= 0.25\n'
    '  "subject_clarity": int 0-100  // legible subject high; abstract/non-representational low\n'
    '  "originality": int 0-100      // derivative/trademarked low; fresh high\n'
    '  "merch_appeal": int 0-100     // would a stranger buy this on a tee/poster\n'
    '  "predicted_verdict": "premium" | "bazaar" | "quarantined"  // quarantined if garbled text/broken; '
    "premium ONLY if clean AND distinctive AND sellable\n"
    '  "verdict_confidence": float 0.0-1.0\n'
    '  "refusal_risk": float 0.0-1.0  // high if abstract/non-representational/ambiguous\n'
    "Be consistent: if a defect is true, craft and the verdict MUST reflect it."
)

_INT_KEYS = ("craft", "subject_clarity", "originality", "merch_appeal")
_FLOAT_KEYS = ("slop", "verdict_confidence", "refusal_risk")
_VERDICTS = ("premium", "bazaar", "quarantined")
_DEFECT_KEYS = (
    "garbled_text",
    "low_res_or_muddy",
    "compression_or_artifacts",
    "anatomy_or_geometry_errors",
    "generic_ai_stock_feel",
)


def extract_rubric_json(text: str) -> tuple[dict[str, Any], str]:
    """Defensively pull a JSON object out of model text without str.index.

    1. try json.loads on the whole response,
    2. else regex the outermost {...} and json.loads that,
    3. else salvage known fields individually via regex so malformed responses still produce data.

    Returns:
        (extracted_dict, method) where method in {'direct', 'braces', 'salvage', 'empty'}.
    """
    if not text or not text.strip():
        return {}, "empty"
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            return loaded, "direct"
    except Exception:
        pass

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            loaded = json.loads(m.group(0))
            if isinstance(loaded, dict):
                return loaded, "braces"
        except Exception:
            pass

    # field-level salvage fallback
    salvaged: dict[str, Any] = {}
    for k in _INT_KEYS:
        mm = re.search(rf'"{k}"\s*:\s*(-?\d+)', text)
        if mm:
            try:
                salvaged[k] = int(mm.group(1))
            except ValueError:
                pass
    for k in _FLOAT_KEYS:
        mm = re.search(rf'"{k}"\s*:\s*(-?\d+(?:\.\d+)?)', text)
        if mm:
            try:
                salvaged[k] = float(mm.group(1))
            except ValueError:
                pass
    mm = re.search(r'"predicted_verdict"\s*:\s*"?(premium|bazaar|quarantined)"?', text, re.I)
    if mm:
        salvaged["predicted_verdict"] = mm.group(1).lower()

    return salvaged, ("salvage" if salvaged else "empty")


def coerce_rubric(obj: dict[str, Any]) -> dict[str, Any]:
    """Clamp and normalize rubric keys into valid, type-safe ranges."""
    out: dict[str, Any] = {}
    for k in _INT_KEYS:
        if k in obj:
            try:
                out[k] = max(0, min(100, int(round(float(obj[k])))))
            except Exception:
                pass
    for k in _FLOAT_KEYS:
        if k in obj:
            try:
                out[k] = max(0.0, min(1.0, float(obj[k])))
            except Exception:
                pass
    v = str(obj.get("predicted_verdict", "")).lower().strip()
    out["predicted_verdict"] = v if v in _VERDICTS else "bazaar"

    if obj.get("observations"):
        out["observations"] = str(obj["observations"])[:300]

    d = obj.get("defects")
    if isinstance(d, dict):
        out["defects"] = {k: bool(d.get(k)) for k in _DEFECT_KEYS if k in d}

    return out


def build_judge_prompt(prompt: str) -> str:
    """Format full prompt with system guidelines and prompt context."""
    return f"{JUDGE_SYSTEM}\n\nThe design's generation prompt was: {prompt!r}\n\n{JUDGE_INSTRUCTIONS}"


def parse_judge_response(raw_text: str, model_name: str = "unspecified") -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse raw VLM response into normalized vlm{} rubric and parse metadata."""
    parsed, how = extract_rubric_json(raw_text)
    vlm = coerce_rubric(parsed)
    vlm["model"] = model_name
    vlm["rubric_version"] = RUBRIC_VERSION
    meta = {
        "parse_method": how,
        "raw_length": len(raw_text),
    }
    return vlm, meta


def judge_image(
    image_input: Any,
    prompt: str = "",
    model_callable: Callable[[str, Any], str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Judge an image using the rubric.

    Args:
        image_input: Image path, bytes, or array.
        prompt: Original generation prompt for context.
        model_callable: Optional vision model function taking (prompt, image) -> str response.

    Raises:
        NotImplementedError: If model_callable is None (pending task 12 VLM deployment).
    """
    if model_callable is None:
        raise NotImplementedError(
            "no local vision model available (pending task 12: deploy local vision lane)"
        )

    full_prompt = build_judge_prompt(prompt)
    raw_response = model_callable(full_prompt, image_input)
    return parse_judge_response(raw_response)
