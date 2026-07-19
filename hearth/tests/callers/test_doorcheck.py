"""doorcheck v2 tests: toolsurface manifest, backends layer, restart isolation.

STRICT isolation (per the checkmcp v2 brief): nothing here may open a socket to
the live gateway (:8710), invoke the real `schtasks`, run real `gcloud`, or
kill anything. Every external effect is mocked. The manifest-parser test does
import the real toolsurface provider modules (that's the whole point — it is
the SAME source of truth the gateway itself reads at boot) but that is a pure
import + get_tools() call, never a network operation.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.callers import doorcheck
from hearth.toolsurface import backends as backends_mod

BUILD_REQUEST_TOOLS = {
    "create_build_request", "get_build_request", "list_build_requests",
    "update_build_request", "execute_build_request", "close_build_request",
}


class ManifestParserTests(TestCase):
    """Parses --providers from the REAL start-hearth-gateway.cmd and imports
    every module it names — the same authority the gateway itself boots from."""

    def test_real_start_cmd_yields_expected_manifest(self) -> None:
        names, errors = doorcheck._expected_manifest()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(names), 41)
        for tool in BUILD_REQUEST_TOOLS:
            self.assertIn(tool, names)
        self.assertIn("kernel_status", names)
        self.assertIn("kernel_change", names)

    def test_providers_parsed_from_start_cmd_line(self) -> None:
        providers = doorcheck._providers_from_start_cmd()
        self.assertIn("hearth.toolsurface.build_requests", providers)
        self.assertIn("hearth.toolsurface.fs", providers)
        # no stray whitespace/redirect tokens leaked into a module name
        for name in providers:
            self.assertNotIn(" ", name)
            self.assertNotIn(">>", name)


class ToolsurfaceReportTests(TestCase):
    """Stale-door detection: a live tool-name set that is missing tools (the
    2026-07-12 incident shape — build_requests landed in the launcher but the
    running process predated it) must report not-ok with the gap named."""

    def test_matching_live_set_reports_match(self) -> None:
        expected, _ = doorcheck._expected_manifest()
        report = doorcheck._toolsurface_report(set(expected))
        self.assertTrue(report["ok"])
        self.assertEqual(report["line"], f"toolsurface: {len(expected)}/{len(expected)} match")

    def test_stale_door_missing_six_tools_is_not_ok(self) -> None:
        expected, _ = doorcheck._expected_manifest()
        live = expected - BUILD_REQUEST_TOOLS
        report = doorcheck._toolsurface_report(live)

        self.assertFalse(report["ok"])
        self.assertEqual(sorted(report["missing"]), sorted(BUILD_REQUEST_TOOLS))
        self.assertEqual(report["extra"], [])
        self.assertIn("STALE", report["line"])
        self.assertIn("missing 6", report["line"])
        for tool in BUILD_REQUEST_TOOLS:
            self.assertIn(tool, report["line"])

    def test_unexpected_extra_tool_is_not_ok(self) -> None:
        expected, _ = doorcheck._expected_manifest()
        live = set(expected) | {"ghost_tool"}
        report = doorcheck._toolsurface_report(live)

        self.assertFalse(report["ok"])
        self.assertIn("ghost_tool", report["extra"])
        self.assertIn("unexpected", report["line"])

    def test_no_live_handshake_reports_unknown_not_ok(self) -> None:
        report = doorcheck._toolsurface_report(None)
        self.assertFalse(report["ok"])
        self.assertIn("unknown", report["line"])


class GeminiAuthWarningTests(TestCase):
    """gcp-gemini auth failure is a WARNING on the backend entry, never fatal
    to the overall verdict. No real gcloud is ever invoked — subprocess.run and
    the gateway's own _gcloud_executable helper are mocked."""

    def test_auth_failure_reported_as_warning(self) -> None:
        backend = backends_mod.Backend(
            name="gcp-gemini",
            endpoint="https://aiplatform.googleapis.com",
            api="gemini",
            auth_env="GOOGLE_OAUTH_ACCESS_TOKEN",
        )
        auth_fail = subprocess.CompletedProcess(
            args=["gcloud"], returncode=1, stdout="", stderr="not logged in")
        with patch.dict(os.environ, {}, clear=True):
            with patch("hearth.toolsurface.inference._gcloud_executable", return_value="gcloud"):
                with patch("subprocess.run", return_value=auth_fail) as run_mock:
                    entry = doorcheck._backend_status(backend, probe_cloud=False)

        self.assertFalse(entry["auth_ok"])
        self.assertIn("auth FAILED", entry["line"])
        # informational/warning: never gates the exit-code verdict
        self.assertIsNone(entry["up"])
        run_mock.assert_called_once()  # never a live gcloud process

    def test_auth_failure_does_not_affect_default_backend_up(self) -> None:
        pool_toml = textwrap.dedent("""
            default = "omen-ollama"

            [[backend]]
            name = "omen-ollama"
            endpoint = "http://127.0.0.1:11434"
            api = "ollama"
            models = ["qwen3-coder:30b"]
            tags = ["default"]

            [[backend]]
            name = "gcp-gemini"
            endpoint = "https://aiplatform.googleapis.com"
            api = "gemini"
            models = ["gemini-3.5-flash"]
            tags = ["frontier"]
        """)
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pool_path = tmp / "backends.toml"
        pool_path.write_text(pool_toml, encoding="utf-8")

        auth_fail = subprocess.CompletedProcess(
            args=["gcloud"], returncode=1, stdout="", stderr="not logged in")
        with patch.dict(os.environ, {"HEARTH_BACKENDS": str(pool_path)}, clear=False):
            with patch("hearth.toolsurface.inference._gcloud_executable", return_value="gcloud"):
                with patch("subprocess.run", return_value=auth_fail):
                    with patch("hearth.callers.doorcheck._ollama_version",
                               return_value="0.11.0"):
                        report = doorcheck._backends_report(probe_cloud=False)

        self.assertTrue(report["default_up"])
        gemini_entry = next(b for b in report["backends"] if b["name"] == "gcp-gemini")
        self.assertFalse(gemini_entry["auth_ok"])
        self.assertIn("FAILED", gemini_entry["line"])


