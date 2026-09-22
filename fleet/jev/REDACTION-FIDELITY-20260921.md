# Source text corrupted before local-model inference

Initial diagnosis saved 2026-09-21 11:00:47 UTC. The immutable request for cycle15
already contains `ta[REDACTED] routing`; the model did not invent that change.

`hearth/toolsurface/build_requests.py` defines an unanchored `sk-` value pattern.
It matches the suffix of ordinary words, including `task-family`, `disk-cache`,
`mask-value` and `risk-based`. `create_build_request()` applies `_redact()` while
writing the immutable request. `_request_body()` subsequently reads that saved
text, and `_delegation_brief()` sends it to the worker. Redaction therefore
changes the model's actual source input, not merely an audit-log copy.

The exact phrase failure is reproduced locally without inference or credentials.
The immutable request hash is retained in the
[reproduction evidence](evidence/20260921-redaction-reproduction.json), together
with the four exact harmless phrases and their corrupted outputs.

## Source-only proposal and its policy tradeoff

The proposed change adds a preceding non-identifier boundary to the `sk-` branch
only. Other prefix rules, secret-field-name handling, recursive behavior and
immutability are unchanged. It preserves the entire actual renderer source.
Eleven synthetic credential contexts retain the prior redaction result: bare,
quoted, assigned, bearer, URL query, path component, newline, parentheses, and
the existing three other prefix families. Sensitive dictionary values are still
redacted in full even when their text embeds a prefix inside an identifier.
No real credentials were used or emitted; no new test files or model calls.

**This is not identical recognition coverage.** In unstructured text, a prefix
concatenated directly into a longer identifier no longer matches this branch.
That is the explicit precision tradeoff, not proof that every secret is caught.
The proposal therefore needs operator/security review before activation; it is
not being represented as an unchanged privacy guarantee. The check records the
concatenated-input difference rather than hiding it as a passing case.
[Focused boundary checks](evidence/20260921-redaction-boundary-check.json).

No existing request, credential data, caller authority or live gateway has been
modified. Old receipts are intentionally left as evidence of what was sent.

The pilot gateway has this module cached. Its earlier restart was policy-denied.
The source proposal remains pending review and an authorized activation. Do not rewrite
historical receipts, disable redaction, hot-patch the process, move to another
listener, or encode source to evade the existing guard.
