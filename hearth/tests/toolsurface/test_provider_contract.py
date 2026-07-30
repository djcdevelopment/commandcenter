from __future__ import annotations

import inspect
from unittest import TestCase

from hearth.toolsurface import (
    am4,
    build_requests,
    fs,
    git,
    inference,
    knowledge,
    scheduler,
    summon,
    task_lane,
    testing,
)

PROVIDERS = (
    fs,
    git,
    testing,
    knowledge,
    summon,
    inference,
    task_lane,
    scheduler,
    am4,
    build_requests,
)


class ProviderContractTests(TestCase):
    """The frozen H-B provider contract: get_tools() -> list of plain typed callables."""

    def test_every_provider_exposes_get_tools(self) -> None:
        for module in PROVIDERS:
            tools = module.get_tools()
            self.assertIsInstance(tools, list, module.__name__)
            self.assertTrue(tools, f"{module.__name__} exposes no tools")

    def test_every_tool_has_docstring_and_annotations(self) -> None:
        for module in PROVIDERS:
            for tool in module.get_tools():
                with self.subTest(tool=f"{module.__name__}.{tool.__name__}"):
                    self.assertTrue(callable(tool))
                    self.assertTrue((tool.__doc__ or "").strip(),
                                    "docstring becomes the MCP tool description")
                    signature = inspect.signature(tool)
                    for name, parameter in signature.parameters.items():
                        self.assertIsNot(parameter.annotation, inspect.Parameter.empty,
                                         f"param {name} missing type hint")
                    self.assertIsNot(signature.return_annotation, inspect.Signature.empty)

    def test_no_kernel_imports_anywhere(self) -> None:
        for module in PROVIDERS:
            source = inspect.getsource(module)
            self.assertNotIn("hearth.kernel", source, module.__name__)

    def test_observation_modules_are_kernel_free(self) -> None:
        """inference imports hearth.observation.emit, which must not become a laundering
        route around the contract above. hearth/errortax.py exists for exactly that
        reason: the error taxonomy is needed on both sides of the boundary, so it lives
        outside the kernel rather than being duplicated or reached into.

        Checked on the IMPORT GRAPH, not on source text. These modules discuss the kernel
        boundary in their docstrings — explaining why it exists is the opposite of
        crossing it — and a substring grep cannot tell the difference.
        """
        import ast

        from hearth import errortax
        from hearth.observation import emit, identity

        for module in (errortax, emit, identity):
            imported: list[str] = []
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            offenders = [name for name in imported
                         if name == "hearth.kernel" or name.startswith("hearth.kernel.")]
            self.assertEqual(offenders, [], f"{module.__name__} imports {offenders}")

    def test_tool_names_are_unique_across_the_surface(self) -> None:
        names = [tool.__name__ for module in PROVIDERS for tool in module.get_tools()]
        self.assertEqual(len(names), len(set(names)), names)
