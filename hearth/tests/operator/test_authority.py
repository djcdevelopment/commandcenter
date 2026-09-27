"""Authority: evaluated against the real policy, for three real profiles.

These assert the three-value rule and the two invariants that make it safe:
approval never adds a capability (so `denied` stays denied), and the authority
document never leaks into shared truth.
"""

from __future__ import annotations

import json
import unittest

import jsonschema

from hearth.operator import authority as authority_mod
from hearth.operator import core, paths
from hearth.operator.identity import Identity, resolve_from_env
from hearth.tests.operator.support import (ORCHESTRATOR_KEY, OperatorTestCase,
                                           RESEARCH_KEY, UNRESTRICTED_KEY, all_keys)

SCHEMA = json.loads((paths.CONTRACTS / "authority.v1.schema.json").read_text(encoding="utf-8"))
NINE = authority_mod.AUTHORITIES


class AuthorityMapTests(unittest.TestCase):
    def test_the_map_declares_exactly_the_nine_authorities(self) -> None:
        rules = authority_mod.load_authority_map()
        self.assertEqual(set(rules), set(NINE))
        self.assertEqual(len(NINE), 9)

    def test_the_two_always_gated_authorities_are_gated_whatever_the_file_says(self) -> None:
        rules = authority_mod.load_authority_map()
        self.assertTrue(rules["change_machine_or_network"]["human_gate"])
        self.assertTrue(rules["merge_push_deploy"]["human_gate"])
        self.assertEqual(authority_mod.ALWAYS_HUMAN_GATED,
                         {"change_machine_or_network", "merge_push_deploy"})

    def test_every_required_capability_exists_in_the_kernel_taxonomy(self) -> None:
        from hearth.kernel.capabilities import KNOWN_CAPABILITIES

        for name, rule in authority_mod.load_authority_map().items():
            for capability in rule["capabilities"]:
                self.assertIn(capability, KNOWN_CAPABILITIES, f"{name} -> {capability}")

    def test_policy_version_moves_with_either_policy_file(self) -> None:
        import tempfile
        from pathlib import Path

        baseline = authority_mod.policy_version()
        self.assertEqual(baseline, authority_mod.policy_version())
        with tempfile.TemporaryDirectory() as tmp:
            edited = Path(tmp) / "authority-map.toml"
            edited.write_text(
                paths.AUTHORITY_MAP_PATH.read_text(encoding="utf-8").replace(
                    'capabilities = ["test"]', 'capabilities = ["test", "read"]', 1),
                encoding="utf-8")
            self.assertNotEqual(baseline, authority_mod.policy_version(edited))


class AuthorityEvaluationTests(OperatorTestCase):
    def evaluate(self, key) -> dict:
        self.set_key(key)
        document = authority_mod.evaluate(resolve_from_env(), "a" * 64)
        jsonschema.validate(document, SCHEMA)
        return document

    def results(self, key) -> dict:
        return {name: row["result"]
                for name, row in self.evaluate(key)["authorities"].items()}

    def test_research_is_granted_reads_and_generation_and_denied_the_rest(self) -> None:
        results = self.results(RESEARCH_KEY)
        self.assertEqual(results["read_repo"], "granted")
        self.assertEqual(results["call_door_generate"], "granted")
        for denied in ("write_worktree", "run_tests", "submit_mechnet_task",
                       "create_build_request", "lease_gpu",
                       "change_machine_or_network", "merge_push_deploy"):
            self.assertEqual(results[denied], "denied", denied)

    def test_orchestrator_shows_all_three_verdicts(self) -> None:
        results = self.results(ORCHESTRATOR_KEY)
        for granted in ("read_repo", "write_worktree", "run_tests", "call_door_generate",
                        "submit_mechnet_task", "create_build_request"):
            self.assertEqual(results[granted], "granted", granted)
        # It holds repo_write, so merge is gated rather than denied...
        self.assertEqual(results["merge_push_deploy"], "human_required")
        # ...but it holds neither summon nor rotation_admin, so those are DENIED,
        # and a human gate cannot rescue a missing capability.
        self.assertEqual(results["change_machine_or_network"], "denied")
        self.assertEqual(results["lease_gpu"], "denied")

    def test_unrestricted_is_granted_everything_except_the_two_human_gates(self) -> None:
        document = self.evaluate(UNRESTRICTED_KEY)
        results = {name: row["result"] for name, row in document["authorities"].items()}
        self.assertEqual({name for name, value in results.items() if value == "human_required"},
                         {"change_machine_or_network", "merge_push_deploy"})
        self.assertEqual({value for value in results.values()},
                         {"granted", "human_required"})
        self.assertIn("rotation_admin", document["capabilities_granted"])

    def test_a_human_gate_never_rescues_a_missing_capability(self) -> None:
        row = self.evaluate(ORCHESTRATOR_KEY)["authorities"]["change_machine_or_network"]
        self.assertEqual(row["result"], "denied")
        self.assertEqual(row["missing_capabilities"], ["summon"])
        self.assertTrue(row["human_gate"])
        self.assertIn("approval never adds a capability", row["reason"])

    def test_no_identity_denies_everything_with_no_identity(self) -> None:
        document = self.evaluate(None)
        self.assertIsNone(document["caller"])
        self.assertEqual(document["capabilities_granted"], [])
        for name, row in document["authorities"].items():
            self.assertEqual(row["result"], "denied", name)
            self.assertIn("no_identity", row["reason"])

    def test_an_unknown_key_is_no_identity_rather_than_an_error(self) -> None:
        document = self.evaluate("not-a-real-key")
        self.assertIsNone(document["caller"])
        self.assertTrue(all(row["result"] == "denied"
                            for row in document["authorities"].values()))

    def test_the_evaluation_names_the_policy_and_catalog_it_used(self) -> None:
        document = self.evaluate(RESEARCH_KEY)
        self.assertEqual(document["evaluated_against"]["catalog_version"], "a" * 64)
        self.assertEqual(document["evaluated_against"]["policy_version"],
                         authority_mod.policy_version())


