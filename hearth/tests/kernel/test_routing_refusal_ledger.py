"""Payload routing refusals remain structured through the gateway ledger."""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.kernel.auth import AuthRegistry
from hearth.kernel.context import HearthContext
from hearth.kernel.gateway import make_wrapper
from hearth.kernel.guards import GuardStack
from hearth.kernel.ledger import Ledger
from hearth.toolsurface.inference import local_generate


class RoutingRefusalLedgerTest(unittest.TestCase):
    def test_query_contains_machine_readable_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pool = root / "backends.toml"
            pool.write_text(textwrap.dedent("""
                default = "local"
                [[backend]]
                name = "local"
                endpoint = "http://local"
                api = "ollama"
                settings = { context_bytes = 100 }
                [[backend]]
                name = "cloud"
                endpoint = "http://cloud"
                api = "ollama"
                tags = ["cloud-overflow"]
                settings = { context_bytes = 200 }
            """), encoding="utf-8")
            callers = root / "callers.json"
            callers.write_text(json.dumps({
                "key": {"id": "test", "runner_class": "local", "node": "omen",
                        "profile": "unrestricted"},
            }), encoding="utf-8")
            ledger = Ledger(root / "ledger")
            hearth = HearthContext(repo_root=root, ledger=ledger)
            wrapper = make_wrapper(
                local_generate,
                hearth,
                AuthRegistry(callers_path=callers, ledger=ledger),
                GuardStack(repo_root=root),
                lambda: "key",
            )
            with patch.dict("os.environ", {"HEARTH_BACKENDS": str(pool)}), \
                 patch("hearth.toolsurface.inference.check_occupancy",
                       return_value={"occupancy": "unknown"}):
                result = wrapper(prompt="x" * 1000)

            self.assertFalse(result["ok"])
            self.assertEqual(result["routing_refusal"]["reason"],
                             "payload_over_budget_no_eligible_backend")
            events = ledger.query(tool="local_generate")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["error_code"], "routing_refusal")
            refusal = events[0]["routing_refusal"]
            self.assertEqual(refusal["payload_bytes"], 1000)
            self.assertEqual(refusal["required_context_bytes"], 1000)
            self.assertTrue(refusal["attempted"])


if __name__ == "__main__":
    unittest.main()
