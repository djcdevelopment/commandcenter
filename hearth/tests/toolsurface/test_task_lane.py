from __future__ import annotations

import base64
import json
import subprocess
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.task_lane import (
    DEFAULT_BUILDERS,
    submit_task,
    task_status,
)


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr)


class SubmitTaskTests(TestCase):
    def test_writes_inbox_file_with_ccmeta_header_and_prefixed_plan_id(self) -> None:
        captured = {}

        def runner(args, **kw):
            captured["args"] = args
            return _completed(stdout="written\n")

        with patch("subprocess.run", side_effect=runner):
            result = submit_task("list three risks of X", plan_id_hint="risk-brief")

        self.assertTrue(result["ok"])
        self.assertTrue(result["plan_id"].startswith("hearth-"))
        self.assertIn("risk-brief", result["plan_id"])
        self.assertEqual(result["builders"], DEFAULT_BUILDERS)
        self.assertIn("inbox_path", result)
        self.assertIn(result["plan_id"], result["inbox_path"])

        ssh_cmd = captured["args"]
        self.assertEqual(ssh_cmd[0], "ssh")
        remote_command = ssh_cmd[-1]
        self.assertIn("base64 -d", remote_command)
        self.assertIn("mkdir -p", remote_command)

        # Decode the base64 payload embedded in the remote command to verify
        # the CCMETA header + prompt body shape.
        b64_segment = remote_command.split("echo ", 1)[1].split(" | base64", 1)[0]
        decoded = base64.b64decode(b64_segment).decode("utf-8")
        self.assertTrue(decoded.startswith("<!-- CCMETA"))
        self.assertIn('"am4-worker-1"', decoded)
        self.assertIn("list three risks of X", decoded)

    def test_default_builders_meet_fanout_minimum(self) -> None:
        # The conductor's fan-out needs >= 2 targets; the default must satisfy it.
        self.assertGreaterEqual(len(DEFAULT_BUILDERS), 2)
        self.assertEqual(len(DEFAULT_BUILDERS), len(set(DEFAULT_BUILDERS)))

    def test_custom_builders_forwarded(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_task("q", builders=["cc-builder-1", "cc-builder-2"])
        self.assertEqual(result["builders"], ["cc-builder-1", "cc-builder-2"])

    def test_single_builder_is_padded_to_fanout_minimum(self) -> None:
        # A one-builder request would crash the conductor fan-out; it must be
        # padded to >= 2 distinct builders, caller's choice kept first.
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_task("q", builders=["am4-worker-1"])
        self.assertGreaterEqual(len(result["builders"]), 2)
        self.assertEqual(result["builders"][0], "am4-worker-1")
        self.assertEqual(len(result["builders"]), len(set(result["builders"])))

    def test_single_builder_padding_prefers_local_companion(self) -> None:
        # Padding must not reach for the frontier (claude/sonnet) builder
        # cc-builder-1 when a local companion is available.
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_task("q", builders=["am4-worker-1"])
        self.assertIn("cc-builder-2", result["builders"])
        self.assertNotIn("cc-builder-1", result["builders"])

    def test_ssh_failure_is_a_clean_result_not_an_exception(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
            result = submit_task("q")
        self.assertFalse(result["ok"])
        self.assertIn("plan_id", result)
        self.assertIn("TimeoutExpired", result["error"])

    def test_nonzero_ssh_exit_is_reported(self) -> None:
        with patch("subprocess.run",
                   return_value=_completed(stdout="", stderr="permission denied", returncode=255)):
            result = submit_task("q")
        self.assertFalse(result["ok"])
        self.assertIn("permission denied", result["error"])

    def test_empty_prompt_rejected(self) -> None:
        with self.assertRaises(ValueError):
            submit_task("   ")

    def test_empty_builders_list_rejected(self) -> None:
        with self.assertRaises(ValueError):
            submit_task("q", builders=[])

    def test_plan_id_without_hint_still_prefixed(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_task("q")
        self.assertTrue(result["plan_id"].startswith("hearth-"))

    def test_task_class_parameter_sets_ledger_key(self) -> None:
        """submit_task(task_class="build") sets _ledger_task_class in result."""
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_task("q", task_class="build")
        self.assertTrue(result["ok"])
        self.assertIn("_ledger_task_class", result)
        self.assertEqual(result["_ledger_task_class"], "build")

    def test_est_tokens_parameter_included_in_result(self) -> None:
        """submit_task(est_tokens=500) includes est_tokens in result."""
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_task("q", est_tokens=500)
        self.assertTrue(result["ok"])
        self.assertIn("est_tokens", result)
        self.assertEqual(result["est_tokens"], 500)

    def test_task_class_and_est_tokens_together(self) -> None:
        """submit_task with both task_class and est_tokens includes both."""
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_task("q", task_class="research", est_tokens=1000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["_ledger_task_class"], "research")
        self.assertEqual(result["est_tokens"], 1000)

    def test_task_class_and_est_tokens_omitted_when_not_provided(self) -> None:
        """submit_task without task_class/est_tokens omits the keys."""
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_task("q")
        self.assertTrue(result["ok"])
        self.assertNotIn("_ledger_task_class", result)
        self.assertNotIn("est_tokens", result)


class TaskStatusTests(TestCase):
    def test_no_result_yet_reports_done_false_not_error(self) -> None:
        with patch("subprocess.run",
                   return_value=_completed(stdout="__HEARTH_NO_RESULT__\n")):
            result = task_status("hearth-abc123")
        self.assertTrue(result["ok"])
        self.assertFalse(result["done"])

    def test_result_present_returns_parsed_json(self) -> None:
        payload = {"plan_id": "hearth-abc123", "winner": "am4-worker-1"}
        with patch("subprocess.run",
                   return_value=_completed(stdout=json.dumps(payload))):
            result = task_status("hearth-abc123")
        self.assertTrue(result["ok"])
        self.assertTrue(result["done"])
        self.assertEqual(result["result"], payload)

    def test_ssh_failure_is_a_clean_result(self) -> None:
        with patch("subprocess.run", side_effect=OSError("no route to host")):
            result = task_status("hearth-abc123")
        self.assertFalse(result["ok"])
        self.assertFalse(result["done"])

    def test_malformed_json_is_reported_not_raised(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="{not json")):
            result = task_status("hearth-abc123")
        self.assertFalse(result["ok"])
        self.assertIn("non-JSON", result["error"])

    def test_empty_plan_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            task_status("")
