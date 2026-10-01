"""Benchmark perception throughput and GPU seat interference under multitasking.

Measures:
  1. omen-dense-27b and omen-vllm alone (tok/s).
  2. Perception scoring and OCR alone (s/image).
  3. Perception alongside both GPU seats running local_generate concurrently,
     across thread settings (e.g. 2, 4, 8 threads), sampled twice.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

# Ensure hearth backends and token are configured
os.environ.setdefault("HEARTH_BACKENDS", "/home/derek/hearth-production/backends-linux.toml")
os.environ.setdefault("OMEN_ARC_TOKEN", "Zi6yqAWJ74Req4rLWdYndTM8QyP4uhvD2C6qCUGN7_uzBTd2FqIRa2lxjcKCnXzU")

from hearth.toolsurface.inference import local_generate
from perception.ocr import ocr_image
from perception.score import PerceptionScorer

DATA_DIR = Path("/mnt/omen-c-read/work/omen-perception/data")
WEIGHTS = Path(__file__).resolve().parent / "aesthetic_l14.pth"

PROMPT_DENSE = "Write a comprehensive history of the Pacific Northwest lighthouses in the 19th century, in chronological paragraphs."
PROMPT_MOE = "Explain the operating principles of steam locomotives, diesel-electric engines, and modern electric high-speed trains in detail."


def run_gpu_dense(max_tokens: int = 250) -> dict:
    t0 = time.perf_counter()
    res = local_generate(prompt=PROMPT_DENSE, backend="omen-dense-27b", max_tokens=max_tokens)
    dt = time.perf_counter() - t0
    toks = res.get("tokens_out", 0)
    tok_s = toks / dt if dt > 0 else 0
    return {"backend": "omen-dense-27b", "toks": toks, "duration_s": dt, "tok_s": tok_s}


def run_gpu_moe(max_tokens: int = 400) -> dict:
    t0 = time.perf_counter()
    res = local_generate(prompt=PROMPT_MOE, backend="omen-vllm", max_tokens=max_tokens)
    dt = time.perf_counter() - t0
    toks = res.get("tokens_out", 0)
    tok_s = toks / dt if dt > 0 else 0
    return {"backend": "omen-vllm", "toks": toks, "duration_s": dt, "tok_s": tok_s}


def run_perception_scoring(scorer: PerceptionScorer, image_paths: list[Path]) -> dict:
    t0 = time.perf_counter()
    for p in image_paths:
        scorer.score_image(p)
    dt = time.perf_counter() - t0
    n = len(image_paths)
    return {"op": "score", "count": n, "duration_s": dt, "s_per_img": dt / n if n else 0}


def run_perception_ocr(image_paths: list[Path], thread_limit: int = 4) -> dict:
    t0 = time.perf_counter()
    for p in image_paths:
        ocr_image(p, psm=6, thread_limit=thread_limit)
    dt = time.perf_counter() - t0
    n = len(image_paths)
    return {"op": "ocr", "count": n, "duration_s": dt, "s_per_img": dt / n if n else 0}


def main() -> None:
    # Prepare test images
    img_dir = DATA_DIR / "img"
    all_imgs = sorted(list(img_dir.glob("orev2_*.webp")))[:12]
    if len(all_imgs) < 10:
        print("Error: Need at least 10 images from data/img")
        return

    print(f"Loaded {len(all_imgs)} test images.")

    # 1. Measure GPU Baselines Alone (Sample 1 & 2)
    print("\n--- 1. GPU Seats Alone (Baseline) ---")
    gpu_dense_alone_1 = run_gpu_dense()
    gpu_dense_alone_2 = run_gpu_dense()
    avg_dense_baseline = (gpu_dense_alone_1["tok_s"] + gpu_dense_alone_2["tok_s"]) / 2.0
    print(f"omen-dense-27b alone: run 1 = {gpu_dense_alone_1['tok_s']:.2f} tok/s, run 2 = {gpu_dense_alone_2['tok_s']:.2f} tok/s -> avg = {avg_dense_baseline:.2f} tok/s")

    gpu_moe_alone_1 = run_gpu_moe()
    gpu_moe_alone_2 = run_gpu_moe()
    avg_moe_baseline = (gpu_moe_alone_1["tok_s"] + gpu_moe_alone_2["tok_s"]) / 2.0
    print(f"omen-vllm alone: run 1 = {gpu_moe_alone_1['tok_s']:.2f} tok/s, run 2 = {gpu_moe_alone_2['tok_s']:.2f} tok/s -> avg = {avg_moe_baseline:.2f} tok/s")

    # Test thread configurations
    for threads in (8, 4, 2):
        print(f"\n==========================================")
        print(f"Evaluating Perception with threads={threads}")
        print(f"==========================================")

        scorer = PerceptionScorer(weights_path=WEIGHTS, num_threads=threads)

        # Perception Alone
        score_alone_1 = run_perception_scoring(scorer, all_imgs)
        score_alone_2 = run_perception_scoring(scorer, all_imgs)
        avg_score_alone = (score_alone_1["s_per_img"] + score_alone_2["s_per_img"]) / 2.0
        print(f"Scoring alone (threads={threads}): {score_alone_1['s_per_img']:.3f} s/img, {score_alone_2['s_per_img']:.3f} s/img -> avg {avg_score_alone:.3f} s/img")

        ocr_alone_1 = run_perception_ocr(all_imgs, thread_limit=threads)
        ocr_alone_2 = run_perception_ocr(all_imgs, thread_limit=threads)
        avg_ocr_alone = (ocr_alone_1["s_per_img"] + ocr_alone_2["s_per_img"]) / 2.0
        print(f"OCR alone (threads={threads}): {ocr_alone_1['s_per_img']:.3f} s/img, {ocr_alone_2['s_per_img']:.3f} s/img -> avg {avg_ocr_alone:.3f} s/img")

        # Multitasking Alongside: Both GPU seats running + Perception running concurrently
        print(f"\nRunning concurrent multitasking trials (threads={threads})...")
        multitask_results = []
        for sample in (1, 2):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                f_dense = executor.submit(run_gpu_dense, 300)
                f_moe = executor.submit(run_gpu_moe, 500)
                time.sleep(0.1)  # allow GPU requests to begin decoding
                f_score = executor.submit(run_perception_scoring, scorer, all_imgs)
                f_ocr = executor.submit(run_perception_ocr, all_imgs, threads)

                res_dense = f_dense.result()
                res_moe = f_moe.result()
                res_score = f_score.result()
                res_ocr = f_ocr.result()

            dense_delta = (res_dense["tok_s"] - avg_dense_baseline) / avg_dense_baseline * 100.0
            moe_delta = (res_moe["tok_s"] - avg_moe_baseline) / avg_moe_baseline * 100.0

            multitask_results.append({
                "sample": sample,
                "dense_tok_s": res_dense["tok_s"],
                "dense_delta_pct": dense_delta,
                "moe_tok_s": res_moe["tok_s"],
                "moe_delta_pct": moe_delta,
                "score_s_per_img": res_score["s_per_img"],
                "ocr_s_per_img": res_ocr["s_per_img"],
            })
            print(f"Sample {sample}:")
            print(f"  omen-dense-27b: {res_dense['tok_s']:.2f} tok/s ({dense_delta:+.1f}%)")
            print(f"  omen-vllm:      {res_moe['tok_s']:.2f} tok/s ({moe_delta:+.1f}%)")
            print(f"  score s/img:    {res_score['s_per_img']:.3f} s")
            print(f"  ocr s/img:      {res_ocr['s_per_img']:.3f} s")


if __name__ == "__main__":
    main()
