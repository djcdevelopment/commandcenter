def summarize_quality(records):
    """
    Summarize quality metrics from a list of record dictionaries.

    Args:
        records: A list of dictionaries, each with an 'outcome' key.

    Returns:
        A dictionary with:
        - available: bool indicating if input was a list
        - tasks: total number of valid records
        - authored: number of valid records excluding 'not_run'
        - unmodified_delivery: count of records with outcome 'unmodified_delivery'
        - assisted_delivery: count of records with outcome 'assisted_delivery'
        - rejected: count of records with outcome 'rejected'
        - incomplete: count of records with outcome 'incomplete'
        - not_run: count of records with outcome 'not_run'
        - invalid_records: count of records that are not dicts or have invalid outcomes
    """
    if not isinstance(records, list):
        return {
            'available': False,
            'tasks': 0,
            'authored': 0,
            'unmodified_delivery': 0,
            'assisted_delivery': 0,
            'rejected': 0,
            'incomplete': 0,
            'not_run': 0,
            'invalid_records': 0
        }

    counters = {
        'tasks': 0,
        'authored': 0,
        'unmodified_delivery': 0,
        'assisted_delivery': 0,
        'rejected': 0,
        'incomplete': 0,
        'not_run': 0,
        'invalid_records': 0
    }

    valid_outcomes = {
        'unmodified_delivery',
        'assisted_delivery',
        'rejected',
        'incomplete',
        'not_run'
    }

    for record in records:
        if not isinstance(record, dict):
            counters['invalid_records'] += 1
            continue

        outcome = record.get('outcome')
        if not isinstance(outcome, str):
            counters['invalid_records'] += 1
            continue

        if outcome in valid_outcomes:
            counters['tasks'] += 1
            counters[outcome] += 1
            if outcome != 'not_run':
                counters['authored'] += 1
        else:
            counters['invalid_records'] += 1

    return {
        'available': True,
        'tasks': counters['tasks'],
        'authored': counters['authored'],
        'unmodified_delivery': counters['unmodified_delivery'],
        'assisted_delivery': counters['assisted_delivery'],
        'rejected': counters['rejected'],
        'incomplete': counters['incomplete'],
        'not_run': counters['not_run'],
        'invalid_records': counters['invalid_records']
    }