"""Tests for tools/telemetry/collect_physical.py (stream C2).

Adapted from cc-builder-2's lap material (a384889), rewritten against
cc-builder-1's actual API: summarize(temp_series, power_series, clock_series)
-> dict, _normalize_profile_id(name, driver_version) -> str, collect_mock(path),
_query_nvidia().  Structural schema checks follow the house set-inclusion
pattern (stdlib only; jsonschema is NOT installed).
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.telemetry.collect_physical import (
    _normalize_profile_id,
    _query_nvidia,
    collect_mock,
    summarize,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "contracts" / "capacity-observation.v1.schema.json"
MODULE = "tools.telemetry.collect_physical"

# Physical keys the collector emits (model_residency intentionally absent).
EXPECTED_PHYSICAL_KEYS = {
    "gpu_temp_c_peak",
    "gpu_temp_c_sustained_avg",
    "power_w_avg",
    "power_w_peak",
    "fan_rpm_avg",
    "clock_mhz_avg",
    "model_loaded_at_start",
    "model_load_count",
    "model_unload_count",
    "model_load_s",
}


def _py_cmd(body):
    """Build a shell command string invoking this interpreter with `body`."""
    exe = sys.executable
    if " " in exe:
        exe = f'"{exe}"'
    return f"{exe} {body}"


def _run_cli(wrap, out_path, extra_args=(), env=None):
    """Run the collector CLI from the repo root; return CompletedProcess."""
    cmd = [
        sys.executable, "-m", MODULE,
        "--wrap", wrap,
        "--out", str(out_path),
    ] + list(extra_args)
    return subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
        env=env, timeout=120,
    )


class TestSummarize(unittest.TestCase):
    """Pure summary math: exact values, back-ceil rule, n=1, empty series."""

    def test_empty_series_all_null(self):
        result = summarize([], [], [])
        self.assertEqual(set(result.keys()), EXPECTED_PHYSICAL_KEYS)
        for key, value in result.items():
            self.assertIsNone(value, f"{key} should be None for empty series")

    def test_single_sample(self):
        result = summarize([70.0], [150.0], [1800.0])
        self.assertEqual(result["gpu_temp_c_peak"], 70.0)
        self.assertEqual(result["gpu_temp_c_sustained_avg"], 70.0)
        self.assertEqual(result["power_w_avg"], 150.0)
        self.assertEqual(result["power_w_peak"], 150.0)
        self.assertEqual(result["clock_mhz_avg"], 1800.0)
        self.assertIsNone(result["fan_rpm_avg"])

    def test_even_series_exact_values(self):
        temps = [60.0, 65.0, 70.0, 75.0]
        power = [120.0, 130.0, 140.0, 150.0]
        clock = [1600.0, 1700.0, 1800.0, 1900.0]
        result = summarize(temps, power, clock)
        self.assertEqual(result["gpu_temp_c_peak"], 75.0)
        # back half of 4 = last 2 samples
        self.assertEqual(result["gpu_temp_c_sustained_avg"], (70.0 + 75.0) / 2)
        self.assertEqual(result["power_w_avg"], 135.0)
        self.assertEqual(result["power_w_peak"], 150.0)
        self.assertEqual(result["clock_mhz_avg"], 1750.0)

    def test_odd_series_back_ceil_rule(self):
        temps = [60.0, 65.0, 70.0, 75.0, 80.0]
        result = summarize(temps, [100.0] * 5, [1500.0] * 5)
        # odd n: back ceil(5/2) = 3 samples -> mean(70, 75, 80)
        self.assertEqual(result["gpu_temp_c_sustained_avg"], (70.0 + 75.0 + 80.0) / 3)
        self.assertEqual(result["gpu_temp_c_peak"], 80.0)

    def test_fan_and_model_fields_always_null(self):
        result = summarize([70.0], [150.0], [1800.0])
        self.assertIsNone(result["fan_rpm_avg"])
        self.assertIsNone(result["model_loaded_at_start"])
        self.assertIsNone(result["model_load_count"])
        self.assertIsNone(result["model_unload_count"])
        self.assertIsNone(result["model_load_s"])


class TestHardwareProfileId(unittest.TestCase):
    """Normalization: lowercase, spaces become dashes."""

    def test_normalization(self):
        self.assertEqual(
            _normalize_profile_id("NVIDIA GeForce RTX 5070", "576.02"),
            "nvidia-geforce-rtx-5070|576.02",
        )

    def test_mock_source_first_row_and_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "samples.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    [{"name": "RTX 5070", "driver_version": "535.123",
                      "temp_c": 70, "power_w": 150, "fan_pct": 60,
                      "clock_mhz": 1800}],
                    f,
                )
            profile_id, temps, power, clock = collect_mock(path)
            self.assertEqual(profile_id, "rtx-5070|535.123")
            self.assertEqual(temps, [70])
            self.assertEqual(power, [150])
            self.assertEqual(clock, [1800])

            empty_path = os.path.join(tmp, "empty.json")
            with open(empty_path, "w", encoding="utf-8") as f:
                json.dump([], f)
            profile_id, temps, power, clock = collect_mock(empty_path)
            self.assertIsNone(profile_id)
            self.assertEqual((temps, power, clock), ([], [], []))


class TestMockEndToEnd(unittest.TestCase):
    """Mock source through the CLI: whole file is the series, --out written,
    wrapped exit code propagates."""

    MOCK_ROWS = [
        {"name": "NVIDIA GeForce RTX 5070", "driver_version": "576.02",
         "temp_c": t, "power_w": p, "fan_pct": 40, "clock_mhz": c}
        for t, p, c in [
            (60.0, 120.0, 1600.0),
            (65.0, 130.0, 1700.0),
            (70.0, 140.0, 1800.0),
            (75.0, 150.0, 1900.0),
            (80.0, 160.0, 2000.0),
        ]
    ]

    def _write_mock(self, tmp):
        path = os.path.join(tmp, "samples.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.MOCK_ROWS, f)
        return path

    def test_mock_end_to_end_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            mock_path = self._write_mock(tmp)
            out_path = os.path.join(tmp, "physical.json")
            proc = _run_cli(
                _py_cmd('-c "pass"'), out_path,
                ["--source", f"mock:{mock_path}"],
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out_path, encoding="utf-8") as f:
                output = json.load(f)
            self.assertEqual(
                output["hardware_profile_id"],
                "nvidia-geforce-rtx-5070|576.02",
            )
            physical = output["physical"]
            # Values prove the ENTIRE mock file was used as the series,
            # regardless of how fast the wrapped command exited.
            self.assertEqual(physical["gpu_temp_c_peak"], 80.0)
            self.assertEqual(
                physical["gpu_temp_c_sustained_avg"], (70.0 + 75.0 + 80.0) / 3
            )
            self.assertEqual(physical["power_w_avg"], 140.0)
            self.assertEqual(physical["power_w_peak"], 160.0)
            self.assertEqual(physical["clock_mhz_avg"], 1800.0)
            self.assertIsNone(physical["fan_rpm_avg"])

    def test_mock_nonzero_exit_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            mock_path = self._write_mock(tmp)
            out_path = os.path.join(tmp, "physical.json")
            proc = _run_cli(
                _py_cmd('-c "import sys; sys.exit(7)"'), out_path,
                ["--source", f"mock:{mock_path}"],
            )
            self.assertEqual(proc.returncode, 7)
            # Output is still written before exiting with the wrapped code.
            self.assertTrue(os.path.exists(out_path))


class TestMissingSourceDegradation(unittest.TestCase):
    """nvidia source with nvidia-smi absent: all-null physical, null
    hardware_profile_id, wrapped command still runs, exit code propagates,
    exactly one stderr warning."""

    def test_nvidia_smi_absent(self):
        # PATH tricks cannot hide nvidia-smi on Windows (it lives in
        # System32, which CreateProcess searches regardless of PATH), so
        # simulate absence by monkeypatching the module's subprocess.run to
        # raise FileNotFoundError for nvidia-smi ONLY — the wrapped command
        # still executes for real through the un-patched runner.
        import tools.telemetry.collect_physical as cp

        real_run = subprocess.run

        def selective_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args")
            if isinstance(cmd, list) and cmd and cmd[0] == "nvidia-smi":
                raise FileNotFoundError("nvidia-smi not found (simulated)")
            return real_run(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "physical.json")
            # Sleep long enough that the sampler thread attempts (and warns)
            # at least once; exit 5 proves propagation.
            wrap = _py_cmd('-c "import time, sys; time.sleep(1.2); sys.exit(5)"')
            argv = [
                "collect_physical",
                "--wrap", wrap,
                "--out", out_path,
                "--source", "nvidia",
                "--interval-s", "0.2",
            ]
            stderr_buf = io.StringIO()
            with patch(f"{MODULE}.subprocess.run", side_effect=selective_run), \
                    patch.object(sys, "argv", argv), \
                    contextlib.redirect_stderr(stderr_buf):
                with self.assertRaises(SystemExit) as ctx:
                    cp.main()
            self.assertEqual(ctx.exception.code, 5)
            with open(out_path, encoding="utf-8") as f:
                output = json.load(f)
            self.assertIsNone(output["hardware_profile_id"])
            for key, value in output["physical"].items():
                self.assertIsNone(value, f"{key} should be None without nvidia-smi")
            stderr_text = stderr_buf.getvalue()
            self.assertEqual(
                stderr_text.count("WARNING: nvidia-smi unavailable"), 1,
                f"expected exactly one warning, stderr was: {stderr_text!r}",
            )


class TestNvidiaNATolerance(unittest.TestCase):
    """[N/A] tolerance: an unparseable numeric column nulls THAT field only;
    the sample is kept."""

    @staticmethod
    def _fake_smi(stdout_line):
        return SimpleNamespace(returncode=0, stdout=stdout_line + "\n", stderr="")

    def test_na_fan_keeps_sample(self):
        row = "NVIDIA GeForce RTX 5070, 576.02, 65, 250.5, [N/A], 2800"
        with patch(f"{MODULE}.subprocess.run", return_value=self._fake_smi(row)):
            sample = _query_nvidia()
        self.assertIsNotNone(sample)
        self.assertEqual(sample["temp_c"], 65.0)
        self.assertEqual(sample["power_w"], 250.5)
        self.assertEqual(sample["clock_mhz"], 2800.0)

    def test_na_temp_nulls_field_only(self):
        row = "NVIDIA GeForce RTX 5070, 576.02, [N/A], 250.5, 40, 2800"
        with patch(f"{MODULE}.subprocess.run", return_value=self._fake_smi(row)):
            sample = _query_nvidia()
        self.assertIsNotNone(sample)
        self.assertIsNone(sample["temp_c"])
        self.assertEqual(sample["power_w"], 250.5)
        self.assertEqual(sample["clock_mhz"], 2800.0)

    def test_all_na_numerics_kept_when_identified(self):
        # name/driver present: the sample still identifies the hardware.
        row = "NVIDIA GeForce RTX 5070, 576.02, [N/A], [N/A], [N/A], [N/A]"
        with patch(f"{MODULE}.subprocess.run", return_value=self._fake_smi(row)):
            sample = _query_nvidia()
        self.assertIsNotNone(sample)
        self.assertEqual(sample["name"], "NVIDIA GeForce RTX 5070")
        self.assertIsNone(sample["temp_c"])
        self.assertIsNone(sample["power_w"])
        self.assertIsNone(sample["clock_mhz"])

    def test_fully_unparseable_row_discarded(self):
        row = ", , [N/A], [N/A], [N/A], [N/A]"
        with patch(f"{MODULE}.subprocess.run", return_value=self._fake_smi(row)):
            sample = _query_nvidia()
        self.assertIsNone(sample)


class TestSchemaSubset(unittest.TestCase):
    """Emitted physical keys are a subset of observed.physical properties in
    contracts/capacity-observation.v1.schema.json, with declared-nullable
    types; model_residency is NEVER emitted."""

    # JSON Schema type name -> acceptable Python types (bool is not a number).
    TYPE_MAP = {
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "string": str,
    }

    def test_emitted_keys_subset_and_nullable(self):
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.loads(f.read())
        physical_props = (
            schema["properties"]["observed"]["properties"]["physical"]["properties"]
        )
        emitted = summarize([60.0, 70.0], [120.0, 140.0], [1600.0, 1800.0])

        self.assertNotIn("model_residency", emitted)
        self.assertTrue(
            set(emitted.keys()) <= set(physical_props.keys()),
            f"emitted keys not a schema subset: "
            f"{set(emitted.keys()) - set(physical_props.keys())}",
        )
        for key, value in emitted.items():
            declared = physical_props[key]["type"]
            self.assertIn(
                "null", declared, f"{key} is not declared nullable in the schema"
            )
            if value is not None:
                non_null = [t for t in declared if t != "null"]
                self.assertEqual(len(non_null), 1, f"{key}: ambiguous type {declared}")
                expected = self.TYPE_MAP[non_null[0]]
                self.assertIsInstance(value, expected, f"{key}={value!r}")
                if expected == (int, float) or expected is int:
                    self.assertNotIsInstance(
                        value, bool, f"{key} must not be boolean"
                    )


if __name__ == "__main__":
    unittest.main()