class RestartIsolationTests(TestCase):
    """--restart must trigger the scheduled task by name only — never taskkill,
    never a direct process kill — and must never touch a real socket."""

    def test_restart_calls_schtasks_run_only(self) -> None:
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="")
            with patch("hearth.callers.doorcheck._tcp_up", side_effect=[False, True]):
                with patch("time.sleep"):
                    result = doorcheck._restart(timeout_s=5)

        run_mock.assert_called_once_with(
            ["schtasks", "/Run", "/TN", "HearthGatewayRestart"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertTrue(result["triggered"])
        self.assertTrue(result["saw_down"])
        self.assertTrue(result["up"])

    def test_restart_never_shells_out_to_taskkill(self) -> None:
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="")
            with patch("hearth.callers.doorcheck._tcp_up", return_value=True):
                with patch("time.sleep"):
                    doorcheck._restart(timeout_s=1)

        for call in run_mock.call_args_list:
            args = call.args[0] if call.args else call.kwargs.get("args")
            self.assertNotIn("taskkill", args)


class BuildRequestLaneTests(TestCase):
    """Informational lane line; a missing directory/ledger is not a failure."""

    def test_missing_dir_reports_no_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            with patch.dict(os.environ, {"HEARTH_BUILD_REQUEST_DIR": str(missing)}):
                result = doorcheck._build_request_lane()
        self.assertEqual(result, {"ok": True, "line": "build-reqs: no lane"})

    def test_last_ledger_line_reported(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        ledger = tmp / "ledger.jsonl"
        ledger.write_text(
            '{"receipt_id": "br-old", "status": "done", "updated_utc": "2020-01-01T00:00:00Z"}\n'
            '{"receipt_id": "br-new", "status": "running", "updated_utc": "2020-01-01T00:00:00Z"}\n',
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"HEARTH_BUILD_REQUEST_DIR": str(tmp)}):
            result = doorcheck._build_request_lane()
        self.assertEqual(result["receipt_id"], "br-new")
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["line"].startswith("build-reqs: br-new running ("))


