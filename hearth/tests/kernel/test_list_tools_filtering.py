"""Discovery mirrors authorization: a caller lists only what it may call.

Capability enforcement happens at invocation. Without these tests, the tool
LIST still advertised the whole surface, so a caller granted two tools could
enumerate all forty-seven with names and schemas — a map of everything the
model denies it. These pin the three cases and the ledger-quietness property.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

import mcp.types as mcp_types

from hearth.kernel.auth import AuthRegistry
from hearth.kernel.gateway import build_server, register_profile_filtered_list_tools
from hearth.kernel.ledger import Ledger

PROVIDERS = "hearth.toolsurface.fs,hearth.toolsurface.inference"

LEGACY_KEY = "legacy-key"
PROXY_KEY = "proxy-key"
RESEARCH_KEY = "research-key"

REGISTRY = {
    LEGACY_KEY: {"id": "legacy-caller", "runner_class": "frontier", "node": "omen"},
    PROXY_KEY: {"id": "proxy-caller", "runner_class": "local", "node": "omen",
                "profile": "generation-proxy"},
    RESEARCH_KEY: {"id": "research-caller", "runner_class": "local", "node": "omen",
                   "profile": "research"},
}


class ListToolsFilteringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.registry = self.tmp / "callers.json"
        self.registry.write_text(json.dumps(REGISTRY), encoding="utf-8")
        self.ledger_dir = self.tmp / "ledger"
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(Path(__file__).resolve().parents[3])

        self.key = {"value": None}
        self.mcp = build_server(providers_spec=PROVIDERS,
                                callers_path=self.registry,
                                ledger_dir=self.ledger_dir)
        auth = AuthRegistry(callers_path=self.registry, ledger=Ledger(self.ledger_dir))
        # Re-register with a controllable key provider: the production one reads
        # an HTTP header that does not exist outside a live request.
        register_profile_filtered_list_tools(self.mcp, auth, lambda: self.key["value"])

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope

    def _listed(self, key: str | None) -> set[str]:
        self.key["value"] = key
        handler = self.mcp._mcp_server.request_handlers[mcp_types.ListToolsRequest]
        result = asyncio.run(handler(mcp_types.ListToolsRequest(method="tools/list")))
        tools = getattr(getattr(result, "root", result), "tools", [])
        return {tool.name for tool in tools}

    def test_legacy_caller_sees_the_full_surface(self) -> None:
        listed = self._listed(LEGACY_KEY)
        self.assertIn("read_file", listed)
        self.assertIn("write_file", listed)
        self.assertIn("local_generate", listed)
        self.assertIn("kernel_status", listed)

    def test_generation_proxy_sees_only_what_it_may_call(self) -> None:
        self.assertEqual(self._listed(PROXY_KEY), {"local_generate", "kernel_status"})

    def test_research_sees_reads_but_not_writes(self) -> None:
        listed = self._listed(RESEARCH_KEY)
        self.assertIn("read_file", listed)
        self.assertIn("glob_files", listed)
        self.assertIn("local_generate", listed)
        self.assertNotIn("write_file", listed)

    def test_unresolvable_key_advertises_nothing(self) -> None:
        self.assertEqual(self._listed("not-a-real-key"), set())
        self.assertEqual(self._listed(None), set())

    def test_listing_is_ledger_quiet(self) -> None:
        """Discovery must not write rejection events. A client re-lists on every
        session init; using the ledgering resolve() there would flood the ledger
        with auth rejections for what is merely discovery."""
        before = len(Ledger(self.ledger_dir).query())
        for key in (LEGACY_KEY, PROXY_KEY, "not-a-real-key", None):
            self._listed(key)
        self.assertEqual(len(Ledger(self.ledger_dir).query()), before)


if __name__ == "__main__":
    unittest.main()
