from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.am4 import (_probe_moe_models, gather_am4_catalog,
                                    query_am4_catalog)

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


MOE_PAYLOAD = {
    "unit_text": "# b70-moe.service\nExecStart=%h/baseline/serve-moe.sh\n",
    "serve_script": ("llama-server -m gpt-oss-120b-MXFP4.gguf --alias gpt-oss-120b "
                     "-dev SYCL0,SYCL1 -sm layer -c 65536 -np 4 --port 8082"),
    "unit_active": "active",
}

CAPACITY_FACTS = {
    "generated": "2026-07-18",
    "facts": [
        {"backend": "am4-moe", "metric": "solo_decode_rate", "value": 29, "unit": "tok/s"},
        {"backend": "am4-host", "metric": "vram_per_card", "value": 31023, "unit": "MiB"},
    ],
}


def _gather_stdout(models_json=MODELS_JSON, manifests=MANIFESTS, moe=None):
    payload = {"models_json": models_json, "manifests": manifests}
    if moe is not None:
        payload["moe"] = moe
    return f"noise line\nRESULT {json.dumps(payload)}\n"


class Am4ScopedTestCase(TestCase):
    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        # The moe probe keys off this token; keep it out of the test env so no
        # test can ever reach for the real :8082 lane.
        self._previous_token = os.environ.pop("AM4_OXEN_TOKEN", None)

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope
        if self._previous_token is not None:
            os.environ["AM4_OXEN_TOKEN"] = self._previous_token
        import shutil
        shutil.rmtree(self.scope, ignore_errors=True)

    def write_capacity_facts(self, doc=CAPACITY_FACTS) -> None:
        target = self.scope / "am4-fleet-node" / "results" / "capacity-facts-2026-07-18.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc), encoding="utf-8")


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

    def test_moe_signals_add_llama_server_entry(self) -> None:
        self.write_capacity_facts()
        stdout = _gather_stdout(moe=MOE_PAYLOAD)
        api = {"object": "list", "data": [{"id": "gpt-oss-120b"}]}
        with patch("subprocess.run", return_value=_completed(stdout=stdout)), \
             patch("hearth.toolsurface.am4._probe_moe_models", return_value=api):
            catalog = gather_am4_catalog()
        by_id = {m["model_id"]: m for m in catalog["models"]}
        self.assertIn("gpt-oss-120b", by_id)
        moe = by_id["gpt-oss-120b"]
        self.assertEqual(moe["served_by"], "llama-server")
        self.assertEqual(moe["placement"], "dual")
        self.assertEqual(moe["per_card_gb"], 30.3)
        self.assertEqual(moe["expected_gen_tps"], 29.0)
        self.assertTrue(moe["serving"])
        self.assertEqual(moe["port"], 8082)
        # the vllama entry is untouched alongside it
        self.assertIn("qwen2.5-14b-q4", by_id)

    def test_moe_signals_without_probe_or_facts_still_catalogs(self) -> None:
        stdout = _gather_stdout(moe=MOE_PAYLOAD)
        with patch("subprocess.run", return_value=_completed(stdout=stdout)):
            catalog = gather_am4_catalog()  # no token in env -> probe skipped
        by_id = {m["model_id"]: m for m in catalog["models"]}
        moe = by_id["gpt-oss-120b"]
        self.assertTrue(moe["serving"])  # from unit_active
        self.assertIsNone(moe["expected_gen_tps"])  # no facts file in scope

    def test_no_moe_block_keeps_catalog_vllama_only(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout=_gather_stdout())):
            catalog = gather_am4_catalog()
        self.assertEqual([m["model_id"] for m in catalog["models"]], ["qwen2.5-14b-q4"])


class ProbeMoeModelsTests(Am4ScopedTestCase):
    def test_no_token_skips_probe_entirely(self) -> None:
        opener_calls = []
        result = _probe_moe_models(opener=lambda *a, **k: opener_calls.append(a))
        self.assertIsNone(result)
        self.assertEqual(opener_calls, [])

    def test_token_present_returns_parsed_document(self) -> None:
        import io
        from contextlib import contextmanager

        @contextmanager
        def opener(request, timeout):
            self.assertEqual(request.get_header("Authorization"), "Bearer sekret")
            yield io.BytesIO(json.dumps({"data": [{"id": "gpt-oss-120b"}]}).encode())

        os.environ["AM4_OXEN_TOKEN"] = "sekret"
        try:
            result = _probe_moe_models(opener=opener)
        finally:
            os.environ.pop("AM4_OXEN_TOKEN", None)
        self.assertEqual(result, {"data": [{"id": "gpt-oss-120b"}]})

    def test_unreachable_lane_returns_none(self) -> None:
        import urllib.error

        def opener(request, timeout):
            raise urllib.error.URLError("down")

        os.environ["AM4_OXEN_TOKEN"] = "sekret"
        try:
            result = _probe_moe_models(opener=opener)
        finally:
            os.environ.pop("AM4_OXEN_TOKEN", None)
        self.assertIsNone(result)


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
