from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest import TestCase

from hearth.kernel import gateway


class HealthzContractTests(TestCase):
    def test_healthz_is_minimal_and_contains_no_protected_information(self):
        with tempfile.TemporaryDirectory() as tmp:
            callers = Path(tmp) / "callers.json"
            callers.write_text("{}", encoding="utf-8")
            server = gateway.build_server(
                callers_path=callers,
                ledger_dir=Path(tmp) / "ledger",
            )
        route = next(r for r in server._custom_starlette_routes if r.path == "/healthz")
        response = asyncio.run(route.endpoint(None))
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload, {"status": "ok"})
        self.assertNotIn("caller", payload)
        self.assertNotIn("tool", payload)
        self.assertNotIn("backend", payload)
        self.assertNotIn("path", payload)
        self.assertNotIn("secret", payload)
