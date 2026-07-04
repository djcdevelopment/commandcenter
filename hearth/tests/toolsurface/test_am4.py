from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.am4 import gather_am4_catalog, query_am4_catalog

MODELS_JSON = {
    "safety": {
        "max_host_used_gb_preflight": 22.0,
        "telemetry_settle_sec": 10,
        "health_timeout_sec": 180,
        "verdict_gate": True,
    },
    "models": {
        "qwen2.5-14b-q4": {
            "file": "qwen2.5-14b-instruct-q4_K_M.gguf",
            "placement": "single",
            "visible_devices": "0",
            "alias": "qwen2.5-14b-q4",
            "expected_gen_tps": 46.81,
            "note": "8.4 GB single-card territory.",
        },
    },
}

MANIFESTS = [
    {"config_name": "run1", "model_path": "D:\\work\\battlemage\\models\\qwen2.5-14b-instruct-q4_K_M.gguf",
     "warmup": {"wall_ms": 1517}},
    {"config_name": "run2", "model_path": "D:\\work\\battlemage\\models\\qwen2.5-14b-instruct-q4_K_M.gguf",
     "warmup": {"wall_ms": 28384}},
]


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _gather_stdout(models_json=MODELS_JSON, manifests=MANIFESTS):
    payload = {"models_json": models_json, "manifests": manifests}
    return f"noise line\nRESULT {json.dumps(payload)}\n"


class Am4ScopedTestCase(TestCase):
    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope
        import shutil
        shutil.rmtree(self.scope, ignore_errors=True)


class GatherAm4CatalogTests(Am4ScopedTestCase):
    def test_gather_builds_and_writes_catalog(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout=_gather_stdout())):
            catalog = gather_am4_catalog()
        self.assertEqual(catalog["contract_version"], "am4-catalog.v1")
        self.assertEqual(len(catalog["models"]), 1)
        model = catalog["models"][0]
        self.assertEqual(model["model_id"], "qwen2.5-14b-q4")
        self.assertEqual(model["sample_count"], 2)
        self.assertEqual(model["warmup_ms_max"], 28384)

        written = self.scope / "knowledge" / "am4_catalog.json"
        self.assertTrue(written.is_file())
        on_disk = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["contract_version"], "am4-catalog.v1")

    def test_write_false_does_not_touch_knowledge_dir(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout=_gather_stdout())):
            gather_am4_catalog(write=False)
        self.assertFalse((self.scope / "knowledge" / "am4_catalog.json").exists())

    def test_ssh_unreachable_raises(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
            with self.assertRaises(RuntimeError):
                gather_am4_catalog()

    def test_unparseable_result_raises(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="no result line here")):
            with self.assertRaises(ValueError):
                gather_am4_catalog()

    def test_empty_manifests_still_builds_catalog(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout=_gather_stdout(manifests=[]))):
            catalog = gather_am4_catalog()
        model = catalog["models"][0]
        self.assertEqual(model["sample_count"], 0)
        self.assertIsNone(model["warmup_ms_p50"])


class QueryAm4CatalogTests(Am4ScopedTestCase):
    def test_query_before_gather_reports_unavailable(self) -> None:
        result = query_am4_catalog()
        self.assertFalse(result["available"])

    def test_round_trip_write_then_query(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout=_gather_stdout())):
            gather_am4_catalog()
        result = query_am4_catalog()
        self.assertTrue(result["available"])
        self.assertEqual(result["content"]["contract_version"], "am4-catalog.v1")
        self.assertEqual(len(result["content"]["models"]), 1)
