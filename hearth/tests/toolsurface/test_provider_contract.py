from __future__ import annotations

import inspect
from unittest import TestCase

from hearth.toolsurface import fs, git, inference, knowledge, summon, testing

PROVIDERS = (fs, git, testing, knowledge, summon, inference)


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

    def test_tool_names_are_unique_across_the_surface(self) -> None:
        names = [tool.__name__ for module in PROVIDERS for tool in module.get_tools()]
        self.assertEqual(len(names), len(set(names)), names)
