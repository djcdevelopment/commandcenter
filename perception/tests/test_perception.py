"""Tests for perception module: OCR, image scoring, and rubric parsing."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from perception.ocr import (
    NormalizedRegion,
    OcrProfile,
    OcrToken,
    clean_text,
    crop_region,
    ocr_image,
    tesseract_version,
)
from perception.rubric import (
    RUBRIC_VERSION,
    build_judge_prompt,
    coerce_rubric,
    extract_rubric_json,
    judge_image,
    parse_judge_response,
)
from perception.score import (
    Aesthetic,
    PerceptionScorer,
    load_scores,
    write_scores_atomic,
)


class PerceptionOcrTests(unittest.TestCase):
    def test_clean_text(self) -> None:
        self.assertEqual(clean_text("Hello\x0c World \n\n Test"), "Hello World Test")
        self.assertEqual(clean_text("   "), "")

    def test_normalized_region(self) -> None:
        r = NormalizedRegion(0.1, 0.2, 0.3, 0.4)
        self.assertEqual((r.x, r.y, r.width, r.height), (0.1, 0.2, 0.3, 0.4))
        with self.assertRaises(ValueError):
            NormalizedRegion(-0.1, 0.2, 0.3, 0.4)
        with self.assertRaises(ValueError):
            NormalizedRegion(0.8, 0.2, 0.3, 0.4)  # 0.8 + 0.3 > 1

    def test_crop_region(self) -> None:
        img = np.zeros((100, 200, 3), dtype=np.uint8)
        img[20:60, 40:100] = 255
        region = NormalizedRegion(x=0.2, y=0.2, width=0.3, height=0.4)
        cropped = crop_region(img, region)
        self.assertEqual(cropped.shape, (40, 60, 3))
        self.assertTrue(np.all(cropped == 255))

    def test_reader_seam(self) -> None:
        img = np.ones((50, 50, 3), dtype=np.uint8)
        called = []

        def mock_reader(frame: np.ndarray, profile: OcrProfile) -> str:
            called.append((frame.shape, profile.page_segmentation_mode))
            return "MOCK OCR TEXT"

        result = ocr_image(img, psm=6, reader=mock_reader)
        self.assertEqual(result, "MOCK OCR TEXT")
        self.assertEqual(len(called), 1)

    def test_reader_seam_tsv(self) -> None:
        img = np.ones((50, 50, 3), dtype=np.uint8)
        tokens = [OcrToken(text="HELLO", confidence=0.95, left=0, top=0, width=50, height=50)]

        def mock_tsv_reader(_frame: np.ndarray, _profile: OcrProfile) -> tuple[str, list[OcrToken]]:
            return "HELLO", tokens

        text, parsed_tokens = ocr_image(img, tsv=True, reader=mock_tsv_reader)
        self.assertEqual(text, "HELLO")
        self.assertEqual(len(parsed_tokens), 1)
        self.assertEqual(parsed_tokens[0].text, "HELLO")

    def test_tesseract_ocr_synthetic(self) -> None:
        if not tesseract_version():
            self.skipTest("Tesseract not installed on host")
        img = np.ones((120, 500, 3), dtype=np.uint8) * 255
        cv2.putText(img, "MARAUDER 8", (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 4)

        text = ocr_image(img, psm=6)
        self.assertIn("MARAUDER", text)
        self.assertIn("8", text)

        tsv_text, tokens = ocr_image(img, psm=6, tsv=True)
        self.assertIn("MARAUDER", tsv_text)
        self.assertGreaterEqual(len(tokens), 2)
        words = [t.text for t in tokens]
        self.assertIn("MARAUDER", words)


class PerceptionScorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.weights = Path(__file__).resolve().parents[1] / "aesthetic_l14.pth"
        if not cls.weights.exists():
            raise unittest.SkipTest("aesthetic_l14.pth not present")
        cls.scorer = PerceptionScorer(weights_path=cls.weights, num_threads=4)
        cls.data_dir = Path("/mnt/omen-c-read/work/omen-perception/data")

    def test_reference_images_fidelity(self) -> None:
        if not self.data_dir.exists():
            self.skipTest("Windows mount /mnt/omen-c-read not present")

        # 1. Ecola State Park (top aesthetic)
        ecola_id = "orev2_041_0285bb49"
        ecola_img = self.data_dir / "img" / f"{ecola_id}.webp"
        ecola_meta = json.loads((self.data_dir / "meta" / f"{ecola_id}.json").read_text())
        res_ecola = self.scorer.score_image(ecola_img, prompt=ecola_meta["prompt"], return_embedding=True)

        self.assertAlmostEqual(res_ecola["aesthetic"], 7.08, delta=0.02)
        self.assertAlmostEqual(res_ecola["clip"], 0.2508, delta=0.02)
        self.assertEqual(len(res_ecola["embedding"]), 768)

        # 2. Crater Lake (bottom aesthetic)
        crater_id = "orev2_003_daee1dca"
        crater_img = self.data_dir / "img" / f"{crater_id}.webp"
        crater_meta = json.loads((self.data_dir / "meta" / f"{crater_id}.json").read_text())
        res_crater = self.scorer.score_image(crater_img, prompt=crater_meta["prompt"])

        self.assertAlmostEqual(res_crater["aesthetic"], 5.65, delta=0.02)
        self.assertAlmostEqual(res_crater["clip"], 0.2781, delta=0.02)

    def test_atomic_scores_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scores_path = Path(tmp) / "scores.json"
            data = {"job_1": {"aesthetic": 7.08, "clip": 0.25}}
            write_scores_atomic(scores_path, data)
            loaded = load_scores(scores_path)
            self.assertEqual(loaded, data)


class PerceptionRubricTests(unittest.TestCase):
    def test_unimplemented_vision_model_raises(self) -> None:
        with self.assertRaises(NotImplementedError) as ctx:
            judge_image("dummy_path.webp")
        self.assertIn("task 12", str(ctx.exception))

    def test_direct_json_extract(self) -> None:
        raw = json.dumps({"craft": 85, "slop": 0.15, "predicted_verdict": "premium"})
        vlm, meta = parse_judge_response(raw)
        self.assertEqual(vlm["craft"], 85)
        self.assertEqual(vlm["slop"], 0.15)
        self.assertEqual(vlm["predicted_verdict"], "premium")
        self.assertEqual(meta["parse_method"], "direct")

    def test_braces_json_extract(self) -> None:
        raw = "Analysis:\n```json\n{\"craft\": 70, \"slop\": 0.4, \"predicted_verdict\": \"bazaar\"}\n```\nDone."
        vlm, meta = parse_judge_response(raw)
        self.assertEqual(vlm["craft"], 70)
        self.assertEqual(vlm["slop"], 0.4)
        self.assertEqual(vlm["predicted_verdict"], "bazaar")
        self.assertEqual(meta["parse_method"], "braces")

    def test_salvage_json_extract(self) -> None:
        raw = "Malformed response: \"craft\": 65 and \"slop\": 0.6 but missing closing braces"
        vlm, meta = parse_judge_response(raw)
        self.assertEqual(vlm["craft"], 65)
        self.assertEqual(vlm["slop"], 0.6)
        self.assertEqual(vlm["predicted_verdict"], "bazaar")
        self.assertEqual(meta["parse_method"], "salvage")

    def test_empty_json_extract_fallback(self) -> None:
        vlm, meta = parse_judge_response("")
        self.assertEqual(vlm["predicted_verdict"], "bazaar")
        self.assertEqual(meta["parse_method"], "empty")


if __name__ == "__main__":
    unittest.main()
