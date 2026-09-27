"""The capability catalog: byte-stable, drift-detecting, honest about sources.

Compiled from the real repository sources, not a fixture tree. A test that
compiled a toy inventory would prove the code runs; these prove the catalog this
repository actually publishes is the one two cold agents will cite.
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

import jsonschema

from hearth.operator import catalog as catalog_mod
from hearth.operator import core, paths
from hearth.operator.canonical import canonical_json, identity_of, verify_document
from hearth.tests.operator.support import OperatorTestCase, all_keys

SCHEMA = json.loads((paths.CONTRACTS / "capability-catalog.v1.schema.json")
                    .read_text(encoding="utf-8"))


class CatalogCompilationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = catalog_mod.compile_catalog()

    def test_it_validates_against_its_schema(self) -> None:
        jsonschema.validate(self.document, SCHEMA)

    def test_compilation_is_byte_stable_across_two_runs(self) -> None:
        again = catalog_mod.compile_catalog()
        self.assertEqual(catalog_mod.render(self.document), catalog_mod.render(again))
        self.assertEqual(self.document["catalog_version"], again["catalog_version"])

    def test_the_identity_matches_its_content(self) -> None:
        self.assertEqual(self.document["catalog_version"],
                         identity_of(self.document, "catalog_version"))
        self.assertTrue(all(row["ok"] for row in verify_document(self.document)))

    def test_it_carries_no_wall_clock(self) -> None:
        keys = set(all_keys(self.document))
        for forbidden in ("gathered_at", "generated_at", "observed_at", "timestamp", "now"):
            self.assertNotIn(forbidden, keys)

    def test_hosts_carry_the_inventory_gpus(self) -> None:
        omen = next(host for host in self.document["hosts"] if host["id"] == "omen")
        self.assertEqual(len(omen["gpus"]), 2)
        self.assertEqual({gpu["type"] for gpu in omen["gpus"]}, {"intel-arc-b70"})
        # A measured value is a fixed-precision STRING, never a float.
        self.assertEqual({gpu["vram_gb"] for gpu in omen["gpus"]}, {"32.50"})
        self.assertTrue(omen["schedulable"])
        am4 = next(host for host in self.document["hosts"] if host["id"] == "am4")
        self.assertTrue(any(gpu["uuid"] for gpu in am4["gpus"]))

    def test_pin_only_is_derived_from_an_empty_tag_list(self) -> None:
        rungs = {rung["id"]: rung for rung in self.document["rungs"]}
        self.assertFalse(rungs["omen-arc"]["pin_only"], "the default rung is tagged")
        self.assertTrue(rungs["omen-swap"]["pin_only"], "no tags means pin-only")
        self.assertTrue(rungs["omen-arc-oss"]["pin_only"])
        self.assertEqual(rungs["omen-arc"]["context_bytes"], 57344)
        self.assertEqual(rungs["omen-arc"]["parallel_slots"], 8)
        self.assertEqual(rungs["omen-arc"]["node"], "omen")

    def test_retired_rungs_are_visible_and_marked(self) -> None:
        retired = {rung["id"] for rung in self.document["rungs"] if rung["retired"]}
        self.assertEqual(retired, {"omen-ollama", "am4-moe", "am4-oxen"})
        # Visible, not deleted: a tombstone an orchestrator can see is a fact it
        # can plan around; a missing row is a silence it cannot.
        self.assertIn("omen-ollama", {rung["id"] for rung in self.document["rungs"]})

    def test_measurements_exist_only_where_a_structured_source_measured_them(self) -> None:
        models = {model["id"]: model for model in self.document["models"]}
        qwen = models["qwen3-30b-a3b"]
        self.assertEqual(qwen["served_by"], ["omen-arc"])
        self.assertEqual(qwen["resident_on"], ["omen"])
        measurement = qwen["measurements"][0]
        self.assertEqual(measurement["source"], "knowledge/omen_catalog.json")
        self.assertGreaterEqual(measurement["sample_count"], 1)
        self.assertEqual(measurement["expected_gen_tps"], "106.00")
        # A model a rung serves but nothing measured gets NO measurement row.
        unmeasured = [model for model in self.document["models"]
                      if model["served_by"] and not model["measurements"]]
        self.assertTrue(unmeasured, "some served models are unmeasured; that is the honest case")
        for model in self.document["models"]:
            for row in model["measurements"]:
                self.assertTrue(row["source"], "a measurement must name its source")

    def test_tools_come_from_the_kernel_taxonomy(self) -> None:
        from hearth.kernel.capabilities import TOOL_CAPABILITY

        tools = {tool["id"]: tool["capability"] for tool in self.document["tools"]}
        self.assertEqual(tools, dict(TOOL_CAPABILITY))
        self.assertEqual(tools["operator_inspect"], "query")
        self.assertEqual(tools["operator_whoami"], "query")
        self.assertEqual(tools["operator_catalog"], "knowledge_write")

    def test_loops_carry_status_words_and_evidence(self) -> None:
        loops = {loop["id"]: loop for loop in self.document["loops"]}
        self.assertEqual(loops["research"]["implementations"][0]["status"], "LIVE")
        self.assertEqual(loops["review"]["status"], "ABSENT")
        review = {impl["id"]: impl["status"] for impl in loops["review"]["implementations"]}
        self.assertEqual(set(review.values()), {"BUILT NOT DEPLOYED"})
        self.assertEqual(len(review), 2)
        planning = {impl["id"]: impl["status"]
                    for impl in loops["planning"]["implementations"]}
        self.assertEqual(planning["plan_execution"], "LIVE")
        self.assertEqual(planning["propose_schedule"], "BUILT NOT DEPLOYED")
        for loop in self.document["loops"]:
            for impl in loop["implementations"]:
                self.assertTrue(impl["last_evidence"].strip(),
                                f"{impl['id']} has a status with no evidence")

    def test_harnesses_come_from_the_registry(self) -> None:
        harnesses = {row["id"]: row for row in self.document["harnesses"]}
        deep = harnesses["deepagents-run-flash-dense"]
        self.assertEqual(deep["status"], "BUILT NOT DEPLOYED")
        self.assertEqual(deep["commit"], "1bad53cadff5f6b4e4c36e9afbdf81f8667e807c")
        self.assertIn("independent-verifier", deep["subagents"])
        self.assertIn("claude-code", harnesses)
        self.assertIn("codex-cli", harnesses)

    def test_deterministic_tools_and_policies_are_present(self) -> None:
        ids = {row["id"] for row in self.document["deterministic_tools"]}
        self.assertLessEqual({"run_tests", "lint_digest", "git_status", "git_log",
                              "git_diff", "adr_index_check", "doorcheck", "fleet_ping",
                              "rotation_preflight"}, ids)
        self.assertIn("baseline_suites", self.document["test_policy"])
        self.assertEqual(len(self.document["test_policy"]["known_failures"]), 2)
        self.assertTrue(self.document["worktree_policy"]["one_writer_per_mutable_surface"])
        systems = {row["id"] for row in self.document["artifact_systems"]}
        self.assertIn("operator-system-history", systems)

    def test_it_contains_no_key_no_token_and_no_caller(self) -> None:
        keys = set(all_keys(self.document))
        for forbidden in ("caller", "caller_id", "key", "api_key", "token", "secret",
                          "profile", "authority", "x-hearth-key"):
            self.assertNotIn(forbidden, keys, f"catalog carries a {forbidden} field")
        text = canonical_json(self.document).decode("utf-8").lower()
        for smell in ("x-hearth-key", "bearer ", "callers.json"):
            self.assertNotIn(smell, text)


class CatalogDriftTests(OperatorTestCase):
    def test_check_is_green_for_the_committed_catalog(self) -> None:
        ok, message, _ = catalog_mod.check_catalog()
        self.assertTrue(ok, message)

    def test_check_detects_a_written_catalog_that_no_longer_matches(self) -> None:
        target = self.home / "capability_catalog.json"
        document = catalog_mod.compile_catalog()
        mutated = json.loads(catalog_mod.render(document))
        mutated["rungs"][0]["context_bytes"] = 1
        target.write_text(json.dumps(mutated), encoding="utf-8")
        ok, message, _ = catalog_mod.check_catalog(target)
        self.assertFalse(ok)
        self.assertIn("STALE", message)

    def test_check_reports_a_missing_catalog(self) -> None:
        ok, message, _ = catalog_mod.check_catalog(self.home / "absent.json")
        self.assertFalse(ok)
        self.assertIn("missing", message)

    def test_one_changed_source_byte_moves_the_digest_and_the_version(self) -> None:
        """A source edit must be visible in generated_from[] AND in the identity —
        that is the whole mechanism by which a stale catalog is detectable."""
        original = catalog_mod.compile_catalog()
        copy = self.home / "harnesses.toml"
        shutil.copyfile(paths.HARNESSES_PATH, copy)
        text = copy.read_text(encoding="utf-8")
        copy.write_text(text.replace('kind = "cli-agent"', 'kind = "cli-agenT"', 1),
                        encoding="utf-8")
        sources = tuple(copy if path == paths.HARNESSES_PATH else path
                        for path in catalog_mod.SOURCES)
        with mock.patch.object(paths, "HARNESSES_PATH", copy), \
                mock.patch.object(catalog_mod, "SOURCES", sources):
            edited = catalog_mod.compile_catalog()

        before = {row["path"]: row["sha256"] for row in original["generated_from"]}
        after = {Path(row["path"]).name: row["sha256"] for row in edited["generated_from"]}
        self.assertNotEqual(before["hearth/etc/harnesses.toml"], after["harnesses.toml"])
        self.assertNotEqual(original["catalog_version"], edited["catalog_version"])

    def test_a_checkout_line_ending_translation_moves_nothing(self) -> None:
        """git checks this tree out CRLF on Windows and LF on a fleet node. If a
        digest or a --check comparison depended on that, two cold agents on two
        checkouts of the SAME content would report different catalog versions."""
        from hearth.operator.canonical import file_sha256, source_sha256

        lf = self.home / "source-lf.toml"
        crlf = self.home / "source-crlf.toml"
        body = paths.HARNESSES_PATH.read_bytes().replace(b"\r\n", b"\n")
        lf.write_bytes(body)
        crlf.write_bytes(body.replace(b"\n", b"\r\n"))
        self.assertEqual(source_sha256(lf), source_sha256(crlf))
        self.assertNotEqual(file_sha256(lf), file_sha256(crlf))
        self.assertIn("normalized", self.document_rule())

        # ...and the written catalog still checks out green when the checkout
        # gave it CRLF line endings.
        target = self.home / "capability_catalog.json"
        document = catalog_mod.compile_catalog()
        target.write_bytes(catalog_mod.render(document).encode("utf-8")
                           .replace(b"\n", b"\r\n"))
        ok, message, _ = catalog_mod.check_catalog(target)
        self.assertTrue(ok, message)

    def document_rule(self) -> str:
        return catalog_mod.compile_catalog()["source_digest_rule"]

    def test_compile_appends_catalog_compiled_only_when_the_version_changes(self) -> None:
        from hearth.operator import history

        target = self.home / "capability_catalog.json"
        first = core.compile_and_write(target)
        self.assertTrue(first["changed"])
        events = [row for row in history.read_all() if row["event_type"] == "catalog.compiled"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["catalog_version"],
                         first["document"]["catalog_version"])
        self.assertIsNone(events[0]["payload"]["previous_catalog_version"])

        second = core.compile_and_write(target)
        self.assertFalse(second["changed"])
        events = [row for row in history.read_all() if row["event_type"] == "catalog.compiled"]
        self.assertEqual(len(events), 1, "an unchanged compile must not append an event")


if __name__ == "__main__":
    unittest.main()
