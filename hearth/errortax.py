"""Error taxonomy for gateway and provider failures — one table, two consumers.

Lives at `hearth/` top level rather than in `hearth/kernel/` because BOTH sides of the
kernel/toolsurface boundary need it and neither may import the other:

- `hearth.kernel.gateway` stamps `error_code` on every ledger row.
- `hearth.observation.emit` decides whether a failed dispatch is capability evidence
  (a `timeout` says the rung could not do this workload inside its own declared budget)
  or infrastructure to be excluded per ADR-0002 (`cold_start`, `occupancy_skip`,
  `auth_expired`, `quota`, `parse_error` say nothing about the model).

`hearth.kernel.ledger` re-exports `classify_error` so its existing importers are
untouched. Providers must not reach into `hearth.kernel` at all — enforced literally, by
`hearth/tests/toolsurface/test_provider_contract.py::test_no_kernel_imports_anywhere`,
which greps provider *source text* for the string "hearth.kernel". Duplicating the
taxonomy to satisfy that grep would give the lab two drifting definitions of the same
fact; moving it here gives one definition both sides may name.

Kernel-free and stdlib-only, deliberately: it must stay importable from the toolsurface.
"""

from __future__ import annotations

# Ordered, first-match-wins. Infra classes are the ones ADR-0002 excludes from the belief
# layer, so the order below is load-bearing for what counts as evidence: `routing_refusal`
# and `occupancy_skip` must be tested before `timeout`, or an "occupancy busy, timed out
# waiting" string would be misread as a capability limit of the rung.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # "payload_over_budget" is the FAMILY prefix, not one code: the router refuses
    # both when no rung qualifies (..._no_eligible_backend) and when a caller-pinned
    # rung cannot hold the payload (..._for_pinned_backend, ADR-0031). Matching the
    # prefix keeps a new refusal reason from silently classifying as "other" — the
    # gateway re-derives error_code from the message text (kernel/gateway.py), so a
    # code this table does not name is a refusal the ledger cannot count as one.
    ("routing_refusal", ("routing_refusal", "payload_over_budget")),
    ("occupancy_skip", ("occupancy", "busy")),
    ("timeout", ("timed out", "timeout")),
    ("cold_start", ("connection refused", "connect error", "unreachable",
                    "failed to establish", "no connection could be made")),
    ("auth_expired", ("401", "403", "unauthorized", "token", "credentials")),
    ("quota", ("429", "quota", "resource exhausted", "rate limit")),
    ("parse_error", ("json", "parse", "decode", "expecting value")),
)

# Failures that describe the plumbing, not the model. Excluded from the belief layer per
# ADR-0002 ("the belief layer must not ingest infra/harness-caused failures"), and counted
# rather than dropped silently, per its no-silent-caps clause.
INFRA_ERROR_CODES = frozenset({
    "cold_start", "occupancy_skip", "auth_expired", "quota", "parse_error",
})

# Failures that ARE a statement about the rung's capability at this workload.
CAPABILITY_ERROR_CODES = frozenset({"timeout"})


def classify_error(error: str) -> str:
    """Taxonomy for gateway/provider errors (S4): first match wins."""
    if not error:
        return "other"
    lower = error.lower()
    for code, needles in _RULES:
        if any(needle in lower for needle in needles):
            return code
    return "other"
