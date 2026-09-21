# OMEN candidate b2ab223; Codex corrected exact-int and rule checks and combined
# metric/rule processing into one second pass. Original retained in evidence.
def parse_b70(records):
    """Parse B70 observer records and return a list of adapter capacity reports.

    Args:
        records: List of decoded b70tools JSON objects.

    Returns:
        List of dictionaries with adapter capacity data, sorted by adapter_id.
        Each dict contains: adapter_id, bdf, name, total_bytes, adapter_local_committed_bytes,
        adapter_non_local_committed_bytes, observer_process_local_bytes, free_bytes, disagreement_rules.
        All missing fields initialized to None.
    """
    # Step 1: Collect identity information (k=ai) with 'Arc' and 'B70' in description
    identities = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get('k') == 'ai' and isinstance(record.get('desc'), str) and 'Arc' in record['desc'] and 'B70' in record['desc']:
            adapter_id = record.get('a')
            if isinstance(adapter_id, str) and adapter_id:
                identities[adapter_id] = {
                    'adapter_id': adapter_id,
                    'bdf': record.get('bdf'),
                    'name': record.get('desc'),
                    'total_bytes': record.get('dvm') if type(record.get('dvm')) is int and record.get('dvm') >= 0 else None
                }

    # Step 2: Initialize metrics and rules for each adapter
    metrics = {adapter_id: {
        'adapter_local_committed_bytes': None,
        'adapter_non_local_committed_bytes': None,
        'observer_process_local_bytes': None
    } for adapter_id in identities}

    rules = {adapter_id: set() for adapter_id in identities}

    # Step 3: Process metric records (k=ms) with u=Bytes and valid s,n values
    for record in records:
        if not isinstance(record, dict):
            continue
        adapter_id = record.get('a')
        if not isinstance(adapter_id, str) or adapter_id not in identities:
            continue
        if record.get('k') == 'dr' and isinstance(record.get('rule'), str):
            rules[adapter_id].add(record['rule'])
        if record.get('k') != 'ms' or record.get('u') != 'Bytes':
            continue
        value = record.get('v')
        if type(value) is not int or value < 0:
            continue
        source = record.get('s')
        name = record.get('n')

        # Handle PDH_AdapterMemory for local/non-local committed bytes
        if source == 'PDH_AdapterMemory':
            if name == 'gpu.adapter.vram.local.bytes_committed':
                metrics[adapter_id]['adapter_local_committed_bytes'] = value
            elif name == 'gpu.adapter.vram.non_local.bytes_committed':
                metrics[adapter_id]['adapter_non_local_committed_bytes'] = value

        # Handle DXGI_VideoMemoryInfo for observer process local bytes
        elif source == 'DXGI_VideoMemoryInfo' and name == 'vram.local.current_usage_bytes':
            metrics[adapter_id]['observer_process_local_bytes'] = value

    # Combine results
    result = []
    for adapter_id in sorted(identities.keys()):
        row = {
            'adapter_id': identities[adapter_id]['adapter_id'],
            'bdf': identities[adapter_id]['bdf'],
            'name': identities[adapter_id]['name'],
            'total_bytes': identities[adapter_id]['total_bytes'],
            'adapter_local_committed_bytes': metrics[adapter_id]['adapter_local_committed_bytes'],
            'adapter_non_local_committed_bytes': metrics[adapter_id]['adapter_non_local_committed_bytes'],
            'observer_process_local_bytes': metrics[adapter_id]['observer_process_local_bytes'],
            'free_bytes': None,
            'disagreement_rules': sorted(list(rules[adapter_id]))
        }
        result.append(row)

    return result
