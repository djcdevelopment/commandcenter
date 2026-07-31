"""Gateway wrapper: auth gate, timing, digests, ledger provenance — no HTTP."""

import inspect
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any

from hearth.kernel import capabilities
from hearth.kernel.auth import AUTH_TOOL, AuthRegistry
from hearth.kernel.context import HearthContext
from hearth.kernel.gateway import _ledger_safe_args, builtin_get_tools, make_wrapper
from hearth.kernel.guards import GuardStack
from hearth.kernel.ledger import Ledger, json_dumps_canonical, sha256_digest

# ADR-0023: synthetic fixtures must be mapped like real tools. Every MOUNTED
# tool is guaranteed a capability by assert_surface_complete at startup, so an
# unmapped tool is a state production cannot reach; before the fail-open was
# inverted these fixtures rode the profile-less "allow everything" path instead.
# Mapping them here models the production guarantee rather than a hole in it.
def _map_fixture_tools(test, **tools):
    patcher = mock.patch.dict(capabilities.TOOL_CAPABILITY, tools)
    patcher.start()
    test.addCleanup(patcher.stop)



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


class LedgerSafeArgsTest(unittest.TestCase):
    def test_execution_prompt_is_replaced_with_metadata(self):
        original = {
            "operation": "llm.chat",
            "arguments": {"prompt": "snowman \N{SNOWMAN}", "model": "gpt-oss-120b"},
        }

        safe = _ledger_safe_args("submit_execution", original)

        self.assertIsNone(safe["arguments"]["prompt"])
        self.assertEqual(safe["arguments"]["model"], "gpt-oss-120b")
        self.assertEqual(safe["arguments"]["prompt_metadata"]["bytes"], 11)
        self.assertEqual(
            safe["arguments"]["prompt_metadata"]["sha256"],
            "06ab2a8c60adfdefc20e9f26ac58a9151eb716c8c3d34b8cf034ae1a00b0a20a",
        )
        self.assertEqual(
            safe["arguments"]["prompt_metadata"]["content"],
            "redacted_to_execution_artifact",
        )
        self.assertEqual(original["arguments"]["prompt"], "snowman \N{SNOWMAN}")

    def test_delegated_execution_prompt_is_redacted(self):
        safe = _ledger_safe_args(
            "submit_delegated_execution",
            {
                "principal": {"type": "irc_account", "id": "derek"},
                "arguments": {"prompt": "private"},
            },
        )

        self.assertIsNone(safe["arguments"]["prompt"])
        self.assertEqual(safe["principal"]["id"], "derek")
        self.assertNotIn("private", json.dumps(safe))

    def test_prompt_metadata_survives_the_legacy_400_character_preview(self):
        safe = _ledger_safe_args(
            "submit_delegated_execution",
            {
                "arguments": {
                    "prompt": "private",
                    "system": "fixed system context " * 200,
                },
                "principal_id": "derek",
            },
        )
        preview = json_dumps_canonical(safe)[:400]
        self.assertIn('"prompt": null', preview)
        self.assertIn('"prompt_metadata"', preview)
        self.assertNotIn("private", preview)

    def test_non_sensitive_arguments_are_unchanged(self):
        original = {"job_id": "job_123"}
        safe = _ledger_safe_args("get_execution", original)
        self.assertEqual(safe, original)
        self.assertIsNot(safe, original)


class GatewayWrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "knowledge").mkdir()
        self.ledger = Ledger(root / "ledger")
        callers = root / "callers.json"
        callers.write_text(json.dumps({
            "good-key": {"id": "claude", "runner_class": "frontier", "node": "omen",
                         "profile": "unrestricted"},
        }), encoding="utf-8")
        _map_fixture_tools(self, fake_echo="status", fake_boom="status",
                           fake_tool_with_fields="status")
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


class DispatchIdentityPushTest(unittest.TestCase):
    """The wrapper pushes WHO is asking, beside the two authority grants. The observation
    emitter reads it from that ContextVar to stamp workflow_id on dispatch evidence
    (ADR-0027), so identity must be in force during the call and gone afterwards."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "knowledge").mkdir()
        self.ledger = Ledger(root / "ledger")
        callers = root / "callers.json"
        callers.write_text(json.dumps({
            "k": {"id": "claude-frontier", "runner_class": "frontier", "node": "omen",
                  "profile": "unrestricted"},
        }), encoding="utf-8")
        self.auth = AuthRegistry(callers_path=callers, ledger=self.ledger)
        self.guards = GuardStack(repo_root=root)
        self.hearth = HearthContext(repo_root=root, ledger=self.ledger)

    def _wrap(self, fn, tool_name: str):
        _map_fixture_tools(self, **{tool_name: "status"})
        return make_wrapper(fn, self.hearth, self.auth, self.guards,
                            lambda: "k", lambda: "task-9")

    def test_identity_is_in_force_during_the_call(self):
        from hearth.observation.identity import current_identity

        seen = {}

        def capture() -> dict:
            identity = current_identity()
            seen["identity"] = identity
            return {"ok": True}

        capture.__doc__ = "Capture the ambient dispatch identity."
        self._wrap(capture, "capture")()

        identity = seen["identity"]
        self.assertIsNotNone(identity)
        self.assertEqual(identity.caller_id, "claude-frontier")
        self.assertEqual(identity.runner_class, "frontier")
        self.assertEqual(identity.node, "omen")
        self.assertEqual(identity.task_id, "task-9")

    def test_identity_does_not_leak_after_the_call(self):
        from hearth.observation.identity import current_identity

        self._wrap(fake_echo, "fake_echo")(message="x")
        self.assertIsNone(current_identity())

    def test_identity_does_not_leak_when_the_tool_raises(self):
        """Mirrors the caller_scope finally-reset guarantee: a raising tool must not leak
        one caller's identity into the next call on this thread."""
        from hearth.observation.identity import current_identity

        with self.assertRaises(RuntimeError):
            self._wrap(fake_boom, "fake_boom")(message="x")
        self.assertIsNone(current_identity())
