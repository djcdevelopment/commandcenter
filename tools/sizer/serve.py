"""The request sizer's encoder service (ADR-0050): MiniLM-L6 on the Arrow Lake NPU behind loopback HTTP.

    ~/.venvs/npu/bin/python -m tools.sizer.serve --bind 127.0.0.1 --port 8797 --device NPU --fallback CPU
        [--ir ~/models/minilm-ov/fp16] [--head ~/models/minilm-ov/head.npz] [--cache ~/.cache/ov_npu]

  POST /size   {prompt, system, files, payload_bytes, task_family}
               -> the heuristic's answer, re-binned by the linear head when one is loaded
                  (head over [384-d embedding, numeric features]); `source` says "npu" or "cpu"
                  by the device that ran the encoder, `heuristic` when no head is loaded.
  GET  /health -> {ok, device, ir_sha256, head_sha256, compile_cached, p50_ms, calls, npu_busy_us}

The door's client (hearth/sizer/client.py) gives this ~30 ms; anything slower falls back to the
heuristic on the door side and the ledger says so. The service never reads the ledger and never
routes: it answers a question about one request's shape.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hearth.sizer.heuristic import BIN_EDGE, BIN_ORDER, LONG_BINS, size_heuristic  # noqa: E402

SYSFS_BUSY = Path("/sys/bus/pci/devices/0000:00:0b.0/npu_busy_time_us")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def numeric_features(answer: dict) -> list[float]:
    """The heuristic's numbers as head inputs: log prompt tokens, log files bytes, file count, its own bin."""
    import math
    return [math.log1p(answer.get("prompt_tokens") or 0), math.log1p(answer.get("files_bytes") or 0),
            float(answer.get("files") or 0), float(BIN_ORDER.index(answer.get("output_class", "s"))),
            float(answer.get("confidence") or 0.0)]


class Encoder:
    def __init__(self, ir: Path, device: str, fallback: str | None, cache: Path, seq: int = 256) -> None:
        import openvino as ov
        from transformers import AutoTokenizer
        self.core = ov.Core()
        self.tok = AutoTokenizer.from_pretrained(ir, local_files_only=True)
        self.seq = seq
        self.ir_sha = sha256_file(ir / "openvino_model.xml")
        model = self.core.read_model(str(ir / "openvino_model.xml"))
        self.names = {i.get_any_name() for i in model.inputs}
        self.device = None
        self.compile_s = None
        for dev in [device] + ([fallback] if fallback else []):
            if dev not in self.core.available_devices:
                continue
            cdir = cache / dev.lower(); cdir.mkdir(parents=True, exist_ok=True)
            t0 = time.perf_counter()
            try:
                self.compiled = self.core.compile_model(model, dev, {"PERFORMANCE_HINT": "LATENCY", "CACHE_DIR": str(cdir)})
            except Exception as exc:  # noqa: BLE001
                print(f"compile on {dev} failed: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr, flush=True)
                continue
            self.compile_s = round(time.perf_counter() - t0, 3)
            self.device = dev
            break
        if self.device is None:
            raise RuntimeError(f"no device compiled the encoder (wanted {device}, fallback {fallback}; available {self.core.available_devices})")
        self.out = self.compiled.output(0)
        self.lock = threading.Lock()

    def embed(self, text: str):
        import numpy as np
        enc = self.tok([text], padding="max_length", max_length=self.seq, truncation=True, return_tensors="np")
        feed = {k: v for k, v in enc.items() if k in self.names}
        with self.lock:
            hidden = self.compiled(feed)[self.out]
        m = enc["attention_mask"][..., None].astype(hidden.dtype)
        emb = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)
        return (emb / np.linalg.norm(emb, axis=1, keepdims=True))[0]


class Head:
    """A linear head over [embedding ++ numeric features] -> bin logits, saved by tools/sizer/train_head.py."""

    def __init__(self, path: Path) -> None:
        import numpy as np
        d = np.load(path)
        self.W, self.b = d["W"], d["b"]
        self.mu, self.sd = d["mu"], d["sd"]
        self.sha = sha256_file(path)

    def predict(self, emb, feats: list[float]) -> tuple[str, float]:
        import numpy as np
        x = np.concatenate([emb, np.asarray(feats, dtype=np.float32)])
        x = (x - self.mu) / self.sd
        z = x @ self.W + self.b
        z = z - z.max(); p = np.exp(z); p /= p.sum()
        i = int(p.argmax())
        return BIN_ORDER[i], float(p[i])


