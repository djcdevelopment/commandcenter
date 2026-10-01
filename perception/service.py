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
from typing import Any

from perception.ocr import OcrProfile, ocr_image, tesseract_version
from perception.score import PerceptionScorer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("omen-perception")

# Global scorer instance (lazily initialized on startup)
_scorer: PerceptionScorer | None = None
_thread_limit: int = 8


def get_scorer() -> PerceptionScorer:
    global _scorer
    if _scorer is None:
        logger.info("Initializing PerceptionScorer on CPU (threads=%d)...", _thread_limit)
        _scorer = PerceptionScorer(num_threads=_thread_limit)
    return _scorer


def _decode_image_payload(raw_image: Any) -> bytes | str:
    """Resolve image payload: file path string or base64 decoded bytes."""
    if not isinstance(raw_image, str):
        raise ValueError("Image field must be a string (path or base64)")
    raw_str = raw_image.strip()
    if os.path.exists(raw_str):
        return raw_str
    if raw_str.startswith("data:image/") and ";base64," in raw_str:
        raw_str = raw_str.split(";base64,", 1)[1]
    try:
        return base64.b64decode(raw_str)
    except Exception as exc:
        raise ValueError("Failed to decode base64 image data") from exc


class PerceptionHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/healthz"):
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "backend": "omen-perception",
                    "engine": "cpu",
                    "threads": _thread_limit,
                    "tesseract": tesseract_version(),
                    "images_per_second": 4.15,
                },
            )
        elif path in ("/v1/models", "/models"):
            self._send_json(
                HTTPStatus.OK,
                {
                    "object": "list",
                    "data": [
                        {"id": "tesseract-ocr", "object": "model", "owned_by": "omen-perception"},
                        {"id": "clip-vit-l14", "object": "model", "owned_by": "omen-perception"},
                    ],
                },
            )
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown path: {path}"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_body_json()
        except Exception as exc:
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

        elif path in ("/v1/chat/completions", "/chat/completions"):
            # OpenAI compatible endpoint for HEARTH document_ocr routing
            try:
                messages = body.get("messages", [])
                user_msg = messages[-1].get("content", "") if messages else ""
                # Content may be text string or multipart array
                img_to_ocr: Any = None
                if isinstance(user_msg, list):
                    for part in user_msg:
                        if isinstance(part, dict) and part.get("type") == "image_url":
                            img_to_ocr = part.get("image_url", {}).get("url")
                            break
                elif isinstance(user_msg, str):
                    # Check if user_msg contains image path or base64
                    for token in user_msg.split():
                        clean_tok = token.strip("\"'()[]")
                        if os.path.isfile(clean_tok) and any(clean_tok.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp")):
                            img_to_ocr = clean_tok
                            break

                req_model = body.get("model", "tesseract-ocr")
                if img_to_ocr:
                    resolved = _decode_image_payload(img_to_ocr)
                    if req_model == "clip-vit-l14":
                        scorer = get_scorer()
                        prompt_text = " ".join([t for t in user_msg.split() if t.strip("\"'()[]") != img_to_ocr]).strip()
                        score_res = scorer.score_image(resolved, prompt=prompt_text or None)
                        score_res["ok"] = True
                        reply_text = json.dumps(score_res, indent=2)
                    else:
                        extracted = ocr_image(resolved, psm=6, thread_limit=_thread_limit)
                        reply_text = str(extracted)
                else:
                    reply_text = "No image found in request for perception processing."

                dt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                resp = {
                    "id": f"chatcmpl-perc-{int(time.time()*1000)}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": body.get("model", "tesseract-ocr"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": reply_text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": len(user_msg.split()),
                        "completion_tokens": len(reply_text.split()),
                        "total_tokens": len(user_msg.split()) + len(reply_text.split()),
                    },
                    "duration_ms": dt_ms,
                }
                self._send_json(HTTPStatus.OK, resp)
            except Exception as exc:
                logger.exception("Error in /v1/chat/completions handler: %s", exc)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown endpoint: {path}"})

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)


def run_server(host: str = "127.0.0.1", port: int = 18099, threads: int = 8) -> None:
    global _thread_limit
    _thread_limit = threads
    # Eagerly initialize scorer
    get_scorer()
    server = ThreadingHTTPServer((host, port), PerceptionHandler)
    logger.info("Perception service listening on http://%s:%d (threads=%d)", host, port, threads)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping perception service.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="OMEN Perception HTTP Service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18099)
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, threads=args.threads)


if __name__ == "__main__":
    main()
