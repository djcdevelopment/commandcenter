"""JS1: gateway wrapper stamps task_class (via TOOL_CLASS) and threads a
provider-resolved model into the ledger event via the _ledger_model convention.
"""

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any

from hearth.kernel import capabilities
from hearth.kernel.auth import AuthRegistry
from hearth.kernel.context import HearthContext
from hearth.kernel.gateway import (LEDGER_MODEL_KEY, LEDGER_TASK_CLASS_KEY,
                                   _task_class_for, make_wrapper)
from hearth.kernel.guards import GuardStack
from hearth.kernel.ledger import Ledger

# ADR-0023: synthetic fixtures must be mapped like real tools. Every MOUNTED
# tool is guaranteed a capability by assert_surface_complete at startup, so an
# unmapped tool is a state production cannot reach; before the fail-open was
# inverted these fixtures rode the profile-less "allow everything" path instead.
# Mapping them here models the production guarantee rather than a hole in it.
def _map_fixture_tools(test, **tools):
    patcher = mock.patch.dict(capabilities.TOOL_CAPABILITY, tools)
    patcher.start()
    test.addCleanup(patcher.stop)



def local_generate(prompt: str) -> dict[str, Any]:
    """Stand-in for hearth.toolsurface.inference.local_generate (name matters:
    make_wrapper derives task_class from fn.__name__ via TOOL_CLASS)."""
    return {"ok": True, "text": "hi", "model": "qwen3-coder:30b",
            LEDGER_MODEL_KEY: "qwen3-coder:30b"}


def read_file(path: str) -> str:
    """Stand-in io-classed tool (name must match the TOOL_CLASS key)."""
    return "contents"


def fake_mystery_tool() -> str:
    """A tool with no TOOL_CLASS mapping and no matching prefix."""
    return "?"


def submit_task(prompt: str) -> dict[str, Any]:
    """Stand-in dispatch tool carrying a caller-supplied task_class override
    (U6: _ledger_task_class beats the static TOOL_CLASS-derived 'dispatch')."""
    return {"ok": True, "plan_id": "p1", LEDGER_TASK_CLASS_KEY: "build"}


class ToolClassMappingTest(unittest.TestCase):
    def test_exact_matches(self):
        self.assertEqual(_task_class_for("local_generate"), "inference")
        self.assertEqual(_task_class_for("submit_task"), "dispatch")
        self.assertEqual(_task_class_for("task_status"), "dispatch")
        self.assertEqual(_task_class_for("run_tests"), "test")
        self.assertEqual(_task_class_for("read_file"), "io")
        self.assertEqual(_task_class_for("write_file"), "io")
        self.assertEqual(_task_class_for("glob_files"), "io")
        self.assertEqual(_task_class_for("list_dir"), "io")
        self.assertEqual(_task_class_for("project"), "query")
        self.assertEqual(_task_class_for("preflight"), "health")
        self.assertEqual(_task_class_for("masters_pet"), "health")
        self.assertEqual(_task_class_for("patrol"), "health")

    def test_prefix_matches(self):
        self.assertEqual(_task_class_for("git_status"), "vcs")
        self.assertEqual(_task_class_for("git_commit_push"), "vcs")
        self.assertEqual(_task_class_for("query_findings"), "query")
        self.assertEqual(_task_class_for("query_capabilities"), "query")

    def test_unknown_tool_is_none(self):
        self.assertIsNone(_task_class_for("some_unmapped_tool"))


class WrapperStampsTaskClassAndModelTest(unittest.TestCase):
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
        _map_fixture_tools(self, fake_mystery_tool="status")
        self.auth = AuthRegistry(callers_path=callers, ledger=self.ledger)
        self.guards = GuardStack(repo_root=root)
        self.hearth = HearthContext(repo_root=root, ledger=self.ledger)

    def test_model_threaded_from_result_and_stripped_before_return(self):
        wrapped = make_wrapper(local_generate, self.hearth, self.auth,
                               self.guards, lambda: "good-key")
        result = wrapped(prompt="hello")

        # Public contract: the internal ledger hint never leaks to the caller.
        self.assertNotIn(LEDGER_MODEL_KEY, result)
        self.assertEqual(result["model"], "qwen3-coder:30b")

        events = self.ledger.query(tool="local_generate")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["model"], "qwen3-coder:30b")
        self.assertEqual(events[0]["task_class"], "inference")

    def test_io_tool_gets_io_task_class_and_null_model(self):
        wrapped = make_wrapper(read_file, self.hearth, self.auth,
                               self.guards, lambda: "good-key")
        wrapped(path="a.txt")
        events = self.ledger.query(tool="read_file")
        self.assertEqual(events[0]["task_class"], "io")
        self.assertIsNone(events[0]["model"])

    def test_lifted_task_class_overrides_static_map_and_is_stripped(self):
        wrapped = make_wrapper(submit_task, self.hearth, self.auth,
                               self.guards, lambda: "good-key")
        result = wrapped(prompt="build me a thing")
        self.assertNotIn(LEDGER_TASK_CLASS_KEY, result)
        events = self.ledger.query(tool="submit_task")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["task_class"], "build")  # not "dispatch"

    def test_unmapped_tool_gets_null_task_class(self):
        wrapped = make_wrapper(fake_mystery_tool, self.hearth, self.auth,
                               self.guards, lambda: "good-key")
        wrapped()
        events = self.ledger.query(tool="fake_mystery_tool")
        self.assertIsNone(events[0]["task_class"])


if __name__ == "__main__":
    unittest.main()
