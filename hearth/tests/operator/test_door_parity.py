"""CLI/door parity (D-102), proven through the gateway's own loader and checks.

The production gateway runs the code it was started with and is not restarted by
this work item, so parity is proven IN-PROCESS: the provider is imported by
`hearth.kernel.gateway.load_providers`, each tool is wrapped by the gateway's own
`make_wrapper` with a real `AuthRegistry` and a fixture callers registry, and the
capability check that gates every door call decides who gets what.

Nothing here is a mock of the gateway. The only fixtures are the callers registry
and the outside world (door probes, reachability sweep).
"""

from __future__ import annotations

import asyncio
import json
import unittest

from hearth.kernel.auth import AuthRegistry
from hearth.kernel.capabilities import TOOL_CAPABILITY, check_tool_access, load_profiles
from hearth.kernel.context import HearthContext
from hearth.kernel.gateway import build_server, load_providers, make_wrapper
from hearth.kernel.guards import GuardStack
from hearth.kernel.ledger import Ledger

from hearth.operator import core, paths
from hearth.operator.identity import resolve_from_env
from hearth.tests.operator.support import (FakeDoor, OperatorTestCase, RESEARCH_KEY,
                                           UNRESTRICTED_KEY, fake_cli_runner)

PROVIDER = "hearth.toolsurface.operator"
TOOLS = ("operator_inspect", "operator_whoami", "operator_catalog", "operator_approve")


class ProviderContractTests(unittest.TestCase):
    def test_the_gateway_loader_mounts_exactly_the_four_tools(self) -> None:
        providers = load_providers(PROVIDER)
        self.assertIn(PROVIDER, providers, "the loader could not import the provider")
        self.assertEqual({fn.__name__ for fn in providers[PROVIDER]}, set(TOOLS))

    def test_every_mounted_tool_is_typed_and_documented(self) -> None:
        import inspect as stdlib_inspect

        for fn in load_providers(PROVIDER)[PROVIDER]:
            with self.subTest(tool=fn.__name__):
                self.assertTrue((fn.__doc__ or "").strip(),
                                "the docstring becomes the MCP description")
                signature = stdlib_inspect.signature(fn)
                for name, parameter in signature.parameters.items():
                    self.assertIsNot(parameter.annotation,
                                     stdlib_inspect.Parameter.empty, name)
                self.assertIsNot(signature.return_annotation,
                                 stdlib_inspect.Signature.empty)

    def test_the_provider_imports_nothing_from_the_kernel(self) -> None:
        import inspect as stdlib_inspect

        from hearth.toolsurface import operator as provider

        self.assertNotIn("hearth.kernel", stdlib_inspect.getsource(provider))

    def test_the_taxonomy_maps_each_tool_to_its_decided_capability(self) -> None:
        self.assertEqual(TOOL_CAPABILITY["operator_inspect"], "query")
        self.assertEqual(TOOL_CAPABILITY["operator_whoami"], "query")
        self.assertEqual(TOOL_CAPABILITY["operator_catalog"], "knowledge_write")
        self.assertEqual(TOOL_CAPABILITY["operator_approve"], "approve")


class GatewayMountTests(OperatorTestCase):
    def test_build_server_mounts_the_provider_and_the_surface_check_passes(self) -> None:
        """assert_surface_complete refuses to start a gateway that mounts a tool
        with no capability. Building the server IS the proof it is mapped."""
        mcp = build_server(providers_spec=PROVIDER, callers_path=self.registry,
                           ledger_dir=self.home / "ledger")
        names = {tool.name for tool in asyncio.run(mcp.list_tools())}
        self.assertLessEqual(set(TOOLS), names)


