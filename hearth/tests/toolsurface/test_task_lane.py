from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface import task_lane
from hearth.toolsurface.task_lane import (
    DEFAULT_BUILDERS,
    DEFAULT_OUTPUT_ALLOWANCE_TOKENS,
    OUTPUT_ALLOWANCE_TOKENS,
    estimate_tokens,
    get_tools,
    queue_status,
    submit_batch,
    submit_task,
    task_status,
)


def _decode_ccmeta(remote_command: str) -> tuple[dict, str]:
    """Decode the base64 inbox payload out of the captured SSH command and
    split it into (CCMETA dict, prompt body)."""
    b64_segment = remote_command.split("echo ", 1)[1].split(" | base64", 1)[0]
    decoded = base64.b64decode(b64_segment).decode("utf-8")
    header, _, body = decoded.partition("-->\n")
    meta = json.loads(header.split("<!-- CCMETA", 1)[1])
    return meta, body


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
        # Assert against DEFAULT_BUILDERS rather than a literal name: the roster
        # is live infrastructure and gets re-pointed when a rung dies (2026-08-29
        # moved this off am4-worker-1, whose backend had gone dark). The invariant
        # under test is "the default builders reach the CCMETA header", not which
        # machines happen to be alive this month.
        for builder in DEFAULT_BUILDERS:
            self.assertIn(f'"{builder}"', decoded)
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

    def test_task_class_omitted_but_est_tokens_derived_when_not_provided(self) -> None:
        """submit_task without task_class leaves the ledger override out (the
        gateway's static derivation stands) -- but est_tokens is NEVER left
        empty any more (token hole #1): it is derived and labeled as such."""
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_task("q")
        self.assertTrue(result["ok"])
        self.assertNotIn("_ledger_task_class", result)
        self.assertNotIn("task_class", result)
        self.assertEqual(result["est_tokens"], estimate_tokens("q", None))
        self.assertEqual(result["est_tokens_source"], "derived")

    def test_stale_am4_worker_docstring_is_gone(self) -> None:
        # The submit_task docstring named ["am4-worker-1", "cc-builder-2"] as the
        # default long after the roster moved off am4-worker-1 (2026-08-29).
        # The docstring must describe the live roster by reference, not a
        # dead literal.
        self.assertNotIn("am4-worker-1", submit_task.__doc__ or "")
        self.assertIn("DEFAULT_BUILDERS", submit_task.__doc__ or "")


