from __future__ import annotations

# Pure function — no I/O, no wiring, no side effects.
# Sensor mapping (from capacity-observation.v1 physical fields):
#   max_gpu_temp_c  → observed.physical.gpu_temp_c_peak
#   max_power_w     → observed.physical.power_w_peak
#   max_fan_rpm     → observed.physical.fan_rpm_avg  (the only fan sensor)


def check(budget: dict, physical_or_none: dict | None) -> dict:
    """Return a verdict dict: {permitted, violated, unmeasurable}.

    violated entries: {"dimension": str, "limit": ..., "observed": ...}
    unmeasurable entries: {"dimension": str, "reason": str}

    Truth table (evaluated in order):
    1. budget.suspended → permitted False, violated=[{dimension:"suspended", ...}]
    2. unattended_dispatch_allowed == False → permitted False, violated accordingly
    3. physical is None → every constrained dimension in unmeasurable, permitted False
    4. a constrained dimension whose sensor value is None → unmeasurable, permitted False
       ("an abort criterion without a sensor is a wish")
    5. all constrained dimensions measured and within limits → permitted True
    """
    violated: list[dict] = []
    unmeasurable: list[dict] = []

    if budget.get("suspended"):
        violated.append({
            "dimension": "suspended",
            "limit": True,
            "observed": "budget is suspended",
        })
        return {"permitted": False, "violated": violated, "unmeasurable": unmeasurable}

    if not budget.get("unattended_dispatch_allowed"):
        violated.append({
            "dimension": "unattended_dispatch_allowed",
            "limit": True,
            "observed": False,
        })
        return {"permitted": False, "violated": violated, "unmeasurable": unmeasurable}

    _SENSOR_MAP = [
        ("max_gpu_temp_c", "gpu_temp_c_peak"),
        ("max_power_w",    "power_w_peak"),
        ("max_fan_rpm",    "fan_rpm_avg"),
    ]

    constrained = [(limit_key, sensor_key)
                   for limit_key, sensor_key in _SENSOR_MAP
                   if budget.get(limit_key) is not None]

    if physical_or_none is None:
        for limit_key, sensor_key in constrained:
            unmeasurable.append({
                "dimension": limit_key,
                "reason": "sensor absent for constrained dimension",
            })
        permitted = len(constrained) == 0
        return {"permitted": permitted, "violated": violated, "unmeasurable": unmeasurable}

    for limit_key, sensor_key in constrained:
        limit = budget[limit_key]
        observed = physical_or_none.get(sensor_key)
        if observed is None:
            unmeasurable.append({
                "dimension": limit_key,
                "reason": "sensor absent for constrained dimension",
            })
        elif observed > limit:
            violated.append({
                "dimension": limit_key,
                "limit": limit,
                "observed": observed,
            })

    permitted = len(violated) == 0 and len(unmeasurable) == 0
    return {"permitted": permitted, "violated": violated, "unmeasurable": unmeasurable}
