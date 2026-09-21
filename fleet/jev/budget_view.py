# OMEN-authored guided repair; Codex restored finite-input checks removed by the repair.
def summarize_budget(ledger, price_per_token, ceiling_usd):
    """
    Summarize budget usage based on ledger data.
    """
    unavailable = {
        'available': False,
        'calls': None,
        'input_tokens': None,
        'usage_estimate_usd': None,
        'uncertain_reserved_usd': None,
        'booked_usd': None,
        'remaining_usd': None
    }

    if not isinstance(ledger, dict):
        return unavailable

    required_fields = ['calls', 'input_tokens', 'charged_usd']
    for field in required_fields:
        if field not in ledger:
            return unavailable

    calls = ledger['calls']
    input_tokens = ledger['input_tokens']
    charged_usd = ledger['charged_usd']

    if type(calls) is not int or calls < 0:
        return unavailable

    if type(input_tokens) is not int or input_tokens < 0:
        return unavailable

    if not isinstance(charged_usd, (int, float)) or charged_usd < 0 or isinstance(charged_usd, bool):
        return unavailable

    if not isinstance(price_per_token, (int, float)) or price_per_token < 0 or isinstance(price_per_token, bool):
        return unavailable

    if not isinstance(ceiling_usd, (int, float)) or ceiling_usd < 0 or isinstance(ceiling_usd, bool):
        return unavailable

    try:
        if not all(float('-inf') < value < float('inf')
                   for value in (charged_usd, price_per_token, ceiling_usd)):
            return unavailable
        usage_estimate_usd = input_tokens * price_per_token
        if not (float('inf') > usage_estimate_usd > float('-inf')):
            return unavailable

        booked_usd = charged_usd

        if booked_usd < usage_estimate_usd - 1e-12:
            return unavailable

        uncertain_reserved_usd = max(0, booked_usd - usage_estimate_usd)
        if not (float('inf') > uncertain_reserved_usd > float('-inf')):
            return unavailable

        remaining_usd = max(0, ceiling_usd - booked_usd)
        if not (float('inf') > remaining_usd > float('-inf')):
            return unavailable

        return {
            'available': True,
            'calls': calls,
            'input_tokens': input_tokens,
            'usage_estimate_usd': usage_estimate_usd,
            'uncertain_reserved_usd': uncertain_reserved_usd,
            'booked_usd': booked_usd,
            'remaining_usd': remaining_usd
        }

    except (OverflowError, TypeError):
        return unavailable
