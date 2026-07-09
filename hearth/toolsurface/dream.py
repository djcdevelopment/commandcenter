"""HEARTH tool: dream — the guard dog's off-duty spell (Watchfire · art mode).

The third face of the mechnet-watchdog. It patrols (``patrol`` — its eyes), it
heals (``masters_pet`` — its hands), and when the mechs are quiet and the cards are
free (art mode), it **dreams** — renders an image on the fleet's idle GPU. The
guard dog is the dreamer, not a generic caller: every dream is ledgered under the
``mechnet-watchdog`` identity.

Mechanics (the path proven live 2026-07-04): drive the ComfyUI SD3.5-Large backend
on AM4 over SSH — a model-only checkpoint plus a TripleCLIPLoader (clip_g/clip_l/
t5xxl) — thermal-guarded (interrupt at 95C, Derek's policy), then copy the PNG back
to OMEN under ``hearth/var/dreams/`` and return its local path.

Fail closed: if ComfyUI is unreachable (art mode not active / cards held by oxen),
the dream returns ``ok:false`` with a clear reason. It does NOT free a card itself —
the mode transition is the arbiter's job (``fleet.mode_arbiter``, Stream C).
"""
from __future__ import annotations

import base64
import json
import random
import subprocess
from pathlib import Path
from typing import Callable, Optional

AM4_SSH = "derek@192.168.12.233"  # LAN, not tailnet (ADR-0014)
SSH_TIMEOUT_PAD_S = 45
THERMAL_LIMIT_C = 95
DREAMS_DIR = Path(__file__).resolve().parents[1] / "var" / "dreams"
DREAMER_CALLER = {"id": "mechnet-watchdog", "runner_class": "human", "node": "am4"}
DEFAULT_NEGATIVE = ("blurry, low quality, deformed, extra limbs, mutated, text, watermark, "
                    "signature, oversaturated, jpeg artifacts")

# Static script run on AM4's python3; params arrive base64 in argv[1] so nothing is
# shell-quoted. Emits one line: "RESULT <json>". Builds the SD3.5 + TripleCLIP graph
# proven to work on this box, and enforces the 95C thermal interrupt.
_GEN_SCRIPT = r'''
import sys, base64, json, time, glob, urllib.request, urllib.error
P = json.loads(base64.b64decode(sys.argv[1]))
BASE = "http://127.0.0.1:8188"

def temp():
    ts = []
    for f in glob.glob("/sys/class/hwmon/hwmon5/temp*_input") + glob.glob("/sys/class/hwmon/hwmon6/temp*_input"):
        try: ts.append(int(open(f).read()) // 1000)
        except Exception: pass
    return max(ts) if ts else -1

def out(d): print("RESULT " + json.dumps(d))

g = {
 "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd3.5_large.safetensors"}},
 "9": {"class_type": "TripleCLIPLoader", "inputs": {"clip_name1": "clip_g.safetensors", "clip_name2": "clip_l.safetensors", "clip_name3": "t5xxl_fp16.safetensors"}},
 "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 3.0}},
 "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["9", 0], "text": P["prompt"]}},
 "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["9", 0], "text": P["negative"]}},
 "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": P["width"], "height": P["height"], "batch_size": 1}},
 "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0], "seed": P["seed"], "steps": P["steps"], "cfg": P["cfg"], "sampler_name": "euler", "scheduler": "sgm_uniform", "denoise": 1.0}},
 "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
 "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": P["prefix"]}},
}
try:
    data = json.dumps({"prompt": g, "client_id": "hearth-dream"}).encode()
    req = urllib.request.Request(BASE + "/prompt", data=data, headers={"Content-Type": "application/json"})
    pid = json.load(urllib.request.urlopen(req, timeout=25))["prompt_id"]
except urllib.error.HTTPError as e:
    out({"ok": False, "error": "submit rejected: " + e.read().decode()[:300]}); sys.exit(0)
except Exception as e:
    out({"ok": False, "error": "comfyui unreachable: " + str(e)[:200]}); sys.exit(0)
peak = temp()
for _ in range(int(P["timeout_s"] // 3)):
    time.sleep(3); t = temp(); peak = max(peak, t)
    if t >= 95:
        try: urllib.request.urlopen(urllib.request.Request(BASE + "/interrupt", data=b""), timeout=10)
        except Exception: pass
        out({"ok": False, "error": "thermal throttle at %dC" % t, "peak_temp_c": peak}); sys.exit(0)
    try: h = json.load(urllib.request.urlopen(BASE + "/history/" + pid, timeout=10))
    except Exception: continue
    if pid in h:
        e = h[pid]
        if e.get("outputs"):
            imgs = [im["filename"] for _n, o in e["outputs"].items() for im in o.get("images", [])]
            out({"ok": True, "filename": imgs[0] if imgs else None, "peak_temp_c": peak}); sys.exit(0)
        if e.get("status", {}).get("status_str") == "error":
            out({"ok": False, "error": "exec error: " + json.dumps(e["status"].get("messages", []))[:300], "peak_temp_c": peak}); sys.exit(0)
out({"ok": False, "error": "timeout", "peak_temp_c": peak})
'''


