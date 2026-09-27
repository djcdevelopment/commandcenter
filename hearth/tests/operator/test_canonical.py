"""Canonical encoding and content-derived identities.

If these are wrong, everything above them is decoration: two agents cannot cite
the same environment if the identifier for that environment depends on key
order, whitespace, a float repr, or who rendered it last.
"""

from __future__ import annotations

import json
import unittest

from hearth.operator import canonical
from hearth.operator.canonical import (CanonicalError, canonical_json, identity_of,
                                       preimage, verify_document)

CATALOG = {
    "catalog_version": "",
    "generator_version": "test/1.0.0",
    "generated_from": [{"path": "b.toml", "sha256": "b" * 64},
                       {"path": "a.toml", "sha256": "a" * 64}],
    "hosts": [{"id": "omen", "gpus": [{"type": "b70", "vram_gb": "32.50"}]},
              {"id": "am4", "gpus": []}],
    "rungs": [{"id": "omen-arc", "pin_only": False}],
}


class CanonicalJsonTests(unittest.TestCase):
    def test_keys_are_sorted_and_whitespace_is_absent(self) -> None:
        self.assertEqual(canonical_json({"b": 1, "a": {"d": 2, "c": 3}}),
                         b'{"a":{"c":3,"d":2},"b":1}')

    def test_non_ascii_is_not_escaped_and_null_survives(self) -> None:
        self.assertEqual(canonical_json({"note": "fire on the steppe — é", "gone": None}),
                         '{"gone":null,"note":"fire on the steppe — é"}'.encode("utf-8"))

    def test_there_is_no_trailing_newline(self) -> None:
        self.assertFalse(canonical_json({"a": 1}).endswith(b"\n"))

    def test_a_float_is_refused_rather_than_encoded(self) -> None:
        with self.assertRaises(CanonicalError) as caught:
            canonical_json({"vram_gb": 32.5})
        self.assertIn("integers only", str(caught.exception))
        self.assertIn("vram_gb", str(caught.exception))

    def test_a_float_nested_in_a_list_is_refused(self) -> None:
        with self.assertRaises(CanonicalError):
            canonical_json({"rows": [{"tps": 106.0}]})

    def test_decimal_str_is_fixed_precision_and_half_up(self) -> None:
        self.assertEqual(canonical.decimal_str(32.5), "32.50")
        self.assertEqual(canonical.decimal_str("8"), "8.00")
        self.assertEqual(canonical.decimal_str(26.795), "26.80")
        self.assertIsNone(canonical.decimal_str(None))


