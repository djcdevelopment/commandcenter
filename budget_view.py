def summarize_budget(ledger, price_per_token, ceiling_usd):
    """
    Summarize budget usage based on ledger data.

    Args:
        ledger: dict with keys 'calls', 'input_tokens', 'charged_usd'
        price_per_token: float, cost per token
        ceiling_usd: float, budget ceiling

    Returns:
        dict with keys:
        - available: bool
        - calls: int
        - input_tokens: int
        - usage_estimate_usd: float
        - uncertain_reserved_usd: float
        - booked_usd: float
        - remaining_usd: float

    On invalid input, returns available=False and all other fields as None.
    """
    # Validate ledger structure and types
    if not isinstance(ledger, dict):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    # Check required fields exist and are correct types
    required_fields = ['calls', 'input_tokens', 'charged_usd']
    for field in required_fields:
        if field not in ledger:
            return {
                'available': False,
                'calls': None,
                'input_tokens': None,
                'usage_estimate_usd': None,
                'uncertain_reserved_usd': None,
                'booked_usd': None,
                'remaining_usd': None
            }

    calls = ledger['calls']
    input_tokens = ledger['input_tokens']
    charged_usd = ledger['charged_usd']

    # Validate calls and input_tokens are nonnegative integers (not bool)
    if not isinstance(calls, int) or calls < 0 or isinstance(calls, bool):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    if not isinstance(input_tokens, int) or input_tokens < 0 or isinstance(input_tokens, bool):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    # Validate charged_usd is finite nonnegative number (not bool)
    if not isinstance(charged_usd, (int, float)) or charged_usd < 0 or isinstance(charged_usd, bool):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    if not isinstance(price_per_token, (int, float)) or price_per_token < 0 or isinstance(price_per_token, bool):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    if not isinstance(ceiling_usd, (int, float)) or ceiling_usd < 0 or isinstance(ceiling_usd, bool):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    # Check for finite values
    if not (float('inf') > charged_usd > float('-inf')):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    if not (float('inf') > price_per_token > float('-inf')):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    if not (float('inf') > ceiling_usd > float('-inf')):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    # Calculate usage estimate
    usage_estimate_usd = input_tokens * price_per_token

    # Check for overflow or non-finite results
    if not (float('inf') > usage_estimate_usd > float('-inf')):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    # Booked usage
    booked_usd = charged_usd

    # Check for inconsistency: booked_usd < usage_estimate_usd by more than 1e-12
    if booked_usd < usage_estimate_usd - 1e-12:
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    # Calculate uncertain reserved amount
    uncertain_reserved_usd = max(0, booked_usd - usage_estimate_usd)

    # Check for overflow in uncertain_reserved_usd
    if not (float('inf') > uncertain_reserved_usd > float('-inf')):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    # Calculate remaining budget
    remaining_usd = max(0, ceiling_usd - booked_usd)

    # Check for overflow in remaining_usd
    if not (float('inf') > remaining_usd > float('-inf')):
        return {
            'available': False,
            'calls': None,
            'input_tokens': None,
            'usage_estimate_usd': None,
            'uncertain_reserved_usd': None,
            'booked_usd': None,
            'remaining_usd': None
        }

    # All validations passed
    return {
        'available': True,
        'calls': calls,
        'input_tokens': input_tokens,
        'usage_estimate_usd': usage_estimate_usd,
        'uncertain_reserved_usd': uncertain_reserved_usd,
        'booked_usd': booked_usd,
        'remaining_usd': remaining_usd
    }