"""Condition 7 (WI-G2b): the documentation names no function that does not exist.

`hearth/operator/proposal.py` told every reader that "D-107: `propose_via_door`
resolves the door rung" — a function that has never existed in this package. The
code it describes is `resolve_door_rung`. Documentation drift is not cosmetic
here: the module docstring is the first thing a cold agent reads, and a symbol
it cannot find is a dead end in the one place the control plane promises to be
self-describing.

These tests (a) prove the obsolete symbol is gone from code, schemas, examples
and documentation, and (b) generalize the check: every symbol-shaped token in an
operator module docstring must resolve to something this package really has.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from hearth.operator import paths, proposal as proposal_mod

OBSOLETE_SYMBOLS = ("propose_via_door",)

# Where documentation about this package can live. Bounded on purpose: a search
# that takes minutes is a search nobody runs.
SEARCH_ROOTS = (
    Path(paths.REPO_ROOT) / "hearth",
    Path(paths.REPO_ROOT) / "tools",
    Path(paths.REPO_ROOT) / "docs",
)
SEARCH_SUFFIXES = (".py", ".json", ".toml", ".md", ".txt", ".html", ".cmd", ".ps1")

# Backticked words in a docstring that are prose or wire vocabulary, not symbols
# this package defines. Each one is a word a reader would not look up.
PROSE_TOKENS = frozenset({
    "None", "null", "bytes", "operator", "inspect", "whoami", "caller", "approve",
    "denied", "granted", "validated", "needs_approval", "resolve", "lookup",
    "append", "intent", "expected", "constraints", "no_identity",
})

# Symbols the docstrings name in the PAST tense, explaining what a candidate
# removed and why. They are the opposite of drift — but only if they really are
# gone, which `test_the_symbols_the_docstrings_call_removed_are_gone` checks.
REMOVED_SYMBOLS = frozenset({
    "approved_ids",           # the caller-supplied approval set (WI-G2a)
    "requesting_principal",   # the caller-supplied requester dict (WI-G2b)
})


def _module_sources() -> dict[Path, str]:
    package = Path(paths.REPO_ROOT) / "hearth" / "operator"
    return {path: path.read_text(encoding="utf-8")
            for path in sorted(package.glob("*.py"))}


def _strip_docstrings(source: str) -> str:
    """The source with every docstring blanked, so "is this token used in the
    code?" is not answered by the documentation itself."""
    tree = ast.parse(source)
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            spans.append((body[0].value.lineno, body[0].value.end_lineno))
    lines = source.splitlines()
    for start, end in spans:
        for number in range(start, end + 1):
            lines[number - 1] = ""
    return "\n".join(lines)


class ObsoleteSymbolTests(unittest.TestCase):
    def test_the_obsolete_symbol_appears_in_no_code_schema_example_or_document(self) -> None:
        # This file is the register of obsolete symbols, so it names them on
        # purpose and is the one file the search skips. A reference inside a
        # longer identifier (`test_route_propose_via_door_...`) is not a
        # reference to the symbol, which is why the match is word-bounded.
        register = Path(__file__).resolve()
        patterns = {symbol: re.compile(rf"\b{re.escape(symbol)}\b")
                    for symbol in OBSOLETE_SYMBOLS}
        hits: list[str] = []
        for root in SEARCH_ROOTS:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in SEARCH_SUFFIXES:
                    continue
                if "__pycache__" in path.parts or "var" in path.parts:
                    continue
                if path.resolve() == register:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for symbol, pattern in patterns.items():
                    if pattern.search(text):
                        hits.append(f"{path.relative_to(paths.REPO_ROOT).as_posix()}: {symbol}")
        self.assertEqual(hits, [],
                         "these files still reference a symbol this package does not define")

    def test_the_door_rung_documentation_names_the_function_that_exists(self) -> None:
        doc = proposal_mod.__doc__ or ""
        self.assertIn("resolve_door_rung", doc)
        self.assertTrue(callable(getattr(proposal_mod, "resolve_door_rung", None)))
        for symbol in OBSOLETE_SYMBOLS:
            self.assertFalse(hasattr(proposal_mod, symbol))
            self.assertNotIn(symbol, doc)

    def test_every_symbol_named_in_an_operator_docstring_resolves(self) -> None:
        """The general form of the same defect, so the next drift is caught."""
        sources = _module_sources()
        code_only = "\n".join(_strip_docstrings(text) for text in sources.values())
        package_names: set[str] = set()
        for path in sources:
            module = __import__(f"hearth.operator.{path.stem}", fromlist=["*"])
            package_names.update(dir(module))
        # The door mount lives in hearth/toolsurface/operator.py (the G1
        # location), and the approval docstring rightly names it.
        from hearth.toolsurface import operator as operator_tools

        package_names.update(dir(operator_tools))

        unresolved: list[str] = []
        for path, source in sources.items():
            doc = ast.get_docstring(ast.parse(source)) or ""
            for token in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", doc):
                if token in PROSE_TOKENS or token in package_names:
                    continue
                if token in REMOVED_SYMBOLS:
                    continue
                # A wire field, event type, env var or door tool: named in the
                # code as an identifier or a literal, not only in the prose.
                if re.search(rf"\b{re.escape(token)}\b", code_only):
                    continue
                unresolved.append(f"{path.name}: `{token}`")
        self.assertEqual(sorted(unresolved), [],
                         "these docstring symbols resolve to nothing in hearth/operator/")

    def test_the_symbols_the_docstrings_call_removed_are_gone(self) -> None:
        """The other half of the same claim: a docstring that says a parameter
        was removed is drift of its own if the parameter is still there."""
        code_only = "\n".join(_strip_docstrings(text)
                              for text in _module_sources().values())
        for symbol in sorted(REMOVED_SYMBOLS):
            self.assertNotIn(
                symbol, code_only,
                f"the package docstrings say {symbol} was removed, and the code still "
                "uses it")


if __name__ == "__main__":
    unittest.main()