class IdentityTests(unittest.TestCase):
    def test_same_semantic_input_yields_the_same_identity(self) -> None:
        reordered = json.loads(json.dumps(CATALOG))
        reordered["generated_from"] = list(reversed(reordered["generated_from"]))
        reordered["hosts"] = list(reversed(reordered["hosts"]))
        self.assertEqual(identity_of(CATALOG, "catalog_version"),
                         identity_of(reordered, "catalog_version"))

    def test_whitespace_and_key_order_do_not_change_it(self) -> None:
        round_tripped = json.loads(json.dumps(CATALOG, indent=4, sort_keys=True))
        self.assertEqual(identity_of(CATALOG, "catalog_version"),
                         identity_of(round_tripped, "catalog_version"))

    def test_one_changed_material_field_changes_it(self) -> None:
        changed = json.loads(json.dumps(CATALOG))
        changed["rungs"][0]["pin_only"] = True
        self.assertNotEqual(identity_of(CATALOG, "catalog_version"),
                            identity_of(changed, "catalog_version"))

    def test_a_changed_source_digest_changes_it(self) -> None:
        changed = json.loads(json.dumps(CATALOG))
        changed["generated_from"][0]["sha256"] = "c" * 64
        self.assertNotEqual(identity_of(CATALOG, "catalog_version"),
                            identity_of(changed, "catalog_version"))

    def test_the_own_identity_field_is_excluded(self) -> None:
        stamped = dict(CATALOG, catalog_version="deadbeef")
        self.assertNotIn("catalog_version", preimage(stamped, "catalog_version"))
        self.assertEqual(identity_of(CATALOG, "catalog_version"),
                         identity_of(stamped, "catalog_version"))

    def test_presentation_is_excluded_at_every_depth(self) -> None:
        decorated = json.loads(json.dumps(CATALOG))
        decorated["presentation"] = {"generated_at": "2026-09-17T12:00:00Z"}
        decorated["hosts"][0]["presentation"] = {"rendered": "yes"}
        self.assertEqual(identity_of(CATALOG, "catalog_version"),
                         identity_of(decorated, "catalog_version"))
        self.assertNotIn("presentation", canonical_json(
            preimage(decorated, "catalog_version")).decode("utf-8"))

    def test_set_arrays_are_sorted_inside_a_snapshot_field_object(self) -> None:
        """`leases` arrives as a field object, so the declared sort key has to be
        carried across `value` or two agents observing the same two leases in a
        different order would report different snapshot identities."""
        one = {"observed_at": "2026-09-17T12:00:00Z",
               "leases": {"value": [{"id": "b"}, {"id": "a"}], "ttl_s": 30}}
        two = {"observed_at": "2026-09-17T12:00:00Z",
               "leases": {"value": [{"id": "a"}, {"id": "b"}], "ttl_s": 30}}
        self.assertEqual(identity_of(one, "snapshot_id"), identity_of(two, "snapshot_id"))

    def test_authored_order_is_preserved_for_undeclared_arrays(self) -> None:
        one = {"generated_from": [], "steps": ["b", "a"]}
        two = {"generated_from": [], "steps": ["a", "b"]}
        self.assertNotEqual(identity_of(one, "catalog_version"),
                            identity_of(two, "catalog_version"))


class VerifyIdsTests(unittest.TestCase):
    def test_a_byte_edited_after_assignment_is_detected(self) -> None:
        document = dict(CATALOG)
        document["catalog_version"] = identity_of(document, "catalog_version")
        self.assertTrue(all(row["ok"] for row in verify_document(document)))

        tampered = json.loads(json.dumps(document))
        tampered["rungs"][0]["pin_only"] = True     # one byte of meaning
        results = verify_document(tampered)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["declared"], document["catalog_version"])

    def test_a_reference_is_not_mistaken_for_a_document(self) -> None:
        """An inspection bundle cites `catalog: {catalog_version, path}`. That is
        a citation, not a catalog, and re-hashing it would fail forever."""
        bundle = {"catalog": {"catalog_version": "a" * 64, "path": "knowledge/x.json"}}
        self.assertEqual(verify_document(bundle), [])

    def test_the_inline_snapshot_in_a_bundle_is_checked(self) -> None:
        snapshot = {"kind": "planning", "observed_at": "2026-09-17T12:00:00Z",
                    "rungs": {}, "leases": {"value": []}}
        snapshot["snapshot_id"] = identity_of(snapshot, "snapshot_id")
        bundle = {"capacity_snapshot": {"snapshot_id": snapshot["snapshot_id"],
                                        "document": snapshot}}
        self.assertTrue(all(row["ok"] for row in verify_document(bundle)))

        bundle["capacity_snapshot"]["document"]["kind"] = "validation"
        self.assertFalse(all(row["ok"] for row in verify_document(bundle)))


class TimestampTests(unittest.TestCase):
    def test_round_trip_is_second_precision_utc(self) -> None:
        now = canonical.utc_now()
        text = canonical.rfc3339(now)
        self.assertTrue(text.endswith("Z"))
        self.assertEqual(canonical.parse_rfc3339(text), now)

    def test_a_non_conforming_timestamp_is_refused(self) -> None:
        for bad in ("2026-09-17 12:00:00", "2026-09-17T12:00:00+00:00", "", None):
            with self.assertRaises(CanonicalError):
                canonical.parse_rfc3339(bad)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
