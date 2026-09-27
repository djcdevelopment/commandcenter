"""The entry contract: the shim, the stdlib boundary, and the four entry documents.

A cold agent's first three minutes depend entirely on these files, and two of
the failures they guard against are silent: a root-level `operator` package
shadowing Python's standard library `operator` module for everything that runs
with the repository root on `sys.path`, and a shim that quietly grows policy of
its own.
"""

from __future__ import annotations

import difflib
import importlib
import os
import operator as stdlib_operator
import re
import subprocess
import sys
import unittest
from pathlib import Path

from hearth.operator import paths

REPO_ROOT = paths.REPO_ROOT
# omen-linux 2026-09-27: the shim is a POSIX script here; the .cmd is the Windows form.
SHIM = REPO_ROOT / ("operator.cmd" if os.name == "nt" else "operator.sh")
SHIM_LINE = "python -m hearth.operator %*" if os.name == "nt" else 'exec python -m hearth.operator "$@"'
START_HERE = REPO_ROOT / "START-HERE.md"
README = REPO_ROOT / "README.md"
AGENTS = REPO_ROOT / "AGENTS.md"
CLAUDE = REPO_ROOT / "CLAUDE.md"

# The accepted G0 baseline this work item branched from (WI-G1 "Accepted baseline").
BASELINE = "5926c7308a6950b7dfbad9d712a4fb09a6c8611f"

SYNCED_BEGIN = "<!-- hearth-offload:begin -->"
SYNCED_END = "<!-- hearth-offload:end -->"


