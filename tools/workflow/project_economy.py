from __future__ import annotations

# Decision table thresholds — named traced constants with rationale comments (D18).

BATTERY_COST_THRESHOLD_PERCENT: float = 30.0
# 30 %: below this the system is drawing on a finite, depleting reserve and any build
# (~190 s) will consume a material fraction of remaining runtime. At this point ownership
# is irrelevant — physics makes the resource metered regardless of who holds the title.
# "Physics beats ownership."


def derive_economic_context(candidate: dict) -> dict:
    """Derive the optimization objective for a single candidate dispatch.

    Returns {"objective": str, "signals_read": list[str], "reason": str}.
    objective is one of "cost_per_outcome" | "knowledge_per_hour" | "undetermined".

    Decision table — evaluate top-down, first match wins (D18: ordered, traced):
      R1: signals.battery_percent non-null AND < BATTERY_COST_THRESHOLD_PERCENT
          → cost_per_outcome; "physics beats ownership: battery at N%"
      R2: ownership == "metered_provider"
          → cost_per_outcome
      R3: ownership == "owned" AND power_source == "mains"
          → knowledge_per_hour
      R4: anything else (leased, null ownership, owned with null power_source)
          → undetermined; the missing/unexpected input is NAMED in reason (no silent caps)
    """
    signals = candidate.get("signals") or {}
    ownership = candidate.get("ownership")
    power_source = candidate.get("power_source")
    signals_read: list[str] = []

    battery_percent = signals.get("battery_percent")
    if battery_percent is not None:
        signals_read.append("battery_percent")

    # R1: battery override — physics beats ownership
    if battery_percent is not None and battery_percent < BATTERY_COST_THRESHOLD_PERCENT:
        return {
            "objective": "cost_per_outcome",
            "signals_read": signals_read,
            "reason": f"physics beats ownership: battery at {battery_percent}%",
        }

    # R2: metered provider — every dispatch has direct monetary cost
    if ownership == "metered_provider":
        return {
            "objective": "cost_per_outcome",
            "signals_read": signals_read,
            "reason": "metered_provider ownership: each dispatch incurs direct monetary cost",
        }

    # R3: owned hardware on mains — marginal cost near-zero, maximize knowledge rate
    if ownership == "owned" and power_source == "mains":
        return {
            "objective": "knowledge_per_hour",
            "signals_read": signals_read,
            "reason": "owned hardware on mains: marginal cost near-zero, maximize learning rate",
        }

    # R4: undetermined — name the missing or unexpected input (no silent caps)
    if ownership is None and power_source is None:
        reason = "ownership=null, power_source=null: cannot derive economic context without resource facts"
    elif ownership is None:
        reason = "ownership=null: cannot derive economic context without ownership fact"
    elif ownership == "leased":
        reason = (
            "ownership=leased: economic objective undetermined — "
            "leased resource is neither owned-sunk nor pay-per-call; "
            "policy decision pending (see QUESTIONS-D1.md item 3)"
        )
    elif power_source is None:
        reason = (
            f"ownership={ownership}, power_source=null: "
            "cannot determine marginal cost without power source"
        )
    else:
        reason = (
            f"ownership={ownership!r}, power_source={power_source!r}: "
            "no matching decision rule"
        )

    return {
        "objective": "undetermined",
        "signals_read": signals_read,
        "reason": reason,
    }


def _counterfactual_would_have_chosen(objective: str, candidate_pool: list[dict]) -> str | None:
    """Return the builder_id that the given objective's simple preference would select.

    cost_per_outcome  — prefers the metered/cheapest candidate
                        (lowest base_score among metered_provider ownership).
    knowledge_per_hour — prefers the owned+mains candidate
                         (highest base_score among owned + mains).
    """
    if objective == "cost_per_outcome":
        metered = [c for c in candidate_pool if c.get("ownership") == "metered_provider"]
        if not metered:
            return None
        return min(metered, key=lambda c: c.get("base_score", 0.0))["builder_id"]

    if objective == "knowledge_per_hour":
        owned_mains = [
            c for c in candidate_pool
            if c.get("ownership") == "owned" and c.get("power_source") == "mains"
        ]
        if not owned_mains:
            return None
        return max(owned_mains, key=lambda c: c.get("base_score", 0.0))["builder_id"]

    return None


def build_economy_influence(selected_candidate: dict, candidate_pool: list[dict]) -> dict:
    """Build the economy_influence block for a scheduler-decision contract document.

    objective_selected = derived context of the selected candidate.
    counterfactual     = what the opposite objective's simple preference would have chosen
                         (recorded for transparency, never fed into ranking).
    """
    context = derive_economic_context(selected_candidate)
    objective = context["objective"]

    if objective == "undetermined":
        counterfactual = None
    else:
        opposite = (
            "cost_per_outcome" if objective == "knowledge_per_hour" else "knowledge_per_hour"
        )
        would_have = _counterfactual_would_have_chosen(opposite, candidate_pool)
        if opposite == "cost_per_outcome":
            preference_desc = "the metered/cheapest candidate"
        else:
            preference_desc = "the owned+mains candidate"
        note = (
            f"under {opposite} objective, {preference_desc} would be preferred; "
            "recorded for transparency, not applied to candidate ranking"
        )
        counterfactual = {
            "objective": opposite,
            "would_have_chosen": would_have,
            "note": note,
        }

    return {
        "objective_selected": objective,
        "signals_read": context["signals_read"],
        "reason": context["reason"],
        "counterfactual": counterfactual,
    }
