"""Canonical JSON and content-derived identities — implemented once, used by every contract.

One rule, one implementation. Every identity in this control plane is the
lowercase hex SHA-256 of the canonical JSON bytes of a preimage:

  * **Canonical JSON** is the UTF-8 encoding of the JSON text produced with
    object keys sorted by Unicode code point, separators ``,`` and ``:`` with no
    whitespace, ``ensure_ascii`` off, no trailing newline, ``null`` preserved,
    and no Unicode normalization.
  * **Integers only.** A preimage may not contain a float. Anything measured or
    fractional is carried as a fixed-precision decimal STRING (``"26.80"``), so
    the bytes of an identity can never depend on a platform's float repr.
    ``canonical_json`` raises rather than quietly encoding one.
  * **Set arrays** are sorted before hashing by their declared key, so two
    compilations that discovered the same members in a different order agree.
  * **Exclusions.** The preimage never contains the object's own identity field
    and never contains the reserved ``presentation`` object (rendering, computed
    against a reader's clock, which must never move an identity).

The point of all of it: two cold agents, on different machines, reading the same
bytes, report the same identifier — and a byte changed after the fact is
detectable with ``python -m hearth.operator verify-ids <file>``.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from hearth.operator import paths

# The reserved rendering object. Excluded from every preimage, at every depth.
PRESENTATION = "presentation"

# Schema-declared set arrays: key -> the member field they are sorted by before
# hashing. Anything not named here keeps its authored order.
SET_ARRAY_KEYS: dict[str, str] = {
    "generated_from": "path",
    "hosts": "id",
    "rungs": "id",
    "models": "id",
    "tools": "id",
    "harnesses": "id",
    "loops": "id",
    "deterministic_tools": "id",
    "leases": "id",
    "holds": "id",
    "eligible_routes": "route_id",
    "rejected_routes": "route_id",
    "checks": "check_id",
    "artifacts": "sha256",
}

# Which identity field belongs to which contract, and the field that proves a
# mapping is the WHOLE document rather than a reference to it (an inspection
# bundle carries `catalog: {catalog_version, path}`, which is a citation, not a
# catalog, and must not be re-hashed).
IDENTITY_FIELDS: dict[str, str] = {
    "catalog_version": "generated_from",
    "snapshot_id": "observed_at",
    "envelope_id": "intent",
    "proposal_id": "selected_graph",
    "validation_id": "verdict",
    "event_id": "event_type",
}

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class CanonicalError(ValueError):
    """Raised when a value cannot be canonically encoded. Always fatal: an
    identity computed over a guessed encoding is worse than no identity."""


class ContractError(CanonicalError):
    """Raised when a document does not satisfy the contract it declares.

    Fail closed, at every boundary: build, ingestion, store, load, validation,
    projection, replay and artifact import. A document that cannot be checked is
    refused rather than admitted — "nothing checked" and "checked and correct"
    are different answers (the WI-G2 candidate enforced none of its four
    contracts at runtime and admitted a `task-envelope.v2` document).
    """


def _reject_floats(node: Any, path: str = "$") -> None:
    if isinstance(node, bool) or node is None or isinstance(node, (int, str)):
        return
    if isinstance(node, float):
        raise CanonicalError(
            f"{path}: identity preimages contain integers only; carry a measured or "
            f"fractional value as a fixed-precision decimal string (got {node!r})")
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise CanonicalError(f"{path}: object keys must be strings (got {key!r})")
            _reject_floats(value, f"{path}.{key}")
        return
    if isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _reject_floats(value, f"{path}[{index}]")
        return
    raise CanonicalError(f"{path}: {type(node).__name__} is not JSON-encodable here")


def canonical_text(value: Any) -> str:
    """The canonical JSON TEXT for a value (no trailing newline)."""
    _reject_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def canonical_json(value: Any) -> bytes:
    """The canonical JSON BYTES for a value. These are what every identity hashes."""
    return canonical_text(value).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 — the one identity function in this control plane."""
    return hashlib.sha256(data).hexdigest()


def file_sha256(path) -> str:
    """SHA-256 of a file's literal bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Stated in the catalog as `source_digest_rule`, because a reader recomputing a
# digest must be able to get the same number.
SOURCE_DIGEST_RULE = "sha256 of the source file's bytes with CRLF newlines normalized to LF"


def source_sha256(path) -> str:
    """SHA-256 of a source file's CONTENT, newline-normalized.

    This repository is checked out on Windows with ``core.autocrlf=true``, so the
    same committed content is CRLF here and LF on a fleet node. Hashing the
    literal bytes would make ``catalog_version`` depend on which machine checked
    the tree out — and two cold agents reporting different identifiers for the
    same content is exactly the failure the identity exists to prevent. Any real
    content change still moves the digest; only the line-ending translation is
    normalized away.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _canonicalize(node: Any, key: Optional[str] = None) -> Any:
    """Drop `presentation` at every depth and sort declared set arrays.

    `key` is the name the node arrived under. A set array may appear either
    directly (``"hosts": [...]``) or as the value of a snapshot field object
    (``"leases": {"value": [...], ...}``), so the declared name is carried
    across the field object's ``value`` member.
    """
    if isinstance(node, list):
        items = [_canonicalize(item) for item in node]
        sort_field = SET_ARRAY_KEYS.get(key or "")
        if sort_field:
            items.sort(key=lambda item: (str(item.get(sort_field, ""))
                                         if isinstance(item, dict) else str(item)))
        return items
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for name, value in node.items():
            if name == PRESENTATION:
                continue
            carried = key if (name == "value" and (key or "") in SET_ARRAY_KEYS) else name
            out[name] = _canonicalize(value, carried)
        return out
    return node


