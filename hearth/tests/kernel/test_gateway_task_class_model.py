"""Gateway wrapper: task_class lifting and override mechanism."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from hearth.kernel.auth import AuthRegistry
from hearth.kernel.context import HearthContext
from hearth.kernel.gateway import make_wrapper
from hearth.kernel.guards import GuardStack
from hearth.kernel.ledger import Ledger


def tool_with_task_class_override(msg: str) -> dict[str, Any]:
    """A tool that provides its own task_class via _ledger_task_class key."""
    return {
        "result": msg,
        "_ledger_task_class": "build",
    }


def tool_without_override(msg: str) -> dict[str, Any]:
    """A tool that does not override task_class."""
    return {"result": msg}


class GatewayTaskClassTest(unittest.TestCase):
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

    def test_lifted_task_class_lands_in_event(self):
        """A tool result carrying _ledger_task_class lands in the event's task_class."""
        wrapped = make_wrapper(tool_with_task_class_override, self.hearth, self.auth,
                              self.guards, lambda: "good-key")
        result = wrapped(msg="test")

        # The _ledger_task_class key must be stripped from the result
        self.assertEqual(result, {"result": "test"})
        self.assertNotIn("_ledger_task_class", result)

        # The event must contain task_class = "build"
        events = self.ledger.query(tool="tool_with_task_class_override")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["task_class"], "build")

    def test_lifted_task_class_is_stripped_from_caller_result(self):
        """The _ledger_task_class key is removed before returning to caller."""
        wrapped = make_wrapper(tool_with_task_class_override, self.hearth, self.auth,
                              self.guards, lambda: "good-key")
        result = wrapped(msg="test")
        self.assertNotIn("_ledger_task_class", result)

    def test_static_tool_class_used_when_no_override(self):
        """When no _ledger_task_class is provided, static TOOL_CLASS map is used."""
        from hearth.kernel.gateway import TOOL_CLASS
        # Register this tool in the static map
        old_class = TOOL_CLASS.get("tool_without_override")
        TOOL_CLASS["tool_without_override"] = "dispatch"
        self.addCleanup(lambda: TOOL_CLASS.pop("tool_without_override") if old_class is None
                        else TOOL_CLASS.update({"tool_without_override": old_class}))

        wrapped = make_wrapper(tool_without_override, self.hearth, self.auth,
                              self.guards, lambda: "good-key")
        result = wrapped(msg="test")
        self.assertEqual(result, {"result": "test"})

        events = self.ledger.query(tool="tool_without_override")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["task_class"], "dispatch")

    def test_lift_task_class_is_none_when_not_present(self):
        """When tool provides no override, task_class is None if tool not in map."""
        wrapped = make_wrapper(tool_without_override, self.hearth, self.auth,
                              self.guards, lambda: "good-key")
        result = wrapped(msg="test")
        events = self.ledger.query(tool="tool_without_override")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsNone(event["task_class"])


if __name__ == "__main__":
    unittest.main()
