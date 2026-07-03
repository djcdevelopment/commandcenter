'''
Tool: check_doc_claims.py

A gate that validates machine-checkable claims against the projected corpus.

Claims are defined in docs/doc-claims.json and checked against knowledge/*.json.
Each claim has a check with:
  - file: path relative to repo root
  - path: dot-path to target value (e.g. "capability_count")
  - op: comparison operator (eq, gte, exists)
  - value: threshold for comparison

When a path resolves to a list, the length of the list is compared.

Waivers are in docs/doc-claims-waivers.json. A waiver is valid if:
  - claim_id matches
  - created <= today
  - expires is null or expires >= today

The checker exits 0 if all checks PASS or WAIVED.
Exits nonzero if any check FAILS and is not waived.

Usage:
  python tools/workflow/check_doc_claims.py

This tool is a gate, not a projection. It may compare today's date to waiver expiry.
'''

import json
import sys
from datetime import datetime

# Load claims registry
with open("docs/doc-claims.json", "r") as f:
    claims = json.load(f)

# Load waivers
with open("docs/doc-claims-waivers.json", "r") as f:
    waivers = {w["claim_id"]: w for w in json.load(f)}

# Today's UTC date (ISO-8601)
today = datetime.utcnow().strftime("%Y-%m-%d")

# Check each claim
failures = []
for claim in claims:
    claim_id = claim["claim_id"]
    doc = claim["doc"]
    description = claim["description"]
    check = claim["check"]
    
    # Resolve file path relative to repo root
    file_path = check["file"]
    with open(file_path, "r") as f:
        data = json.load(f)
    
    # Resolve dot-path
    keys = check["path"].split(".")
    value = data
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            raise KeyError(f"Path {check['path']} not found in {file_path}")
    
    # Handle list length
    if isinstance(value, list):
        value = len(value)
    
    # Evaluate op
    op = check["op"]
    threshold = check["value"]
    passed = False
    if op == "eq":
        passed = value == threshold
    elif op == "gte":
        passed = value >= threshold
    elif op == "exists":
        passed = value is not None
    else:
        raise ValueError(f"Unknown op: {op}")
    
    # Check waiver
    waived = False
    if claim_id in waivers:
        waiver = waivers[claim_id]
        created = waiver["created"]
        expires = waiver["expires"]
        
        if created <= today:
            if expires is None or expires >= today:
                waived = True
    
    # Record result
    result = "PASS" if passed else "FAIL"
    if waived:
        result = "WAIVED"
    
    if not passed and not waived:
        failures.append(claim_id)
    
    # Print table row
    print(f"{claim_id:<25} {threshold:<10} {value:<10} {result}")

# Exit with failure if any unwaived failure
if failures:
    print(f"\nFAILURES: {', '.join(failures)}")
    sys.exit(1)
else:
    print("\nAll checks passed.")
    sys.exit(0)