class DoorDispatchTests(OperatorTestCase):
    """Dispatch through the gateway's wrapper: auth, capability check, ledger."""

    def setUp(self) -> None:
        super().setUp()
        self.ledger = Ledger(self.home / "ledger")
        self.auth = AuthRegistry(callers_path=self.registry, ledger=self.ledger)
        self.guards = GuardStack(repo_root=paths.REPO_ROOT)
        self.hearth = HearthContext(repo_root=paths.REPO_ROOT, ledger=self.ledger)
        self.tools = {fn.__name__: fn for fn in load_providers(PROVIDER)[PROVIDER]}
        self.key = {"value": UNRESTRICTED_KEY}

        from hearth.operator import catalog as catalog_mod

        self.catalog = catalog_mod.compile_catalog()
        self.refreshed = core.refresh(door=FakeDoor(), cli_runner=fake_cli_runner(),
                                      catalog=self.catalog)

    def dispatch(self, tool: str, **kwargs):
        wrapped = make_wrapper(self.tools[tool], self.hearth, self.auth, self.guards,
                               lambda: self.key["value"])
        return wrapped(**kwargs)

    def test_the_door_and_the_cli_return_the_same_document(self) -> None:
        door_result = self.dispatch("operator_inspect")
        self.assertTrue(door_result["ok"], door_result.get("error"))

        self.set_key(UNRESTRICTED_KEY)
        cli_bundle = core.inspect_bundle(resolve_from_env())

        door_bundle = door_result["bundle"]
        self.assertEqual(door_bundle["capacity_snapshot"]["snapshot_id"],
                         self.refreshed["snapshot"]["snapshot_id"])
        for document in (door_bundle, cli_bundle):
            document.pop("presentation")
        self.assertEqual(door_bundle, cli_bundle,
                         "door and CLI must agree on everything but presentation")

    def test_whoami_agrees_across_the_two_entry_points(self) -> None:
        door = self.dispatch("operator_whoami")["whoami"]
        self.set_key(UNRESTRICTED_KEY)
        cli = core.whoami_document(resolve_from_env())
        self.assertEqual(door["caller"], cli["caller"])
        self.assertEqual(door["authority"], cli["authority"])
        # The one honest difference: where the identity came from.
        self.assertIn("door", door["identity_source"])
        self.assertIn(paths.API_KEY_ENV, cli["identity_source"])

    def test_a_research_caller_is_allowed_inspect_and_denied_catalog(self) -> None:
        self.key["value"] = RESEARCH_KEY
        allowed = self.dispatch("operator_inspect")
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["bundle"]["authority"]["caller"]["profile"], "research")

        with self.assertRaises(PermissionError) as caught:
            self.dispatch("operator_catalog")
        self.assertIn("knowledge_write", str(caught.exception))

        denials = [row for row in self.ledger.query(tool="operator_catalog", ok=False)]
        self.assertEqual(len(denials), 1)
        # A refused call is ledgered with args=None: the audit trail records that
        # the call was refused, never the arguments it was refused for.
        from hearth.kernel.ledger import sha256_digest

        self.assertEqual(denials[0]["args_digest"], sha256_digest(None))
        self.assertIn("capability", denials[0]["error"])

    def test_the_capability_check_agrees_with_the_shipped_profiles(self) -> None:
        profiles = load_profiles(paths.PROFILES_PATH)
        for tool, expected in (("operator_inspect", True), ("operator_whoami", True),
                               ("operator_catalog", False)):
            allowed, capability = check_tool_access(profiles["research"], tool)
            self.assertEqual(allowed, expected, tool)
            self.assertEqual(capability, TOOL_CAPABILITY[tool])

    def test_an_unknown_key_never_reaches_the_tool(self) -> None:
        self.key["value"] = "not-a-real-key"
        with self.assertRaises(PermissionError):
            self.dispatch("operator_inspect")

    def test_the_door_inspect_writes_nothing(self) -> None:
        """`operator_inspect` is mapped to `query`, so it must not write — not
        even the per-caller convenience bundle."""
        before = sorted(p.name for p in paths.var_dir().rglob("*"))
        self.dispatch("operator_inspect")
        self.assertEqual(sorted(p.name for p in paths.var_dir().rglob("*")), before)
        self.assertFalse(paths.inspect_dir().exists())

    def test_the_door_refuses_honestly_when_there_is_no_current_snapshot(self) -> None:
        paths.current_path().unlink()
        result = self.dispatch("operator_inspect")
        self.assertFalse(result["ok"])
        self.assertIn("--refresh", result["error"])

    def test_catalog_check_through_the_door_reports_the_committed_catalog(self) -> None:
        result = self.dispatch("operator_catalog", check=True)
        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["catalog_version"], self.catalog["catalog_version"])
        self.assertEqual(
            json.loads(paths.CATALOG_PATH.read_text(encoding="utf-8"))["catalog_version"],
            self.catalog["catalog_version"])


if __name__ == "__main__":
    unittest.main()
