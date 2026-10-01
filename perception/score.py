"""perception.score — CPU image scoring with CLIP ViT-L/14 and LAION aesthetic head.

Provenance:
Ported from Derek's OMEN perception node (work/omen-perception: score_omen.py, score_v2.py):
  - ViT-L-14 / openai / force_quick_gelu=True on CPU PyTorch
  - LAION aesthetic predictor head (aesthetic_l14.pth, 3.7 MB)
  - Prompt adherence cosine (clip score) with prompt-embedding cache
  - Atomic JSON merge writer
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import open_clip
import torch
import torch.nn as nn
from PIL import Image

DEFAULT_WEIGHTS = Path(__file__).resolve().parent / "aesthetic_l14.pth"


class Aesthetic(nn.Module):
    """LAION improved aesthetic predictor head for 768-d CLIP embeddings."""

    def __init__(self, n: int = 768) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(n, 1024),
            nn.Dropout(0.2),
            nn.Linear(1024, 128),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.Dropout(0.1),
            nn.Linear(64, 16),
            nn.Linear(16, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


def load_scores(path: Path | str) -> dict[str, Any]:
    """Load JSON scores file if it exists, else return empty dict."""
    p = Path(path)
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def write_scores_atomic(path: Path | str, scores: Mapping[str, Any]) -> None:
    """Atomic temp-file write and os.replace for scores.json."""
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(prefix=".scores-", dir=str(p.parent))
    try:
        with open(temp_fd, "w", encoding="utf-8") as fh:
            json.dump(dict(scores), fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(temp_path, p)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


class PerceptionScorer:
    """CPU image scorer implementing CLIP ViT-L/14 + LAION aesthetic predictor."""

    def __init__(
        self,
        weights_path: Path | str | None = None,
        device: str = "cpu",
        num_threads: int | None = None,
    ) -> None:
        self.device = device
        if num_threads is not None and num_threads > 0:
            torch.set_num_threads(num_threads)

        weights = Path(weights_path) if weights_path else DEFAULT_WEIGHTS
        if not weights.exists():
            raise FileNotFoundError(f"Aesthetic weights not found at: {weights}")

        # force_quick_gelu=True: OpenAI weights expect QuickGELU
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            "ViT-L-14",
            pretrained="openai",
            force_quick_gelu=True,
            device=self.device,
        )
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer("ViT-L-14")

        self.aesthetic_head = Aesthetic().to(self.device)
        self.aesthetic_head.load_state_dict(
            torch.load(weights, map_location=self.device, weights_only=True)
        )
        self.aesthetic_head.eval()

        self._text_cache: dict[str, torch.Tensor] = {}

    def _prepare_image(self, image_input: str | Path | bytes | Image.Image | np.ndarray) -> torch.Tensor:
        if isinstance(image_input, (str, Path)):
            pil_img = Image.open(str(image_input)).convert("RGB")
        elif isinstance(image_input, (bytes, bytearray)):
            pil_img = Image.open(io.BytesIO(image_input)).convert("RGB")
        elif isinstance(image_input, Image.Image):
            pil_img = image_input.convert("RGB")
        elif isinstance(image_input, np.ndarray):
            # assume BGR from cv2
            rgb = image_input[:, :, ::-1] if image_input.ndim == 3 and image_input.shape[2] == 3 else image_input
            pil_img = Image.fromarray(rgb).convert("RGB")
        else:
            raise TypeError(f"Unsupported image input type: {type(image_input)}")

        return self.preprocess(pil_img).unsqueeze(0).to(self.device)

    def encode_text(self, prompt: str) -> torch.Tensor:
        """Return L2-normalized 768-d text embedding tensor, cached."""
        if prompt in self._text_cache:
            return self._text_cache[prompt]
        tokens = self.tokenizer([prompt]).to(self.device)
        with torch.no_grad():
            emb = self.model.encode_text(tokens)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        self._text_cache[prompt] = emb
        return emb

    def encode_image(self, image_input: str | Path | bytes | Image.Image | np.ndarray) -> torch.Tensor:
        """Return L2-normalized 768-d image embedding tensor."""
        tensor = self._prepare_image(image_input)
        with torch.no_grad():
            emb = self.model.encode_image(tensor)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        return emb

    def score_image(
        self,
        image_input: str | Path | bytes | Image.Image | np.ndarray,
        prompt: str | None = None,
        return_embedding: bool = False,
    ) -> dict[str, Any]:
        """Compute aesthetic score and optional prompt adherence (clip score).

        Returns:
            {
                "aesthetic": float,
                "clip": float | None,
                "embedding": list[float] (if return_embedding=True),
                "duration_ms": float,
            }
        """
        t0 = time.perf_counter()
        img_emb = self.encode_image(image_input)

        with torch.no_grad():
            aesthetic_score = float(self.aesthetic_head(img_emb.float()).item())

        clip_score = None
        if prompt:
            txt_emb = self.encode_text(prompt)
            clip_score = float((img_emb @ txt_emb.T).item())

        dt_ms = (time.perf_counter() - t0) * 1000.0

        res: dict[str, Any] = {
            "aesthetic": round(aesthetic_score, 4),
            "clip": round(clip_score, 4) if clip_score is not None else None,
            "duration_ms": round(dt_ms, 2),
        }
        if return_embedding:
            res["embedding"] = img_emb.squeeze(0).cpu().numpy().tolist()
        return res
