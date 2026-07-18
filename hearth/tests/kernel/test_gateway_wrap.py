"""Gateway wrapper: auth gate, timing, digests, ledger provenance — no HTTP."""

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from hearth.kernel.auth import AUTH_TOOL, AuthRegistry
from hearth.kernel.context import HearthContext
from hearth.kernel.gateway import builtin_get_tools, make_wrapper
from hearth.kernel.guards import GuardStack
from hearth.kernel.ledger import Ledger, sha256_digest


def fake_echo(message: str, repeat: int = 1) -> dict[str, Any]:
    """Echo a message a number of times."""
    return {"echo": message * repeat}


def fake_boom(message: str) -> str:
    """Always fails."""
    raise RuntimeError(f"boom: {message}")


def fake_tool_with_fields(message: str) -> dict[str, Any]:
    """Returns a routed-inference-shaped result: ok:false + provenance + tokens."""
    return {
        "ok": False,
        "error": "HTTPConnectionPool... Read timed out",
        "backend": "b70",
        "routed_by": "pinned",
        "occupancy": "busy",
        "tokens_in": 123,
        "tokens_out": 45,
    }


class GatewayWrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "knowledge").mkdir()
        self.ledger = Ledger(root / "ledger")
        callers = root / "callers.json"
        callers.write_text(json.dumps({
            "good-key": {"id": "claude", "runner_class": "frontier", "node": "omen"},
        }), encoding="utf-8")
        self.auth = AuthRegistry(callers_path=callers, ledger=self.ledger)
        self.guards = GuardStack(repo_root=root)
        self.hearth = HearthContext(repo_root=root, ledger=self.ledger)
        self.key = "good-key"
        self.wrapped = make_wrapper(fake_echo, self.hearth, self.auth, self.guards,
                                    lambda: self.key)

    def test_result_returned_and_provenance_logged(self):
        result = self.wrapped(message="hi", repeat=2)
        self.assertEqual(result, {"echo": "hihi"})
        events = self.ledger.query(tool="fake_echo")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["caller"],
                         {"id": "claude", "runner_class": "frontier", "node": "omen"})
        self.assertTrue(event["ok"])
        self.assertIsNone(event["error"])
        self.assertEqual(event["args_digest"], sha256_digest({"message": "hi", "repeat": 2}))
        self.assertEqual(event["result_digest"], sha256_digest({"echo": "hihi"}))
        self.assertIn("hi", event["args_preview"])
        self.assertGreaterEqual(event["duration_ms"], 0)

    def test_unknown_key_rejected_and_auth_event_logged(self):
        self.key = "bad-key"
        with self.assertRaises(PermissionError):
            self.wrapped(message="hi")
        self.assertEqual(len(self.ledger.query(tool=AUTH_TOOL, ok=False)), 1)
        self.assertEqual(len(self.ledger.query(tool="fake_echo")), 0)

    def test_provider_exception_logged_and_propagated(self):
        wrapped = make_wrapper(fake_boom, self.hearth, self.auth, self.guards,
                               lambda: "good-key")
        with self.assertRaises(RuntimeError):
            wrapped(message="x")
        events = self.ledger.query(tool="fake_boom", ok=False)
        self.assertEqual(len(events), 1)
        self.assertIn("boom: x", events[0]["error"])

    def test_wrapper_preserves_name_doc_and_signature(self):
        self.assertEqual(self.wrapped.__name__, "fake_echo")
        self.assertEqual(self.wrapped.__doc__, fake_echo.__doc__)
        params = inspect.signature(self.wrapped).parameters
        self.assertEqual(list(params), ["message", "repeat"])
        self.assertIs(params["message"].annotation, str)
        self.assertEqual(params["repeat"].default, 1)

    def test_wrapper_sets_caller_on_context(self):
        self.wrapped(message="hi")
        self.assertEqual(self.hearth.caller.id, "claude")

    def test_builtin_kernel_status_and_kernel_change(self):
        import os
        os.environ["HEARTH_ROOT"] = self.tmp.name
        self.addCleanup(os.environ.pop, "HEARTH_ROOT", None)
        tools = builtin_get_tools(self.hearth, ["builtin"])
        by_name = {fn.__name__: fn for fn in tools}
        self.assertEqual(set(by_name), {"kernel_status", "kernel_change"})

        status = make_wrapper(by_name["kernel_status"], self.hearth, self.auth,
                              self.guards, lambda: "good-key")()
        self.assertEqual(status["providers"], ["builtin"])

        change = make_wrapper(by_name["kernel_change"], self.hearth, self.auth,
                              self.guards, lambda: "good-key")
        ack = change(description="test ceremony", diff_path="none.diff")
        self.assertTrue(ack["acknowledged"])
        self.assertTrue(Path(ack["snapshot"]).is_file())
        ceremony = self.ledger.query(tool="kernel_change.snapshot")
        self.assertEqual(len(ceremony), 1)
        self.assertEqual(ceremony[0]["caller"]["id"], "claude")
        self.assertEqual(len(self.ledger.query(tool="kernel_change")), 1)

    def test_wrapper_extracts_new_fields_and_classifies_error(self):
        wrapped = make_wrapper(fake_tool_with_fields, self.hearth, self.auth,
                               self.guards, lambda: "good-key")
        result = wrapped(message="x")
        self.assertEqual(result["backend"], "b70")
        events = self.ledger.query(tool="fake_tool_with_fields")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["backend"], "b70")
        self.assertEqual(event["routed_by"], "pinned")
        self.assertEqual(event["occupancy"], "busy")
        self.assertEqual(event["error_code"], "timeout")
        self.assertEqual(event["cost"]["tokens_in"], 123)
        self.assertEqual(event["cost"]["tokens_out"], 45)
        self.assertTrue(event["ok"])


if __name__ == "__main__":
    unittest.main()