class FacetAndExitTests(TestCase):
    """Phase 4 contract: door health is independent from backend readiness."""

    def _check(self, *, listener=True, auth=True, surface=True, backend=True,
               config_error=None, strict=False):
        mcp = {"ok": True, "auth_ok": auth, "tools": 1,
               "tool_names": ["kernel_status"], "handshake_ms": 1}
        with patch.object(doorcheck, "_tcp_up", return_value=listener), \
             patch.object(doorcheck, "_mcp_handshake", return_value=mcp), \
             patch.object(doorcheck, "_toolsurface_report", return_value={"ok": surface, "line": "surface"}), \
             patch.object(doorcheck, "_backends_report", return_value={
                 "backends": [], "default_up": backend,
                 "config_error": config_error}), \
             patch.object(doorcheck, "_last_ledger_event", return_value=None), \
             patch.object(doorcheck, "_build_request_lane", return_value={"ok": True, "line": "lane"}), \
             patch.object(doorcheck, "_trial_burn_report", return_value="trial"):
            return doorcheck.check(strict=strict)

    def test_door_up_backend_cold_default_passes_strict_fails(self):
        report = self._check(backend=False)
        self.assertTrue(report["ok"])
        self.assertEqual(report["facets"]["backend_dependency"], "cold")
        strict = self._check(backend=False, strict=True)
        self.assertFalse(strict["ok"])

    def test_door_down_fails_process_facet(self):
        report = self._check(listener=False)
        self.assertFalse(report["ok"])
        self.assertEqual(report["facets"]["process_listener"], "down")
        self.assertEqual(report["facets"]["authentication"], "unknown")

    def test_auth_failure_is_distinct(self):
        report = self._check(auth=False)
        self.assertFalse(report["ok"])
        self.assertEqual(report["facets"]["authentication"], "failed")

    def test_tool_surface_failure_is_distinct(self):
        report = self._check(surface=False)
        self.assertFalse(report["ok"])
        self.assertEqual(report["facets"]["mcp_surface"], "degraded")

    def test_fully_healthy(self):
        report = self._check()
        self.assertTrue(report["ok"])
        self.assertTrue(all(v == "healthy" for v in report["facets"].values()))

    def test_malformed_backend_configuration_is_hard_failure(self):
        report = self._check(config_error="invalid TOML")
        self.assertFalse(report["ok"])
        self.assertEqual(report["facets"]["backend_dependency"], "failed")
        self.assertTrue(report["hard_failure"])

    def test_default_and_strict_cli_forward_distinct_contracts(self):
        healthy = {
            "gateway": "up", "revived": False, "mcp": {},
            "toolsurface": {"line": "surface"}, "backends": [],
            "last_ledger_event": None, "build_requests": {"line": "lane"},
            "trial_burn_line": "trial", "facets": {
                "process_listener": "healthy", "authentication": "healthy",
                "mcp_surface": "healthy", "backend_dependency": "cold"},
            "requested_facet": "door", "strict": False,
            "hard_failure": False, "ok": True,
        }
        with patch.object(doorcheck, "check", return_value=healthy) as check_mock:
            self.assertEqual(doorcheck.main([]), 0)
        check_mock.assert_called_once_with(
            revive=False, probe_cloud=False, facet="door", strict=False)

        strict = dict(healthy)
        strict.update({"requested_facet": "door", "strict": True, "ok": False})
        with patch.object(doorcheck, "check", return_value=strict) as check_mock:
            self.assertEqual(doorcheck.main(["--strict"]), 1)
        check_mock.assert_called_once_with(
            revive=False, probe_cloud=False, facet="door", strict=True)