ENVELOPE_METADATA_EXCLUSIONS = frozenset({"submitted_by", "submission_source", "submitted_at"})


def preimage(document: dict, own_field: str) -> dict:
    """The bytes-to-be of an identity: the document minus its own identity field
    and minus `presentation`, with declared set arrays sorted. For TaskEnvelope,
    also minus submission metadata (submitted_by, submission_source, submitted_at)."""
    if not isinstance(document, dict):
        raise CanonicalError("an identity preimage is computed over an object")
    exclusions = {own_field}
    if own_field == "envelope_id":
        exclusions |= ENVELOPE_METADATA_EXCLUSIONS
    body = {name: value for name, value in document.items() if name not in exclusions}
    return _canonicalize(body)


def identity_of(document: dict, own_field: str) -> str:
    """The content-derived identity a document should carry in `own_field`."""
    return sha256_hex(canonical_json(preimage(document, own_field)))


def stamp_identity(document: dict, own_field: str) -> dict:
    """Return the document with its identity field set to its computed identity."""
    stamped = dict(document)
    stamped[own_field] = identity_of(document, own_field)
    return stamped


def decimal_str(value: Any, places: int = 2) -> Optional[str]:
    """A measured number as a fixed-precision decimal string, or None.

    This is the ONLY way a fractional measurement enters an identity preimage.
    Half-up rounding, fixed places, so "32.5" and 32.5 both become "32.50".
    """
    if value is None:
        return None
    try:
        quantum = Decimal(1).scaleb(-places)
        return str(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CanonicalError(f"cannot render {value!r} as a decimal string") from exc


def utc_now() -> datetime:
    """Now, in UTC, truncated to the second (the precision the contract declares)."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def rfc3339(moment: datetime) -> str:
    """An RFC 3339 UTC timestamp ending in Z, to the second."""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_rfc3339(text: str) -> datetime:
    """Parse an RFC 3339 UTC timestamp written by this control plane."""
    if not isinstance(text, str) or not TIMESTAMP_RE.match(text):
        raise CanonicalError(f"not an RFC 3339 UTC timestamp ending in Z: {text!r}")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def plus_seconds(moment: datetime, seconds: int) -> datetime:
    return moment + timedelta(seconds=int(seconds))


# --------------------------------------------------------------------------- #
# contract enforcement
# --------------------------------------------------------------------------- #

# Every contract this package writes or ingests. The version string IS the file
# name, so a document that declares an unknown version names a schema that does
# not exist and is refused rather than guessed at.
CONTRACTS = (
    "task-envelope.v1",
    "route-proposal.v1",
    "validation-result.v1",
    "operator-history-event.v1",
    "approval.v1",
    "work-items.v1",
)


@functools.lru_cache(maxsize=None)
def contract_schema(contract_version: str) -> dict:
    """The JSON Schema for a contract version, read from hearth/contracts/."""
    target = Path(paths.CONTRACTS) / f"{contract_version}.schema.json"
    if not target.is_file():
        raise ContractError(
            f"no contract schema for {contract_version!r} at "
            f"{paths.repo_relative(target)}; this control plane enforces "
            f"{', '.join(CONTRACTS)}")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ContractError(f"contract schema {contract_version} is not JSON: {exc}") from exc


def validate_contract(document: Any, contract_version: str, *, label: str = "$") -> dict:
    """Gate `contract_version` and validate `document` against that schema.

    Returns the document so a caller can write ``doc = validate_contract(doc, ...)``.
    Raises ContractError — never a bare jsonschema error — so every boundary
    refuses with a reason a reader can act on.
    """
    if not isinstance(document, dict):
        raise ContractError(f"{label}: a {contract_version} document is an object, "
                            f"got {type(document).__name__}")
    declared = document.get("contract_version")
    if declared != contract_version:
        raise ContractError(
            f"{label}: unknown contract version {declared!r}; this control plane "
            f"reads {contract_version} only. Re-issue the document under "
            f"{contract_version} or add the new contract before ingesting it.")
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - the venv carries it
        raise ContractError(
            f"{label}: {contract_version} cannot be enforced — jsonschema is not "
            "importable in this interpreter. Refusing rather than admitting an "
            "unchecked document.") from exc
    validator = jsonschema.Draft202012Validator(contract_schema(contract_version))
    errors = sorted(validator.iter_errors(document), key=lambda err: list(err.path))
    if errors:
        first = errors[0]
        where = "$" + "".join(f"[{part!r}]" for part in first.path)
        raise ContractError(
            f"{label}: {contract_version} violation at {where}: {first.message}"
            + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""))
    return document


# --------------------------------------------------------------------------- #
# approval identity (D-112 item 3)
# --------------------------------------------------------------------------- #

APPROVAL_CONTRACT_VERSION = "approval.v1"
RECEIPT_CONTRACT_VERSION = "approval-receipt.v1"

# What the approval identity BINDS. Mutation of any one of these invalidates the
# approval, because the identity is recomputed over exactly this preimage on
# every load. The decision itself is bound separately, by the receipt below, so
# that an approval can be referenced before it is decided without its identity
# moving afterwards.
APPROVAL_BINDING_FIELDS = (
    "contract_version",
    "run_id",
    "proposal_id",
    "validation_id",
    "authorities",
    "node_ids",
    "targets",
    "catalog_version",
    "snapshot_id",
    "requested_by",
    "requested_at",
    "expires_at",
    "policy_version",
)

# Sorted before hashing: the SET is bound, not the order it was written in.
APPROVAL_SET_FIELDS = ("authorities", "node_ids", "targets")

RECEIPT_BINDING_FIELDS = (
    "contract_version",
    "approval_id",
    "decision",
    "decided_at",
    "approving_principal",
    "scope",
    "policy_version",
)


def _binding_preimage(document: Any, fields: tuple[str, ...], what: str) -> dict:
    if not isinstance(document, dict):
        raise CanonicalError(f"a {what} preimage is computed over an object")
    missing = [name for name in fields if name not in document]
    if missing:
        raise CanonicalError(
            f"{what} is missing bound field(s) {', '.join(missing)}; an identity "
            "computed over a partial binding would bind nothing")
    body = {name: document[name] for name in fields}
    for name in APPROVAL_SET_FIELDS:
        if isinstance(body.get(name), list):
            body[name] = sorted(str(item) for item in body[name])
    return _canonicalize(body)


def approval_preimage(record: dict) -> dict:
    return _binding_preimage(record, APPROVAL_BINDING_FIELDS, "an approval record")


def approval_identity(record: dict) -> str:
    """The identity an approval record must carry in `approval_id`."""
    return sha256_hex(canonical_json(approval_preimage(record)))


def receipt_preimage(receipt: dict) -> dict:
    return _binding_preimage(receipt, RECEIPT_BINDING_FIELDS, "a decision receipt")


def receipt_identity(receipt: dict) -> str:
    """The identity a decision receipt must carry in `receipt_id`."""
    return sha256_hex(canonical_json(receipt_preimage(receipt)))


def _is_whole_document(node: Any, identity_field: str) -> bool:
    marker = IDENTITY_FIELDS[identity_field]
    return (isinstance(node, dict) and identity_field in node and marker in node)


def verify_document(document: dict, *, label: str = "$") -> list[dict]:
    """Recompute every identity this document carries and report each result.

    Returns one row per identity checked: ``{path, field, declared, computed,
    ok}``. An empty list means the document declared no identity this control
    plane knows how to recompute — reported by the CLI as a refusal, never as a
    pass, because "nothing to check" and "checked and correct" are different
    answers.
    """
    results: list[dict] = []

    def check(node: dict, path: str) -> None:
        for field in IDENTITY_FIELDS:
            if _is_whole_document(node, field):
                declared = node.get(field)
                computed = identity_of(node, field)
                results.append({"path": path, "field": field, "declared": declared,
                                "computed": computed, "ok": declared == computed})

    def check_approval(node: dict, path: str) -> None:
        """An approval's identity is its BINDING, not the whole record, so it
        cannot be recomputed by the generic rule above."""
        if node.get("contract_version") != APPROVAL_CONTRACT_VERSION:
            return
        if "approval_id" not in node:
            return
        try:
            computed = approval_identity(node)
        except CanonicalError:
            computed = None
        declared = node.get("approval_id")
        results.append({"path": path, "field": "approval_id", "declared": declared,
                        "computed": computed, "ok": computed is not None
                        and declared == computed})
        receipt = node.get("receipt")
        if isinstance(receipt, dict) and "receipt_id" in receipt:
            try:
                computed_receipt = receipt_identity(receipt)
            except CanonicalError:
                computed_receipt = None
            results.append({"path": f"{path}.receipt", "field": "receipt_id",
                            "declared": receipt.get("receipt_id"),
                            "computed": computed_receipt,
                            "ok": computed_receipt is not None
                            and receipt.get("receipt_id") == computed_receipt})

    if isinstance(document, dict):
        check(document, label)
        check_approval(document, label)
        snapshot = document.get("capacity_snapshot")
        if isinstance(snapshot, dict) and isinstance(snapshot.get("document"), dict):
            check(snapshot["document"], f"{label}.capacity_snapshot.document")
            declared = snapshot.get("snapshot_id")
            inline = snapshot["document"].get("snapshot_id")
            results.append({"path": f"{label}.capacity_snapshot.snapshot_id",
                            "field": "snapshot_id", "declared": declared,
                            "computed": inline, "ok": declared == inline})
        proposal_copy = document.get("proposal_copy")
        if isinstance(proposal_copy, dict):
            check(proposal_copy, f"{label}.proposal_copy")
    return results