def _run_ssh(remote_command: str, timeout_s: float,
             runner: Optional[Callable[..., subprocess.CompletedProcess]] = None
             ) -> "tuple[Optional[str], Optional[str]]":
    active = runner or subprocess.run
    try:
        done = active(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout=15", AM4_SSH, remote_command],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if done.returncode != 0:
        return None, f"ssh exit {done.returncode}: {(done.stderr or '').strip()[:300]}"
    return done.stdout, None


def _parse_result(stdout: Optional[str]) -> dict:
    for line in (stdout or "").splitlines():
        if line.startswith("RESULT "):
            try:
                return json.loads(line[len("RESULT "):])
            except json.JSONDecodeError:
                break
    return {"ok": False, "error": "no parseable RESULT from am4"}


def _fetch_image(filename: str, runner=None) -> "tuple[Optional[str], Optional[str]]":
    """Copy the PNG from AM4 to OMEN (base64 over SSH). Returns (local_path, error)."""
    stdout, error = _run_ssh(f"base64 -w0 ~/ComfyUI/output/{filename}", 60, runner=runner)
    if error is not None:
        return None, f"fetch failed: {error}"
    try:
        raw = base64.b64decode((stdout or "").strip())
    except Exception as exc:
        return None, f"fetch decode failed: {exc}"
    DREAMS_DIR.mkdir(parents=True, exist_ok=True)
    local = DREAMS_DIR / filename
    local.write_bytes(raw)
    return str(local), None


def _ledger_dream(prompt: str, seed: int, result: dict) -> Optional[str]:
    """Record the dream under the mechnet-watchdog identity. Best-effort."""
    try:
        from hearth.kernel.ledger import Ledger, new_event
        return Ledger().append(new_event(
            DREAMER_CALLER, "mechnet_watchdog.dream",
            args={"prompt": prompt[:200], "seed": seed},
            result={"ok": bool(result.get("ok")), "filename": result.get("filename"),
                    "peak_temp_c": result.get("peak_temp_c")},
            ok=bool(result.get("ok")),
            error=None if result.get("ok") else result.get("error"),
        ))
    except Exception:
        return None


def dream(prompt: str, negative: str = "", width: int = 1024, height: int = 1024,
          steps: int = 28, cfg: float = 4.5, seed: int = -1, timeout_s: int = 180) -> dict:
    """The guard dog dreams: render an image on the fleet's idle GPU (art mode).

    Drives ComfyUI SD3.5 on AM4, thermal-guarded (interrupts at 95C), copies the
    result to ``hearth/var/dreams/`` and returns its local path. Fail-closed if
    art mode isn't active (ComfyUI unreachable). Ledgered as ``mechnet-watchdog``.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if seed is None or seed < 0:
        seed = random.randint(1, 2_000_000_000)
    params = {"prompt": prompt, "negative": negative or DEFAULT_NEGATIVE,
              "width": int(width), "height": int(height), "steps": int(steps),
              "cfg": float(cfg), "seed": int(seed), "prefix": "dream",
              "timeout_s": int(timeout_s)}
    script_b64 = base64.b64encode(_GEN_SCRIPT.encode("utf-8")).decode("ascii")
    params_b64 = base64.b64encode(json.dumps(params).encode("utf-8")).decode("ascii")
    remote = f"echo {script_b64} | base64 -d | python3 - {params_b64}"

    stdout, error = _run_ssh(remote, timeout_s + SSH_TIMEOUT_PAD_S)
    if error is not None:
        result = {"ok": False, "error": f"art mode unreachable: {error}"}
        _ledger_dream(prompt, seed, result)
        return {**result, "seed": seed}

    result = _parse_result(stdout)
    if not result.get("ok"):
        _ledger_dream(prompt, seed, result)
        return {**result, "seed": seed}

    local_path, fetch_err = _fetch_image(result["filename"])
    if fetch_err is not None:
        result = {"ok": False, "error": fetch_err, "filename": result.get("filename"),
                  "peak_temp_c": result.get("peak_temp_c")}
        _ledger_dream(prompt, seed, result)
        return {**result, "seed": seed}

    event_id = _ledger_dream(prompt, seed, result)
    return {"ok": True, "dreamer": "mechnet-watchdog", "local_path": local_path,
            "filename": result["filename"], "peak_temp_c": result.get("peak_temp_c"),
            "seed": seed, "prompt": prompt, "ledger_event_id": event_id}


def get_tools() -> "list[Callable]":
    return [dream]
