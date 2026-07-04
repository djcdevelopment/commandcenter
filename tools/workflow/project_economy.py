from typing import Any, Dict, Optional

# Traced constants with rationale comments (house style)
BATTERY_COST_THRESHOLD_PERCENT = 30  # Rationale: below 30% battery, cost per outcome dominates due to physics of power depletion


def derive_economic_context(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Derive the economic context for a candidate based on ownership, power_source, and signals.
    Returns: dict with objective, signals_read, and reason.
    """
    signals = candidate.get("signals")
    signals_read = []

    # Rule R1: battery_percent < 30 → cost_per_outcome
    if signals and "battery_percent" in signals:
        battery_percent = signals["battery_percent"]
        signals_read.append("battery_percent")
        if battery_percent < BATTERY_COST_THRESHOLD_PERCENT:
            return {
                "objective": "cost_per_outcome",
                "signals_read": signals_read,
                "reason": f"physics beats ownership: battery at {battery_percent}%"
            }

    # Rule R2: ownership == "metered_provider" → cost_per_outcome
    ownership = candidate.get("ownership")
    if ownership == "metered_provider":
        return {
            "objective": "cost_per_outcome",
            "signals_read": signals_read,
            "reason": "ownership: metered_provider"
        }

    # Rule R3: ownership == "owned" AND power_source == "mains" → knowledge_per_hour
    power_source = candidate.get("power_source")
    if ownership == "owned" and power_source == "mains":
        return {
            "objective": "knowledge_per_hour",
            "signals_read": signals_read,
            "reason": "ownership: owned with mains power"
        }

    # Rule R4: anything else → undetermined
    # Report null/leased input in reason
    reason_parts = []
    if ownership is None:
        reason_parts.append("null ownership")
    elif ownership == "leased":
        reason_parts.append("leased")
    elif ownership == "metered_provider":
        # Already handled
        pass
    else:
        reason_parts.append(f"ownership: {ownership}")

    if power_source is None:
        reason_parts.append("null power_source")
    elif power_source == "battery":
        reason_parts.append("battery power")

    reason = ", ".join(reason_parts)
    return {
        "objective": "undetermined",
        "signals_read": signals_read,
        "reason": f"input not covered: {reason}"
    }

def _simple_preference_cost_per_outcome(candidates: list[Dict[str, Any]]) -> Optional[str]:
    """
    Helper: prefer the metered/cheapest candidate for cost_per_outcome objective.
    Returns: builder_id of the selected candidate, or None if no candidate is metered.
    """
    metered_candidates = [c for c in candidates if c.get("ownership") == "metered_provider"]
    if not metered_candidates:
        return None
    # Return the one with the lowest base_score
    return min(metered_candidates, key=lambda c: c["base_score"])["builder_id"]

def _simple_preference_knowledge_per_hour(candidates: list[Dict[str, Any]]) -> Optional[str]:
    """
    Helper: prefer owned+mains candidate for knowledge_per_hour objective.
    Returns: builder_id of the selected candidate, or None if no such candidate.
    """
    owned_mains_candidates = [
        c for c in candidates 
        if c.get("ownership") == "owned" and c.get("power_source") == "mains"
    ]
    if not owned_mains_candidates:
        return None
    # Return the one with the lowest base_score
    return min(owned_mains_candidates, key=lambda c: c["base_score"])["builder_id"]

def _compute_counterfactual(candidates: list[Dict[str, Any]], objective: str) -> Dict[str, Any]:
    """
    Compute counterfactual: what would have been chosen under the objective.
    Returns: dict with objective, would_have_chosen, note.
    """
    if objective == "cost_per_outcome":
        would_have_chosen = _simple_preference_cost_per_outcome(candidates)
    elif objective == "knowledge_per_hour":
        would_have_chosen = _simple_preference_knowledge_per_hour(candidates)
    else:
        would_have_chosen = None

    return {
        "objective": objective,
        "would_have_chosen": would_have_chosen,
        "note": "computed by simple preference rule"
    }