def git_show(path: str) -> str:
    """The baseline bytes of a tracked file, or None when git cannot answer."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{BASELINE}:{path}"],
            capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8")


class StdlibShadowingTests(unittest.TestCase):
    """D-102: `operator` is a standard library module. Nothing here may take it."""

    def test_import_operator_still_resolves_to_the_standard_library(self) -> None:
        self.assertIn(str(REPO_ROOT), [str(Path(entry).resolve()) for entry in sys.path
                                       if entry] or [str(REPO_ROOT)],
                      "this test is only meaningful with the repository root importable")
        module = importlib.import_module("operator")
        self.assertEqual(module.add(1, 2), 3)
        origin = Path(module.__spec__.origin or "builtin")
        self.assertFalse(str(origin).startswith(str(REPO_ROOT)),
                         f"stdlib `operator` resolved inside the repository: {origin}")
        self.assertIs(module, stdlib_operator)

    def test_the_package_lives_under_hearth(self) -> None:
        package = importlib.import_module("hearth.operator")
        self.assertTrue(Path(package.__file__).resolve()
                        .is_relative_to(REPO_ROOT / "hearth" / "operator"))
        self.assertTrue(callable(package.main))

    def test_the_repository_root_holds_no_operator_module_or_package(self) -> None:
        self.assertFalse((REPO_ROOT / "operator.py").exists())
        self.assertFalse((REPO_ROOT / "operator").exists())
        self.assertTrue(SHIM.is_file(), f"the root shim is {SHIM.name}, and only that")

    def test_the_door_provider_does_not_shadow_it_either(self) -> None:
        importlib.import_module("hearth.toolsurface.operator")
        self.assertIs(importlib.import_module("operator"), stdlib_operator)
        self.assertEqual(stdlib_operator.add(1, 2), 3)


class ShimTests(unittest.TestCase):
    def test_the_shim_forwards_and_does_nothing_else(self) -> None:
        lines = [line.strip() for line in SHIM.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        executable = [line for line in lines
                      if not line.lower().startswith(("rem ", "rem", "@echo off", "#"))]
        self.assertEqual(executable, [SHIM_LINE],
                         "the shim must be exactly one forwarding invocation")

    def test_the_shim_contains_no_logic(self) -> None:
        text = SHIM.read_text(encoding="utf-8")
        body = "\n".join(line for line in text.splitlines()
                         if not line.strip().lower().startswith(("rem", "#")))
        for forbidden in (r"\bif\b", r"\bgoto\b", r"\bfor\b", r"\bset\b", r"\bwhere\b",
                          r"\bcall\b", r"\bpushd\b", r"\bcd\b", r"&&", r"\|\|"):
            self.assertIsNone(re.search(forbidden, body, re.IGNORECASE),
                              f"the shim contains {forbidden}: policy belongs in the package")


class StartHereTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = START_HERE.read_text(encoding="utf-8")

    def test_it_names_the_eight_entry_steps(self) -> None:
        for number in range(1, 9):
            self.assertRegex(self.text, rf"\|\s*{number}\s*\|",
                             f"entry step {number} is missing")
        for command in ("operator inspect --json", "operator task submit",
                        "route validate", "operator execute", "operator replay"):
            # 2026-09-27: `operator reconcile/verify/outcome` never existed; replay/explain/learn are
            # the recorded step-8 commands (hearth/operator/cli.py), so the contract names those.
            self.assertIn(command, self.text)

    def test_it_names_the_identity_environment_variable(self) -> None:
        self.assertIn("HEARTH_API_KEY", self.text)
        self.assertIn("HEARTH_ROOT", self.text)
        self.assertIn("no_identity", self.text)

    def test_it_carries_the_status_vocabulary(self) -> None:
        for word in ("LIVE", "BUILT NOT DEPLOYED", "STOPPED", "BLOCKED",
                     "HISTORICAL", "ABSENT"):
            self.assertIn(word, self.text)

    def test_it_says_what_to_do_before_changing_anything(self) -> None:
        self.assertIn("Before changing anything", self.text)
        self.assertIn("test_scheduler_gpu.py", self.text)
        self.assertIn("hearth-private", self.text)

    def test_it_states_both_freshness_horizons(self) -> None:
        self.assertIn("300", self.text)
        for ttl in ("120", "30", "3600"):
            self.assertIn(ttl, self.text)
        self.assertIn("never be described as fresh", self.text)

    def test_the_commands_it_gives_are_the_ones_the_cli_has(self) -> None:
        from hearth.operator.cli import build_parser

        parser = build_parser()
        actions = [action for action in parser._actions
                   if getattr(action, "choices", None) and
                   isinstance(action.choices, dict)]
        commands = set(actions[0].choices)
        entry_commands = {"catalog", "inspect", "whoami", "verify-ids"}
        self.assertTrue(entry_commands.issubset(commands),
                        f"CLI must include all entry commands {entry_commands}")
        for command in entry_commands:
            self.assertIn(f"operator {command}", self.text)


class ReadmeTests(unittest.TestCase):
    def test_the_fallback_points_at_the_entry_contract(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertIn("START-HERE.md", text)
        self.assertIn("operator", text)
        self.assertIn("control plane", text)


class AgentsTests(unittest.TestCase):
    def test_the_entry_contract_comes_first(self) -> None:
        text = AGENTS.read_text(encoding="utf-8")
        self.assertIn("START-HERE.md", text)
        self.assertLess(text.index("START-HERE.md"), text.index(SYNCED_BEGIN),
                        "the entry contract must come before the offload block")

    def test_the_synced_block_is_byte_identical_to_the_baseline(self) -> None:
        baseline = git_show("AGENTS.md")
        if baseline is None:
            self.skipTest("git could not read the baseline revision")
        current = AGENTS.read_text(encoding="utf-8")
        for text in (baseline, current):
            self.assertIn(SYNCED_BEGIN, text)
            self.assertIn(SYNCED_END, text)

        def block(text: str) -> str:
            return text[text.index(SYNCED_BEGIN):text.index(SYNCED_END) + len(SYNCED_END)]

        self.assertEqual(block(current), block(baseline),
                         "the synced offload block is generated elsewhere; edit its source")


class ClaudeMdTests(unittest.TestCase):
    def test_it_differs_from_the_baseline_by_exactly_one_added_line(self) -> None:
        baseline = git_show("CLAUDE.md")
        if baseline is None:
            self.skipTest("git could not read the baseline revision")
        current = CLAUDE.read_text(encoding="utf-8")
        diff = list(difflib.unified_diff(baseline.splitlines(True),
                                         current.splitlines(True), n=0))
        added = [line for line in diff if line.startswith("+") and not line.startswith("+++")]
        removed = [line for line in diff if line.startswith("-") and not line.startswith("---")]
        self.assertEqual(len(added), 1, f"expected one added line, got {added}")
        self.assertEqual(removed, [], "nothing in CLAUDE.md may be removed or rewritten")
        self.assertIn("START-HERE.md", added[0])

    def test_the_pointer_is_the_first_line(self) -> None:
        first = CLAUDE.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("START-HERE.md", first)


if __name__ == "__main__":
    unittest.main()
