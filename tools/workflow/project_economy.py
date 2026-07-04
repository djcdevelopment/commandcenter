from typing import Dict, Any, Optional

# Traced constants with rationale comments (house style)
BATTERY_COST_THRESHOLD_PERCENT = 30  # R1: physics beats ownership; battery below 30% is cost-driven


def derive_economic_context(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Derive the economic context for a candidate based on ownership, power_source, and signals.
    Returns: {'objective': 'cost_per_outcome'|'knowledge_per_hour'|'undetermined',
             'signals_read': [...],
             'reason': '...'}
    """
    signals = candidate.get("signals")
    signals_read = []

    # R1: battery_percent < 30% → cost_per_outcome
    if signals and "battery_percent" in signals:
        battery_percent = signals["battery_percent"]
        signals_read.append("battery_percent")
        if battery_percent < BATTERY_COST_THRESHOLD_PERCENT:
            return {
                "objective": "cost_per_outcome",
                "signals_read": signals_read,
                "reason": f"physics beats ownership: battery at {battery_percent}%"
            }

    # R2: ownership == "metered_provider" → cost_per_outcome
    ownership = candidate.get("ownership")
    if ownership == "metered_provider":
        return {
            "objective": "cost_per_outcome",
            "signals_read": signals_read,
            "reason": "ownership: metered_provider"
        }

    # R3: ownership == "owned" AND power_source == "mains" → knowledge_per_hour
    power_source = candidate.get("power_source")
    if ownership == "owned" and power_source == "mains":
        return {
            "objective": "knowledge_per_hour",
            "signals_read": signals_read,
            "reason": "ownership: owned with mains power"
        }

    # R4: anything else → undetermined with reason
    reason_parts = []
    if ownership is None:
        reason_parts.append("null ownership")
    elif ownership == "leased":
        reason_parts.append("leased")
    elif ownership == "metered_provider":
        # already handled
        pass
    elif ownership == "owned" and power_source is None:
        reason_parts.append("owned with null power_source")
    else:
        reason_parts.append(f"unknown ownership: {ownership}")

    return {
        "objective": "undetermined",
        "signals_read": signals_read,
        "reason": ", ".join(reason_parts)
    }