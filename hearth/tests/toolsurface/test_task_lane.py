from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.task_lane import (
    DEFAULT_BUILDERS,
    get_tools,
    queue_status,
    submit_batch,
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

    def test_out_file_must_be_non_empty_when_given(self) -> None:
        with self.assertRaises(ValueError):
            task_status("hearth-abc123", out_file="   ")


class TaskStatusOutFileTests(TestCase):
    """out_file lands the full result in a scoped file and returns only an ACK."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.scope = Path(self._tmp.name).resolve()
        self._prev = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._prev
        self._tmp.cleanup()

    def test_done_result_written_to_out_file_and_only_ack_returned(self) -> None:
        payload = {"plan_id": "hearth-abc123", "winner": "am4-worker-1", "ok": True,
                   "big": "x" * 5000}
        with patch("subprocess.run", return_value=_completed(stdout=json.dumps(payload))):
            result = task_status("hearth-abc123", out_file="runs/out/abc.json")

        self.assertTrue(result["ok"])
        self.assertTrue(result["done"])
        # The big blob must NOT come back inline — only an ACK.
        self.assertNotIn("result", result)
        self.assertIn("out_file", result)
        self.assertEqual(result["bytes_written"], len(json.dumps(payload).encode("utf-8")))
        # Cheap scalars lifted for the caller.
        self.assertEqual(result["winner"], "am4-worker-1")
        self.assertTrue(result["result_ok"])
        self.assertTrue(result["parse_ok"])
        # File is inside the sandbox and holds the full result text.
        written = Path(result["out_file"])
        self.assertTrue(written.is_relative_to(self.scope))
        self.assertEqual(json.loads(written.read_text(encoding="utf-8")), payload)

    def test_out_file_not_written_when_run_unfinished(self) -> None:
        target = self.scope / "runs" / "out" / "pending.json"
        with patch("subprocess.run", return_value=_completed(stdout="__HEARTH_NO_RESULT__\n")):
            result = task_status("hearth-pending", out_file="runs/out/pending.json")
        self.assertTrue(result["ok"])
        self.assertFalse(result["done"])
        self.assertFalse(target.exists())

    def test_out_file_escaping_sandbox_is_rejected(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout='{"ok": true}')):
            with self.assertRaises(ValueError):
                task_status("hearth-abc123", out_file="../escape.json")

    def test_out_file_written_even_when_result_not_json(self) -> None:
        # A completed-but-unparseable result still lands as full text; parse_ok flags it.
        with patch("subprocess.run", return_value=_completed(stdout="{not json")):
            result = task_status("hearth-abc123", out_file="runs/out/raw.txt")
        self.assertTrue(result["done"])
        self.assertFalse(result["parse_ok"])
        self.assertEqual(Path(result["out_file"]).read_text(encoding="utf-8"), "{not json")


class QueueStatusTests(TestCase):
    def test_parses_counts_from_conductor(self) -> None:
        with patch("subprocess.run",
                   return_value=_completed(stdout="queued=2 running=1 done=5 hearth_queued=1\n")):
            result = queue_status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["queued"], 2)
        self.assertEqual(result["running"], 1)
        self.assertEqual(result["done"], 5)
        self.assertEqual(result["hearth_queued"], 1)

    def test_one_ssh_round_trip(self) -> None:
        calls = {"n": 0}

        def runner(args, **kw):
            calls["n"] += 1
            return _completed(stdout="queued=0 running=0 done=0 hearth_queued=0\n")

        with patch("subprocess.run", side_effect=runner):
            queue_status()
        self.assertEqual(calls["n"], 1)

    def test_ssh_failure_is_clean_result(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=15)):
            result = queue_status()
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_garbled_output_defaults_to_zero(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="unexpected\n")):
            result = queue_status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["queued"], 0)


class SubmitBatchTests(TestCase):
    def test_fans_out_each_item_via_submit_task(self) -> None:
        writes = {"n": 0}

        def runner(args, **kw):
            writes["n"] += 1
            return _completed(stdout="written\n")

        manifest = [
            {"prompt": "brief one", "plan_id_hint": "one"},
            {"prompt": "brief two", "builders": ["cc-builder-1", "cc-builder-2"],
             "task_class": "research"},
        ]
        with patch("subprocess.run", side_effect=runner):
            result = submit_batch(manifest)

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["plan_ids"]), 2)
        # One inbox write per item — the same single-submit mechanism, N times.
        self.assertEqual(writes["n"], 2)
        for pid in result["plan_ids"]:
            self.assertTrue(pid.startswith("hearth-"))
        # Per-item builders forwarded.
        self.assertEqual(result["submitted"][1]["builders"], ["cc-builder-1", "cc-builder-2"])

    def test_partial_failure_is_visible_per_task(self) -> None:
        with patch("subprocess.run",
                   return_value=_completed(stdout="", stderr="denied", returncode=255)):
            result = submit_batch([{"prompt": "a"}, {"prompt": "b"}])
        self.assertFalse(result["ok"])
        self.assertEqual(result["plan_ids"], [])
        self.assertFalse(result["submitted"][0]["ok"])

    def test_empty_manifest_rejected(self) -> None:
        with self.assertRaises(ValueError):
            submit_batch([])

    def test_non_list_manifest_rejected(self) -> None:
        with self.assertRaises(ValueError):
            submit_batch({"prompt": "x"})  # type: ignore[arg-type]

    def test_item_without_prompt_rejected_before_any_write(self) -> None:
        # A malformed item must abort the whole batch before any inbox write.
        with patch("subprocess.run", return_value=_completed(stdout="written\n")) as m:
            with self.assertRaises(ValueError):
                submit_batch([{"prompt": "ok one"}, {"builders": ["am4-worker-1"]}])
        m.assert_not_called()

    def test_bad_builders_type_rejected(self) -> None:
        with self.assertRaises(ValueError):
            submit_batch([{"prompt": "x", "builders": "not-a-list"}])


class GetToolsTests(TestCase):
    def test_exposes_all_four_task_lane_tools(self) -> None:
        names = {fn.__name__ for fn in get_tools()}
        self.assertEqual(
            names, {"submit_task", "task_status", "queue_status", "submit_batch"})
