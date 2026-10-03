"""perception.service — Loopback HTTP service for CPU OCR and image scoring.

Exposes:
  - GET  /health or /healthz -> 200 OK {"status": "ok", ...}
  - POST /ocr                -> {"ok": true, "text": ..., "tokens": [...]}
  - POST /score              -> {"ok": true, "aesthetic": ..., "clip": ...}
  - POST /v1/chat/completions -> OpenAI-compatible response for HEARTH task_family=document_ocr
  - GET  /v1/models          -> list of available models
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Malformed JSON: {exc}"})
            return

        t0 = time.perf_counter()

        if path == "/ocr":
            try:
                raw_img = body.get("image")
                if not raw_img:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing required 'image' field"})
                    return
                image_input = _decode_image_payload(raw_img)
                region = body.get("region")
                psm = body.get("psm", 6)
                lang = body.get("language", "eng")
                upscale = body.get("upscale", 1)
                tsv = bool(body.get("tsv", False))

                res = ocr_image(
                    image_input,
                    region=region,
                    psm=psm,
                    language=lang,
                    upscale=upscale,
                    tsv=tsv,
                    thread_limit=_thread_limit,
                )
                dt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                if tsv:
                    text, tokens = res  # type: ignore
                    token_dicts = [t.to_dict() for t in tokens]
                    self._send_json(HTTPStatus.OK, {"ok": True, "text": text, "tokens": token_dicts, "duration_ms": dt_ms})
                else:
                    self._send_json(HTTPStatus.OK, {"ok": True, "text": res, "duration_ms": dt_ms})
            except Exception as exc:
                logger.exception("Error in /ocr handler: %s", exc)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        elif path == "/score":
            try:
                raw_img = body.get("image")
                if not raw_img:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing required 'image' field"})
                    return
                image_input = _decode_image_payload(raw_img)
                prompt = body.get("prompt")
                ret_emb = bool(body.get("return_embedding", False))

                scorer = get_scorer()
                res = scorer.score_image(image_input, prompt=prompt, return_embedding=ret_emb)
                res["ok"] = True
                self._send_json(HTTPStatus.OK, res)
            except Exception as exc:
                logger.exception("Error in /score handler: %s", exc)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

