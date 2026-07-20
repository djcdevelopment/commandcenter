"""Auth: caller resolution from callers.json + rejection recorded in the ledger."""

import json
import tempfile
import unittest
from pathlib import Path

from hearth.kernel.auth import AUTH_TOOL, AuthRegistry, Caller
from hearth.kernel.ledger import Ledger


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.callers_path = root / "callers.json"
        self.callers_path.write_text(json.dumps({
            "k-frontier": {"id": "claude", "runner_class": "frontier", "node": "omen"},
        }), encoding="utf-8")
        self.ledger = Ledger(root / "ledger")
        self.auth = AuthRegistry(callers_path=self.callers_path, ledger=self.ledger)

    def test_resolve_known_key(self):
        caller = self.auth.resolve("k-frontier")
        self.assertEqual(caller, Caller(id="claude", runner_class="frontier", node="omen"))

    def test_unknown_key_rejected_and_logged(self):
        self.assertIsNone(self.auth.resolve("not-a-key"))
        events = self.ledger.query(tool=AUTH_TOOL, ok=False)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["caller"]["id"], "__unauthenticated__")
        self.assertIn("unknown or missing", events[0]["error"])

    def test_missing_key_rejected_and_logged(self):
        self.assertIsNone(self.auth.resolve(None))
        self.assertEqual(len(self.ledger.query(tool=AUTH_TOOL, ok=False)), 1)

    def test_raw_key_never_written_to_ledger(self):
        self.auth.resolve("super-secret-key")
        self.assertNotIn("super-secret-key",
                         self.ledger.events_path.read_text(encoding="utf-8"))

    def test_shipped_dev_local_key(self):
        """The checked-in default registry carries a PUBLIC secret — it is in
        git. ADR-0023 gives it the `probe` role (kernel_status only); before
        that it had no profile and therefore the whole 47-tool surface, so
        starting the gateway without --callers put the lab one known string
        away from full authority. The profile assertion is the guard on that."""
        registry = AuthRegistry()
        caller = registry.resolve("dev-local")
        self.assertEqual(caller, Caller(id="dev-local", runner_class="human",
                                        node="omen", profile="probe"))
        self.assertFalse(caller.is_legacy)

    def test_bad_runner_class_in_registry_refused(self):
        self.callers_path.write_text(json.dumps({
            "k": {"id": "x", "runner_class": "robot", "node": "omen"},
        }), encoding="utf-8")
        with self.assertRaises(ValueError):
            AuthRegistry(callers_path=self.callers_path)


if __name__ == "__main__":
    unittest.main()
