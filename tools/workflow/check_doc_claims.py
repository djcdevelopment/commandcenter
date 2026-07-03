import json
import sys
import os
from datetime import datetime

# Resolve repo root from script location
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load the claims registry
def load_claims():
    claims_path = os.path.join(REPO_ROOT, "docs", "doc-claims.json")
    with open(claims_path, "r") as f:
        return json.load(f)

# Load waivers
def load_waivers():
    waivers_path = os.path.join(REPO_ROOT, "docs", "doc-claims-waivers.json")
    if not os.path.exists(waivers_path):
        return {}
    with open(waivers_path, "r") as f:
        return json.load(f)

# Evaluate a single check
def evaluate_check(claim):
    file_path = os.path.join(REPO_ROOT, claim["check"]["file"])
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found")
        return False, "File not found"

    with open(file_path, "r") as f:
        data = json.load(f)

    # Navigate the dot-path
    try:
        value = data
        for key in claim["check"]["path"].split("."):
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list):
                # For list paths, compare length
                if key == "length":
                    value = len(value)
                else:
                    raise KeyError(key)
            else:
                raise KeyError(key)
    except (KeyError, TypeError):
        return False, "Path not found"

    # Apply operation
    op = claim["check"]["op"]
    expected = claim["check"]["value"]

    if op == "eq":
        result = value == expected
    elif op == "gte":
        result = value >= expected
    elif op == "exists":
        result = value is not None
    else:
        raise ValueError(f"Unsupported operation: {op}")

    return result, value

# Check if waiver is active
def is_waived(claim_id, waivers):
    if claim_id not in waivers:
        return False, None
    waiver = waivers[claim_id]
    created = datetime.fromisoformat(waiver["created"])
    expires = waiver["expires"]
    if expires is None:
        return True, "never expires"
    expires_date = datetime.fromisoformat(expires)
    if expires_date < datetime.now():
        return False, "expired"
    return True, "active"

# Main execution
def main():
    claims = load_claims()
    waivers = load_waivers()

    # Table header
    print(f"{'claim_id':<25} {'expected':<15} {'actual':<15} {'status':<10}")
    print(f"{'-'*25} {'-'*15} {'-'*15} {'-'*10}")

    failed = False
    for claim in claims:
        claim_id = claim["claim_id"]
        expected = claim["check"]["value"]
        result, actual = evaluate_check(claim)
        waived, reason = is_waived(claim_id, waivers)

        status = "PASS"
        if not result:
            if waived:
                status = "WAIVED"
            else:
                status = "FAIL"
                failed = True
        elif waived:
            status = "WAIVED"

        print(f"{claim_id:<25} {expected:<15} {actual:<15} {status:<10}")

    if failed:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()