"""The CLI surface: exit codes, refusals, and `verify-ids`.

Exit codes are part of the contract — a cold agent scripts against them — so
they are asserted rather than described.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from hearth.operator import core, paths
from hearth.operator.cli import main
from hearth.tests.operator.support import (FakeDoor, OperatorTestCase, RESEARCH_KEY,
                                           fake_cli_runner)


class CliTestCase(OperatorTestCase):
    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()


class CatalogCommandTests(CliTestCase):
    def test_check_is_green_against_the_committed_catalog(self) -> None:
        code, out, _ = self.run_cli("catalog", "--check")
        self.assertEqual(code, 0)
        self.assertIn("current", out)
        self.assertIn(json.loads(paths.CATALOG_PATH.read_text(encoding="utf-8"))
                      ["catalog_version"], out)


class InspectCommandTests(CliTestCase):
    def refresh(self) -> dict:
        from hearth.operator import catalog as catalog_mod

        return core.refresh(door=FakeDoor(), cli_runner=fake_cli_runner(),
                            catalog=catalog_mod.compile_catalog())

    def test_inspect_without_a_current_snapshot_exits_1_with_the_reason(self) -> None:
        code, _, err = self.run_cli("inspect")
        self.assertEqual(code, 1)
        self.assertIn("never captures", err)
        self.assertIn("--refresh", err)

    def test_inspect_json_emits_the_bundle(self) -> None:
        snapshot_id = self.refresh()["snapshot"]["snapshot_id"]
        code, out, _ = self.run_cli("inspect", "--json")
        self.assertEqual(code, 0)
        bundle = json.loads(out)
        self.assertEqual(bundle["contract_version"], "inspection-bundle.v1")
        self.assertEqual(bundle["capacity_snapshot"]["snapshot_id"], snapshot_id)
        self.assertIsNone(bundle["authority"]["caller"])

    def test_the_human_rendering_names_both_identifiers(self) -> None:
        result = self.refresh()
        code, out, _ = self.run_cli("inspect")
        self.assertEqual(code, 0)
        self.assertIn(result["snapshot"]["snapshot_id"], out)
        self.assertIn(result["snapshot"]["catalog_version"], out)
        self.assertIn("planning window closes", out)

    def test_a_corrupt_current_json_exits_1(self) -> None:
        self.refresh()
        paths.current_path().write_text("{ not json", encoding="utf-8")
        code, _, err = self.run_cli("inspect", "--json")
        self.assertEqual(code, 1)
        self.assertIn("corrupt", err)


class WhoamiCommandTests(CliTestCase):
    def test_with_no_key_the_caller_is_null_and_everything_is_denied(self) -> None:
        code, out, _ = self.run_cli("whoami", "--json")
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertIsNone(document["caller"])
        self.assertTrue(all(row["result"] == "denied"
                            for row in document["authority"]["authorities"].values()))

    def test_with_a_key_it_names_the_profile(self) -> None:
        self.set_key(RESEARCH_KEY)
        code, out, _ = self.run_cli("whoami")
        self.assertEqual(code, 0)
        self.assertIn("fixture-research", out)
        self.assertIn("research", out)
        self.assertNotIn(RESEARCH_KEY, out, "a key must never be echoed")


class VerifyIdsCommandTests(CliTestCase):
    def test_the_committed_catalog_verifies(self) -> None:
        code, out, _ = self.run_cli("verify-ids", str(paths.CATALOG_PATH))
        self.assertEqual(code, 0)
        self.assertIn("identities match", out)

    def test_a_tampered_document_exits_1_and_shows_both_identities(self) -> None:
        document = json.loads(paths.CATALOG_PATH.read_text(encoding="utf-8"))
        document["rungs"][0]["context_bytes"] = 1
        tampered = self.home / "tampered.json"
        tampered.write_text(json.dumps(document), encoding="utf-8")
        code, out, err = self.run_cli("verify-ids", str(tampered))
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertIn("the bytes changed after the identity was assigned", err)

    def test_a_document_with_no_identity_exits_2_rather_than_passing(self) -> None:
        plain = self.home / "plain.json"
        plain.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
        code, _, err = self.run_cli("verify-ids", str(plain))
        self.assertEqual(code, 2)
        self.assertIn("nothing checked is not the same as verified", err)

    def test_a_missing_file_exits_2(self) -> None:
        code, _, err = self.run_cli("verify-ids", str(self.home / "absent.json"))
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_a_snapshot_file_verifies_and_detects_an_edit(self) -> None:
        from hearth.operator import catalog as catalog_mod

        result = core.refresh(door=FakeDoor(), cli_runner=fake_cli_runner(),
                              catalog=catalog_mod.compile_catalog())
        code, _, _ = self.run_cli("verify-ids", str(result["path"]))
        self.assertEqual(code, 0)

        document = json.loads(result["path"].read_text(encoding="utf-8"))
        document["rungs"]["omen-arc"]["ready"]["value"] = False
        edited = self.home / "edited-snapshot.json"
        edited.write_text(json.dumps(document), encoding="utf-8")
        code, _, _ = self.run_cli("verify-ids", str(edited))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
