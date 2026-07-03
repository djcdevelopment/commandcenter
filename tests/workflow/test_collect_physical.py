import json
import os
import tempfile
import unittest
from unittest.mock import patch, mock_open

from tools.telemetry.collect_physical import compute_summary, get_hardware_profile_id, collect_mock_samples

class TestCollectPhysical(unittest.TestCase):

    def test_compute_summary_empty(self):
        """Test that empty input returns all nulls."""
        result = compute_summary([])
        expected = {
            "gpu_temp_c_peak": None,
            "gpu_temp_c_sustained_avg": None,
            "power_w_avg": None,
            "power_w_peak": None,
            "fan_rpm_avg": None,
            "clock_mhz_avg": None
        }
        self.assertEqual(result, expected)

    def test_compute_summary_single(self):
        """Test with a single sample."""
        samples = [
            {"temp_c": 70, "power_w": 150, "clock_mhz": 1800}
        ]
        result = compute_summary(samples)
        expected = {
            "gpu_temp_c_peak": 70,
            "gpu_temp_c_sustained_avg": 70,
            "power_w_avg": 150,
            "power_w_peak": 150,
            "fan_rpm_avg": None,
            "clock_mhz_avg": 1800
        }
        self.assertEqual(result, expected)

    def test_compute_summary_multiple(self):
        """Test with multiple samples, verifying sustained_avg is mean of second half."""
        samples = [
            {"temp_c": 60, "power_w": 120, "clock_mhz": 1600},
            {"temp_c": 65, "power_w": 130, "clock_mhz": 1700},
            {"temp_c": 70, "power_w": 140, "clock_mhz": 1800},
            {"temp_c": 75, "power_w": 150, "clock_mhz": 1900}
        ]
        result = compute_summary(samples)
        expected = {
            "gpu_temp_c_peak": 75,
            "gpu_temp_c_sustained_avg": (70 + 75) / 2,  # mean of last 2
            "power_w_avg": 135,
            "power_w_peak": 150,
            "fan_rpm_avg": None,
            "clock_mhz_avg": 1750
        }
        self.assertEqual(result, expected)

    def test_compute_summary_odd_length(self):
        """Test with odd-length series; sustained_avg should be mean of ceil(n/2) samples."""
        samples = [
            {"temp_c": 60, "power_w": 120, "clock_mhz": 1600},
            {"temp_c": 65, "power_w": 130, "clock_mhz": 1700},
            {"temp_c": 70, "power_w": 140, "clock_mhz": 1800},
            {"temp_c": 75, "power_w": 150, "clock_mhz": 1900},
            {"temp_c": 80, "power_w": 160, "clock_mhz": 2000}
        ]
        result = compute_summary(samples)
        expected = {
            "gpu_temp_c_peak": 80,
            "gpu_temp_c_sustained_avg": (70 + 75 + 80) / 3,  # mean of last 3 (ceil(5/2)=3)
            "power_w_avg": 140,
            "power_w_peak": 160,
            "fan_rpm_avg": None,
            "clock_mhz_avg": 1800
        }
        self.assertEqual(result, expected)

    def test_get_hardware_profile_id(self):
        """Test hardware profile ID extraction and normalization."""
        samples = [
            {"name": "RTX 5070", "driver_version": "535.123"}
        ]
        result = get_hardware_profile_id(samples)
        expected = "rtx-5070|535.123"
        self.assertEqual(result, expected)

    def test_get_hardware_profile_id_empty(self):
        """Test that empty samples return None."""
        result = get_hardware_profile_id([])
        self.assertIsNone(result)

    def test_collect_mock_samples_valid(self):
        """Test reading valid mock data."""
        mock_data = [
            {"name": "RTX 5070", "driver_version": "535.123", "temp_c": 70, "power_w": 150, "fan_pct": 60, "clock_mhz": 1800}
        ]
        with patch("builtins.open", mock_open(read_data=json.dumps(mock_data))):
            result = collect_mock_samples("/fake/path")
        self.assertEqual(result, mock_data)

    def test_collect_mock_samples_invalid(self):
        """Test reading invalid mock data."""
        with patch("builtins.open", mock_open(read_data="not a list")):
            result = collect_mock_samples("/fake/path")
        self.assertEqual(result, [])

    def test_collect_mock_samples_file_error(self):
        """Test handling of file read errors."""
        with patch("builtins.open", side_effect=IOError("File not found")):
            result = collect_mock_samples("/fake/path")
        self.assertEqual(result, [])

if __name__ == "__main__":
    unittest.main()