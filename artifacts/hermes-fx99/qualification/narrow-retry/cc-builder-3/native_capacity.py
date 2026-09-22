import datetime

ALIAS = "am4-dense-27b"
PHYSICAL = "am4:127.0.0.1:18090"
MODEL_BASE = "Qwen3.8-27B-Q4_K_M.gguf"
CTX = 131072
SLOTS = 1
MIN_AGE = -5
MAX_AGE = 30


def _fail(observed_at, reason):
    return {"ready": False, "parallel_slots": 0, "observed_at": observed_at,
            "reason": reason, "gpu_placed": None}


def _parse(ts):
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def normalize_am4_native(payload, observed_at, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    obs = _parse(observed_at)
    if obs is None:
        return _fail(observed_at, "invalid observed_at timestamp")
    age = (now - obs).total_seconds()
    if age < MIN_AGE or age > MAX_AGE:
        return _fail(observed_at, "stale or future timestamp")
    if not isinstance(payload, dict) or payload.get("all_ready") is not True:
        return _fail(observed_at, "payload not all_ready")
    aliases = payload.get("aliases")
    if not isinstance(aliases, list):
        return _fail(observed_at, "missing aliases list")
    rows = [a for a in aliases if isinstance(a, dict) and a.get("alias") == ALIAS]
    if len(rows) != 1:
        return _fail(observed_at, "selected alias absent or conflicting duplicates")
    r = rows[0]
    if r.get("ready") is not True:
        return _fail(observed_at, "alias not ready")
    if r.get("status") != 200:
        return _fail(observed_at, "status not 200")
    if type(r.get("context_length")) is not int or r["context_length"] != CTX:
        return _fail(observed_at, "wrong context_length")
    if type(r.get("parallel_slots")) is not int or r["parallel_slots"] != SLOTS:
        return _fail(observed_at, "wrong parallel_slots")
    if r.get("physical_resource") != PHYSICAL:
        return _fail(observed_at, "wrong physical_resource")
    model = r.get("model")
    if not isinstance(model, str) or model.rsplit("/", 1)[-1] != MODEL_BASE:
        return _fail(observed_at, "wrong model")
    return {"ready": True, "parallel_slots": SLOTS, "observed_at": observed_at,
            "model": model, "context_length": CTX, "physical_resource": PHYSICAL,
            "gpu_placed": None,
            "reason": "HTTP readiness does not prove GPU placement"}
