from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from hearth.projection.dashboard import build_dashboard_html, write_dashboard


class DashboardScopedTestCase(TestCase):
    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope
        shutil.rmtree(self.scope, ignore_errors=True)


class TestDashboard(DashboardScopedTestCase):
    def test_full_fixture_render(self) -> None:
        k_dir = self.scope / "knowledge"
        k_dir.mkdir(parents=True, exist_ok=True)

        offload = {
            "offload_ratio": 0.9,
            "evidence_watermark": "2024-01-01T10:00:00Z",
            "per_class": {"trial": {"tokens_in": 100, "tokens_out": 50}},
            "est_usd_saved": {"usd": 12.34},
            "buckets": [{
                "backend": "my-backend",
                "cost_class": "trial",
                "calls": 10,
                "ok_rate": 1.0,
                "tokens_in": 100,
                "tokens_out": 50,
                "last_seen": "now",
            }],
        }
        (k_dir / "offload.json").write_text(json.dumps(offload), encoding="utf-8")

        capacity = {
            "evidence_watermark": "2024-01-01T10:00:00Z",
            "bucket_count": 5,
        }
        (k_dir / "capacity.json").write_text(json.dumps(capacity), encoding="utf-8")

        ledger_path = self.scope / "events.ndjson"
        events = [
            {"ts": "2024-01-01T12:00:00+00:00", "tool": "local_generate", "backend": "my-backend", "ok": True},
            {"ts": "2024-01-01T12:01:00+00:00", "tool": "other", "routed_by": "escalation:a->b"},
            {"ts": "2024-01-01T12:02:00+00:00", "tool": "other", "error_code": "timeout", "routed_by": "ask:quality-best"},
        ]
        with ledger_path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        rendered = build_dashboard_html(k_dir, ledger_path, now_iso="2024-01-01T14:00:00+00:00")

        self.assertIn("0.9", rendered)
        self.assertIn("my-backend", rendered)
        self.assertIn("Escalations", rendered)
        self.assertIn("timeout", rendered)
        self.assertIn('<meta http-equiv="refresh" content="300">', rendered)

    def test_empty_sandbox_degrades_gracefully(self) -> None:
        k_dir = self.scope / "empty_knowledge"
        ledger_path = self.scope / "no_ledger.ndjson"

        rendered = build_dashboard_html(k_dir, ledger_path)
        self.assertIn("<title>HEARTH Dashboard</title>", rendered)

    def test_write_dashboard(self) -> None:
        out_path = self.scope / "dash.html"
        k_dir = self.scope / "empty_knowledge"
        ledger_path = self.scope / "no_ledger.ndjson"

        res = write_dashboard(out_path, k_dir, ledger_path)
        self.assertTrue(out_path.exists())
        self.assertEqual(res["path"], str(out_path))
        self.assertGreater(res["bytes"], 0)