class AuthorityStaysOutOfSharedTruthTests(OperatorTestCase):
    def test_authority_is_absent_from_the_snapshot_and_from_current_json(self) -> None:
        from hearth.operator import catalog as catalog_mod
        from hearth.operator import inspection
        from hearth.tests.operator.support import FakeDoor, fake_cli_runner

        catalog = catalog_mod.compile_catalog()
        self.set_key(UNRESTRICTED_KEY)
        result = core.refresh(door=FakeDoor(), cli_runner=fake_cli_runner(),
                              catalog=catalog)
        snapshot_keys = set(all_keys(result["snapshot"]))
        self.assertNotIn("authority", snapshot_keys)
        self.assertNotIn("authorities", snapshot_keys)
        current = json.loads(paths.current_path().read_text(encoding="utf-8"))
        self.assertNotIn("authority", set(all_keys(current)))

        bundle = core.inspect_bundle(resolve_from_env())
        self.assertIn("authority", bundle)
        self.assertEqual(bundle["authority"]["caller"]["id"], "fixture-unrestricted")

        # The per-caller bundle is the ONLY place a caller is named, and it is
        # off to one side of the canonical state.
        written = paths.inspect_dir() / "fixture-unrestricted" / "latest.json"
        self.assertTrue(written.is_file())
        self.assertNotIn("fixture-unrestricted",
                         json.dumps(json.loads(
                             inspection.snapshot_path(result["snapshot"]["snapshot_id"])
                             .read_text(encoding="utf-8"))))

    def test_whoami_needs_no_snapshot(self) -> None:
        self.set_key(RESEARCH_KEY)
        document = core.whoami_document(resolve_from_env())
        self.assertEqual(document["caller"]["profile"], "research")
        self.assertEqual(document["authority"]["authorities"]["read_repo"]["result"],
                         "granted")

    def test_identity_resolution_writes_nothing(self) -> None:
        """Resolving identity must not append a ledger row or touch the registry:
        `lookup`, not `resolve`, and no ledger at all."""
        before = {path.name: path.stat().st_mtime_ns
                  for path in (paths.hearth_root() / "var").iterdir()}
        self.set_key("not-a-real-key")
        resolve_from_env()
        self.set_key(RESEARCH_KEY)
        resolve_from_env()
        after = {path.name: path.stat().st_mtime_ns
                 for path in (paths.hearth_root() / "var").iterdir()}
        self.assertEqual(before, after)


class IdentitySeparationTests(OperatorTestCase):
    def test_the_two_environment_variables_do_different_jobs(self) -> None:
        """HEARTH_ROOT finds the registry; HEARTH_OPERATOR_HOME places state. If
        these were one variable, a worktree run would write into the deployed tree."""
        self.assertEqual(paths.callers_registry_path(), self.registry)
        self.assertEqual(paths.current_path(), self.home / "CURRENT.json")
        self.assertTrue(str(paths.snapshots_dir()).startswith(str(self.home)))

    def test_a_missing_registry_is_no_identity_with_a_remedy(self) -> None:
        self.registry.unlink()
        self.set_key(RESEARCH_KEY)
        identity = resolve_from_env()
        self.assertFalse(identity.present)
        self.assertIn("HEARTH_ROOT", identity.reason)

    def test_an_identity_with_no_profile_holds_nothing(self) -> None:
        from hearth.operator.identity import _capabilities_for

        self.assertEqual(_capabilities_for(None), frozenset())
        self.assertEqual(_capabilities_for("unprofiled"), frozenset())
        self.assertIn("read", _capabilities_for("research"))

    def test_a_caller_with_no_profile_field_is_denied_everything(self) -> None:
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["legacy-key"] = {"id": "fixture-legacy", "runner_class": "human",
                                  "node": "omen"}
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        self.set_key("legacy-key")
        identity = resolve_from_env()
        self.assertTrue(identity.present)
        self.assertEqual(identity.capabilities, frozenset())
        document = authority_mod.evaluate(identity, "a" * 64)
        self.assertTrue(all(row["result"] == "denied"
                            for row in document["authorities"].values()))


if __name__ == "__main__":
    unittest.main()
