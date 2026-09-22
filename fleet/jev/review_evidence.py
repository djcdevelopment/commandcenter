# Hermes/Dense authored the selected final block; Codex corrected the byte-size
# guard to compare complete packets. Both original blocks are retained in evidence.
import json

MARKER = "\n## Operator-supplied execution evidence\n"
_UNRECOGNIZED = {"mode": "unchanged", "reason": "unrecognized_evidence"}
_LEGEND = "passed=false is an executed failing check, not an inconsistent result"


def compact_review_packet(packet):
    if not isinstance(packet, str) or packet.count(MARKER) != 1:
        return packet, _UNRECOGNIZED

    head, rest = packet.split(MARKER, 1)
    body = rest.lstrip()
    try:
        evidence, end = json.JSONDecoder().raw_decode(body)
    except ValueError:
        return packet, _UNRECOGNIZED

    cases = evidence.get("cases") if isinstance(evidence, dict) else None
    if not isinstance(cases, list) or not cases:
        return packet, _UNRECOGNIZED

    new_cases = []
    failed_names = []
    for case in cases:
        if (not isinstance(case, dict)
                or not isinstance(case.get("name"), str)
                or not isinstance(case.get("passed"), bool)):
            return packet, _UNRECOGNIZED
        if case["passed"] is False:
            failed_names.append(case["name"])
            new_cases.append(dict(case))
        else:
            new_cases.append({"name": case["name"], "passed": True})

    modified = dict(evidence)
    modified["cases"] = new_cases
    wrapper = {
        "summary": {
            "passed_count": len(cases) - len(failed_names),
            "failed_count": len(failed_names),
            "failed_case_names": failed_names,
            "legend": _LEGEND,
        },
        "evidence": modified,
    }
    try:
        replacement = json.dumps(wrapper, indent=2, ensure_ascii=False, allow_nan=False)
    except ValueError:
        return packet, _UNRECOGNIZED

    new_packet = head + MARKER + rest[: len(rest) - len(body)] + replacement + body[end:]
    if len(new_packet.encode("utf-8")) >= len(packet.encode("utf-8")):
        return packet, {"mode": "unchanged", "reason": "not_smaller"}

    return new_packet, {"mode": "compacted",
                        "passed_count": len(cases) - len(failed_names),
                        "failed_count": len(failed_names)}