class EstimateTokensTests(TestCase):
    """estimate_tokens is pure: same inputs, same number; no I/O."""

    def test_returns_int_and_is_deterministic(self) -> None:
        a = estimate_tokens("write a proposal about X", "research")
        b = estimate_tokens("write a proposal about X", "research")
        self.assertIsInstance(a, int)
        self.assertEqual(a, b)

    def test_longer_prompt_costs_more(self) -> None:
        short = estimate_tokens("x" * 40, "research")
        long = estimate_tokens("x" * 4000, "research")
        self.assertGreater(long, short)
        # ~4 chars/token: 4000 chars -> 1000 prompt tokens on top of the allowance.
        self.assertEqual(long - short, (4000 - 40) // 4)

    def test_prompt_tokens_are_ceil_of_chars_over_four(self) -> None:
        allowance = OUTPUT_ALLOWANCE_TOKENS["research"]
        self.assertEqual(estimate_tokens("", "research"), allowance)
        self.assertEqual(estimate_tokens("a", "research"), allowance + 1)
        self.assertEqual(estimate_tokens("abcd", "research"), allowance + 1)
        self.assertEqual(estimate_tokens("abcde", "research"), allowance + 2)

    def test_task_class_selects_its_output_allowance(self) -> None:
        prompt = "same prompt"
        for task_class, allowance in OUTPUT_ALLOWANCE_TOKENS.items():
            with self.subTest(task_class=task_class):
                self.assertEqual(estimate_tokens(prompt, task_class),
                                 estimate_tokens(prompt, None)
                                 - DEFAULT_OUTPUT_ALLOWANCE_TOKENS + allowance)

    def test_unknown_or_missing_class_uses_the_default_allowance(self) -> None:
        self.assertEqual(estimate_tokens("", None), DEFAULT_OUTPUT_ALLOWANCE_TOKENS)
        self.assertEqual(estimate_tokens("", "no-such-class"), DEFAULT_OUTPUT_ALLOWANCE_TOKENS)

    def test_default_allowance_matches_hindsights_flat_default(self) -> None:
        # A derived estimate must never fall BELOW what hindsight would have
        # assumed anyway (hearth/scheduler/hindsight.py DEFAULT_EST_TOKENS).
        self.assertEqual(DEFAULT_OUTPUT_ALLOWANCE_TOKENS, 2000)

    def test_non_string_prompt_rejected(self) -> None:
        with self.assertRaises(ValueError):
            estimate_tokens(None, "research")  # type: ignore[arg-type]

    def test_not_exposed_as_a_tool(self) -> None:
        # A pure helper, not a door: the tool surface stays exactly four.
        self.assertNotIn("estimate_tokens", {fn.__name__ for fn in get_tools()})


class TokenHoleStampTests(TestCase):
    """Token hole #1: every submit stamps task_class / est_tokens, on the result
    AND in the CCMETA header the conductor reads."""

    def _submit(self, *args, **kwargs) -> tuple[dict, dict, str]:
        captured = {}

        def runner(cmd, **kw):
            captured["cmd"] = cmd[-1]
            return _completed(stdout="written\n")

        with patch("subprocess.run", side_effect=runner):
            result = submit_task(*args, **kwargs)
        meta, body = _decode_ccmeta(captured["cmd"])
        return result, meta, body

    def test_absent_est_tokens_is_derived_from_prompt_and_class(self) -> None:
        prompt = "design a rubric for grading research briefs " * 20
        result, meta, body = self._submit(prompt, task_class="research")
        expected = estimate_tokens(prompt, "research")
        self.assertEqual(result["est_tokens"], expected)
        self.assertEqual(result["est_tokens_source"], "derived")
        self.assertEqual(result["task_class"], "research")
        self.assertEqual(result["_ledger_task_class"], "research")
        # The header carries the same stamps so the conductor can copy them
        # into result.json (hole #2).
        self.assertEqual(meta["builders"], DEFAULT_BUILDERS)
        self.assertEqual(meta["task_class"], "research")
        self.assertEqual(meta["est_tokens"], expected)
        self.assertEqual(meta["est_tokens_source"], "derived")
        self.assertEqual(body, prompt)

    def test_caller_est_tokens_is_kept_verbatim_and_labeled(self) -> None:
        result, meta, _ = self._submit("q", task_class="build", est_tokens=777)
        self.assertEqual(result["est_tokens"], 777)
        self.assertEqual(result["est_tokens_source"], "caller")
        self.assertEqual(meta["est_tokens"], 777)
        self.assertEqual(meta["est_tokens_source"], "caller")
        self.assertEqual(meta["task_class"], "build")

    def test_header_omits_task_class_when_caller_gave_none(self) -> None:
        result, meta, _ = self._submit("q")
        self.assertNotIn("task_class", meta)
        self.assertIn("est_tokens", meta)
        self.assertEqual(meta["est_tokens_source"], "derived")
        self.assertEqual(result["est_tokens"], meta["est_tokens"])

    def test_header_stays_one_json_object_between_the_markers(self) -> None:
        # conductor_maf.py's _extract_ccmeta parses the JSON between the
        # markers; the added keys must not break the shape it already reads.
        captured = {}

        def runner(cmd, **kw):
            captured["cmd"] = cmd[-1]
            return _completed(stdout="written\n")

        with patch("subprocess.run", side_effect=runner):
            submit_task("q", task_class="research", est_tokens=5)
        b64_segment = captured["cmd"].split("echo ", 1)[1].split(" | base64", 1)[0]
        decoded = base64.b64decode(b64_segment).decode("utf-8")
        self.assertTrue(decoded.startswith("<!-- CCMETA\n"))
        header_json = decoded.split("<!-- CCMETA\n", 1)[1].split("\n-->\n", 1)[0]
        self.assertEqual(json.loads(header_json)["builders"], DEFAULT_BUILDERS)
        self.assertEqual(decoded.count("<!-- CCMETA"), 1)

    def test_integral_float_est_tokens_accepted_as_int(self) -> None:
        # JSON-over-MCP may hand an integral number back as a float.
        result, meta, _ = self._submit("q", est_tokens=1500.0)
        self.assertEqual(result["est_tokens"], 1500)
        self.assertIsInstance(result["est_tokens"], int)
        self.assertEqual(result["est_tokens_source"], "caller")

    def test_bad_est_tokens_rejected_before_any_write(self) -> None:
        for bad in (-1, True, "lots", 12.5, [1]):
            with self.subTest(bad=bad):
                with patch("subprocess.run", return_value=_completed(stdout="written\n")) as m:
                    with self.assertRaises(ValueError):
                        submit_task("q", est_tokens=bad)  # type: ignore[arg-type]
                m.assert_not_called()

    def test_blank_task_class_rejected(self) -> None:
        with self.assertRaises(ValueError):
            submit_task("q", task_class="   ")

    def test_failed_write_still_carries_the_stamps(self) -> None:
        # The ledger event of a failed dispatch must not lose its class or
        # estimate just because SSH hiccupped.
        with patch("subprocess.run",
                   return_value=_completed(stdout="", stderr="denied", returncode=255)):
            result = submit_task("q", task_class="research")
        self.assertFalse(result["ok"])
        self.assertEqual(result["_ledger_task_class"], "research")
        self.assertEqual(result["est_tokens"], estimate_tokens("q", "research"))
        self.assertEqual(result["est_tokens_source"], "derived")

    def test_batch_items_each_get_an_estimate(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="written\n")):
            result = submit_batch([
                {"prompt": "one", "task_class": "research"},
                {"prompt": "two", "task_class": "build", "est_tokens": 42},
            ])
        self.assertTrue(result["ok"])
        first, second = result["submitted"]
        self.assertEqual(first["est_tokens"], estimate_tokens("one", "research"))
        self.assertEqual(first["est_tokens_source"], "derived")
        self.assertEqual(second["est_tokens"], 42)
        self.assertEqual(second["est_tokens_source"], "caller")

    def test_batch_bad_est_tokens_rejected_before_any_write(self) -> None:
        with patch("subprocess.run", return_value=_completed(stdout="written\n")) as m:
            with self.assertRaises(ValueError):
                submit_batch([{"prompt": "ok"}, {"prompt": "bad", "est_tokens": -3}])
        m.assert_not_called()

    def test_ccmeta_header_helper_shape(self) -> None:
        header = task_lane._ccmeta_header(["a", "b"], task_class="proofing",
                                          est_tokens=9, est_tokens_source="caller")
        meta = json.loads(header.split("<!-- CCMETA\n", 1)[1].split("\n-->\n", 1)[0])
        self.assertEqual(meta, {"builders": ["a", "b"], "task_class": "proofing",
                                "est_tokens": 9, "est_tokens_source": "caller"})
        # Bare form is byte-identical to the pre-M3 header.
        self.assertEqual(task_lane._ccmeta_header(["a", "b"]),
                         '<!-- CCMETA\n{"builders": ["a", "b"]}\n-->\n')


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
                   return_value=_completed(
                       stdout="queued=2 running=1 done=5 hearth_queued=1 "
                              "running_undispatched=1\n")):
            result = queue_status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["queued"], 2)
        self.assertEqual(result["running"], 1)
        self.assertEqual(result["done"], 5)
        self.assertEqual(result["hearth_queued"], 1)
        self.assertEqual(result["running_undispatched"], 1)

    def test_running_counts_every_run_dir_and_splits_out_the_undispatched(self) -> None:
        # ONE definition of a run (ADR-0033): the occupancy universe is every
        # runs/<id>/ dir keyed on result.json — the same universe patrol sweeps.
        # nodes.json only splits the count; it never filters it.
        captured = {}

        def runner(args, **kw):
            captured["cmd"] = args[-1]
            return _completed(stdout="queued=0 running=0 done=0 hearth_queued=0 "
                                     "running_undispatched=0\n")

        with patch("subprocess.run", side_effect=runner):
            queue_status()
        cmd = captured["cmd"]
        self.assertIn("result.json", cmd)
        self.assertIn("nodes.json", cmd)
        # nodes.json must never gate the sweep: the only `continue` is the
        # nullglob guard, and the nodes.json test sits inside the else-branch
        # (after result.json) where it can only sub-count what already counted.
        self.assertEqual(cmd.count("continue"), 1)
        self.assertLess(cmd.index("result.json"), cmd.index("nodes.json"))

    def test_older_conductor_output_without_the_split_still_parses(self) -> None:
        with patch("subprocess.run",
                   return_value=_completed(stdout="queued=0 running=2 done=185 hearth_queued=0\n")):
            result = queue_status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["running"], 2)
        self.assertEqual(result["running_undispatched"], 0)

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
