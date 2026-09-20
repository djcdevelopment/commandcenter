import datetime

ALIAS = "am4-dense-27b"
RESOURCE = "am4:127.0.0.1:18090"
MODEL_BASE = "Qwen3.8-27B-Q4_K_M.gguf"
CTX = 131072
SLOTS = 1
MIN_AGE = -5
MAX_AGE = 30


def _fail(observed_at, reason):
    return {"ready": False, "parallel_slots": 0, "observed_at": observed_at,
            "reason": reason, "gpu_placed": None}


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        return None
    return ts


def normalize_am4_native(payload, observed_at, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    obs = _parse_ts(observed_at)
    if obs is None:
        return _fail(observed_at, "observed_at is not a timezone-aware ISO8601 timestamp")
    age = (now - obs).total_seconds()
    if age < MIN_AGE or age > MAX_AGE:
        return _fail(observed_at, "observed_at is outside [-5,+30] second age window")
    if not isinstance(payload, dict):
        return _fail(observed_at, "payload is not a mapping")
    if payload.get("all_ready") is not True:
        return _fail(observed_at, "all_ready is not True")
    aliases = payload.get("aliases")
    if not isinstance(aliases, list):
        return _fail(observed_at, "aliases is not a list")
    rows = [a for a in aliases if isinstance(a, dict) and a.get("alias") == ALIAS]
    if len(rows) != 1:
        return _fail(observed_at, "selected alias row is absent or has conflicting duplicates")
    row = rows[0]
    if row.get("ready") is not True:
        return _fail(observed_at, "ready is not exactly True")
    if row.get("status") != 200 or isinstance(row.get("status"), bool):
        return _fail(observed_at, "status is not 200")
    ctx = row.get("context_length")
    if not isinstance(ctx, int) or isinstance(ctx, bool) or ctx != CTX:
        return _fail(observed_at, "context_length is not exactly 131072")
    slots = row.get("parallel_slots")
    if not isinstance(slots, int) or isinstance(slots, bool) or slots != SLOTS:
        return _fail(observed_at, "parallel_slots is not exactly 1")
    if row.get("physical_resource") != RESOURCE:
        return _fail(observed_at, "physical_resource is not the expected AM4 endpoint")
    model = row.get("model")
    if not isinstance(model, str) or model.rsplit("/", 1)[-1] != MODEL_BASE:
        return _fail(observed_at, "model basename is not the expected Qwen3.8-27B gguf")
    return {"ready": True, "parallel_slots": 1, "observed_at": observed_at,
            "reason": "HTTP readiness does not prove GPU placement; gpu_placed remains unknown",
            "gpu_placed": None, "model": model, "context_length": ctx,
            "physical_resource": RESOURCE}