STATE = {"encoder": None, "head": None, "lat": [], "calls": 0, "started": time.time()}


def size_with_encoder(body: dict) -> dict:
    t0 = time.perf_counter()
    prompt = body.get("prompt") or ""
    answer = size_heuristic(prompt, system=body.get("system"), files=body.get("files"),
                            payload_bytes=body.get("payload_bytes"), task_family=body.get("task_family"))
    enc: Encoder = STATE["encoder"]
    head: Head | None = STATE["head"]
    if enc is not None and head is not None:
        emb = enc.embed(prompt[:4000])
        bin_name, p = head.predict(emb, numeric_features(answer))
        answer["heuristic_class"] = answer["output_class"]
        answer["output_class"] = bin_name
        answer["expected_output_tokens"] = BIN_EDGE[bin_name]
        answer["confidence"] = round(p, 3)
        answer["source"] = enc.device.lower().split(".")[0]
        answer["signals"] = list(answer.get("signals", [])) + [f"head:{bin_name}:{p:.2f}"]
        declared = answer.get("declared_family")
        answer["task_family"] = "tool_long_output" if declared == "tool_execution" and bin_name in LONG_BINS else declared
    elif enc is not None:
        # No head yet: the encoder still runs so the service's latency and the NPU's cost are real,
        # but the heuristic's bin stands and `source` says so.
        enc.embed(prompt[:4000])
        answer["source"] = "heuristic"
        answer["encoder_device"] = enc.device
    answer["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    STATE["lat"].append(answer["ms"]); STATE["calls"] += 1
    if len(STATE["lat"]) > 2000:
        del STATE["lat"][:1000]
    return answer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/health"):
            enc: Encoder = STATE["encoder"]
            lat = sorted(STATE["lat"][-500:])
            busy = None
            try:
                busy = int(SYSFS_BUSY.read_text().strip())
            except (OSError, ValueError):
                pass
            self._json(200, {"ok": enc is not None, "device": enc.device if enc else None, "compile_s": enc.compile_s if enc else None,
                             "ir_sha256": enc.ir_sha if enc else None, "head_sha256": STATE["head"].sha if STATE["head"] else None,
                             "seq": enc.seq if enc else None, "calls": STATE["calls"],
                             "p50_ms": (statistics.median(lat) if lat else None), "npu_busy_us": busy,
                             "uptime_s": round(time.time() - STATE["started"])})
            return
        self._json(404, {"error": "unknown path"})

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/size"):
            self._json(404, {"error": "unknown path"}); return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
        except (ValueError, OSError) as exc:
            self._json(400, {"error": f"bad request: {type(exc).__name__}"}); return
        try:
            self._json(200, size_with_encoder(body))
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bind", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8797)
    ap.add_argument("--device", default="NPU"); ap.add_argument("--fallback", default="CPU")
    ap.add_argument("--ir", type=Path, default=Path.home() / "models" / "minilm-ov" / "fp16")
    ap.add_argument("--head", type=Path, default=Path.home() / "models" / "minilm-ov" / "head.npz")
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "ov_npu")
    ap.add_argument("--seq", type=int, default=256)
    ap.add_argument("--no-encoder", action="store_true", help="serve the heuristic only (no OpenVINO)")
    args = ap.parse_args()
    if not args.no_encoder:
        STATE["encoder"] = Encoder(args.ir, args.device, args.fallback or None, args.cache, args.seq)
        if args.head.exists():
            STATE["head"] = Head(args.head)
        enc = STATE["encoder"]
        print(f"sizer encoder on {enc.device} (compile {enc.compile_s}s, ir {enc.ir_sha[:12]}, head {'yes' if STATE['head'] else 'none'})", flush=True)
    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"omen-sizer listening on http://{args.bind}:{args.port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
